# Changelog

## 0.4.0（仪表盘重做 + 数据模型修复）

**重做**

- Web 仪表盘从零重写：无 emoji、无第三方字体、无框架，纯 ES Module + CSS 变量；响应式（桌面左导航栏 → 平板窄栏 → 手机底部标签栏），暗色/亮色跟随系统
- 新增「洞察」页：区间报告（对比上一周期）、个人纪录、心率区间分布、数据缺口
- 趋势页支持 14 个指标按日/周/月聚合
- 仪表盘代理增加严格 CSP、X-Frame-Options、Referrer-Policy，JS 模板自动转义防 XSS

**修复**

- 睡眠按入睡日归属时，0:08 入睡与 23:21 入睡的两晚落在同一天，睡眠序列出现翻倍的假峰值；改为按起床日归属
- 总览页睡眠分期条无法显示（flex 布局嵌套 grid）
- 图表 X 轴最后一个标签与相邻标签重叠
- 图表悬浮数据放在 HTML 属性里，改回内存对象

**测试**：115 → 132 个，新增前端专项（无 emoji、CSP 兼容、XSS 转义、静态资源可访问）

## 0.3.0（分析与可靠性）

**新增**

- 演示模式：`--demo` / `MI_FITNESS_DEMO=1` 用合成数据体验全部功能，无需小米账号；写入独立的 `demo.db`
- `dashboard` 命令：一条命令同时启动 API 与仪表盘并打开浏览器
- 命令行：`status` `overview` `report` `records` `gaps` `export`，均不联网，支持 `--json`；`sync` 增加 `--full`、`-v` 进度；全局 `--version`
- 6 个分析型 MCP 工具与对应 REST 端点：`get_health_overview` `get_period_report`（自动对比上一等长区间）`get_sleep_summary`（入睡时间波动、各阶段占比）`get_heart_rate_summary`（心率区间）`get_personal_records`（含连续达标）`find_data_gaps`
- `query_metric_series` 指标从 3 个扩到 14 个：睡眠/运动时长、静息/平均/最高心率、血氧、压力、体重、BMI、体脂
- 仪表盘新增「健康洞察」页（区间报告、个人纪录、数据缺口）；指标页覆盖全部指标
- 数据库权限：新建时收紧为 0600（类 Unix）

**修复**

- 同日增量同步崩溃：游标落在今天时，起点（带时刻）晚于终点（当天零点），抛 `start_date must not be after end_date`。现在从游标所在日重拉，UPSERT 保证不重复
- 同步统计失真：`updated` 恒为 0（UPSERT 的 rowcount 总是 1），现在如实区分新增 / 更新 / 未变
- 游标可能越过未入库的数据：改为每个分块整体入库成功后才推进，且只前进不后退（补拉历史不再把增量起点拉回去）
- 多账号互相覆盖：记录 ID 不含账号，两个账号同日数据会共用一行，后者覆盖前者；主键改为 `(user_id, id)`，同步游标改为按账号。**旧库启动时自动迁移，数据不丢**
- `aggregation=latest` 被静默当成求和；`weight_kg` 在 MCP 枚举里却始终返回空
- 仪表盘概览卡片全是 `--`：前端读取的字段名与 API 实际返回不符；睡眠分期条同理
- 仪表盘 API Key 列表无法吊销（读取了不存在的字段），且标签、用户 ID 未转义（存储型 XSS）
- 按前缀吊销 Key 时 `mif_` 这样的短前缀会吊销全部 Key；现在要求至少 12 位且唯一匹配
- 扫码确认后重复轮询会反复返回完整 API Key；现在只在确认那一次返回

**性能与结构**

- 写入改为单事务批量 UPSERT，并启用 WAL：一天的逐分钟心率（1440 条）从约 12 秒降到约 20 毫秒
- 日期过滤改用时间列的范围比较，走 `(user_id, 时间)` 索引，不再对每行 `substr`
- 心率统计在 SQL 里聚合，不再把几万条样本读进内存
- 8 张表的 DDL、写入、过滤、覆盖统计由一份声明式定义生成，存储层从 878 行降到 559 行
- MCP 服务改为装饰器注册工具，schema 与处理函数同处定义；MCP 与 REST 共用同一个 `sync_many`
- 数据类型清单只在 `models.DATA_TYPES` 定义一份
- 密钥比较改用 `hmac.compare_digest`（恒定时间）
- 多设备同日记录取各指标最大值而非相加（预防性处理：当前云端适配器不写 `device_id`，尚不会触发）
- 签名 nonce 的时间戳改用 `time.time()`（结果不变，仅去掉对本地时区函数的依赖）
- 删除未使用的模型、配置项与重复的前端目录 `testpage/`（与 `web_assets/` 逐字节相同）；探测脚本移入 `tools/`

**测试**：28 → 115 个，覆盖迁移、同步游标、分析、API 鉴权、CLI 与 MCP 工具注册

## 0.2.2（零第三方请求）

- 移除 Web 仪表盘与测试页的 Google Fonts 外链：打开页面不再向 Google 发送任何请求（此前浏览器会上传 IP/UA），改用系统字体栈，国内加载也更快、离线可渲染
- 发往小米的请求覆盖默认 `python-httpx/*` User-Agent 为浏览器 UA，去除脚本指纹（与 Cookie 登录来源一致）
- `docs/PRIVACY.md` 补充浏览器端零外链与最小化出站请求头说明

## 0.2.1（隐私加固）

- 数据安全审计文档 `docs/PRIVACY.md`：全部对外请求清单（仅小米域名）、本机数据存放表、威胁模型与自验方法
- CORS 收紧：默认仅允许本机来源（原来为 `*`，恶意网页可跨站读取本地健康数据）；可用 `MI_FITNESS_CORS_ORIGINS` 覆盖
- Host 头白名单中间件（FastAPI + Flask 双侧）：阻断 DNS 重绑定攻击；非回环绑定时 CLI 自动放行绑定地址，可用 `MI_FITNESS_ALLOWED_HOSTS` 覆盖
- API Key 的 passToken 迁出 SQLite 明文，改存系统 keyring（启动时自动迁移历史数据）；吊销时同步清理
- `api` 命令默认关闭 uvicorn 访问日志（查询参数不再落入终端）
- 新增 `GET /api/export`：本地数据导出为 JSON/CSV（含 Excel BOM），纯离线操作

## 0.2.0

- 内置 Web 仪表盘（`mi-fitness-mcp web`，Flask）：可视化测试全部 API 端点、Key 管理、扫码登录，内置反向代理（自动附加 X-API-Key/X-Admin-Key，上游耗时透传）
- REST API 服务（`mi-fitness-mcp api`，FastAPI）：全部数据端点 + 同步（前台/后台）+ 覆盖统计，交互式文档 `/docs`
- API Key 体系：`POST /api/auth/keys` 用小米凭据换取 `mif_sk_*` Key（类大模型平台风格），支持多账号上下文、last_used 统计、按前缀吊销；静态 Key 经 `MI_FITNESS_API_KEY`、管理端点经 `MI_FITNESS_ADMIN_KEY`
- 扫码登录：逆向小米通用扫码流程（`sid=xiaomiio`），`/api/auth/qr/start|poll` 生成二维码并轮询换取凭据后自动发放 Key，无需浏览器 F12
- Rust 测试页 `testpage/`（可选，内置 Web 仪表盘的 Rust 替代实现）
- 修复：`mcp` 依赖加上界 `<2`（MCP SDK 2.x 移除 `Server.list_tools` 导致启动崩溃）
- 文档：隐私与数据安全审计 `docs/PRIVACY.md`

## 0.1.0

- initial standalone `mi-fitness-mcp` repository
- Xiaomi auth via `userId + passToken`
- Mi Fitness cloud sync for steps, heart rate, calories, body measurements
- MCP tools for sync, summaries, heart rate, body measurements, coverage
