#!/usr/bin/env python3
"""Re-harvest: crawl every zh-CN page live again, extract FRESH signed asset URLs,
build path->freshurl map, download every missing asset file, then rewrite nothing
(paths are stable). Signatures expire ~1h so harvest first, download immediately."""
import re, os, json, sys, time, urllib.parse
import concurrent.futures as cf
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crawler

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = crawler.OUT

# 1. list zh pages + their local paths
pages = []
zh_dir = os.path.join(SITE, 'zh-CN')
for root, dirs, fns in os.walk(zh_dir):
    for fn in fns:
        if fn.endswith('.html'):
            rel = os.path.relpath(os.path.join(root, fn), SITE)
            pages.append(rel)
print("zh pages:", len(pages))

# 2. check which asset files are missing
def disk_ok(rel):
    return os.path.isfile(os.path.join(SITE, rel))
# collect referenced paths
ref_pat = re.compile(r'/assets/[A-Za-z0-9/_.%\-]+')
referenced = {}
def scan(rel):
    with open(os.path.join(SITE, rel), encoding='utf-8') as f:
        html = f.read()
    for m in ref_pat.finditer(html):
        p = m.group(0)
        referenced.setdefault(p, rel)
with cf.ThreadPoolExecutor(max_workers=8) as ex:
    list(ex.map(scan, pages))
print("referenced asset paths:", len(referenced))
missing = [p for p in referenced if not disk_ok(p.lstrip('/'))]
print("missing on disk:", len(missing))

# 3. map missing local path back to origin URL path using state
state = json.load(open(os.path.join(HERE, 'state', 'state.json'), encoding='utf-8'))
local2url = {}
for local, a in state['assets'].items():
    local2url['/' + local] = a['url']
    if a.get('final'):
        local2url.setdefault('/' + a['final'], a['url'])
# For attach assets, origin path = path part of URL (strip query)
def origin_path(local_ref):
    u = local2url.get(local_ref)
    if not u:
        return None
    p = urllib.parse.urlsplit(u)
    return p.hostname, p.path

# 4. re-fetch zh pages live to harvest FRESH signed URLs for needed origin paths
need_origin = {}
for ref in missing:
    hp = origin_path(ref)
    if hp:
        need_origin[(hp[0], hp[1])] = ref
print("distinct origin paths needed:", len(need_origin))

# page urls (from state) for zh-CN
page_urls = [u for u, s in state['pages'].items() if s.get('rel', '').startswith('zh-CN/') and s.get('status') in ('done', 'redir')]
print("zh page urls:", len(page_urls))

harvest = {}
found_pages = set()
lock_h = __import__('threading').Lock()

ORIGIN_HOSTS = ('onekey-38b2143bfbb5.intercom-attachments-7.com', 'downloads.intercomcdn.com')

def harvest_page(u):
    if len(found_pages) > 0 and False:
        pass
    content, ct = crawler.fetch_url(u, redirect=True)
    if content is None:
        return
    html = content.decode('utf-8', errors='replace')
    got = 0
    for m in re.finditer(r'https?://([a-z0-9.-]+\.intercom-attachments-\d+\.com|downloads\.intercomcdn\.com)(/i/o/[^"\'\s<>\\]+?)(?:\?[^"\'\s<>\\]*)?(?=["\'<>\s])', html):
        host, path = m.group(1), m.group(2)
        key = (host, urllib.parse.unquote(path))
        with lock_h:
            if key in need_origin and key not in harvest:
                harvest[key] = m.group(0)
                got += 1
    if got:
        with lock_h:
            found_pages.add(u)

print("harvesting fresh signed urls from live pages...")
t0 = time.time()
with cf.ThreadPoolExecutor(max_workers=6) as ex:
    list(ex.map(harvest_page, page_urls))
print(f"harvested {len(harvest)}/{len(need_origin)} fresh urls in {time.time()-t0:.0f}s")

# also try matching by path only (host may differ: downloads vs attachments)
by_path = {}
for (host, path), ref in need_origin.items():
    by_path.setdefault(path, ref)
harvest_by_path = {}
for (host, path), url in harvest.items():
    harvest_by_path.setdefault(path, url)
matched = {path: url for path, url in harvest_by_path.items() if path in by_path}
print("path-matched:", len(matched))

# 5. download missing assets using fresh urls
def dl(item):
    path, url = item
    ref = by_path[path]
    local = ref.lstrip('/')
    content, ct = crawler.fetch_url(url)
    if content is None:
        return 'fail'
    ext = crawler.sniff_ext(content, ct)
    final = local if (ext and local.endswith(ext)) else (local + (ext or ''))
    full = os.path.join(SITE, final)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'wb') as f:
        f.write(content)
    # if we appended ext, fix refs in all html
    if final != local:
        for root, dirs, fns in os.walk(SITE):
            for fn in fns:
                if not fn.endswith('.html'):
                    continue
                fp = os.path.join(root, fn)
                with open(fp, encoding='utf-8') as f:
                    html = f.read()
                if '/' + local in html:
                    with open(fp, 'w', encoding='utf-8') as f:
                        f.write(html.replace('/' + local, '/' + final))
    return 'ok'

todo = list(matched.items())
print("downloading", len(todo), "missing assets...")
ok = fail = 0
with cf.ThreadPoolExecutor(max_workers=10) as ex:
    for r in ex.map(dl, todo):
        if r == 'ok':
            ok += 1
        else:
            fail += 1
print(f"downloaded ok={ok} fail={fail}")

# 6. any still-missing refs -> replace with transparent placeholder (layout intact)
PLACEHOLDER = 'assets/dead-image.png'
ph = os.path.join(SITE, PLACEHOLDER)
if not os.path.isfile(ph):
    os.makedirs(os.path.dirname(ph), exist_ok=True)
    png = bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082')
    with open(ph, 'wb') as f:
        f.write(png)
still = [p for p in referenced if not disk_ok(p.lstrip('/'))]
n = 0
for rel in pages:
    fp = os.path.join(SITE, rel)
    with open(fp, encoding='utf-8') as f:
        html = f.read()
    orig = html
    for p in still:
        if p in html:
            html = html.replace(p, '/' + PLACEHOLDER)
    if html != orig:
        with open(fp, 'w', encoding='utf-8') as f:
            f.write(html)
        n += 1
print(f"placeholders applied in {n} files for {len(still)} still-missing refs")
print("DONE")
