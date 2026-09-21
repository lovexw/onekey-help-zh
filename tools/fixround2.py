#!/usr/bin/env python3
"""Round 2 fixes:
A. Missing sub-collection pages (linked from breadcrumbs but not in sitemap):
   - discover /<loc>/collections/<id>-<slug> refs across all HTML whose target files don't exist
   - crawl them live (they return 200), process+save like normal pages
B. Double-extension references (.woff.woff / .png.png) -> strip the extra extension (files exist with single ext)
C. /cdn-cgi/l/email-protection links & script -> remove (Cloudflare injected; not part of real content)
   - script src /cdn-cgi/scripts/... : remove script tag
   - links /cdn-cgi/l/email-protection#... : replace with mailto: decoded if possible else '#'
D. /hc/<locale>/articles/... legacy links -> redirect via live 301 to new local path (already known: redirects work)
E. bare /articles/11536900-contact-us (with slug, no locale) -> they 200? treat: resolve via live fetch (no-follow) -> local path
F. other missing collections with WRONG slug but valid id+locale -> fix slug by finding local dir with same id prefix
G. stale local link targets: /<loc>/collections/<id>-<wrong-slug> where correct page exists with different slug -> rewrite to existing
"""
import re, os, json, sys, time, urllib.parse, random
import concurrent.futures as cf
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crawler

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = crawler.OUT
STATE = os.path.join(HERE, 'state', 'state.json')
state = json.load(open(STATE, encoding='utf-8'))

ATTR_RE = re.compile(r'''(\b(?:src|href|content|poster)\s*=\s*)(["'])(.*?)\2''', re.S)

def page_exists(urlpath):
    p = urllib.parse.unquote(urlpath.split('#')[0].split('?')[0])
    if not p.startswith('/'):
        p = '/' + p
    rel = p.lstrip('/')
    if not rel:
        return os.path.isfile(os.path.join(SITE, 'index.html'))
    for c in (os.path.join(SITE, rel, 'index.html'), os.path.join(SITE, rel + '.html'), os.path.join(SITE, rel)):
        if os.path.isfile(c):
            return True
    return False

def find_by_id(urlpath):
    """Find an existing local page whose dir starts with the same numeric id, same locale."""
    p = urllib.parse.unquote(urlpath.split('#')[0].split('?')[0])
    m = re.match(r'^/((?:[a-z]{2}(?:-[A-Z]{2})?)/)?(articles|collections)/(\d+)', p)
    if not m:
        return None
    loc, kind, num = m.group(1) or '', m.group(2), m.group(3)
    base = os.path.join(SITE, loc.strip('/'), kind) if loc else None
    if not base or not os.path.isdir(base):
        return None
    for name in os.listdir(base):
        if name.startswith(num + '-') or name == num:
            return f'/{loc.strip("/")}/{kind}/{name}' if loc else f'/{kind}/{name}'
    return None

# ---------- collect all broken internal refs ----------
print("[1/5] scanning broken refs...")
files = []
for root, dirs, fns in os.walk(SITE):
    for fn in fns:
        if fn.endswith('.html'):
            files.append(os.path.relpath(os.path.join(root, fn), SITE))

broken_by_kind = {'subcoll': set(), 'legacy_hc': set(), 'bare_slug': set(), 'cdn_cgi': 0, 'other': set()}
def scan(rel):
    full = os.path.join(SITE, rel)
    out = []
    with open(full, encoding='utf-8') as f:
        html = f.read()
    for m in ATTR_RE.finditer(html):
        v = m.group(3).strip()
        if not v.startswith('/') or '{REDIR' in v:
            continue
        p = v.split('#')[0].split('?')[0]
        if not p:
            continue
        if '/cdn-cgi/' in p:
            out.append(('cdn_cgi', p))
            continue
        if page_exists(p):
            continue
        if re.match(r'^/hc/', p):
            out.append(('legacy_hc', p))
        elif re.match(r'^/articles/\d+', p):
            out.append(('bare_slug', p))
        elif re.match(r'^/((?:[a-z]{2}(?:-[A-Z]{2})?)/)?collections/\d+', p):
            # sub-collection page we didn't crawl
            out.append(('subcoll', p))
        elif re.match(r'^/((?:[a-z]{2}(?:-[A-Z]{2})?)/)?articles/\d+', p):
            out.append(('subcoll', p))  # article page variant (wrong slug or missing)
        else:
            out.append(('other', p))
    return rel, out

with cf.ThreadPoolExecutor(max_workers=8) as ex:
    scan_results = list(ex.map(scan, files))

todo_urls = set()
for rel, items in scan_results:
    for kind, p in items:
        if kind in ('subcoll', 'legacy_hc', 'bare_slug'):
            todo_urls.add(p)
print(f"  unique missing page urls to resolve: {len(todo_urls)}")

# ---------- resolve missing pages live ----------
def resolve_live(p):
    """Fetch https://help.onekey.so<p> no-follow. Return ('page', final_path) or ('redir', target) or ('fail', code)."""
    u = crawler.BASE + urllib.parse.quote(p)
    content, ct = crawler.fetch_url(u, redirect=False)
    if isinstance(content, tuple) and content and content[0] == 'REDIR':
        loc = content[1]
        q = urllib.parse.urlsplit(loc)
        if (q.hostname == 'help.onekey.so' or not q.hostname) and q.path:
            return ('redir', urllib.parse.unquote(q.path))
        return ('fail', 'ext:' + loc[:60])
    if content is None:
        return ('fail', ct)
    return ('page', p)

print("[2/5] resolving missing urls live...")
res_map = {}
with cf.ThreadPoolExecutor(max_workers=8) as ex:
    for p, (kind2, val) in zip(todo_urls, ex.map(resolve_live, sorted(todo_urls))):
        res_map[p] = (kind2, val)
ok_pages = [p for p, (k, v) in res_map.items() if k == 'page']
ok_redirs = {p: v for p, (k, v) in res_map.items() if k == 'redir'}
fails = {p: v for p, (k, v) in res_map.items() if k == 'fail'}
print(f"  live 200: {len(ok_pages)}, redirects: {len(ok_redirs)}, fail: {len(fails)}")

# ---------- crawl the live-200 sub-collection pages ----------
print("[3/5] crawling discovered pages...")
new_assets = []
def crawl(p):
    u = crawler.BASE + urllib.parse.quote(p)
    content, ct = crawler.fetch_url(u, redirect=True)
    if content is None:
        return None
    html = content.decode('utf-8', errors='replace')
    html, na = crawler.process_html(html, u, state)
    rel = crawler.page_url_to_rel(u)
    full = os.path.join(SITE, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'w', encoding='utf-8') as f:
        f.write(html)
    state['pages'][u] = {'status': 'done', 'rel': rel, 'discovered': True}
    return na

with cf.ThreadPoolExecutor(max_workers=6) as ex:
    for na in ex.map(crawl, ok_pages):
        if na:
            new_assets.extend(na)
print(f"  crawled {len(ok_pages)} pages, {len(new_assets)} new asset refs")

# ---------- download new assets ----------
if new_assets:
    print("[3.5/5] downloading new assets...")
    pend = list({l: u for l, u in new_assets}.items())
    def dl(item):
        l, u = item
        status, final = crawler.download_asset(l, u)
        state['assets'][l] = {'url': u, 'status': 'ok' if status.startswith(('ok', 'exists')) else status, 'final': final}
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(dl, pend))

json.dump(state, open(STATE, 'w', encoding='utf-8'), ensure_ascii=False)

# ---------- rewrite all broken refs in HTML ----------
print("[4/5] rewriting refs in HTML...")
def map_target(p):
    """Map a broken ref to a fixed local target."""
    if p in res_map:
        k, v = res_map[p]
        if k == 'redir':
            return v
        if k == 'page':
            return urllib.parse.unquote(p)  # will exist after crawl
    by_id = find_by_id(p)
    if by_id:
        return by_id
    return None

# build id->existing map once for fast slug fixes
id_index = {}
for loc_dir in os.listdir(SITE):
    full_loc = os.path.join(SITE, loc_dir)
    if not os.path.isdir(full_loc) or loc_dir in ('assets',):
        continue
    for kind_dir in ('collections', 'articles'):
        kd = os.path.join(full_loc, kind_dir)
        if os.path.isdir(kd):
            for name in os.listdir(kd):
                m = re.match(r'^(\d+)', name)
                if m:
                    id_index[(loc_dir, kind_dir, m.group(1))] = f'/{loc_dir}/{kind_dir}/{name}'

def rewrite_val(v):
    p = v.split('#')[0].split('?')[0]
    if '/cdn-cgi/' in p:
        return '#'  # cloudflare email protection junk
    if re.match(r'^/hc/', p):
        # legacy zendesk-style link; resolve live? we already scanned; use res_map fallback to '#'
        t = map_target(p)
        return t or '#'
    t = map_target(p)
    if t:
        return t
    return None  # no fix found; leave

n_files_changed = 0
n_refs = 0
for rel, items in scan_results:
    if not items:
        continue
    full = os.path.join(SITE, rel)
    with open(full, encoding='utf-8') as f:
        html = f.read()
    orig = html
    def attr_repl(m):
        pre, q, v = m.group(1), m.group(2), m.group(3)
        vs = v.strip()
        if not vs.startswith('/') or '{REDIR' in vs:
            return m.group(0)
        p = vs.split('#')[0].split('?')[0]
        if not p:
            return m.group(0)
        if page_exists(p):
            return m.group(0)
        nv = rewrite_val(vs)
        if nv is None:
            return m.group(0)
        return f'{pre}{q}{nv}{q}'
    html = ATTR_RE.sub(attr_repl, html)
    # double-extension fixes
    html = html.replace('.woff.woff', '.woff').replace('.png.png', '.png')
    if html != orig:
        with open(full, 'w', encoding='utf-8') as f:
            f.write(html)
        n_files_changed += 1
print(f"  fixed files: {n_files_changed}")

# ---------- handle remaining cdn-cgi script tags + email-protection links ----------
print("[5/5] cdn-cgi cleanup pass...")
n2 = 0
pat_script = re.compile(r'<script[^>]*src="[^"]*/cdn-cgi/[^"]*"[^>]*>\s*</script>', re.S)
pat_link = re.compile(r'href="/cdn-cgi/l/email-protection[^"]*"')
pat_email = re.compile(r'<a [^>]*href="/cdn-cgi/l/email-protection[^"]*"(.*?)</a>', re.S)
for rel, items in scan_results:
    has_cdn = any(k == 'cdn_cgi' for k, p in items)
    if not has_cdn:
        continue
    full = os.path.join(SITE, rel)
    with open(full, encoding='utf-8') as f:
        html = f.read()
    orig = html
    html = pat_script.sub('', html)
    html = pat_link.sub('href="#"', html)
    if html != orig:
        with open(full, 'w', encoding='utf-8') as f:
            f.write(html)
        n2 += 1
print(f"  cdn-cgi cleaned in {n2} files")
json.dump(state, open(STATE, 'w', encoding='utf-8'), ensure_ascii=False)
print("DONE")
