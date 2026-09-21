#!/usr/bin/env python3
"""Build search index from all zh-CN article pages.
Extracts title, url, collection, date, plain text from each article's HTML.
Output: site/search-index.json (~500KB expected)
"""
import re, os, json, html as htmllib
from concurrent.futures import ThreadPoolExecutor

SITE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'site'))

def strip_tags(s):
    s = re.sub(r'<script.*?</script>', '', s, flags=re.S)
    s = re.sub(r'<style.*?</style>', '', s, flags=re.S)
    s = re.sub(r'<[^>]+>', ' ', s)
    s = htmllib.unescape(s)
    return re.sub(r'\s+', ' ', s).strip()

def parse(rel):
    full = os.path.join(SITE, rel)
    with open(full, encoding='utf-8') as f:
        h = f.read()
    # title
    m = re.search(r'<h1[^>]*class="[^"]*mb-1[^"]*"[^>]*>(.*?)</h1>', h, re.S)
    if not m:
        m = re.search(r'<title[^>]*>(.*?)\s*\|\s*OneKey', h, re.S)
        title = strip_tags(m.group(1)) if m else ''
    else:
        title = strip_tags(m.group(1))
    if not title:
        return None
    # date
    md = re.search(r'<time[^>]*dateTime="([^"]+)"', h)
    date = md.group(1)[:10] if md else ''
    # collection breadcrumb (first breadcrumb link)
    mc = re.search(r'data-testid="breadcrumb-0"[^>]*>([^<]+)<', h)
    collection = mc.group(1).strip() if mc else ''
    # article body: anchored on article_body marker, cut at first stop marker
    text = ''
    ia = h.find('article_body')
    if ia >= 0:
        seg = h[ia:]
        cut = len(seg)
        for stop in ('<section class="jsx-62724fba related', '</article>', '<fieldset'):
            j = seg.find(stop)
            if 0 < j < cut:
                cut = j
        seg = seg[:cut]
        mib = re.search(r'<div class="intercom-interblocks.*', seg, re.S)
        if mib:
            t = strip_tags(mib.group(0))
            t = re.sub(r'(?:العربية|Portugu[eê]s do Brasil|Portugu[eê]s|English|Deutsch|日本語|한국어|Bahasa Melayu|Pусский|简体中文|Espa[nñ]ol|ภาษาไทย|繁體中文|T[uü]rk[cç]e|Ti[eę]ng Vi[eệ]t)\s*', ' ', t)
            text = t.strip()
    # excerpt: first 160 chars
    return {
        'title': title,
        'url': '/' + rel[:-len('/index.html')] if rel.endswith('/index.html') else '/' + rel,
        'collection': collection,
        'date': date,
        'text': text[:8000],
    }

def main():
    arts_dir = os.path.join(SITE, 'zh-CN', 'articles')
    rels = []
    for name in sorted(os.listdir(arts_dir)):
        p = os.path.join(arts_dir, name, 'index.html')
        if os.path.isfile(p):
            rels.append(f'zh-CN/articles/{name}/index.html')
    print(f"articles: {len(rels)}")
    with ThreadPoolExecutor(max_workers=8) as ex:
        docs = [d for d in ex.map(parse, rels) if d]
    print(f"parsed: {len(docs)}")
    # sort by title locale-aware-ish
    docs.sort(key=lambda d: d['title'])
    out = {'v': 1, 'generated': '2026-09-21', 'locale': 'zh-CN', 'docs': docs}
    dst = os.path.join(SITE, 'search-index.json')
    with open(dst, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    print(f"index: {dst}  size={os.path.getsize(dst)/1024:.0f}KB  docs={len(docs)}")

if __name__ == '__main__':
    main()
