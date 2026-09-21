#!/usr/bin/env python3
"""
OneKey Help Center full mirror crawler. v2
Pipeline: pages -> scan-more -> assets -> fixredir -> finalize
"""
import re, os, sys, json, time, hashlib, urllib.parse, random, logging
import concurrent.futures as cf
from threading import Lock
import requests

BASE = 'https://help.onekey.so'
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, '..', 'site'))
STATE_DIR = os.path.join(HERE, 'state')
LOG_FILE = os.path.join(STATE_DIR, 'crawler.log')

os.makedirs(STATE_DIR, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8'), logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger('crawler')

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15'

ASSET_HOSTS = {}  # filled below

KEEP_REMOTE_DOMAINS = {
    'widget.intercom.io', 'js.intercomcdn.com', 'api-iam.intercom.io',
    'www.google-analytics.com', 'googletagmanager.com', 'www.googletagmanager.com',
    'cdn.cookielaw.org', 'www.intercom.com',
    'www.facebook.com', 'platform.twitter.com', 'fonts.intercomcdn.com',
}

LOCALES = ['zh-CN', 'zh-TW', 'en', 'ar', 'de', 'es', 'ja', 'ko', 'ms', 'pt', 'pt-BR', 'ru', 'th', 'tr', 'vi']

_session = None
def get_session():
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({'User-Agent': UA, 'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'})
        adapter = requests.adapters.HTTPAdapter(pool_connections=64, pool_maxsize=64)
        s.mount('https://', adapter)
        s.mount('http://', adapter)
        _session = s
    return _session

# ---------------- asset path mapping ----------------
def url_to_local(url):
    """Map remote asset URL -> local path under site/assets/ (no extension guessing here)."""
    p = urllib.parse.urlsplit(url)
    host = p.hostname or ''
    path = urllib.parse.unquote(p.path)
    if host == 'help.onekey.so':
        # fonts, favicon and other origin assets
        segs = [s for s in path.split('/') if s]
        name = segs[-1] if segs else 'root'
        h = hashlib.md5(path.encode()).hexdigest()[:10]
        base = re.sub(r'[^A-Za-z0-9._-]', '_', name)[:60] or 'asset'
        return f'assets/origin/{h}_{base}'
    if host == 'static.intercomassets.com':
        return 'assets/_next-static' + urllib.parse.unquote(p.path)
    if host == 'intercom.help':
        return 'assets/intercom-help' + urllib.parse.unquote(p.path)
    # cdn images & attachments
    if host.endswith('.intercomcdn.com'):
        d = 'assets/cdn'
    elif re.match(r'^onekey-[a-z0-9-]+\.intercom-attachments-\d+\.com$', host):
        d = 'assets/attach'
    else:
        d = 'assets/other'
    segs = [s for s in path.split('/') if s]
    if len(segs) >= 2:
        name = segs[-2] + '_' + segs[-1]
    elif segs:
        name = segs[-1]
    else:
        name = 'unnamed'
    h = hashlib.md5((host + path).encode()).hexdigest()[:10]
    ext = os.path.splitext(name)[1][:8]
    base = re.sub(r'[^A-Za-z0-9._-]', '_', name)[:80]
    if ext and not base.lower().endswith(ext.lower()):
        base = base + ext
    return f'{d}/{h}_{base}'

# ---------------- state ----------------
STATE_FILE = os.path.join(STATE_DIR, 'state.json')
_state_lock = Lock()

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding='utf-8') as f:
            return json.load(f)
    return {'pages': {}, 'assets': {}, 'redirects': {}, 'errors': []}

def save_state(state):
    for attempt in range(5):
        try:
            tmp = STATE_FILE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False)
            os.replace(tmp, STATE_FILE)
            return
        except RuntimeError:
            time.sleep(0.2 * (attempt + 1))
    log.warning('save_state failed after retries')

# ---------------- page path ----------------
def page_url_to_rel(u):
    p = urllib.parse.urlsplit(u)
    path = urllib.parse.unquote(p.path)
    path = path.strip('/')
    if not path:
        return 'index.html'
    return '/'.join(path.split('/')) + '/index.html'

# ---------------- URL rewriting ----------------
ATTR_RE = re.compile(r'''(\b(?:src|href|content|data-src|poster)\s*=\s*)(["'])(.*?)\2''', re.S)
# JSON-safe URL: includes \u0026 sequences (escaped ampersand inside JSON strings)
JSON_URL_RE = re.compile(r'''https?://[^\s"'<>\\)\]\u0000-\u001f]+(?:\\u0026[^\s"'<>\\)\]\u0000-\u001f]+)*''')

def is_asset_url(u):
    p = urllib.parse.urlsplit(u)
    host = p.hostname or ''
    if host == 'help.onekey.so':
        return p.path.startswith('/assets/')
    if host == 'static.intercomassets.com' or host == 'intercom.help':
        return True
    if host.endswith('.intercomcdn.com'):
        return True
    if re.match(r'^[a-z0-9-]+\.intercom-attachments-\d+\.com$', host):
        return True
    return False

def handle_url(raw, page_url, state, new_assets):
    """raw: URL text as it appears in doc (may contain &amp; or \\u0026). Returns replacement text."""
    if not raw or raw.startswith(('#', 'data:', 'javascript:', 'mailto:', 'tel:', '{')):
        return raw
    # fetchable form
    u = raw.replace('\\u0026', '&').replace('&amp;', '&')
    try:
        p = urllib.parse.urlsplit(u)
    except ValueError:
        return raw
    host = p.hostname or ''

    # --- internal pages ---
    if not p.scheme:
        if not raw.startswith('/'):
            return raw  # relative junk / non-URL values (meta content etc.)
        path = p.path
    elif host == 'help.onekey.so':
        path = p.path
        # origin-hosted assets (fonts etc.)
        if path.startswith('/assets/'):
            local = url_to_local(u)
            if local not in state['assets']:
                state['assets'][local] = {'url': u, 'status': 'pending'}
                new_assets.append((local, u))
            return '/' + local
        path = p.path
    else:
        # --- external asset hosts ---
        if host in KEEP_REMOTE_DOMAINS:
            return raw
        if is_asset_url(u):
            local = url_to_local(u)
            if local not in state['assets']:
                state['assets'][local] = {'url': u, 'status': 'pending'}
                new_assets.append((local, u))
            return '/' + local
        return raw  # ordinary external link (onekey.so, youtube embeds, ...)

    # internal page path handling
    if path == '/' or path == '':
        return '/index.html'
    m = re.match(r'^/((?:[a-z]{2}(?:-[A-Z]{2})?)?/?)?articles/(\d+)/?$', path)
    if m and '/' not in path.split('/articles/')[-1].strip('/'):
        # bare article id link -> redirect placeholder
        loc = (m.group(1) or '').strip('/')
        aid = m.group(2)
        key = f'{loc}/{aid}' if loc else aid
        state['redirects'].setdefault(key, None)
        return f'/{{REDIR:{key}}}'
    return urllib.parse.unquote(path)

def process_html(html, page_url, state):
    new_assets = []
    # pass 1: attributes
    def attr_repl(m):
        pre, q, val = m.group(1), m.group(2), m.group(3)
        if not val or not re.match(r'''^(https?://|/|[a-z0-9-]+\.[a-z0-9-])''', val):
            return m.group(0)
        nv = handle_url(val, page_url, state, new_assets)
        return f'{pre}{q}{nv}{q}'
    html = ATTR_RE.sub(attr_repl, html)
    # pass 2: script/style blocks only (JSON image URLs etc.)
    def block_repl(m):
        open_tag, body, close_tag = m.group(1), m.group(2), m.group(3)
        def url_repl(mm):
            return handle_url(mm.group(0), page_url, state, new_assets)
        return open_tag + JSON_URL_RE.sub(url_repl, body) + close_tag
    html = re.sub(r'(<script[^>]*>)(.*?)(</script>)', block_repl, html, flags=re.S)
    html = re.sub(r'(<style[^>]*>)(.*?)(</style>)', block_repl, html, flags=re.S)
    return html, new_assets

# ---------------- fetch ----------------
def fetch_url(u, referer=None, retries=4, redirect=True):
    s = get_session()
    headers = {'Referer': referer} if referer else {}
    last_err = None
    for attempt in range(retries):
        try:
            r = s.get(u, headers=headers, timeout=45, allow_redirects=redirect)
            if redirect:
                if r.status_code == 200:
                    return r.content, r.headers.get('content-type', '')
                if r.status_code in (429, 502, 503, 504):
                    time.sleep(2 + attempt * 3 + random.random() * 2)
                    continue
                return None, str(r.status_code)
            else:
                # no-follow mode: return redirect info
                if r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers.get('location', '')
                    # requests decodes headers as latin-1; fix UTF-8 mojibake
                    try:
                        fixed = loc.encode('latin-1').decode('utf-8')
                        if fixed and not fixed.startswith('\ufffd'):
                            loc = fixed
                    except (UnicodeEncodeError, UnicodeDecodeError):
                        pass
                    return ('REDIR', loc), 'redirect'
                if r.status_code == 200:
                    return r.content, r.headers.get('content-type', '')
                return None, str(r.status_code)
        except Exception as e:
            last_err = e
            time.sleep(1.5 + attempt * 1.5)
    return None, f'error:{last_err}'

MAGIC = [
    (b'\x89PNG\r\n\x1a\n', '.png'), (b'\xff\xd8\xff', '.jpg'), (b'GIF8', '.gif'),
    (b'RIFF', '.webp'), (b'%PDF', '.pdf'), (b'wOFF', '.woff'), (b'wOF2', '.woff2'),
    (b'\x00\x01\x00\x00', '.ttf'), (b'\x1a\x45\xdf\xa3', '.webm'),
]
def sniff_ext(data, ct=''):
    for magic, ext in MAGIC:
        if data.startswith(magic):
            if ext == '.webp' and data[8:12] != b'WEBP':
                continue
            return ext
    if data[:4] == b'\x00\x00\x01\x00':
        return '.ico'
    head = data[:600].lstrip()
    if head.startswith(b'<?xml') or head.startswith(b'<svg'):
        return '.svg'
    if data[4:8] == b'ftyp':
        return '.mp4'
    if 'image/png' in ct: return '.png'
    if 'jpeg' in ct: return '.jpg'
    if 'gif' in ct: return '.gif'
    if 'webp' in ct: return '.webp'
    if 'svg' in ct: return '.svg'
    if 'mp4' in ct: return '.mp4'
    if 'webm' in ct: return '.webm'
    if 'woff2' in ct: return '.woff2'
    if 'woff' in ct: return '.woff'
    if 'javascript' in ct or 'ecmascript' in ct: return '.js'
    if 'css' in ct: return '.css'
    return ''

def download_asset(local, url):
    """Returns (status, final_rel)."""
    full = os.path.join(OUT, local)
    final = local
    if not os.path.exists(full):
        content, ct = fetch_url(url)
        if content is None:
            return f'fail:{ct[:40]}', local
        os.makedirs(os.path.dirname(full), exist_ok=True)
        # decide final name with extension
        if not os.path.splitext(local)[1]:
            ext = sniff_ext(content, ct)
            if ext:
                final = local + ext
        with open(os.path.join(OUT, final), 'wb') as f:
            f.write(content)
    return 'ok', final

# ---------------- urls ----------------
def load_urls():
    with open(os.path.join(STATE_DIR, 'all_urls.txt'), encoding='utf-8') as f:
        return [u.strip() for u in f if u.strip()]

def priority_sort(urls):
    loc_order = {l: i for i, l in enumerate(LOCALES)}
    def key(u):
        p = urllib.parse.urlsplit(u).path
        seg = [s for s in p.split('/') if s]
        loc = loc_order.get(seg[0], 50) if seg else 0
        kind = 0 if (len(seg) <= 1) else (1 if seg[1] == 'collections' else 2)
        return (loc, kind, p)
    return sorted(set(urls), key=key)

# ---------------- phases ----------------
REDIR_PAGE_TPL = '''<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<title>{title}</title>
<meta http-equiv="refresh" content="0;url={target}">
<link rel="canonical" href="{target}">
<script>location.replace('{target_js}');</script>
</head>
<body><p><a href="{target}">{title}</a></p></body>
</html>
'''

def redirect_page(target, title='Redirecting…'):
    tj = target.replace("'", "\\'")
    return REDIR_PAGE_TPL.format(lang='zh-CN', title=title, target=target, target_js=tj)

def phase_pages(state):
    urls = load_urls()
    roots = [f'{BASE}/{l}/' for l in LOCALES]
    urls = roots + urls
    urls = priority_sort(urls)
    todo = [u for u in urls if state['pages'].get(u, {}).get('status') != 'done']
    log.info('[pages] todo=%d total=%d', len(todo), len(urls))
    lock = Lock()
    counters = {'done': 0, 'fail': 0, 'redir': 0}
    all_assets = []
    t0 = time.time()

    def work(u):
        rel = page_url_to_rel(u)
        # probe without following redirects first
        content, ct = fetch_url(u, referer=BASE + '/', redirect=False)
        if isinstance(content, tuple) and content and content[0] == 'REDIR':
            target = content[1]
            p2 = urllib.parse.urlsplit(target)
            if p2.hostname == 'help.onekey.so' or not p2.hostname:
                tpath = urllib.parse.unquote(p2.path)
            else:
                tpath = target
            os.makedirs(os.path.dirname(os.path.join(OUT, rel)), exist_ok=True)
            with open(os.path.join(OUT, rel), 'w', encoding='utf-8') as f:
                f.write(redirect_page(tpath, title='OneKey Help Center'))
            with lock:
                state['pages'][u] = {'status': 'redir', 'rel': rel, 'target': tpath}
                counters['redir'] += 1
            return
        if content is None:
            with lock:
                state['pages'][u] = {'status': 'fail', 'ct': ct, 'rel': rel}
                counters['fail'] += 1
            return
        html = content.decode('utf-8', errors='replace')
        html, new_assets = process_html(html, u, state)
        full = os.path.join(OUT, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w', encoding='utf-8') as f:
            f.write(html)
        with lock:
            state['pages'][u] = {'status': 'done', 'rel': rel}
            all_assets.extend(new_assets)
            counters['done'] += 1
            if counters['done'] % 50 == 0:
                save_state(state)
                log.info('[pages] %d done, %d fail, %d redir, %d assets queued, %.0fs',
                         counters['done'], counters['fail'], counters['redir'], len(state['assets']), time.time() - t0)

    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(work, todo))
    save_state(state)
    log.info('[pages] COMPLETE done=%d fail=%d redir=%d assets=%d %.0fs',
             counters['done'], counters['fail'], counters['redir'], len(state['assets']), time.time() - t0)

def phase_scan_more(state):
    """Catch asset URLs on domains that survived pass1 (returned as external links)."""
    PAT = re.compile(r'''https?://[^\s"'<>\\)\]]+(?:\\u0026[^\s"'<>\\)\]]+)*''')
    found = 0
    pages = [(u, s['rel']) for u, s in state['pages'].items() if s.get('status') == 'done']
    log.info('[scan-more] scanning %d files', len(pages))
    for u, rel in pages:
        full = os.path.join(OUT, rel)
        if not os.path.exists(full):
            continue
        html = open(full, encoding='utf-8').read()
        new_assets = []
        changed = False
        def repl(m):
            nonlocal changed
            raw = m.group(0)
            clean = raw.replace('\\u0026', '&')
            p = urllib.parse.urlsplit(clean)
            host = p.hostname or ''
            # broad intercom asset catch
            if (host.endswith('.intercomcdn.com') or host.endswith('.intercom-attachments-7.com')
                    or host in ('static.intercomassets.com', 'intercom.help')):
                local = url_to_local(clean)
                if local not in state['assets']:
                    state['assets'][local] = {'url': clean, 'status': 'pending'}
                changed = True
                return '/' + local
            return raw
        html2 = PAT.sub(repl, html)
        if changed:
            with open(full, 'w', encoding='utf-8') as f:
                f.write(html2)
            found += 1
    save_state(state)
    log.info('[scan-more] files changed=%d, total assets now=%d', found, len(state['assets']))

def phase_assets(state):
    pending = [(l, a['url']) for l, a in state['assets'].items() if a.get('status') in ('pending',) or ('final' not in a and a.get('status') == 'ok')]
    log.info('[assets] pending=%d', len(pending))
    lock = Lock()
    done = [0]
    t0 = time.time()
    def work(item):
        local, url = item
        status, final = download_asset(local, url)
        with lock:
            state['assets'][local]['status'] = 'ok' if status.startswith(('ok', 'exists')) else status
            state['assets'][local]['final'] = final
            done[0] += 1
            if done[0] % 200 == 0:
                save_state(state)
                log.info('[assets] %d/%d %.0fs', done[0], len(pending), time.time() - t0)
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(work, pending))
    save_state(state)
    fails = [l for l, a in state['assets'].items() if a.get('status', '').startswith('fail')]
    log.info('[assets] COMPLETE total=%d fails=%d %.0fs', len(state['assets']), len(fails), time.time() - t0)

def phase_fixredir(state):
    keys = [k for k, v in state['redirects'].items() if not v]
    log.info('[fixredir] resolving %d bare-article links', len(keys))
    def resolve(key):
        if '/' in key:
            loc, aid = key.split('/', 1)
        else:
            loc, aid = '', key
        u = f'{BASE}/{loc}/articles/{aid}' if loc else f'{BASE}/articles/{aid}'
        s = get_session()
        try:
            r = s.get(u, timeout=30, allow_redirects=False)
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get('location', '')
                try:
                    fixed = loc.encode('latin-1').decode('utf-8')
                    if fixed and not fixed.startswith('\ufffd'):
                        loc = fixed
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
                return key, loc
            elif r.status_code == 200:
                return key, u
            else:
                return key, f'error:{r.status_code}'
        except Exception as e:
            return key, f'error:{e}'
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for key, target in ex.map(resolve, keys):
            state['redirects'][key] = target
    save_state(state)
    ok = sum(1 for v in state['redirects'].values() if v and not v.startswith('error'))
    log.info('[fixredir] resolved=%d/%d', ok, len(state['redirects']))

def phase_finalize(state):
    """Rewrite REDIR placeholders + asset extension renames in all saved HTML."""
    # build replacement maps
    redir_map = {}
    for key, target in state['redirects'].items():
        if not target or target.startswith('error'):
            redir_map[key] = '/'
        else:
            p = urllib.parse.urlsplit(target)
            if (p.hostname == 'help.onekey.so' or not p.hostname) and p.path:
                redir_map[key] = urllib.parse.unquote(p.path)
            else:
                redir_map[key] = target
    asset_renames = []  # (old_path_str, new_path_str)
    for local, a in state['assets'].items():
        final = a.get('final', local)
        if final != local:
            asset_renames.append(('/' + local, '/' + final))
    log.info('[finalize] redirs=%d renames=%d', len(redir_map), len(asset_renames))

    pages = [(u, s['rel']) for u, s in state['pages'].items() if s.get('status') == 'done']
    redir_pat = re.compile(r'\{REDIR:([^}]+)\}')
    n_redir = n_rename = 0
    for u, rel in pages:
        full = os.path.join(OUT, rel)
        if not os.path.exists(full):
            continue
        with open(full, encoding='utf-8') as f:
            html = f.read()
        orig = html
        if '{REDIR:' in html:
            html = redir_pat.sub(lambda m: redir_map.get(m.group(1), '/'), html)
            n_redir += 1
        if asset_renames:
            for old, new in asset_renames:
                if old in html:
                    html = html.replace(old, new)
                    n_rename += 1
        if html != orig:
            with open(full, 'w', encoding='utf-8') as f:
                f.write(html)
    log.info('[finalize] files with redir fixes=%d, rename hits=%d', n_redir, n_rename)

    # root index.html -> redirect to /zh-CN/
    root_idx = os.path.join(OUT, 'index.html')
    if not os.path.exists(root_idx):
        with open(root_idx, 'w', encoding='utf-8') as f:
            f.write('''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>OneKey 帮助中心</title>
<meta http-equiv="refresh" content="0;url=/zh-CN/">
<script>location.replace('/zh-CN/');</script>
</head>
<body><p><a href="/zh-CN/">OneKey 帮助中心</a></p></body>
</html>
''')
    log.info('[finalize] root index.html written')

def phase_report(state):
    total = len(state['pages'])
    done = sum(1 for s in state['pages'].values() if s.get('status') == 'done')
    fails = [(u, s.get('ct')) for u, s in state['pages'].items() if s.get('status') != 'done']
    log.info('[report] pages: %d/%d ok, %d fail', done, total, len(fails))
    for u, ct in fails[:30]:
        log.info('   FAIL %s (%s)', u, ct)
    aok = sum(1 for a in state['assets'].values() if a.get('status') in ('ok', 'exists'))
    log.info('[report] assets: %d/%d ok', aok, len(state['assets']))
    for l, a in list(state['assets'].items()):
        if a.get('status', '').startswith('fail'):
            log.info('   ASSET FAIL %s -> %s', l, a['url'][:100])

def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else 'pages'
    state = load_state()
    t0 = time.time()
    if phase == 'pages':
        phase_pages(state)
    elif phase == 'scan-more':
        phase_scan_more(state)
    elif phase == 'assets':
        phase_assets(state)
    elif phase == 'fixredir':
        phase_fixredir(state)
    elif phase == 'finalize':
        phase_finalize(state)
    elif phase == 'report':
        phase_report(state)
    elif phase == 'smoke':
        # tiny end-to-end test on a few URLs
        test_urls = [
            f'{BASE}/zh-CN/',
            f'{BASE}/zh-CN/collections/13033821-%E5%AE%A2%E6%88%B7%E6%94%AF%E6%8C%81',
            f'{BASE}/zh-CN/articles/11461077-%E5%BF%AB%E9%80%9F%E5%85%A5%E9%97%A8-onekey-%E7%A1%AC%E4%BB%B6%E9%92%B1%E5%8C%85',
            f'{BASE}/pt/articles/11461077',
        ]
        for u in test_urls:
            rel = page_url_to_rel(u)
            content, ct = fetch_url(u)
            if content is None:
                log.info('SMOKE FAIL %s -> %s', u, ct)
                continue
            html = content.decode('utf-8', errors='replace')
            html, na = process_html(html, u, state)
            full = os.path.join(OUT, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'w', encoding='utf-8') as f:
                f.write(html)
            state['pages'][u] = {'status': 'done', 'rel': rel}
            log.info('SMOKE OK %s -> %s (%d bytes, %d assets)', u, rel, len(html), len(na))
        save_state(state)
        # download the queued assets
        phase_assets(state)
        phase_fixredir(state)
        phase_finalize(state)
    log.info('phase %s took %.1fs', phase, time.time() - t0)

if __name__ == '__main__':
    main()
