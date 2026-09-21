# OneKey 帮助中心镜像（zh-CN）

[OneKey 官方帮助中心](https://help.onekey.so/zh-CN) 的完整中文静态镜像，用于在中国大陆无法访问原站时提供只读访问。

> 内容版权归原作者 OneKey 所有。本镜像仅为可访问性用途，内容与原站保持一致（抓取时间 2026-09-21）。

## 站点结构

- `zh-CN/` — 帮助中心全部 15 个分类（含子分类）与 324 篇文章，HTML + `__NEXT_DATA__` 原样保留
- `assets/` — 全部本地化资源：Next.js JS/CSS chunks、Roobert 字体、文章图片（已剥离过期签名参数）
- `index.html` — 根路径跳转到 `/zh-CN/`
- `articles/11536900-contact-us/` — 旧版无语言前缀链接的兼容跳转

## 部署（Cloudflare Pages）

静态站，无构建步骤：

```bash
npx wrangler pages deploy site --project-name onekey-help-zh
```

或在 Cloudflare Dashboard 连接本 GitHub 仓库：构建命令留空，输出目录填 `site`。

然后按需绑定自定义域名（Pages 项目 → Custom domains）。

## 已知限制

- 站内搜索依赖 Intercom 后端 API，镜像上不可用（页面 UI 保留）
- Intercom 在线客服窗口不可用（原站第三方服务）
- 文章"是否有帮助"表情投票不会上报

## 重新抓取 / 更新

工具在 `tools/`（爬虫基于 sitemap + 内链发现，资源全量本地化，断链自检）。镜像抓取脚本：

```bash
cd tools && python3 crawler.py pages && python3 crawler.py assets
```

## 站内搜索（已上线）

- 搜索页：`/zh-CN/search/`（全站搜索框自动跳转到该页）
- 台账：`/zh-CN/search-log/`（本机记录 + CSV 导出）
- 索引：`search-index.json`（324 篇，由 `tools/build_search_index.py` 生成）
- 交接文档：`tools/SEARCH-HANDOVER.md`（实现说明 / KV 绑定 / 测试记录 / 限制与优化建议）
