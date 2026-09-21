#!/usr/bin/env python3
"""Wire the static search into every zh-CN page:
1. Rewrite the search form action -> /zh-CN/search/ with GET (input name=q already set)
2. Inject a tiny shim script that intercepts form submit & Enter key, and pre-fills
   /zh-CN/search/?q=... navigation (works without React hydration)
Idempotent: skips pages already patched.
"""
import re, os

SITE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'site'))

SHIM = '<script data-search-shim>(function(){var f=document.querySelector(\'form[action="/zh-CN/search/"]\');if(!f)return;f.addEventListener("submit",function(e){e.preventDefault();var i=f.querySelector(\'input[name="q"]\');var q=i?i.value.trim():"";if(q){var a=f.querySelector("#search-input")&&f.querySelector("#search-input").getAttribute("data-go")||"/zh-CN/search/";location.href=a+"?q="+encodeURIComponent(q)}});})();</script>'

n_changed = 0
zh_dir = os.path.join(SITE, 'zh-CN')
for root, dirs, fns in os.walk(zh_dir):
    for fn in fns:
        if not fn.endswith('.html'):
            continue
        full = os.path.join(root, fn)
        with open(full, encoding='utf-8') as f:
            html = f.read()
        if 'data-search-shim' in html:
            continue
        orig = html
        # only patch real content pages (not redirect stubs)
        if 'search-bar' not in html:
            continue
        html = html.replace('action="/zh-CN/"', 'action="/zh-CN/search/"', 1)
        # inject shim before </body>
        html = html.replace('</body>', SHIM + '</body>', 1)
        if html != orig:
            with open(full, 'w', encoding='utf-8') as f:
                f.write(html)
            n_changed += 1
print(f"patched {n_changed} pages with search action + shim")

# also link the search page from root index? not needed (redirects to zh-CN home)
print("done")
