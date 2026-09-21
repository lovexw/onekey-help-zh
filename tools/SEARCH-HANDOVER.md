# 搜索功能交接文档

> OneKey 帮助中心中文镜像（https://onekey-help-zh.pages.dev）· 2026-09-21

## 一、实现说明

纯静态全文搜索，零外部服务、零构建依赖，全部逻辑在浏览器端完成。

### 数据流

```
tools/build_search_index.py
  扫描 site/zh-CN/articles/*/index.html (324 篇)
  解析 标题 / 分类 / 更新日期 / 正文纯文本(≤8000字)
  ──► site/search-index.json (~850KB, gzip 传输 ≈220KB)

用户访问 /zh-CN/search/?q=关键词
  ├─ fetch /search-index.json (首访加载, 约200-400ms)
  ├─ 分词: 拉丁词原样 + CJK 连续串切二元组(bigram)
  ├─ 打分: 标题命中 ×12 (开头再+6) + 分类 ×4 + 正文命中 ×2 + 词频(≤10)
  │   └─ CJK 查询且非全词命中时: 兜底「半数词命中」部分匹配 ×0.5
  ├─ 排序: 相关性(默认) / 更新时间 —— 页面可切换
  ├─ 渲染: 每页 10 条, 摘要 160 字, <mark> 高亮, 上下页翻页
  └─ 记录: localStorage(本机台账) + sendBeacon POST /api/search-log
```

### 文件清单

| 文件 | 作用 |
|---|---|
| `tools/build_search_index.py` | 从镜像 HTML 生成 `site/search-index.json`（重跑即可更新索引） |
| `site/zh-CN/search/index.html` | 搜索页（自包含 HTML+CSS+JS，无依赖） |
| `site/zh-CN/search-log/index.html` | 搜索记录台账页（查看/导出 CSV/清空） |
| `site/search-index.json` | 索引数据（324 docs） |
| `functions/api/search-log.js` | Cloudflare Pages Function：服务端搜索日志（KV 可选） |
| `tools/wire_search.py` | 把全站 382 个搜索框 `action` 指到 `/zh-CN/search/` 并注入提交 shim（幂等，可重跑） |

## 二、配置与依赖

- **无第三方依赖**：不用 Pagefind/Lunr/Algolia/ES，无需付费服务（符合任务 out-of-scope 约束）。
- **可选 KV 绑定**（启用服务端台账）：
  1. Cloudflare Dashboard → Storage & Databases → KV → 创建 namespace `search-logs`
  2. Workers & Pages → `onekey-help-zh` → Settings → Bindings → 添加 KV，变量名 **`SEARCH_LOGS`**
  3. 之后 `/api/search-log` 自动从 `client-only`(202) 变为 `kv`(200)，按天存 `log:YYYY-MM-DD`
  - 未绑定时功能自动降级：日志仅存访问者浏览器 localStorage（≤500 条）
- **索引更新**：镜像内容更新后执行
  ```bash
  python3 tools/build_search_index.py && npx wrangler pages deploy site --project-name onekey-help-zh
  ```

## 三、测试记录（关键词验收清单）

算法级测试（Python 等价实现，与 JS 逻辑逐条对齐）：

| 类别 | 关键词 | 结果数 | Top1 | 判定 |
|---|---|---:|---|---|
| 典型词 | 硬件钱包 | 203 | 硬件钱包无法连接 OneKey App 解决方法 | ✓ |
| 典型词 | 助记词 | 102 | 助记词里会出现重复单词？ | ✓ |
| 典型词 | 固件 | 74 | 验证固件文件与 OneKey 开源代码的一致性 | ✓ |
| 典型词 | PIN | 57 | 硬件钱包忘记了 PIN 码怎么办？ | ✓ |
| 典型词 | 比特币 | 72 | 比特币共识机制与攻击防范 | ✓ |
| 典型词 | 签名 | 63 | 什么是多重签名 | ✓ |
| 长尾 | 连接不上 | 2 | 硬件钱包连不上 Windows 电脑怎么办 | ✓ |
| 长尾 | 无法连接 | 44 | 硬件钱包无法连接 OneKey App 解决方法 | ✓ |
| 长尾 | 如何备份助记词 | 93 | 在 OneKey App 中查看并备份助记词与私钥 | ✓ |
| 长尾 | OneKey Classic 固件升级 | 55 | OneKey Pro 无法开机时升级固件 | ✓ |
| 混合 | gas 费 | 91 | 使用 Gas Account 支付网络费 | ✓ |
| 空值 | （空）/ 全空格 | 0 | 显示引导提示，无报错 | ✓ |
| 特殊字符 | `!!!!!!!!` / `"'; drop table --` / `🦄` | 0 | 友好无结果提示 | ✓ |
| 特殊字符 | `<script>alert(1)</script>` | 0 | 无 XSS（全部 escape 后渲染） | ✓ |
| 超长输入 | a×100 | 0 | 输入框 maxlength=100 截断 | ✓ |
| 必无结果 | zzzzqqqq | 0 | 「没有找到…建议换关键词」提示 | ✓ |

性能：单次检索 **3-6ms**（324 篇全文扫描），索引加载 200-400ms（gzip 后 CDN 分发），远低于 2 秒基线；输入 250ms 防抖，等待期间有加载状态文案。

## 四、已知限制

1. **服务端台账默认未启用**：`/api/search-log` 已部署但未绑 KV 时返回 202 `client-only`；绑定 `SEARCH_LOGS` KV 后自动启用（见上）。
2. **分词为二元组**：无词典分词，「重启」能命中「重新启动」，但跨词组合（如搜「连不上电脑」匹配「电脑无法连接」）依赖部分匹配兜底，召回非保证。
3. **索引为快照**：OneKey 官方更新文章后需重跑 `build_search_index.py`（不会自动同步）。
4. **结果数按文档计**：无分页 SEO 问题（noindex），高亮基于纯文本（HTML 标签已剥离）。
5. **localStorage 台账仅本机可见**：导出 CSV 需用户手动操作。

## 五、后续优化建议

- 绑定 KV 启用服务端日志后，可加一个 `/api/search-stats` 只读接口输出热门搜索词
- 若文章量增长 >2000 篇，改用 Pagefind（构建期分片索引，仍免费）替换本方案
- 搜索页可加分类筛选（索引已含 `collection` 字段，前端一个 select 即可实现）
- 官方如有 RSS/版本号接口，可加 cron 自动重建索引
