# 镜像工具

按顺序运行（需 python3 + requests）：

```bash
pip3 install requests
python3 crawler.py pages      # 抓取 sitemap 全部页面 + 15 语言首页（如只需中文，保留默认即可，后续 zhonly 裁剪）
python3 crawler.py scan-more  # 补漏资源发现
python3 crawler.py assets     # 下载全部资源（含签名 URL 本地化）
python3 crawler.py fixredir   # 解析无 slug 文章跳转链接
python3 crawler.py finalize   # HTML 重写占位符 + 根跳转页
python3 crawler.py report     # 报告失败项
python3 verify.py             # 断链 / 资源完整性自检
```

辅助脚本（历史轮次修复，按需参考）：
- `fixleftover.py` 旧 intercom.help 文章链接改写 + 死图占位
- `fixround2.py` 子集合页补抓 + 断链修复
- `reharvest.py` 重新采集签名过期的图片 URL 并补下

状态文件在 `tools/state/`（支持断点续爬）。
