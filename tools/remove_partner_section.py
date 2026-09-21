#!/usr/bin/env python3
"""Remove 联名产品与合作伙伴 section: delete pages, rewrite refs to /zh-CN/ (DOM only)."""
import re, os, json, shutil, sys
from concurrent.futures import ThreadPoolExecutor

SITE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'site'))
DEL_COLLECTIONS = ['13034487', '13034488', '13034489']
DEL_ARTICLES = ['11461254', '11461255', '11461258', '13405885']
DEL_IDS = DEL_COLLECTIONS + DEL_ARTICLES
FALLBACK = '/zh-CN/'
link_pat = re.compile(r'/zh-CN/(?:articles|collections)/(?:%s)(?:-[^"\'<>#?\s]*)?' % '|'.join(DEL_IDS))

# 1. delete dirs
deleted = []
for kind, ids in (('collections', DEL_COLLECTIONS), ('articles', DEL_ARTICLES)):
    base = os.path.join(SITE, 'zh-CN', kind)
    for name in os.listdir(base):
        if name.split('-')[0] in ids:
            shutil.rmtree(os.path.join(base, name))
            deleted.append(f'{kind}/{name}')
print("deleted:", deleted)

# 2. rewrite DOM references (skip __NEXT_DATA__ JSON) in every zh page
nd_split = re.compile(r'(<script id="__NEXT_DATA__"[^>]*>.*?</script>)', re.S)
def process(path):
    with open(path, encoding='utf-8') as f:
        html = f.read()
    orig = html
    total = 0
    parts = nd_split.split(html)
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            out.append(part)
        else:
            new_part, k = link_pat.subn(FALLBACK, part)
            total += k
            out.append(new_part)
    html = ''.join(out)
    if html != orig:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(html)
    return total > 0, total

files = []
for root, dirs, fns in os.walk(os.path.join(SITE, 'zh-CN')):
    for fn in fns:
        if fn.endswith('.html'):
            files.append(os.path.join(root, fn))
with ThreadPoolExecutor(max_workers=8) as ex:
    results = list(ex.map(process, files))
n_files = sum(1 for ok, _ in results if ok)
n_links = sum(t for _, t in results)
print(f"rewritten {n_files} files, {n_links} links")
