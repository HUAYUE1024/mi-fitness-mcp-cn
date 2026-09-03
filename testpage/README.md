# Mi Fitness Web UI & API 调试控制台（Python Flask）

现代化的小米运动健康数据可视化面板与反向代理控制台（基于 Python Flask），提供开箱即用的 Web 界面，方便直观查看健康数据、触发同步、管理 API Key 及扫码登录授权。

- 📊 **现代化仪表盘**：涵盖每日活动、心率采样、睡眠分期、运动训练、体重体成分、血氧、压力与异常心跳。
- 📈 **多维指标序列分析**：支持步数、距离、卡路里按天/周/月进行 sum/avg/max/min 聚合与图表走势。
- 📱 **扫码登录**：内嵌小米官方 App / 米家扫码登录流程，自动轮询并一键换取 API Key。
- 🔑 **API Key 体系**：多账号凭据发放、列表查询与前缀吊销。
- 💻 **开发者控制台**：实时响应检视、JSON 语法高亮/过滤、耗时统计与一键生成 cURL 命令。
- ⚡ **无缝代理**：自动反向代理 `/proxy/*` 到后端 FastAPI（默认 `http://127.0.0.1:8321`），并自动中继服务端安全 Key（`X-API-Key` / `X-Admin-Key`）。

## 快速启动

### 方式 1：通过主项目 CLI

```bash
mi-fitness-mcp web [--host 127.0.0.1] [--port 8322] [--backend-url http://127.0.0.1:8321]
```

### 方式 2：直接运行脚本

```bash
python testpage/app.py
# 浏览器打开 http://127.0.0.1:8322/
```

## 环境变量

| 变量名 | 默认值 | 说明 |
|---|---|---|
| `BIND` | `127.0.0.1:8322` | Web 界面监听地址与端口 |
| `MI_FITNESS_API_URL` | `http://127.0.0.1:8321` | FastAPI 后端服务地址 |
| `MI_FITNESS_API_KEY` | 无 | 设置后代理自动附加 `X-API-Key`（密钥不进浏览器） |
| `MI_FITNESS_ADMIN_KEY` | 无 | 设置后代理自动附加 `X-Admin-Key`（管理端点鉴权） |
