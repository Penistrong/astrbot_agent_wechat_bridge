# 更新日志

本项目遵循“每次版本更新都记录变更”的约定。

## 1.0.1 - 2026-10-30

- **修复 SSE 事件流与 REST 轮询竞态导致的消息重复消费问题**：
  - 在 `get_message` 异步网络请求前立即登记 `in_flight_message_ids` 与进程级全局去重集合，防止跨机 RTT 窗口期内被并发重入
  - 引入 `event_lock` 串行化 SSE 推送与 REST 增量轮询事件分发，并在 SSE 在线期间抑制无意义的 `sync_event` 轮询唤醒
  - 在 `AgentWeChatBridgePlugin.initialize` / `terminate` 中接入 `PlatformManager.reload`，解决 AstrBot 热更新/重载插件时未自动重启后台旧平台适配器实例的问题

## 1.0.0 - 2026-10-29

- **全面升级适配 `agent-wechat` 1.2.0+ Native `/v1` API**：
  - 移除已废弃的 0.16.x 容器 `/api/*` 与 `/api/ws/events` 接口
  - 新增 `/v1/accounts` 自动发现与 `account_id` 绑定机制
  - 事件通道迁移至 `GET /v1/accounts/{account}/events/stream` (SSE) + `GET /v1/accounts/{account}/events` 增量游标轮询 + `POST /consumers/{consumer}/ack` 位点持久化
  - 入站消息改为基于 `message.committed` / `message.recalled` 事件驱动，支持原生 `@提及` (`NativeMentions`)、内嵌引用 (`quote`)、链接卡片 (`link`)、位置 (`location`) 与撤回抑制
  - 媒体下载迁移至 `/v1/accounts/{account}/messages/{id}/media` + `POST /media-transfers` 异步任务轮询 + `/media/{assetId}/content`，支持微信 `image/x-wxgf` 预览图降级获取
  - 出站消息迁移至 `POST /v1/accounts/{account}/uploads` (multipart) + `POST /v1/accounts/{account}/sends`（强制携带 `Idempotency-Key` 并在超时后通过 `by-idempotency-key` 恢复查询，避免重复发送）
  - 支持跨服务器远程部署：`/wxauth` 改为通过远端 `/v1/accounts/{account}/auth/login` 与 `/operations/{id}/qr` 直接拉取 PNG 登录二维码，不再依赖本机安装 `wx` CLI（兼容 AstrBot 所在服务器 `glibc < 2.36`、远端独立部署 `agent-wechat` 的跨机场景）
  - 新增 `/wxstatus` 指令用于查看远端 `agent-wechat` 守护进程版本与账号在线状态
  - 平台配置新增 `account_id`、`verify_tls`、`read_policy`、`require_group_mention` 及私聊/群聊访问控制策略

## 0.3.18 - 2026-03-27

- 修复“掉线重连后回灌离线消息”的问题
- 在鉴权从非 `logged_in` 恢复到 `logged_in` 时，先执行会话基线同步（将 `last_seen_id` 对齐到当前最新）
- 基线同步期间临时抑制入站事件处理，避免并发窗口内把离线期间历史消息再次转发到 AstrBot
