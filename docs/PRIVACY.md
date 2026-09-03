# 隐私与数据安全审计

> 本文回答一个问题：**我的健康数据到底去了哪里？**
> 结论先行：本项目是"拉取式"本地工具——数据只从小米云**下载**到你的电脑，除小米自己的服务器外**不上传到任何第三方**；无遥测、无统计、无崩溃上报；查询功能完全离线可用。

---

## 1. 数据流向总览

```
小米手环/手表 ──蓝牙──> 小米运动健康 App ──> 小米健康云（小米官方，与本项目无关）
                                              │
                                              │ ① 拉取（登录 + 分页下载，RC4 加密通道）
                                              ▼
                          ┌──────────── 本项目（你的电脑）────────────┐
                          │  SQLite（健康数据）                        │
                          │  系统 keyring（passToken / API Key 密钥）  │
                          │  config.json（无敏感信息）                 │
                          └───────────────────────────────────────────┘
                             │ ② 读取（纯本地，断网可用）
                             ├──> MCP 客户端（stdio，本机进程）
                             ├──> REST API（默认 127.0.0.1）
                             └──> Web 仪表盘（默认 127.0.0.1）
```

**唯一出网的方向是 ①：从小米云拉你自己的数据。** 没有任何"上传健康数据"的代码路径。

## 2. 全部对外网络请求清单（代码审计）

对 `src/` 全量 grep `https://` 的结果，所有外联域名均属小米：

| 端点 | 用途 | 方向 |
|---|---|---|
| `https://account.xiaomi.com/pass/serviceLogin` | passToken 换服务会话 | 请求小米 |
| `https://account.xiaomi.com/longPolling/loginUrl` | 扫码登录：生成二维码会话 | 请求小米 |
| `https://hlth.io.mi.com` / `https://{region}.hlth.io.mi.com` | 健康数据分页拉取 | 请求小米 |
| `https://sts.api.io.mi.com/sts` | 仅作为扫码接口的 callback **参数**（小米校验用），本项目不请求它 | — |
| `https://127.0.0.1:8321` | Web 仪表盘反代到本机 API | 仅本机 |

扫码登录流程中由**小米动态下发**的跳转地址（`c3.account.xiaomi.com`、`c3.lp.account.xiaomi.com` 等）也全部位于 `*.xiaomi.com` 小米域名下。

**不存在**：遥测(telemetry)、统计(analytics)、Sentry/PostHog/Mixpanel 等上报 SDK、GitHub star 上报、版本检查回传、任何第三方域名。

**浏览器端同样零外链**：Web 仪表盘与测试页不引用任何 CDN（无 Google Fonts/图表库外链），字体全部使用系统字体栈——打开页面不会向任何第三方发出请求，离线也能完整渲染。

**最小化出站请求头**：发往小米的请求不携带脚本指纹（覆盖默认 `python-httpx/*` User-Agent 为浏览器 UA，与 Cookie 登录来源一致），请求体只含协议必需字段。可用以下命令自行复核：

```bash
grep -rn "https\?://" src/ --include="*.py" | grep -v 127.0.0.1   # 应只见 *.xiaomi.com / *.io.mi.com
grep -rniE "telemetry|analytics|sentry|posthog|mixpanel" src/      # 应无结果
```

## 3. 数据在本机的存放位置

| 数据 | 位置 | 保护 |
|---|---|---|
| 健康数据（8 类） | `<用户数据目录>/mi-fitness-mcp/mi_fitness.db`（SQLite） | 本机文件权限；不上传 |
| 小米 passToken（setup 凭据） | 系统 keyring（Windows: DPAPI 加密 / macOS: Keychain / Linux: Secret Service） | OS 级加密 |
| API Key 对应的 passToken | 系统 keyring（v0.2.1 起；旧版本曾存 SQLite 明文，启动时自动迁移并清空） | OS 级加密 |
| `mif_sk_*` Key 本身、user_id、region、发放/使用时间 | SQLite `api_keys` 表 | 无秘密性要求（可吊销） |
| 配置 | `<用户配置目录>/mi-fitness-mcp/config.json` | 不含凭据 |
| 仓库内 | 无任何上述文件（`.gitignore` 排除 `.venv/ *.db .env config.json` 等） | — |

## 4. 本地暴露面与防护（v0.2.1 加固）

数据"不上传"之外，还要防**被动窃取**。本项目默认只绑 `127.0.0.1`，针对本机攻击面的防护：

| 威胁 | 场景 | 防护 |
|---|---|---|
| 恶意网页跨站读取 | 你浏览器里开着的任意网页用 JS 请求 `http://127.0.0.1:8321` 并读取响应 | CORS 默认只允许 `localhost/127.0.0.1/[::1]` 来源（正则限定，任意端口），其他域名一律无 CORS 头，浏览器阻止读取 |
| DNS 重绑定 | 攻击者域名解析到 127.0.0.1 绕过同源策略 | **Host 头白名单中间件**（FastAPI 与 Flask 双侧）：Host 非本机域名直接 403 |
| 本机其他进程读取 | 同机运行的任何程序请求本地 API | 设置环境变量 `MI_FITNESS_API_KEY` 强制鉴权；管理端点默认仅本机且可设 `MI_FITNESS_ADMIN_KEY` |
| 凭据落盘明文 | SQLite 被复制/同步盘上传 | passToken 全部走系统 keyring；`api_keys` 表启动时自动迁移历史明文并清空 |
| 日志泄密 | 终端/日志文件留下查询参数（日期等） | `api` 命令默认关闭 uvicorn 访问日志 |
| 扫码端点滥用 | 他人把他的账号"登录"到你的服务器 | 扫码/Key 管理端点要求 `X-Admin-Key`（未设则仅限 127.0.0.1） |

环境变量开关（见 `.env.example`）：`MI_FITNESS_ALLOWED_HOSTS`（Host 白名单，非回环绑定时 CLI 自动设置）、`MI_FITNESS_CORS_ORIGINS`（显式放开跨域来源，慎用 `*`）。

## 5. 如何自行验证

1. **断网测试**：拔网线后所有查询端点、`/api/export`、Web 仪表盘图表照常工作（同步/扫码会失败，属预期）——证明查询链路纯本地。
2. **抓包**：`netstat`/Wireshark 观察进程外联，目标应只有 `*.xiaomi.com` / `*.io.mi.com`。
3. **代码审计**：第 2 节的两条 grep 命令；全部网络代码集中在 `adapters/mi_fitness_cloud.py`、`api.py`（扫码）、`web.py`（本机反代）。
4. **仓库审计**：`git ls-files | grep -iE "\.db|\.sqlite|config\.json|\.env$"` 应无结果。

## 6. 明确不做的事

- ❌ 不上传、不转发、不备份健康数据到任何第三方服务
- ❌ 不内置任何遥测、统计、崩溃上报
- ❌ 不在代码仓库或文档中存放任何凭据
- ❌ 不写入小米账号（全协议只读；唯一"写"是登录换 token，属认证必需）

## 7. 边界说明（与本项目无关的上传）

- 手环 → 小米云的上传由**小米官方 App**完成，这是设备生态本身的行为；本项目只读取。介意者请在小米 App 内管理同步策略。
- 你若把 SQLite 文件自行放进网盘/同步盘，属于你自己的操作，请自行加密。
