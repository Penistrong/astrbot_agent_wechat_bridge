# AstrBot Agent WeChat Bridge (v1.0.0 - 适配 agent-wechat 1.2.0+)

这是一个用于将 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 接入个人微信的平台适配器插件，全面重构以对接 [`agent-wechat`](https://github.com/thisnick/agent-wechat) **1.0.0 ~ 1.2.0+ Native `/v1` HTTP & SSE API**（完全移除 0.16.x 时代的 noVNC + AT-SPI `/api/*` 旧接口依赖）。

支持 **AstrBot 与 `agent-wechat` 跨服务器分离部署**（例如 AstrBot 运行在 CentOS Stream 9 `glibc=2.32`，`agent-wechat` 运行在 Ubuntu 24.04 LTS `glibc=2.39`）。

---

## 核心特性

- **原生 `/v1` 协议全面对接**：
  - `GET /v1/accounts/{account}/events/stream`：实时 SSE 事件流监听（`message.committed` / `message.recalled` / `account.updated`）
  - `GET /v1/accounts/{account}/events` + `POST /consumers/{consumer}/ack`：断线增量游标回放与持久化位点确认
  - `GET /v1/accounts/{account}/messages/{id}`：拉取结构化消息（支持文本、图片、语音、视频、文件、表情、引用消息、链接卡片、位置）
  - `GET /v1/accounts/{account}/messages/{id}/media` + `POST /media-transfers` + `GET /media/{id}/content`：按需触发 CDN 媒体下载、轮询 Job 状态，自动支持微信 `image/x-wxgf` 静态预览图降级转换
  - `POST /v1/accounts/{account}/uploads` + `POST /v1/accounts/{account}/sends`：带 `Idempotency-Key` 幂等保障的消息与多媒体发送（支持群聊原生 `@提及`、引用回复 `quote`、卡片分享 `share`）
  - `POST /v1/accounts/{account}/threads/{id}/read-state`：可配置的已读回执策略（`on_inbound` / `never`）
- **远程扫码与状态管理指令**：
  - `/wxstatus`：通过 HTTP API 查询远端 `agent-wechat` 守护进程版本、账号 ID、认证与在线状态
  - `/wxauth`：无需在 AstrBot 所在机器安装 `wx` CLI，直接调用远端 `/v1/accounts/{account}/auth/login` 与 `/operations/{id}/qr` 获取 PNG 登录二维码并推送至当前会话及提醒列表

---

## 跨服务器部署指南（CentOS Stream 9 + Ubuntu 24.04 LTS）

由于 `agent-wechat` 1.x 原生守护进程要求 `glibc >= 2.36`，当 AstrBot 部署在 CentOS Stream 9 时，推荐按如下方式配置两台服务器互联：

### 1. 在 Ubuntu 24.04 LTS 服务器部署 `agent-wechat`

```bash
# 1. 安装 CLI 与守护进程（建议同时安装 ffmpeg 以支持语音/视频处理）
sudo apt update && sudo apt install -y ffmpeg
npm install -g @agent-wechat/cli

# 2. 启动后台守护进程并创建账号（或挂载已有 Linux 微信目录）
wx daemon start
wx accounts create --alias bot
wx auth login
```

获取账号 ID 与访问令牌（Bearer Token）：
```bash
# 查看 accountId（例如 "bot" 或自动生成的 ID）
wx accounts list

# 直接读取 owner token，或创建专用于 AstrBot 的授权 token：
cat ~/.local/share/agent-wechat/api-token
# 或者创建细粒度 token：
wx tokens create astrbot --accounts <ACCOUNT_ID> \
  --scopes read --scopes media --scopes send --scopes login --scopes read-state
```

#### 开启跨服务器远程访问（二选一）

`agent-wechat` 默认仅监听回环地址 `127.0.0.1:6174`。跨服务器访问可选择：

- **方式 A（原生 HTTPS 监听 `networkListen`）**：
  编辑 Ubuntu 上的 `~/.config/agent-wechat/server/config.toml`：
  ```toml
  schemaVersion = 1
  listen = "127.0.0.1:6174"
  networkListen = "0.0.0.0:7443"
  tlsCertFile = "/path/to/cert.pem"
  tlsKeyFile = "/path/to/key.pem"
  ```
  重启守护进程 `wx daemon stop && wx daemon start`。若使用自签名证书，在 AstrBot 平台配置中将 `verify_tls` 设为 `false`，`server_url` 填写 `https://<Ubuntu_IP>:7443`。

- **方式 B（Nginx 反代 / SSH 隧道 / 内网转发）**：
  在 Ubuntu 上用 Nginx / Caddy 反代 `127.0.0.1:6174`（注意 SSE 路径 `/v1/` 需设置 `proxy_buffering off;`），或在 CentOS 上建立 SSH 隧道：
  ```bash
  ssh -N -L 6174:127.0.0.1:6174 user@<Ubuntu_IP>
  ```

---

### 2. 在 CentOS Stream 9 服务器配置 AstrBot 插件

在 AstrBot 管理面板 -> **平台管理** -> **创建机器人** -> 选择 **`agent_wechat` (Agent WeChat)**：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `server_url` | `http://127.0.0.1:6174` | Ubuntu 上的 `agent-wechat` 地址（如 `https://192.168.1.20:7443` 或 `http://192.168.1.20:6174`） |
| `token` | 空 | 必填，Ubuntu 上生成的 Bearer Token |
| `account_id` | 空 | 可选，`wx accounts list` 中的账号 ID 或别名；留空将自动选择第一个已认证账号 |
| `verify_tls` | `false` | 使用自签名 HTTPS 证书连接 `7443` 端口时保持 `false` |
| `read_policy` | `on_inbound` | `on_inbound`（处理后自动标记会话已读）或 `never`（保持手机未读红点） |
| `require_group_mention` | `false` | 群聊是否仅在 `@机器人` 时唤醒大模型 |
| `dm_policy` | `open` | 私聊策略：`open` / `allowlist` / `disabled` |
| `dm_allowlist` | `[]` | 私聊白名单 `wxid` 列表 |
| `group_policy` | `open` | 群聊策略：`open` / `allowlist` / `disabled` |
| `group_allowlist` | `[]` | 群聊白名单（支持填群 ID `xxx@chatroom` 或发言人 `wxid`） |

---

## 本地校验

```bash
python3 -m compileall main.py src tests
pytest
```
