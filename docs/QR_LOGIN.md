# 小米扫码登录实现方案

> 本文档只覆盖「扫码登录」这一部分：协议原理 → 服务端实现 → API 用法 → 安全与限制。
> 代码位置：`src/mi_fitness_mcp/api.py`（`/api/auth/qr/*` 三个端点）；测试页入口：`mi-fitness-api-test`（扫码面板）。
> 协议来源：逆向小米账号通用扫码流程，参考 python-miio `cloud_qr.py`（MIT）。

---

## 1. 目标

用「手机扫码」替代「浏览器 F12 手抠 Cookie」来获取小米凭据 `userId + passToken`，扫码成功后服务端自动发放本平台的 API Key（`mif_sk_*`）。整个过程中小米凭据只出现在服务端，调用方全程只接触 API Key。

小米对第三方**密码登录**已加验证码，但**扫码流程不受影响**——这是选择扫码方案的核心原因。

## 2. 小米扫码协议（逆向所得）

整个协议只有两个云端端点，全部是 GET，无需任何预置凭据：

```
手机App                我们的服务端                     小米账号服务
  │                        │                              │
  │                        │ ① GET /longPolling/loginUrl   │
  │                        │◄──────────────────────────────│
  │                        │  返回 qr / lp / loginUrl/ticket│
  │                        │                              │
  │   ② 用户扫码→点确认      │                              │
  │◄───────────────────────│   (二维码 = 带 ticket 的URL)   │
  │──── 确认登录 ──────────────────────────────────────────►│
  │                        │                              │
  │                        │ ③ GET {lp} 长轮询(挂起)        │
  │                        │◄──────────────────────────────│
  │                        │  返回 userId + passToken       │
```

### 2.1 端点一：生成登录会话

```
GET https://account.xiaomi.com/longPolling/loginUrl
```

| 参数 | 值 | 说明 |
|---|---|---|
| `sid` | `xiaomiio` | **必须是这个值**，换 `miothealth` 会返回 10025 错误 |
| `callback` | `https://sts.api.io.mi.com/sts` | **必须是这个值**，其他值同样 10025 |
| `qs` | `%3Fsid%3Dxiaomiio%26_json%3Dtrue` | 即 `?sid=xiaomiio&_json=true` 的 URL 编码 |
| `_qrsize` | `480` | 二维码图片尺寸 |
| `_hasLogo` | `false` | 二维码中央不放 logo |
| `_locale` | `zh_CN` | 界面语言 |
| `_dc` | 当前毫秒时间戳 | 防缓存 |

响应体以固定前缀 `&&&START&&&` 开头（小米账号接口的通用 guard），剥掉后是 JSON：

```json
{
  "qr":       "https://account.xiaomi.com/pass/qr/login?ticket=lp_xxxx",
  "loginUrl": "https://c3.account.xiaomi.com/longPolling/login?ticket=lp_xxxx",
  "lp":       "https://c3.lp.account.xiaomi.com/lp/s?k=lp_xxxx",
  "timeout":  "300"
}
```

| 字段 | 含义 |
|---|---|
| `qr` | 二维码图片的 URL（内容就是它自己，服务端需代抓 PNG） |
| `loginUrl` | 电脑端兜底：浏览器打开它 → 登录账号 → 点确认，效果等同扫码 |
| `lp` | 长轮询地址，用来等"用户已确认"的结果 |
| `timeout` | 本次登录会话有效期（秒），一般 300 |

三个 URL 共享同一个一次性 `ticket`（`lp_` 前缀），ticket 就是"这一次登录会话"的身份。

### 2.2 端点二：长轮询确认结果

```
GET {lp}
```

- 用户**尚未确认**：请求挂起（长轮询），十几秒后超时返回，继续轮询即可。
- 用户**已确认**：立即返回 HTTP 200，剥 `&&&START&&&` 前缀后：

```json
{"code": 0, "userId": 1234567890, "passToken": "V1:..."}
```

`userId` + `passToken` 与浏览器 Cookie 中手抠到的完全同种，且是**账号级**凭据（不绑定 xiaomiio 服务）。

### 2.3 关键坑（已修复并验证）

| 坑 | 现象 | 结论 |
|---|---|---|
| sid 不能用 `miothealth` | 返回 `{"code":10025,"desc":"Callback连接不合法"}` | 扫码入口只认 `sid=xiaomiio` + `callback=https://sts.api.io.mi.com/sts` 这一个组合 |
| passToken 是否绑服务 | 担心 xiaomiio 的 token 不能用于运动健康 | **不绑定**。拿到后走 `serviceLogin?sid=miothealth` 可正常换运动健康会话 |
| 响应前缀 | 直接 `json.loads` 报错 | 所有账号接口都要先剥 `&&&START&&&` |
| 长轮询挂起 | 超时/网络异常频繁 | 属正常节奏，捕获后继续轮询，不算失败 |

## 3. 服务端实现（api.py）

小米的流程是"一次长轮询阻塞到底"，直接搬到 HTTP API 会让请求挂几分钟。实现上改造成**提交-轮询**模式，拆成三个端点：

### 3.1 状态机

```
POST /start 生成会话
        │
        ▼
    [waiting] ──poll 时调 lp 且收到 code=0+userId+passToken──► [confirmed]（含 api_key）
        │                                                        │
        └─── 超过 timeout+10 秒 ──► [expired]        之后 poll 永远返回 confirmed 结果
```

### 3.2 端点一：`POST /api/auth/qr/start?region=cn`

服务端向小米请求登录会话，把 `lp` / `loginUrl` / 二维码 PNG / 过期时间 / region 存入内存字典 `qr_sessions`（key 为 uuid `qr_token`），对外返回：

```json
{
  "qr_token":  "b5ca13315c1f4a62805a47916c82404a",
  "qr_image":  "/api/auth/qr/b5ca13315c1f4a62805a47916c82404a.png",
  "login_url": "https://c3.account.xiaomi.com/longPolling/login?ticket=lp_...",
  "expires_in": 300,
  "instruction": "用小米运动健康/小米账号 App 扫码，或在浏览器打开 login_url 确认；然后轮询 /api/auth/qr/poll?token=..."
}
```

二维码 PNG 由服务端代抓并缓存进会话，浏览器拿到的是现成图片，不直连小米。

### 3.3 端点二：`GET /api/auth/qr/{token}.png`

返回会话中缓存的 PNG（480×480，实测约 15KB），前端 `<img src>` 直接显示。token 不存在返回 404。

### 3.4 端点三：`GET /api/auth/qr/poll?token=xxx`

每次调用最多阻塞约 15 秒（lp 单次挂起上限），无异常时返回三种状态之一：

| 返回 | 含义 | 后续动作 |
|---|---|---|
| `{"status":"waiting"}` | 未确认/网络抖动 | 前端隔几秒再调 |
| `{"status":"confirmed","api_key":"mif_sk_...","user_id":"..."}` | 登录成功，Key 已发放 | 前端存下 Key，此后带头调用 |
| `{"status":"expired"}` | 超 5 分钟未确认 | 重新调 start 生成新码 |

**confirmed 时服务端做的三件事**（全部在返回前完成）：

1. 从 lp 结果提取 `userId` / `passToken`；
2. 用这套凭据真实构造 `MiFitnessCloudAdapter` 并 `connect()` 一次——验证 passToken 能换到运动健康的 serviceToken，避免发放无效 Key；验证失败则返回 `confirmed` 但 `api_key: null` 并附说明；
3. 凭据写入主库 `api_keys` 表（key / user_id / pass_token / region / created_at），发放 `mif_sk_<40位hex>`，完整 Key 仅此一次返回。

### 3.5 设计取舍

| 决策 | 理由 |
|---|---|
| 长轮询放进 poll 而不是 start | start 里阻塞等扫码会让 HTTP 请求挂 5 分钟，网关/浏览器易超时；改为前端每几秒调一次 poll，单次最多 15 秒 |
| `qr_sessions` 纯内存 | 存的是未完成登录的临时 ticket，天然短命，重启失效无副作用（重新生成即可），不值得持久化 |
| passToken 不进前端 | 扫码结果由服务端直接落库换 Key，浏览器全程只见 `mif_sk_*`，与 Key 体系安全模型一致 |
| 发 Key 前真实验证 | 防止把"扫码成功但服务受限"的凭据发出去，问题在发放时刻暴露而非首次调用时 |
| poll 的 confirmed 常驻内存 | 让前端可以重复查询结果；代价是进程重启后确认记录丢失（重新扫码即可） |

## 4. 凭据的后续链路（扫码之外）

扫码只解决"凭据从哪来"。拿到 `userId + passToken` 后的流程与手动配置完全一致：

```
passToken ──GET serviceLogin?sid=miothealth──► ssecurity(签名密钥) + serviceToken(会话Cookie)
                     │
                     └──► 健康数据接口（RC4 加密 + SHA1 签名，见主文档第 8 章）
```

即：扫码、`mi-fitness-mcp setup` 手动配置、`POST /api/auth/keys` 发放，三条路最终汇合到同一套凭据模型，扫码登录对 sync/query 全部端点透明生效。

## 5. 使用方法

### 5.1 测试页（推荐）

打开 `http://127.0.0.1:8322/` → 「扫码登录」面板 → 点「生成登录二维码」→ 手机小米账号/米家 App 扫码并确认 → 回页面点「轮询扫码状态」→ confirmed 后 Key 自动填入右上角请求头。

### 5.2 纯 API 调用

```bash
# 1. 生成二维码
curl -X POST "http://127.0.0.1:8321/api/auth/qr/start?region=cn"
# → {"qr_token":"...","qr_image":"/api/auth/qr/....png","login_url":"https://...","expires_in":300}

# 2. 展示二维码：浏览器打开 http://127.0.0.1:8321/api/auth/qr/{qr_token}.png
#    或让用户直接打开返回的 login_url 在网页上确认

# 3. 轮询（未确认返回 waiting，可循环调用）
curl "http://127.0.0.1:8321/api/auth/qr/poll?token={qr_token}"
# → {"status":"confirmed","api_key":"mif_sk_...","user_id":"..."}
```

### 5.3 鉴权要求

三个 `/api/auth/qr/*` 端点都受管理鉴权保护：

- 设置了环境变量 `MI_FITNESS_ADMIN_KEY`：必须带 `X-Admin-Key` 头；
- 未设置：仅允许 `127.0.0.1` 本机调用，非本机返回 403。

## 6. 安全说明

- **passToken 是长期凭据**：等同登录态，泄露后应退出并重新登录小米账号使其失效。本方案中它只在服务端内存与 `api_keys` 表中出现一次。
- **扫码 = 把账号登录到你的服务器**：`/api/auth/qr/start` 绝不能无鉴权暴露在局域网/公网。
- **ticket 一次性且 5 分钟过期**：小米侧控制，无法延长；过期重新生成即可。
- **内存态会话**：服务重启丢失所有未完成的扫码会话，无持久化泄露面。

## 7. 已验证与已知边界

| 状态 | 项 |
|---|---|
| ✅ 已验证 | 会话生成、二维码 PNG 有效（480×480）、poll 未扫码返回 waiting、10025 问题修复、Key 发放→使用→吊销全链路 |
| ⏳ 需人工 | 用手机实际扫码走完 confirmed 全流程（扫码登录的本意，无法程序化替代） |
| 已知限制 | 二维码 5 分钟过期；confirmed 记录仅存内存；协议为逆向所得，小米改版（如 ticket 格式变化）会静默失效，需重新抓包对照 |
