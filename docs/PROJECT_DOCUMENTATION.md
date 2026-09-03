# mi-fitness-mcp-cn 项目完整技术文档

> **文档目的**：作为重构/二次开发的唯一权威参考。内容基于源码逐行整理（基线 commit `7fcd069`），所有行为描述均与代码实际实现一一对应，不包含猜测。
> **阅读对象**：执行重构的 AI 或工程师。重构约束与已知问题见第 15、16 章，**动手前必读**。

---

## 目录

1. [项目定位与背景](#1-项目定位与背景)
2. [快速事实卡](#2-快速事实卡)
3. [总体架构](#3-总体架构)
4. [目录结构与代码地图](#4-目录结构与代码地图)
5. [配置层 config.py](#5-配置层)
6. [凭据与认证 auth](#6-凭据与认证)
7. [数据模型 models](#7-数据模型)
8. [小米健康云协议（逆向核心）](#8-小米健康云协议)
9. [适配器层 mi_fitness_cloud.py](#9-适配器层)
10. [同步服务 sync_service.py](#10-同步服务)
11. [存储层 storage](#11-存储层)
12. [查询服务 query_service.py](#12-查询服务)
13. [MCP Server server.py](#13-mcp-server)
14. [CLI、辅助脚本与测试](#14-cli辅助脚本与测试)
15. [已知问题清单（重构目标）](#15-已知问题清单)
16. [重构方案建议](#16-重构方案建议)
17. [本地环境备注](#17-本地环境备注)

---

## 1. 项目定位与背景

**mi-fitness-mcp-cn** 是一个本地运行的 Python MCP（Model Context Protocol）Server，用于把用户自己的「小米运动健康」App 云端数据同步到本地 SQLite，并通过 MCP 工具供 AI 客户端（如 Claude Desktop）查询。

- 改造自 `kubulashvili/mi-fitness-mcp`（国际区），本 fork 的增量：
  1. 支持中国区云端（`--region cn`，API host `hlth.io.mi.com`，时区 UTC+8）；
  2. 新增睡眠（`sleep`）与运动记录（`workouts`）同步；
  3. 额外逆向并接入 4 个数据 key：`resting_heart_rate`、`spo2`、`stress`、`abnormal_heart_beat`；
  4. CLI 输出与注释中文化。
- **非官方项目**，接口全部为逆向所得，无任何官方保证，随时可能因小米改版而失效。
- 仅用于读取分析用户**自己的**健康数据。MIT 协议。

## 2. 快速事实卡

| 项 | 值 |
|---|---|
| 语言 / 最低版本 | Python ≥ 3.11（用了 `datetime.UTC`） |
| 核心依赖 | `mcp`（**必须 <2**，2.x 移除了 `Server.list_tools`）、`httpx`、`pydantic` ≥2、`platformdirs`、`keyring`、`click`（声明了但**未使用**）、`rich`（声明了但**未使用**） |
| 构建 | hatchling；入口 `mi-fitness-mcp = mi_fitness_mcp.cli_mi_fitness:main` |
| 源码规模 | src 约 3800 行 + 测试 370 行 + 独立探测脚本 268 行 |
| 异步模型 | 全 asyncio（httpx.AsyncClient + MCP stdio）；但 DB 层是**同步 sqlite3** |
| 配置文件 | `<user_config_dir>/mi-fitness-mcp/config.json`（Windows: `C:\Users\<u>\AppData\Local\mi-fitness-mcp\mi-fitness-mcp\config.json`） |
| 凭据存储 | OS keyring，服务名 `mi-fitness-mcp`，账号 `mi_fitness_auth_user_id` / `mi_fitness_auth_pass_token` |
| 数据库 | `<user_data_dir>/mi-fitness-mcp/mi_fitness.db`（Windows 同上双目录），SQLite，8 张业务表 + 1 张同步状态表 |
| 认证方式 | 小米账号 Cookie 中的 `userId` + `passToken`（用户手工从浏览器获取），换取 `serviceToken` + `ssecurity` |
| 区域 | 已知 `ru, cn, de, i2, sg, us`；cn 用 `hlth.io.mi.com` + UTC+8，其余用 `{region}.hlth.io.mi.com` + UTC |

## 3. 总体架构

```
                       ┌──────────────────────────────────────────────┐
                       │                MCP 客户端 (AI)                │
                       └───────────────▲──────────────────────────────┘
                                       │ stdio (JSON-RPC)
┌──────────────────────────────────────┴──────────────────────────────┐
│  cli_mi_fitness.py ──► main.py (argparse CLI: serve/setup/doctor/sync)
│         serve ─────────────► server.py  (mcp.server.Server, 模块级单例)│
│                                  │ list_tools / call_tool            │
│         sync ──┐                 ▼                                   │
│                │      ┌─────────────────────┐  ┌──────────────────┐ │
│                └─────►│   SyncService       │  │   QueryService   │ │
│                       │  分块/增量/去重     │  │  聚合/过滤(内存) │ │
│                       └─────────┬───────────┘  └────────┬─────────┘ │
│                                 ▼                       ▼           │
│                       ┌──────────────────────────────────┐          │
│                       │   Database (sqlite3, 同步)        │          │
│                       └──────────────────────────────────┘          │
└─────────────▲───────────────────────────────────────────────────────┘
              │ AsyncIterator[Pydantic Model]
│  ┌──────────┴───────────────────────┐
│  │ MiFitnessCloudAdapter            │
│  │  登录换取 serviceToken/ssecurity │
│  │  RC4 加密 + SHA1 签名            │
│  │  分页拉取 + 解析为模型            │
│  └──────────┬───────────────────────┘
└─────────────┼───────────────────────────────────────────────────────┘
              │ HTTPS (加密表单)
   ┌──────────┴──────────────────┐        ┌─────────────────────────┐
   │ account.xiaomi.com 登录      │        │ hlth.io.mi.com 数据接口  │
   └─────────────────────────────┘        └─────────────────────────┘
```

分层职责一句话：

- **协议层（adapter 内嵌）**：登录、加密签名、分页、把云端 JSON 解析成 Pydantic 模型。
- **服务层**：SyncService 负责「按 7 天分块拉取→落库→记录增量游标」；QueryService 负责从本地库读出后做内存聚合/过滤。
- **接口层**：server.py 暴露 14 个 MCP 工具；main.py 暴露 4 个 CLI 子命令。
- **存储层**：sqlite3 同步连接，UPSERT 写入，字符串日期过滤查询。

## 4. 目录结构与代码地图

```
mi-fitness-mcp-cn/
├── pyproject.toml              # 构建/依赖/工具链（mcp>=1.0.0,<2 为本地修复，上游仍是 >=1.0.0）
├── probe_mifitness.py          # 独立交互式探测脚本：复制了一份登录+加密代码（268 行，代码重复）
├── .env.example                # 声明了 MI_FITNESS_USER_ID/PASS_TOKEN/REGION —— 但代码里【没有任何 env 加载逻辑】
├── mcp-server-config.example.json
├── docs/PROJECT_DOCUMENTATION.md  # 本文档
├── tests/                      # pytest + pytest-asyncio(auto) + respx
│   ├── test_config.py          # 14 行：配置存取 roundtrip
│   ├── test_mi_fitness_cloud.py# 102 行：适配器纯函数（解析/时区/分页辅助）
│   ├── test_server_sync.py     # 77 行：server 同步编排（mock adapter）
│   ├── test_storage_query.py   # 47 行：入库/查询
│   └── test_sync_service.py    # 130 行：分块、增量、异常→partial
└── src/mi_fitness_mcp/
    ├── __init__.py             # 3 行
    ├── cli_mi_fitness.py       # 5 行：entry point 转发器 → main.main
    ├── main.py                 # 193 行：argparse CLI（serve/setup/doctor/sync）⚠ 名字叫 main 实为 CLI
    ├── server.py               # 657 行：MCP Server（tools + handlers + 后台同步 + 生命周期）
    ├── config.py               # 76 行：Config 模型 + JSON 读写
    ├── auth/__init__.py        # 45 行：keyring 存取 userId/passToken
    ├── models/__init__.py      # 190 行：全部 Pydantic 模型
    ├── adapters/
    │   ├── base.py             # 116 行：DataAdapter 抽象基类（8 个 iter_* + 4 个基础方法）
    │   └── mi_fitness_cloud.py # 805 行：★核心★ 协议实现 + 8 类数据解析器
    ├── services/
    │   ├── sync_service.py     # 290 行：分块增量同步引擎
    │   └── query_service.py    # 366 行：读侧聚合/过滤
    └── storage/__init__.py     # 878 行：Database 类（建表/UPSERT/查询/覆盖统计）
```

**模块依赖方向**（重构时保持或理顺）：`cli → server/main`、`server → services + adapter + storage + auth + config`、`services → adapter + storage`、`adapter → models + base`、`storage → models`。无循环依赖。

## 5. 配置层

`Config`（pydantic BaseModel）字段全表，含**实际使用状态**：

| 字段 | 类型/默认 | 实际用途 | 状态 |
|---|---|---|---|
| `mode` | `Literal["mi_fitness_cloud","not_configured"]` = `not_configured` | 判定是否已配置 | 使用中 |
| `region` | str = `"ru"` | API host 与时区选择 | 使用中；⚠ 默认值与 README 推荐的 `cn` 不一致 |
| `timezone` | str = `"UTC"` | 仅 `get_profile` 返回值里出现 | **准死代码** |
| `database_path` | Path = platformdirs | DB 位置 | 使用中 |
| `logs_path` | Path = platformdirs | —— | **完全未使用**（无 logging 配置代码） |
| `auto_sync_on_start` | bool = True | —— | **未使用**（server 启动明确不连接不同步） |
| `stale_after_minutes` | int = 60 | —— | **未使用** |
| `store_raw_payloads` | bool = True | —— | **未使用**（原始报文从未落库） |
| `default_lookback_days` | 30 (1–3650) | 未给 start_date 时的回溯天数 | 使用中 |
| `sync_chunk_days` | 7 (1–90) | 分块大小 | 使用中 |
| `http_timeout_seconds` | 20.0 (0–120] | httpx 超时 | 使用中 |
| `request_retries` | 3 (1–10] | 请求重试次数 | 使用中 |
| `health_check_timeout_seconds` | 10.0 | 状态工具的健康检查 wait_for | 使用中 |
| `sync_type_timeout_seconds` | 180.0 | 单类型同步 asyncio.wait_for 上限 | 使用中（仅 MCP 路径；CLI 路径无超时） |
| `max_pages` | 200 (1–5000) | 单 key 分页安全上限 | 使用中 |

- 存取：`load_config()` 无文件则生成默认配置并**立即写盘**；`save_config()` 把 Path 序列化为字符串。
- 路径：`platformdirs.user_config_dir("mi-fitness-mcp")` / `user_data_dir(...)` / `user_log_dir(...)`。Windows 实测为 `AppData\Local\mi-fitness-mcp\mi-fitness-mcp\`（appauthor 与 appname 双层目录）。
- ⚠ `.env.example` 是孤儿文件：代码不读取任何环境变量。

## 6. 凭据与认证

`auth/__init__.py`，基于系统 keyring：

- `save_mi_fitness_token(user_id, pass_token)`：写两条密码项，服务名 `mi-fitness-mcp`，账号名 `mi_fitness_auth_user_id` / `mi_fitness_auth_pass_token`。
- `load_mi_fitness_token()`：读取，任何异常吞掉并返回 `(None, None)`。
- `delete_mi_fitness_token()`：删除。**没有任何 CLI/MCP 命令调用它**（无 logout 功能）。
- README 提示：无系统 keyring 时可装 `keyrings.alt`（可能明文存盘）。

## 7. 数据模型

`models/__init__.py`。所有业务实体继承 `BaseEntity`：

| 公共字段 | 说明 |
|---|---|
| `id` | 确定性主键，格式 `mi_fitness_{类别}_{业务键}`（详见 §11 UPSERT） |
| `provider` | 恒为 `mi_fitness` |
| `source_type` | 恒为 `cloud_session` |
| `source_record_id` | 云端记录 `time` 字符串，可空 |
| `user_id` / `device_id` | device_id 恒为 None（云端未解析设备） |
| `timezone` | 记录级时区名（`zone_name`），默认 UTC |
| `collected_at` | 记录时间（带记录自身 offset） |
| `created_at/updated_at` | 模型层默认 now(UTC) 去掉 tzinfo；**落库时并不写入这两列** |

业务实体：

| 模型 | 关键字段 | 备注 |
|---|---|---|
| `DailyActivity` | `date`(YYYY-MM-DD)、`steps`、`distance_m`、`active_kcal`；可选 `total_kcal/floors/active_minutes` | 云端只解析前 3+2 项 |
| `SleepSession` | `sleep_id`、`start_at/end_at`、`duration_minutes`、`time_asleep/time_awake_minutes`、`sleep_score`(0-100)、`is_nap`、`stages: list[SleepStage]` | stages 落库为 JSON 字符串 |
| `SleepStage` | `stage ∈ {deep,light,rem,awake}`、`minutes` | state 映射见 §9.4 |
| `Workout` | `workout_id`、`activity_type`、`start_at/end_at`、`duration_minutes`、距离/卡路里/平均最高心率/配速/步数 | 可选字段 0 值会被转 None |
| `BodyMeasurement` | `timestamp`、`weight_kg`(必填>0)、bmi/体脂/肌肉/水分/骨量/内脏脂肪/基础代谢/代谢年龄 | |
| `HeartRateSample` | `timestamp`、`bpm`、`sample_type ∈ {resting,active,passive,workout}` | `workout` 枚举**永远不会被赋值** |
| `SpO2Sample` | `timestamp`、`spo2_pct`(0-100) | |
| `StressSample` | `timestamp`、`stress_score`(0-100)、`level ∈ {low,medium,high}` | level 阈值 <30 low，<60 medium，否则 high |
| `AbnormalHeartBeatEvent` | `event_id`、`start_at/end_at`、`duration_seconds` | |

响应/辅助模型：

- `QueryResponse`：所有 MCP 查询工具的统一信封 `{status, source, generated_at, timezone, data, error}`。
- `ConnectionStatus`：连接状态工具用（代码里还手工 merge 了 `connection_state/region/last_health_check_at/last_connection_error/sync_in_progress` 等额外键）。
- `SyncResult`、`UserProfile`、`DeviceInfo`、`DataCoverage`：**已定义但基本未使用**（SyncResult 的字段被 server 手工拼 dict；UserProfile 仅部分字段复用；DataCoverage.gaps_detected 从未计算）。

## 8. 小米健康云协议

> 本章是本项目最有价值的资产，重构时**协议行为必须保持逐字节兼容**，否则登录/签名会失效。

### 8.1 登录（passToken → serviceToken + ssecurity）

```
GET https://account.xiaomi.com/pass/serviceLogin?_json=true&sid=miothealth
Cookie: userId={user_id}; passToken={pass_token}
```

- 响应体以 **`&&&START&&&`** 前缀开头，去掉前缀后为 JSON，关键字段：
  - `passToken`（新 token，登录成功后**回写覆盖** self.pass_token）
  - `userId`（回写，转 str）
  - `ssecurity`（base64，解码后保存为 bytes，用于后续签名）
  - `location`（重定向 URL）
- 随后 `GET {location}`，收集响应的 `set-cookie`（取每条的 `k=v` 部分），拼接为会话 Cookie（含 `serviceToken` 等）。
- ⚠ `httpx.AsyncClient(follow_redirects=False)`：location 是手工 GET 的；该响应的 cookie 不会自动进 cookie jar，而是拼成字符串 `self._cookies` 手工携带。

### 8.2 请求加密与签名（RC4 + 双签名）

对数据端点的每次 POST：

1. 明文表单：`data = json.dumps(payload, separators=(",", ":"))`（紧凑 JSON）。
2. `nonce = os.urandom(8) + struct.pack(">I", unix_ts // 60)`（12 字节，后 4 字节是**分钟级**时间戳，大端）。
3. `signed_nonce = sha256(ssecurity + nonce)`（32 字节原始摘要）。
4. RC4 加密：以 `signed_nonce` 为 key，KSA 标准初始化后 **PRGA 先丢弃 1024 字节**，再异或明文。
5. 签名函数 `_gen_signature(method, path, values, signed_nonce)`：
   `base = method + "&" + path + "&data=" + values["data"]`（若 values 里有 `rc4_hash__` 则追加 `&rc4_hash__=...`）+ `"&" + base64(signed_nonce)`；结果 `base64(sha1(base))`。
6. 组装最终表单（全部再 urlencode）：
   - `data`：RC4(明文 JSON)
   - `rc4_hash__`：对**明文**表单计算的签名
   - `signature`：对**密文**表单（含 data、rc4_hash__）计算的签名
   - `_nonce`：base64(nonce)
7. `POST {base_url}{api_path}`，`Content-Type: application/x-www-form-urlencoded`，`Cookie: {会话cookie}`。
8. 响应体是 **base64 文本**，用 `signed_nonce` RC4 解密得 JSON：`{code, message, result}`。
   - `code != 0` → 抛错；`code ∈ {401,403,-6,-10001}` 或 message 命中 auth 标记（`unauthorized / not logged in / session expired / invalid pass token / login required / ...`，大小写折叠匹配）→ 判定为认证失败。

### 8.3 端点与分页

| 端点 | 用途 | 请求 payload | 响应关键字段 |
|---|---|---|---|
| `POST /app/v1/data/get_fitness_data_by_time` | 健康数据（steps/calories/heart_rate/resting_heart_rate/weight/sleep/spo2/stress/abnormal_heart_beat） | `{start_time, end_time, key, next_key?}`（epoch 秒） | `data_list[]`、`has_more`、`next_key` |
| `POST /app/v1/data/get_sport_records_by_time` | 运动记录 | `{start_time, end_time, limit: 50, next_key?}` | `sport_records[]`、`has_more`、`next_key` |

- host：region 为 `cn` 或空 → `https://hlth.io.mi.com`；否则 `https://{region}.hlth.io.mi.com`。
- 分页：循环携带 `next_key` 直到 `has_more` 为假；用 `seen_keys` 集合检测游标成环（报 `pagination cursor loop detected`）；页数超过 `max_pages`（默认 200）报 `pagination exceeded safety limit`。
- 列表项公共字段：`time`（epoch 秒，记录时间）、`zone_offset`（秒）、`zone_name`（如 `Asia/Shanghai`）、`sid`（设备/来源 ID）、`value`（**JSON 字符串或 dict**）。
- 时间换算：请求范围由「日期字符串 + 区域时区」决定——cn/空 → UTC+8，其他 → UTC；`end_date` 取到当天 `23:59:59`。

### 8.4 各数据 key 的载荷格式与字段映射

`value` 内字段 → 模型字段（括号内为备选字段名）：

**steps / calories（daily_activity 两次请求）**
- steps：`{steps, distance, calories}` → 按天累加 steps/distance_m/active_kcal（初值）
- calories：`{calories}` → 按天累加后**整体覆盖**该日的 `active_kcal`
- 日归属：以记录 `time` + `zone_offset` 格式化为 `YYYY-MM-DD`

**sleep**
- 入睡：`bedtime`（备选 `device_bedtime`、`bed_timestamp`）
- 醒来：`wake_up_time`（备选 `device_wake_up_time`、`out_bed_timestamp`、兜底 `time`）
- `duration`（分钟；缺省用 end-start）；清醒 `awake_duration`（备选 `sleep_awake_duration`）；入睡分钟 = duration - awake
- `score`（备选 `sleep_score`）→ sleep_score；`is_nap`
- 分段 `items[]`：`{start_time, end_time, state}`，state 映射：`1→deep, 2→light, 3→light, 4→awake, 5→rem`，其他→light；分钟数=(end-start)//60，0 分钟丢弃
- `sleep_id = f"{sid or user_id}_{item.time or int(睡眠结束ts)}"`

**heart_rate**
- `{type, bpm}`：`type==0 → sample_type="passive"`，否则 `"active"`
- **resting_heart_rate**（独立 key）：`{date_time, bpm}` → `sample_type="resting"`；时间戳优先 `date_time`

**weight（body_measurements）**
- `weight`；`bmi`；`body_fat_rate→body_fat_pct`；`muscle_rate→muscle_mass_kg`；`moisture_rate→water_pct`；`bone_mass`;`visceral_fat→visceral_fat_score`;`basal_metabolism→basal_metabolism_kcal`;`body_age→metabolic_age`
- id：`mi_fitness_weight_{item.time}`

**workouts（sport 端点）**
- `start_time`（兜底 `time`）、`end_time`（缺省 = start+duration 秒）、`duration`（秒）
- `distance`、`calories`（备选 `total_cal`）、`avg_hrm`、`max_hrm`、`avg_pace`、`max_pace`、`steps`（备选 `total_steps`）、`sport_type`
- `activity_type = item.category or item.key or sport_type or "workout"`
- `workout_id = f"{sid or user_id}_{item.key or 'workout'}_{item.time or start_ts}"`

**spo2**：`{time, spo2 | value}`；timestamp 优先 value 内 `time`
**stress**：`{time, stress | score | value}`；level 按分数分段
**abnormal_heart_beat**：`{start_time, end_time}`；`event_id = f"{sid or user_id}_{int(start_ts)}"`

数值清洗：`_optional_float/_optional_int` 把 **0 值转 None**（缺省语义与“真实 0”被合并，见 §15-I6）。

### 8.5 错误处理与重试（_request 循环）

- 重试 `request_retries`（默认 3）次，指数退避 `min(4s, 0.5*2^attempt) + rand(0..0.1)`。
- 可重试：httpx 超时/网络错误；HTTP 429、≥500。
- 认证失败（401/403 或业务码/message 命中）：`_connected=False`，若还有重试余量且持有凭据 → **用 passToken 重新登录**并继续重试；重新登录失败 → 立即终止。
- 其他业务错误：立即终止。
- 最终失败统一抛 `RuntimeError("Mi Fitness request failed: ...")`（认证错误原样抛 `MiFitnessAuthenticationError`）。
- ⚠ 全部错误被包装后，上层无法再区分认证失败与普通失败（`MiFitnessAuthenticationError` 在 `_request` 末尾被重新抛出，这一条保留；但 `connect()` 会把一切折叠成 `False` + 字符串 last_error）。

### 8.6 区域发现与时区

- `_discover_region(preferred)`：对候选区域依次试拉 `weight/steps/heart_rate`（固定区间 2025-04-01~2025-05-31），有数据即选中。**当前永远不会被调用**（region 恒为非空，见 §9.1 注释）——准死代码。
- `_request_timezone`：`cn`/空 → UTC+8；其余 → UTC。`Config.timezone` 字段不参与此逻辑。

## 9. 适配器层

`adapters/base.py`：`DataAdapter(ABC)` 抽象 4 个基础方法（`connect/is_connected/get_user_id/get_available_data_types`）+ 8 个 `iter_*`（同步或异步迭代器二选一，签名返回 `Iterator | AsyncIterator` 联合类型——重构时建议收敛为纯 async）。

`adapters/mi_fitness_cloud.py` 实现要点：

- **懒连接**：MCP 启动时只构造不连接（`server.main` 注释：stdio 必须快速可用）；所有工具按需 `connect()`。`connect()` 有 `asyncio.Lock` 防并发，成功后 `_discover_data_types()` 返回**固定 8 类型列表**（不再探测，注释说明了原因：固定历史区间探测会静默漏数据）。
- `health_check()`：真实调用 `_fetch_key("steps", today, today)`（1 天小请求验证认证+数据通路）；瞬时网络失败**不会**使有效会话失效。
- `iter_*` 的共同骨架：`if not connected or 没日期: return; yield`（空迭代器）→ 拉取 → 解析 yield。**注意 `return; yield` 写法**是为了把生成函数变为 async generator 的惯用法。
- 分页辅助 `_fetch_key`（fitness）/ `_fetch_sport_records_by_time`（sport）：见 §8.3。
- `_record_datetime`：`datetime.fromtimestamp(item.time, tz=timezone(item.zone_offset))` —— 所有记录时间都带**记录自身**的时区偏移。
- 每次 `connect()` 都会新建 `httpx.AsyncClient` 并重登录；`close()` 关闭并置 `_connected=False`。
- 可调参数（由 server 从 Config 注入）：`http_timeout / request_retries / max_pages`。

## 10. 同步服务

`services/sync_service.py`。`SyncService(adapter, db, default_lookback_days=30, chunk_days=7)`。

### 10.1 并发控制

- `sync_data_type()`：`if self._sync_active or self._sync_lock.locked(): raise RuntimeError` → 置 `_sync_active=True`（**无 await 的检查+置位**，在单事件循环内原子）→ `async with lock` 执行 → finally 复位。
- server.py 另有全局 `sync_active` 布尔（拒绝并发 sync_data 工具调用）——**两把锁叠加**。

### 10.2 单类型同步算法 `_sync_data_type_unlocked`

1. 增量游标：非 force_full 时读 `sync_state.last_record_timestamp`（`datetime.fromisoformat` 后 `replace(tzinfo=None)` —— ⚠ 丢偏移量）。
2. 日期缺省：`end_date` 默认今天（**本地时区 naive**）；`start_date` 缺省 = 游标日期（若有）否则 `end - (lookback-1)` 天。
3. 校验 `start ≤ end`，格式必须 `YYYY-MM-DD`。
4. **分块循环**：以 `chunk_days`（7）切分区间，逐块调 `_sync_range`；单块异常 → 记录 chunk error 并**立即停止**后续块，返回 `status=partial`（若已有成功块）或 `error`。
5. 汇总 `{status, data_type, added, updated, skipped, start_date, end_date, chunks[]}`；每个 chunk 记录 added/updated/skipped。
6. `skipped` 恒为 0（计数器从未自增）。

### 10.3 `_sync_range`（单块落库）

- 按 `data_type` 分派到 8 个 `iter_*`，逐条 `db.insert_*`；返回 True 计 added，False 计 updated（**实际永远 True**，见 §15-I2）。
- 逐条跟踪 `last_ts`（各类型用不同字段：activity 用 `collected_at`、sleep/workout/abnormal 用 `start_at`、其余用 `timestamp`）。
- 块结束时 `db.update_sync_state(data_type, last_ts)`（仅当 last_ts 非空）。
- 未知类型抛 `ValueError`。
- `sync_data_type_sync()`：`asyncio.run` 包装的同步版本 —— **在已有事件循环内会炸**，当前无人调用（死代码）。

### 10.4 性能特征（实测）

- 8 个月全类型同步 ≈ 每类型 `ceil(天数/7)` 次端点调用（heart_rate 还要 ×2：active+resting keys），单线程串行，总耗时数分钟。首次全量 2026-01-01→08-30 实测约 5–9 分钟；昨天+今天增量 <10 秒。

## 11. 存储层

`storage/__init__.py`（878 行）。同步 sqlite3，`sqlite3.Row`，**每次操作独立连接**（`contextmanager` 打开/关闭），每条 INSERT 单独 `conn.commit()`。

### 11.1 表结构（8 业务表 + sync_state）

所有业务表公共列：`id TEXT PK, provider, source_type, source_record_id, user_id, device_id, timezone, collected_at, created_at, updated_at, …业务列`。时间值以 **ISO 字符串（带原 offset）** 存储。

| 表 | 业务列 | 业务 UNIQUE 约束 | UPSERT 冲突目标 |
|---|---|---|---|
| `daily_activity` | date, steps, distance_m, active_kcal, total_kcal, floors, active_minutes | `(user_id, date, device_id)` | `ON CONFLICT(id)` → 更新 steps/distance/active_kcal/timezone/collected_at 等 |
| `sleep_sessions` | sleep_id, start_at, end_at, duration_minutes, time_asleep_minutes, time_awake_minutes, sleep_score, is_nap, stages(JSON TEXT) | `(user_id, sleep_id)` | `ON CONFLICT(user_id, sleep_id)` → 不更新 start_at/end_at（⚠ 后端变化不会修正时间） |
| `workouts` | workout_id, activity_type, start_at, end_at, duration_minutes, distance_m, calories_kcal, avg/max_heart_rate_bpm, avg/max_pace_sec_per_km, total_steps | `(user_id, workout_id)` | `ON CONFLICT(user_id, workout_id)` → **不更新 activity_type/start_at/end_at/pace/steps** |
| `body_measurements` | timestamp, weight_kg, bmi, body_fat_pct, muscle_mass_kg, water_pct, bone_mass_kg, visceral_fat_score, basal_metabolism_kcal, metabolic_age | `(user_id, timestamp, device_id)` | `ON CONFLICT(id)` → 只更新 weight/bmi/body_fat/muscle/water |
| `heart_rate_samples` | timestamp, bpm, sample_type | `(user_id, timestamp, sample_type)` | `ON CONFLICT(id)` → 全字段更新；⚠ id=`mi_fitness_hr_{time}` 与业务约束不一致：同秒不同类型 → id 不同但撞业务 UNIQUE → `IntegrityError` |
| `spo2_samples` | timestamp, spo2_pct | `(user_id, timestamp)` | `ON CONFLICT(id)` |
| `stress_samples` | timestamp, stress_score, level | `(user_id, timestamp)` | `ON CONFLICT(id)` |
| `abnormal_heart_beat_events` | event_id, start_at, end_at, duration_seconds | `(user_id, event_id)` | `ON CONFLICT(user_id, event_id)` |
| `sync_state` | id PK, data_type UNIQUE, last_sync_at, last_record_timestamp, records_count | —— | `ON CONFLICT(data_type)`；⚠ `records_count` 永远是 0（从不更新） |

索引：8 个 `(user_id, 日期/时间列)` 索引。无迁移机制（仅 `CREATE TABLE IF NOT EXISTS`）。

### 11.2 关键语义（重构必须知道）

1. **insert 返回值不可信**：`cursor.rowcount > 0` 在 SQLite 的 `INSERT … ON CONFLICT DO UPDATE` 下插入和更新都返回 1 → added/updated 统计失真（实测重复同步显示「新增 242、更新 0」）。要区分需比较 `total_changes()` 或先查后写。
2. **日期过滤是字符串比较**：查询用 `substr(时间列,1,10) >= ? AND <= ?`，比较的是 ISO 字符串前 10 位——即**记录自身时区的日期**。混合 `+08:00` 与 `Z` 记录时，同一“本地日”可能被切错；边界记录（23:xx / 00:xx）可能落入相邻日期桶。
3. **created_at/updated_at 落库靠 SQLite 默认值**（CURRENT_TIMESTAMP，UTC），模型字段不传入。
4. **阻塞风险**：所有 DB 调用是同步的，且被 async 服务直接调用（无 `to_thread`）——大查询会卡 MCP 事件循环。
5. `get_data_coverage()`：对每表做 `MIN/MAX/COUNT(DISTINCT date)` 统计；daily 用 `date` 列，sleep/workouts/measurements/HR 用 `substr(...,1,10)`，spo2/stress/abnormal 用 `date(expr)`（三种风格并存）。

## 12. 查询服务

`services/query_service.py`。`QueryService(db, user_id)` —— 全部为**本地库读后内存加工**，不做 SQL 聚合。

| 方法 | 行为 |
|---|---|
| `get_daily_summaries(start, end)` | 拉区间内 daily_activity，按日期分组求和（steps/distance/active_kcal/total_kcal/floors/active_minutes） |
| `get_metric_series(metric, start, end, granularity, aggregation)` | 基于 summaries 取单指标序列；`granularity=week/month` 时内存聚合（周以周一为起点）；**metric 枚举里的 `weight_kg` 永远取不到值**（summaries 没有该字段） |
| `get_sleep_sessions(..., include_naps)` | 过滤 nap；stages 从 JSON 解析；按 start_at 排序 |
| `get_workouts(...)` | 内存过滤 activity_types（lower 比较）/min_duration/min_distance_km |
| `get_body_measurements(...)` | 只输出非 None 指标；metrics 参数做字段白名单过滤 |
| `get_heart_rate_samples(...)` | 内存过滤 sample_type；**limit 在 Python 侧切片**（全量拉出后截断，大区间低效） |
| `get_spo2_samples / get_stress_samples / get_abnormal_heart_beat_events` | 同上模式（stress 支持 level 过滤） |
| `get_data_coverage(data_types?)` | 转发 db 统计，可按类型过滤 |

## 13. MCP Server

`server.py`。**架构特征：模块级全局单例** `config/db/adapter/sync_service/query_service/sync_tasks/sync_active`，由 `main()` 初始化。

### 13.1 生命周期

- `main()`：加载配置 → 建 `Database`（建表）→ `mode==mi_fitness_cloud` 且 keyring 有凭据时构造 adapter 并注入 http_timeout/retries/max_pages → 构造两个 service → `stdio_server()` 启动。**启动时不 connect**（保证 MCP 握手不被网络阻塞）。
- 退出 finally：取消所有后台 sync task（gather 吞异常）→ `adapter.close()`。
- ⚠ `config` 为 None 或 mode=not_configured 时：连接类工具报错，查询类工具走空库（QueryService 依然创建）。

### 13.2 工具清单（14 个）

| 工具 | 必填参数 | 行为要点 |
|---|---|---|
| `get_connection_status` | — | 同步中→`is_connected()`；否则 `health_check()`（带 `health_check_timeout`）。合并 last_sync（8 类型里最新的 sync_state.last_sync_at）、available_types（连接成功用 adapter 的，否则从 sync_state 推断）、附加 `connection_state/region/last_health_check_at/last_connection_error/sync_in_progress` |
| `sync_data` | — | `data_types[]`（缺省=8 类型全量）、`start_date/end_date`、`force_full_sync`、`background`。空数组报错；未知类型报错。**全局 sync_active 防重入** |
| `get_sync_status` | `sync_id` | 查后台任务表（剔除 task 对象） |
| `get_profile` | — | 静态拼装 `{user_id, timezone, devices:[]}` |
| `get_daily_summary` | `date` 或 `start_date+end_date` | date 同时充当 start/end |
| `query_metric_series` | `metric, start_date, end_date` | metric 枚举 `steps/distance_m/active_kcal/weight_kg`（weight_kg 死枚举）；granularity `day/week/month`；aggregation `sum/avg/min/max/latest`（⚠ latest 在服务层落到 else 分支=**sum**） |
| `query_heart_rate` | `start_date, end_date` | `sample_type` 枚举 `resting/active/passive/workout`；`limit` |
| `query_body_measurements` | 同上 | `metrics[]` 白名单；`latest_only` 取最后一条 |
| `query_sleep` | 同上 | `include_naps` 默认 True |
| `query_workouts` | 同上 | `activity_types/min_duration/min_distance_km` |
| `query_spo2` / `query_stress` / `query_abnormal_heart_beat` | 同上 | stress 支持 `level` 枚举过滤 |
| `get_data_coverage` | — | `data_types[]` 可选过滤 |

完整 inputSchema JSON 见附录 A。响应统一为 `[TextContent(json.dumps(result))]`，异常捕获后返回 `{"status":"error","error":str}`（不抛给 MCP 层）。

### 13.3 后台同步（background=true）

- `_prune_sync_tasks()`：清理已完成任务至多保留 `MAX_SYNC_TASKS=100`。
- `sync_id=uuid4`；task 表条目 `{sync_id, status: queued→running→ok/partial/error/cancelled, created_at, started_at, …结果}`。
- 前台/后台共用 `_run_sync_data`：按需 `adapter.connect()` → 校验/展开 data_types → 逐类型 `asyncio.wait_for(sync_service.sync_data_type(...), sync_type_timeout_seconds)`（**超时只标记该类型 error，不中断其他类型**）→ 汇总 `status: ok/partial/error`。
- 取消传播：`asyncio.CancelledError` 单独处理置 `cancelled`。
- ⚠ 任务表是内存 dict：**MCP 进程重启即丢失**；`get_sync_status` 对未知 id 返回 error。

## 14. CLI、辅助脚本与测试

### 14.1 CLI（main.py；注意：文件名叫 main，职责是 CLI）

| 子命令 | 行为 |
|---|---|
| `serve`（或**无参数**） | `asyncio.run(server.main())` —— ⚠ 裸跑 `mi-fitness-mcp` 会直接启动 MCP server |
| `setup` | `--mode mi_fitness_cloud --user-id --pass-token --region`（mode 未给时进入交互式 input 向导）；保存 keyring + config.json。⚠ 无任何参数校验（非空校验仅在交互路径） |
| `doctor` | 打印配置路径/加载配置/读凭据/真实 `connect()` 测连通（打印区域+8 类型）/检查 DB 文件存在性；配置缺失时 `sys.exit(1)` |
| `sync` | `--type`（单类型）+ `--start-date/--end-date`（缺省 lookback 30 天）；连接失败 exit(1)；逐类型打印 `新增/更新` 条数；**没有分块超时，也没有 `--force-full` 参数** |

### 14.2 probe_mifitness.py

独立交互式诊断脚本（268 行）：**复制粘贴**了一份登录/RC4/签名代码（与 adapter 重复），支持对任意 key/区间做原始请求打印，用于逆向调试。重构候选：改为调用 adapter 的导出函数。

### 14.3 测试（18 个，全部通过）

- `test_config.py`：配置 roundtrip。
- `test_mi_fitness_cloud.py`：`_optional_*`、`_parse_value`（JSON 字符串与 dict 双形态）、`_record_datetime` zone_offset、分页游标环检测、`_is_authentication_error` 标记匹配等纯函数；respx mock HTTP。
- `test_sync_service.py`：mock adapter 的分块/增量/partial 语义。
- `test_storage_query.py`：入库+查询。
- `test_server_sync.py`：server 层编排（mock adapter）。
- 覆盖缺口：CLI 无测试；`_request` 加密路径无 golden-vector 测试；MCP 工具 handler 仅覆盖 sync 部分。

## 15. 已知问题清单

> 分级：**P0**=破坏正确性/可用性；**P1**=行为缺陷或明显技术债；**P2**=清理项。重构时按此核对。

**P0**

- **I1 `mcp` 依赖无上界**：`mcp>=1.0.0` 会拉到 2.x，`Server.list_tools` 不存在 → server 与测试收集直接崩（已在本地 pyproject 修为 `>=1.0.0,<2`，**上游未修**，重构时保留上界或迁移到新 API）。
- **I2 added/updated 统计失真**：UPSERT 下 `cursor.rowcount>0` 恒真 → 永远报“新增”（§11.2-1），连带 `sync_state.records_count` 恒 0。
- **I3 同步游标时区混用**：`last_record_ts` 存的是带 offset 的 ISO 串，续传时 `replace(tzinfo=None)` 直接剥掉 → 跨时区/夏令时场景增量起点可能偏移数小时（边界记录重复或遗漏）。
- **I4 HR 唯一约束与 id 冲突目标不一致**：`UNIQUE(user_id, timestamp, sample_type)` vs `ON CONFLICT(id)`，同秒双类型样本会抛 `IntegrityError` 中断整块同步（sync_range 无 per-record 容错）。

**P1**

- **I5 事件循环阻塞**：同步 sqlite3 + 内存聚合直接在 async 上下文执行；大区间 heart_rate 查询（16k+ 行）会冻结 MCP 心跳/其他工具调用。
- **I6 0 值被吞**：`_optional_float/int(0) → None`，把合法 0（如 0 步运动、0 kcal）当缺省丢弃。
- **I7 日期过滤语义**：`substr(iso,1,10)` 字符串比较混合多 offset 记录（§11.2-2），查询/覆盖统计的“日”归属不稳定。
- **I8 死枚举/死指标**：`query_metric_series.weight_kg` 永远无数据；`aggregation=latest` 落到 sum；`sample_type=workout` 永不产生；`skipped` 恒 0。
- **I9 双重并发闸门**：server `sync_active` + SyncService 锁叠加；`sync_tasks` 内存态不可恢复。
- **I10 协议层无测试保护**：RC4/签名/登录无 golden vectors，重构协议层极易悄悄破坏兼容性。
- **I11 无迁移机制**：改表只能靠手工删库。
- **I12 CLI 与 MCP 路径行为不一致**：CLI 无 per-type 超时/force_full；`serve` 是无参数默认行为（危险默认）。
- **I13 每 chunk 单独 commit**：非原子块，中途断电留半块数据（靠 UPSERT 幂等兜底，但 sync_state 已前移时可能漏补）。

**P2**

- **I14 死代码/孤儿**：`logs_path、auto_sync_on_start、stale_after_minutes、store_raw_payloads、timezone` 配置；`delete_mi_fitness_token`（无 logout 命令）；`SyncResult/UserProfile/DeviceInfo/DataCoverage(gaps_detected)` 模型；`_discover_region`；`sync_data_type_sync`；`click/rich` 依赖；`.env.example`（无 env 加载）。
- **I15 代码重复**：probe_mifitness.py 复制协议代码；query_service 周/月聚合两份近似函数；server 里 14 个 handler 高度模板化。
- **I16 可观测性**：logger 无 handler（日志不知去向）；`last_error` 只进工具响应。
- **I17 命名混乱**：`main.py` 实为 CLI；`server.py` 里 `main()` 实为 MCP 入口。
- **I18 Config 默认 `region="ru"`** 与文档/实际用法（cn）不一致。
- **I19 base.py 抽象签名** `Iterator | AsyncIterator` 联合类型增加调用方分支（`_iterate_records` 就是为兼容它存在）。

## 16. 重构方案建议

### 16.1 硬性约束（不可破坏的兼容契约）

1. **MCP 工具名与 inputSchema 保持不变**（14 个工具；客户端可能已固化调用方式）。响应 JSON 顶层字段保持兼容。
2. **协议层逐字节兼容**：nonce 构造、RC4 drop-1024、双签名 base 串、登录前缀 `&&&START&&&`、分页参数——改协议必须先有 §16.4 的 golden vectors。
3. **本地 DB 平滑升级**：老库文件必须能被新版本打开（或提供一次性迁移脚本），`sync_state` 游标不能丢。
4. CLI 四个子命令与关键 flag 保持（可新增不可删除）。
5. 保持 Python ≥3.11、`mcp>=1,<2` 上界。

### 16.2 目标架构（建议）

```
src/mi_fitness_mcp/
├── app.py               # AppContext：配置、db、adapter、services 集中装配，替代 server 模块级全局
├── cli/                 # main.py 拆分：cli/{__main__,setup,doctor,sync}.py
├── mcp_layer/
│   ├── tools.py         # 14 个工具的 schema 声明（数据驱动，替代 300 行 if-elif）
│   └── handlers.py      # 薄 handler：解析参数→调 service→包 QueryResponse
├── protocol/            # ★从 adapter 抽出，纯函数化、可独立测试
│   ├── xiaomi_auth.py   # 登录（返回 serviceToken/ssecurity/cookies）
│   ├── crypto.py        # nonce/rc4/signature（golden vectors）
│   └── client.py        # XiaomiHealthClient：request/fetch_key/fetch_sport_records（重试/分页/错误分类）
├── adapters/
│   ├── base.py          # 纯 AsyncIterator 抽象
│   └── mi_fitness_cloud.py  # 仅做 payload→model 映射
├── services/
│   ├── sync_service.py  # 分块/增量/游标（tz-aware）
│   └── query_service.py
├── storage/
│   ├── db.py            # aiosqlite（或 run_in_executor）+ 事务化 chunk commit
│   ├── schema.py        # DDL + schema_version 迁移表
│   └── repo.py          # 按类型的仓储（UPSERT 用 total_changes 判定 added/updated）
├── config.py            # pydantic-settings：JSON + 真实 env/.env 支持（落地 .env.example）
└── models/              # 现有模型 + 删除未用模型或接线
```

### 16.3 优先修复映射（问题 → 方案）

| 问题 | 方案 |
|---|---|
| I2 | UPSERT 后用 `conn.total_changes()` 差值或 `RETURNING` 判定 inserted/updated；顺带维护 `records_count` |
| I3 | 游标统一存 UTC（或带 offset 的完整 ISO），续传转 aware 再比较；默认日期一律 aware |
| I4 | HR 的 UPSERT 冲突目标改为业务 UNIQUE（`user_id,timestamp,sample_type`），id 与约束二选一统一 |
| I5 | storage 全链路 aiosqlite/线程池；查询下推 SQL（LIMIT/聚合） |
| I7 | 日期列改为存 UTC + 原始 offset 两列，或查询时用 `unixepoch()` 换算到用户时区再比较 |
| I6 | `_optional_*` 只把 None/空转 None，保留 0 |
| I8 | 删 weight_kg 枚举或实现体重序列（join body_measurements）；`latest` 聚合单独实现；HR workout 类型留待协议支持或删枚举 |
| I9 | AppContext 单一同步闸门；后台任务可选落盘（sync_state 里记 running 状态） |
| I13 | 每 chunk 一个事务（数据+游标同 commit） |
| I11 | `schema_version` 表 + 线性迁移函数链 |

### 16.4 验收标准

1. `pytest` 通过，且新增：crypto golden vectors（固定 ssecurity/nonce → 固定密文/签名）、8 类 payload fixture 解析快照测试、分块边界（chunk=1 天/跨月）、UPSERT added/updated 正确性、时区边界（+08:00 与 Z 混合查询）。
2. 手工验收序列（当前环境可直接跑）：
   - `mi-fitness-mcp doctor` → 连接 ✅、8 类型齐全；
   - `sync --start-date <昨天> --end-date <今天>` → 与重构前逐表 rowcount/内容 diff 一致；
   - `serve` + MCP 客户端调用全部 14 工具成功；
   - 老库文件（本机 `AppData\Local\mi-fitness-mcp\mi-fitness-mcp\mi_fitness.db`，约 19k 行数据）可直接打开且覆盖统计正确。
3. 行为不回退：8 个月全量同步耗时不显著劣化；单类型超时（180s）语义保留。

## 17. 开发环境备注

- 要求 Python ≥ 3.11；开发安装 `pip install -e '.[dev]'`（含 pytest/ruff/build）。
- 跑测试：`python -m pytest -q`（18 passed，pytest-asyncio auto 模式）。
- Lint：`python -m ruff check src tests`（CI 同款规则集，见 pyproject `[tool.ruff]`）。
- 构建：`python -m build`（hatchling）。
- 运行时产物均在系统用户目录（platformdirs），不在仓库内：config.json、SQLite 库、keyring 凭据；`.gitignore` 已排除 `.venv/ *.db .env` 等。

## 18. HTTP API 扩展（新增模块）

在 MCP 之外提供同能力的 REST 封装，供普通程序直接调用：

- 模块：`src/mi_fitness_mcp/api.py`（FastAPI + uvicorn，可选依赖组 `pip install -e '.[api]'`）。
- 启动：`mi-fitness-mcp api [--host 127.0.0.1] [--port 8321]`（新 CLI 子命令；绑定非本机地址且未设 key 时打印警告）。
- 鉴权：设置环境变量 `MI_FITNESS_API_KEY` 后，全部接口要求请求头 `X-API-Key`；未设置则不鉴权（仅建议 127.0.0.1 场景）。已全局启用 CORS。
- 生命周期：lifespan 中装配 config/Database/adapter/SyncService/QueryService（与 `server.main` 相同的懒连接策略），退出时取消后台同步任务并关闭 adapter。查询端点为同步 `def`（FastAPI 线程池执行，不阻塞事件循环）。
- 端点一览（均为 `/api` 前缀，响应信封 `{"status","count","data"}` 或同步结果对象）：
  - `GET /` 服务信息；`GET /docs` Swagger UI
  - `GET /api/status?deep=true|false`（deep=true 真实健康检查）
  - `POST /api/sync` body `{data_types?, start_date?, end_date?, force_full_sync?, background?}`；前台等待返回汇总，`background=true` 返回 `{status:"accepted", sync_id}`
  - `GET /api/sync/{sync_id}` 后台任务状态（内存态，重启即失）
  - `GET /api/summary?start_date&end_date`
  - `GET /api/metric-series?metric&start_date&end_date&granularity&aggregation`（metric 限 steps/distance_m/active_kcal，已在 API 层校验）
  - `GET /api/heart-rate?start_date&end_date&sample_type&limit`
  - `GET /api/sleep?start_date&end_date&include_naps`
  - `GET /api/workouts?start_date&end_date&activity_types&min_duration&min_distance_km`
  - `GET /api/body-measurements?start_date&end_date&metrics&latest_only`
  - `GET /api/spo2` / `GET /api/stress?level` / `GET /api/abnormal-heart-beat`
  - `GET /api/coverage?data_types`
- 错误约定：参数格式错→400，重复同步→409，未初始化→503，云连接失败→502，未知 sync_id→404，鉴权失败→401。
- 已知取舍：`sync_tasks` 为内存态；查询与同步并发时 sqlite 写入可能偶发 busy（上游 I5/I13 修复后自然消除）。

## 19. API Key 体系与扫码登录（v0.2 新增）

### 19.1 鉴权模型（类大模型平台）

三级访问，实现在 `api.py` 的 `_gate`（入口闸门）+ `_resolve_context`（凭据解析）：

1. **默认凭据**：不带头 = 使用本机配置（keyring）的凭据，仅建议 127.0.0.1。
2. **静态 Key**：环境变量 `MI_FITNESS_API_KEY` 设置后强制全员带头；该值本身也始终有效（管理员通道）。
3. **发放 Key**：`mif_sk_<40 hex>`，存主库 `api_keys` 表（key/label/user_id/pass_token/region/created_at/last_used_at/revoked）。带 Key 请求按 Key 绑定的小米凭据解析出**独立 UserContext**（adapter/sync/query 三服务按凭据缓存），实现多账号隔离；每次使用更新 last_used_at。

管理端点（keys/qr）鉴权：环境变量 `MI_FITNESS_ADMIN_KEY` + `X-Admin-Key` 头；未设置该变量时仅允许本机调用。

### 19.2 Key 管理端点

- `POST /api/auth/keys` body `{user_id, pass_token, region?, label?}` → **真实登录小米验证凭据后**发放 Key（失败 400 返回原因）。完整 Key 仅返回一次。
- `GET /api/auth/keys` → 列表（Key 打码，如 `mif_sk_ab12cd…ef34`）。
- `DELETE /api/auth/keys/{prefix}` → 按完整 Key 或唯一前缀吊销（软删 revoked=1）。

### 19.3 扫码登录（逆向自 account.xiaomi.com 通用 QR 流程）

流程与端点（参考 python-miio `cloud_qr.py`，MIT）：

1. `GET https://account.xiaomi.com/longPolling/loginUrl`，参数**必须** `sid=xiaomiio` + `callback=https://sts.api.io.mi.com/sts`（其他组合返回 code=10025「Callback连接不合法」；`miothealth` 不被扫码接口接受）。响应剥 `&&&START&&&` 后含 `qr`（二维码图片 URL）、`lp`（长轮询 URL）、`loginUrl`（浏览器确认链接）、`timeout`（300 秒）。
2. 用户用小米账号/米家 App 扫码确认，或浏览器打开 loginUrl 登录确认。
3. `GET {lp}` 长轮询，确认后返回 `userId` + 账号级 `passToken`（passToken 与 sid 无关，可再用 serviceLogin 换 miothealth 会话）。
4. 服务端校验凭据可登录后自动发放 API Key。

API 封装：`POST /api/auth/qr/start?region=cn`（返回 qr_token、PNG 地址、login_url）→ `GET /api/auth/qr/{token}.png` → `GET /api/auth/qr/poll?token=...`（waiting / confirmed{api_key,user_id} / expired）。qr_sessions 为内存态，重启失效。

### 19.4 Python Flask Web 仪表盘与反向代理（web.py / testpage）

基于 Python Flask 构建的现代化 Web 仪表盘与反向代理服务（无需 Rust 工具链，纯 Python 生态一键启动）：
- `GET /`：提供极富设计感的现代化中文健康数据仪表盘（全部数据端点 + 同步中心 + API Key 发放/列表/吊销 + 扫码二维码登录弹窗 + 多维图表 + 实时响应检视器）。
- `/proxy/*`：反向代理到 Python FastAPI 后端，服务端自动中继 `X-API-Key` 与 `X-Admin-Key`（凭据与密钥不进浏览器），4xx/5xx 状态码与耗时（`X-Upstream-Time-Ms` 响应头）原样透传。
- 启动命令：`mi-fitness-mcp web` 或 `python testpage/app.py`。
- 环境变量：`BIND`（默认 127.0.0.1:8322）、`MI_FITNESS_API_URL`（默认 127.0.0.1:8321）、`MI_FITNESS_API_KEY`、`MI_FITNESS_ADMIN_KEY`。

---

## 附录 A：MCP 工具 inputSchema 全量 JSON

```json
{
  "get_connection_status": {"type": "object", "properties": {}},
  "sync_data": {
    "type": "object",
    "properties": {
      "data_types": {"type": "array", "items": {"type": "string"}},
      "start_date": {"type": "string"},
      "end_date": {"type": "string"},
      "force_full_sync": {"type": "boolean"},
      "background": {"type": "boolean", "default": false}
    }
  },
  "get_sync_status": {
    "type": "object",
    "properties": {"sync_id": {"type": "string"}},
    "required": ["sync_id"]
  },
  "get_profile": {"type": "object", "properties": {}},
  "get_daily_summary": {
    "type": "object",
    "properties": {"date": {"type": "string"}, "start_date": {"type": "string"}, "end_date": {"type": "string"}}
  },
  "query_metric_series": {
    "type": "object",
    "properties": {
      "metric": {"type": "string", "enum": ["steps", "distance_m", "active_kcal", "weight_kg"]},
      "start_date": {"type": "string"},
      "end_date": {"type": "string"},
      "granularity": {"type": "string", "enum": ["day", "week", "month"]},
      "aggregation": {"type": "string", "enum": ["sum", "avg", "min", "max", "latest"]}
    },
    "required": ["metric", "start_date", "end_date"]
  },
  "query_heart_rate": {
    "type": "object",
    "properties": {
      "start_date": {"type": "string"},
      "end_date": {"type": "string"},
      "sample_type": {"type": "string", "enum": ["resting", "active", "passive", "workout"]},
      "limit": {"type": "integer"}
    },
    "required": ["start_date", "end_date"]
  },
  "query_body_measurements": {
    "type": "object",
    "properties": {
      "start_date": {"type": "string"},
      "end_date": {"type": "string"},
      "metrics": {
        "type": "array",
        "items": {"type": "string", "enum": ["weight_kg", "bmi", "body_fat_pct", "muscle_mass_kg", "water_pct"]}
      },
      "latest_only": {"type": "boolean"}
    },
    "required": ["start_date", "end_date"]
  },
  "query_sleep": {
    "type": "object",
    "properties": {
      "start_date": {"type": "string"},
      "end_date": {"type": "string"},
      "include_naps": {"type": "boolean"}
    },
    "required": ["start_date", "end_date"]
  },
  "query_workouts": {
    "type": "object",
    "properties": {
      "start_date": {"type": "string"},
      "end_date": {"type": "string"},
      "activity_types": {"type": "array", "items": {"type": "string"}},
      "min_duration": {"type": "integer"},
      "min_distance_km": {"type": "number"}
    },
    "required": ["start_date", "end_date"]
  },
  "query_spo2": {
    "type": "object",
    "properties": {
      "start_date": {"type": "string"},
      "end_date": {"type": "string"},
      "limit": {"type": "integer"}
    },
    "required": ["start_date", "end_date"]
  },
  "query_stress": {
    "type": "object",
    "properties": {
      "start_date": {"type": "string"},
      "end_date": {"type": "string"},
      "level": {"type": "string", "enum": ["low", "medium", "high"]},
      "limit": {"type": "integer"}
    },
    "required": ["start_date", "end_date"]
  },
  "query_abnormal_heart_beat": {
    "type": "object",
    "properties": {
      "start_date": {"type": "string"},
      "end_date": {"type": "string"},
      "limit": {"type": "integer"}
    },
    "required": ["start_date", "end_date"]
  },
  "get_data_coverage": {
    "type": "object",
    "properties": {"data_types": {"type": "array", "items": {"type": "string"}}}
  }
}
```

## 附录 B：响应信封示例

查询类（`QueryResponse`）：

```json
{"status": "ok", "source": "cache", "generated_at": "2026-08-31T10:00:00", "timezone": "UTC", "data": {"samples": [{"timestamp": "2026-08-08T04:09:00+08:00", "bpm": 93, "sample_type": "passive"}], "count": 1}, "error": null}
```

同步类（`_run_sync_data`）：

```json
{
  "status": "ok | partial | error",
  "sync_id": "uuid",
  "started_at": "...", "finished_at": "...",
  "records_added": 0, "records_updated": 0, "records_skipped": 0,
  "data_types_synced": ["daily_activity"],
  "results": [{"data_type": "daily_activity", "status": "ok", "added": 1, "updated": 0, "skipped": 0, "start_date": "...", "end_date": "...", "chunks": [{"start_date": "...", "end_date": "...", "status": "ok", "added": 1, "updated": 0, "skipped": 0}], "duration_seconds": 1.2}]
}
```

## 附录 C：config.json 实例（本机实际生成）

```json
{
  "mode": "mi_fitness_cloud",
  "region": "cn",
  "timezone": "UTC",
  "database_path": "C:\\Users\\<user>\\AppData\\Local\\mi-fitness-mcp\\mi-fitness-mcp\\mi_fitness.db",
  "logs_path": "C:\\Users\\<user>\\AppData\\Local\\mi-fitness-mcp\\mi-fitness-mcp\\logs\\mi_fitness.log",
  "auto_sync_on_start": true,
  "stale_after_minutes": 60,
  "store_raw_payloads": true,
  "default_lookback_days": 30,
  "sync_chunk_days": 7,
  "http_timeout_seconds": 20.0,
  "request_retries": 3,
  "health_check_timeout_seconds": 10.0,
  "sync_type_timeout_seconds": 180.0,
  "max_pages": 200
}
```
