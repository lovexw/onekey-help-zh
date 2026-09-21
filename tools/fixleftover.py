#!/usr/bin/env python3
"""Fix leftover broken references:
1. /assets/intercom-help/onekey-help/<loc>/articles/<id> links -> resolve redirect to local page path
2. Attach URLs that 404 (deleted assets) -> strip the <a> wrapper? No: point image to a transparent
   placeholder + keep alt text, so layout stays intact.
"""
import re, os, json, sys, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crawler

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = crawler.OUT
STATE = os.path.join(HERE, 'state', 'state.json')

state = json.load(open(STATE, encoding='utf-8'))

# --- 1. build bare-article redirect map for old intercom.help article links ---
# These are stored as /assets/intercom-help/onekey-help/<loc>/articles/<id>
pat = re.compile(r'/assets/intercom-help/onekey-help/((?:[a-z]{2}(?:-[A-Z]{2})?)/)?articles/(\d+)')

def resolve_article(loc, aid):
    # use fixredir logic via crawler session
    u = f'{crawler.BASE}/{loc}/articles/{aid}' if loc else f'{crawler.BASE}/articles/{aid}'
    s = crawler.get_session()
    try:
        r = s.get(u, timeout=30, allow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308):
            loc_hdr = r.headers.get('location', '')
            try:
                fixed = loc_hdr.encode('latin-1').decode('utf-8')
                if fixed and not fixed.startswith('\ufffd'):
                    loc_hdr = fixed
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
            p = urllib.parse.urlsplit(loc_hdr)
            if (p.hostname == 'help.onekey.so' or not p.hostname) and p.path:
                return urllib.parse.unquote(p.path)
            return loc_hdr
        if r.status_code == 200:
            return urllib.parse.unquote(urllib.parse.urlsplit(u).path)
    except Exception:
        pass
    return None

# resolve on demand with cache
res_cache = {}
def rep(m):
    loc = m.group(1) or ''
    aid = m.group(2)
    key = (loc, aid)
    if key not in res_cache:
        res_cache[key] = resolve_article(loc.strip('/'), aid)
    t = res_cache[key]
    return t if t else m.group(0)

fixed_pages = 0
total_hits = 0
for u, ps in list(state['pages'].items()):
    if ps.get('status') != 'done' and ps.get('status') != 'redir':
        continue
    rel = ps['rel']
    full = os.path.join(SITE, rel)
    if not os.path.exists(full):
        continue
    with open(full, encoding='utf-8') as f:
        html = f.read()
    if '/assets/intercom-help/onekey-help/' not in html:
        continue
    html2, n = pat.subn(rep, html)
    if n:
        with open(full, 'w', encoding='utf-8') as f:
            f.write(html2)
        fixed_pages += 1
        total_hits += n
print(f"old-intercom-help links fixed: {total_hits} in {fixed_pages} pages")

# --- 2. remove failed attach assets from state (they are dead on origin) ---
fails = [l for l, a in state['assets'].items() if a.get('status', '').startswith('fail')]
dead_attach = [l for l in fails if l.startswith('assets/attach/')]
print("dead attach assets:", len(dead_attach))

# replace references to dead attach assets with 1px transparent placeholder
PLACEHOLDER = 'assets/dead-image.png'
ph_full = os.path.join(SITE, PLACEHOLDER)
os.makedirs(os.path.dirname(ph_full), exist_ok=True)
# 1x1 transparent png
png = bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010806000000'
                    '1f15c4890000000d49444154789c626001000000ffff030000060005'
                    '57bfabd40000000049454e44ae426082')
with open(ph_full, 'wb') as f:
    f.write(png)

replaced = 0
for local in dead_attach:
    ref_old = '/' + local
    for u, ps in list(state['pages'].items()):
        rel = ps['rel']
        full = os.path.join(SITE, rel)
        if not os.path.exists(full):
            continue
        with open(full, encoding='utf-8') as f:
            html = f.read()
        if ref_old not in html:
            continue
        html = html.replace(ref_old, '/' + PLACEHOLDER)
        with open(full, 'w', encoding='utf-8') as f:
            f.write(html)
        replaced += 1
print(f"dead attach refs replaced in {replaced} page-visits")

# --- 3. mark old-intercom-help asset entries as resolved (page-redirects, not downloads) ---
for l in list(state['assets'].keys()):
    if l.startswith('assets/intercom-help/onekey-help/'):
        del state['assets'][l]
json.dump(state, open(STATE, 'w', encoding='utf-8'), ensure_ascii=False)
print("state cleaned")
