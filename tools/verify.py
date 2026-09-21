#!/usr/bin/env python3
"""Full integrity verification of the mirror.
Checks:
1. Every internal href/src in every HTML resolves to an existing file
2. No remaining remote asset URLs on asset domains (except allowed keep-remote)
3. Report stats: pages, assets, broken links by type
"""
import re, os, json, sys, urllib.parse
from collections import Counter
import concurrent.futures as cf

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.normpath(os.path.join(HERE, '..', 'site'))

ATTR_RE = re.compile(r'''(?:\b(?:src|href|content|poster)\s*=\s*)(["'])(.*?)\1''', re.S)

def local_path_for(urlpath):
    """Map a root-relative URL path to a file on disk. Returns existing path or None."""
    p = urllib.parse.unquote(urlpath)
    p = p.split('#')[0].split('?')[0]
    if not p.startswith('/'):
        p = '/' + p
    if p == '/' or p == '':
        cand = os.path.join(SITE, 'index.html')
    else:
        rel = p.lstrip('/')
        # try as-is (dir index), with .html, and exact file
        cands = [
            os.path.join(SITE, rel, 'index.html'),
            os.path.join(SITE, rel + '.html'),
            os.path.join(SITE, rel),
        ]
        # also try percent-encoded roundtrip
        for c in cands:
            if os.path.isfile(c):
                return c
        # encoded variant (browser would request %-encoded)
        enc = urllib.parse.quote(rel)
        for c in [os.path.join(SITE, enc, 'index.html'), os.path.join(SITE, enc)]:
            if os.path.isfile(c):
                return c
        return None
    return cand if os.path.isfile(cand) else None

KEEP_HOSTS = {
    'widget.intercom.io', 'js.intercomcdn.com', 'api-iam.intercom.io',
    'www.google-analytics.com', 'googletagmanager.com', 'www.googletagmanager.com',
    'cdn.cookielaw.org', 'www.intercom.com', 'www.facebook.com', 'platform.twitter.com',
    'fonts.intercomcdn.com',
}

ASSET_HOST_PAT = re.compile(
    r'https?://([^/]+\.intercomcdn\.com|[a-z0-9-]+\.intercom-attachments-\d+\.com|static\.intercomassets\.com|intercom\.help|help\.onekey\.so|fonts\.intercomcdn\.com)')

def check_file(rel):
    full = os.path.join(SITE, rel)
    broken_internal = []
    remote_assets = []
    with open(full, encoding='utf-8') as f:
        html = f.read()
    for m in ATTR_RE.finditer(html):
        val = m.group(2)
        if not val:
            continue
        v = val.strip()
        if v.startswith(('#', 'data:', 'javascript:', 'mailto:', 'tel:')):
            continue
        if v.startswith('//'):
            v = 'https:' + v
        if v.startswith('http'):
            p = urllib.parse.urlsplit(v)
            host = p.hostname or ''
            if host in KEEP_HOSTS or 'intercom.com' in host:
                continue
            if ASSET_HOST_PAT.match(v):
                remote_assets.append(v[:120])
            # other external links are fine (onekey.so etc.)
            continue
        if v.startswith('/'):
            if '{REDIR:' in v:
                broken_internal.append(('UNRESOLVED_REDIR', v[:120]))
                continue
            if local_path_for(v) is None:
                broken_internal.append(('MISSING', v[:150]))
    # also check CSS url() in style blocks
    for m in re.finditer(r'url\((["\']?)(/[^)"\']+)\1\)', html):
        v = m.group(2)
        if local_path_for(v) is None:
            broken_internal.append(('CSS_MISSING', v[:150]))
    return rel, broken_internal, remote_assets

def main():
    files = []
    for root, dirs, fnames in os.walk(SITE):
        dirs[:] = [d for d in dirs if d != '.git']
        for fn in fnames:
            if fn.endswith('.html'):
                files.append(os.path.relpath(os.path.join(root, fn), SITE))
    print(f"checking {len(files)} html files...")
    total_broken = Counter()
    broken_samples = []
    remote_hits = Counter()
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for rel, broken, remote in ex.map(check_file, files):
            if broken:
                for kind, v in broken:
                    total_broken[kind] += 1
                    if len(broken_samples) < 40:
                        broken_samples.append((rel, kind, v))
            if remote:
                for v in remote:
                    remote_hits[v[:80]] += 1
    print("\n=== broken internal references by type ===")
    for k, n in total_broken.most_common():
        print(f"  {k}: {n}")
    for rel, kind, v in broken_samples[:30]:
        print(f"   [{kind}] {rel[:60]} -> {v}")
    print("\n=== remote asset URLs remaining (should be 0) ===")
    if not remote_hits:
        print("  none ✓")
    else:
        for v, n in remote_hits.most_common(10):
            print(f"  {n:5} {v}")
    # file stats
    n_files = 0
    n_bytes = 0
    for root, dirs, fnames in os.walk(SITE):
        dirs[:] = [d for d in dirs if d != '.git']
        for fn in fnames:
            n_files += 1
            n_bytes += os.path.getsize(os.path.join(root, fn))
    print(f"\n=== site stats ===\n  files: {n_files}\n  size: {n_bytes/1e6:.1f} MB")
    return 0 if not total_broken and not remote_hits else 1

if __name__ == '__main__':
    sys.exit(main())
