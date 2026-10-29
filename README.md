# AstrBot Agent WeChat Bridge (v1.0.0 - 适配 agent-wechat 1.2.0+)

这是一个用于将 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 接入个人微信的平台适配器插件，全面重构以对接 [`agent-wechat`](https://github.com/thisnick/agent-wechat) **1.0.0 ~ 1.2.0+ Native `/v1` HTTP & SSE API**（完全移除 0.16.x 时代的 noVNC + AT-SPI `/api/*` 旧接口依赖）。

支持 **AstrBot 与 `agent-wechat` 同机部署或跨服务器分离部署**（例如当部署 AstrBot 的**服务器 A** 系统 `glibc < 2.36` 如 `glibc=2.32`，而 `agent-wechat` 1.x 原生守护进程要求 `glibc >= 2.36` 时，可将 `agent-wechat` 独立部署在满足 `glibc >= 2.36` 的**服务器 B** 上，两者通过 HTTP/HTTPS 与 SSE 互联）。

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
  - `/wxauth`：无需在服务器 A 上安装 `wx` CLI，直接调用远端 `/v1/accounts/{account}/auth/login` 与 `/operations/{id}/qr` 获取 PNG 登录二维码并推送至当前会话及提醒列表

---

## 跨服务器部署指南（服务器 A + 服务器 B）

- **服务器 A**：部署 **AstrBot**（无高版本 `glibc` 限制，例如 `glibc=2.32` 亦可运行）
- **服务器 B**：部署 **`agent-wechat` 1.2.0+**（要求系统 `glibc >= 2.36`，并建议安装 `ffmpeg` 以支持语音和视频处理）

### 1. 在服务器 B 部署 `agent-wechat`

```bash
# 1. 安装 CLI 与原生守护进程（请确保已安装 ffmpeg / ffprobe）
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

---

### 2. 在服务器 B 开启跨服务器远程访问（任选其一）

`agent-wechat` 默认仅监听本地回环地址 `127.0.0.1:6174`。为了让服务器 A 访问服务器 B，可选择以下任一方案：

#### 方式一：使用 Nginx 反向代理（推荐）

在服务器 B 的 Nginx 站点配置中添加如下反向代理规则（兼顾大体积媒体上传与 SSE 实时事件流零缓冲推送）：

```nginx
    # 允许上传大图片、视频与文件（agent-wechat 最大支持 512MB）
    client_max_body_size 520m;

    # 1. agent-wechat SSE 实时事件流（匹配 /v1/accounts/*/events/stream）
    location ~ ^/v1/accounts/[^/]+/events/stream$ {
        proxy_pass http://127.0.0.1:6174;
        proxy_http_version 1.1;
        proxy_set_header Connection "";

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 禁用缓冲、缓存与压缩，确保 SSE 事件 0 延迟推送
        proxy_buffering off;
        proxy_cache off;
        gzip off;
        chunked_transfer_encoding on;

        # 长连接超时设置（agent-wechat 每 15s 会发一次 keepalive 注释帧）
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    # 2. agent-wechat 常规 REST API 与媒体上传/下载（/v1/* 及 /health）
    location / {
        proxy_pass http://127.0.0.1:6174;
        proxy_http_version 1.1;
        proxy_set_header Connection "";

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 大文件/视频上传与下载超时
        proxy_request_buffering off;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }
```

配置完成后，可在**服务器 A** 上执行以下 `curl` 命令验证连通性：

```bash
# 1. 验证健康检查（无需 Token，正常应返回 {"status":"ok"}）
curl -i http://<服务器B地址>/health

# 2. 验证 SSE 事件流（正常应立即输出 ": connected" 并保持长连接）
curl -N -H "Authorization: Bearer <TOKEN>" \
  "http://<服务器B地址>/v1/accounts/<ACCOUNT_ID>/events/stream?after=0"
```

#### 方式二：使用 `agent-wechat` 原生 HTTPS 监听 (`networkListen`)

编辑服务器 B 上的 `~/.config/agent-wechat/server/config.toml`：
```toml
schemaVersion = 1
listen = "127.0.0.1:6174"
networkListen = "0.0.0.0:7443"
tlsCertFile = "/path/to/cert.pem"
tlsKeyFile = "/path/to/key.pem"
```
重启守护进程：`wx daemon stop && wx daemon start`。若使用自签名证书，在 AstrBot 平台配置中将 `verify_tls` 设为 `false`，`server_url` 填写 `https://<服务器B地址>:7443`。

#### 方式三：SSH 端口转发隧道

在服务器 A 上建立指向服务器 B 的本地端口转发：
```bash
ssh -N -L 6174:127.0.0.1:6174 user@<服务器B地址>
```

---

### 3. 在服务器 A 配置 AstrBot 插件

在 AstrBot 管理面板 -> **平台管理** -> **创建机器人** -> 选择 **`agent_wechat` (Agent WeChat)**：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `server_url` | `http://127.0.0.1:6174` | 服务器 B 上的 `agent-wechat` 访问地址（如 Nginx 反代地址或 `https://<服务器B地址>:7443`） |
| `token` | 空 | 必填，服务器 B 上生成的 Bearer Token |
| `account_id` | 空 | 可选，`wx accounts list` 中的账号 ID 或别名；留空将自动选择第一个已认证账号 |
| `verify_tls` | `false` | 使用自签名 HTTPS 证书连接时保持 `false`；使用受信证书可设为 `true` |
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
