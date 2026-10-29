# 更新日志

本项目遵循“每次版本更新都记录变更”的约定。

## 1.0.0 - 2026-10-29

- **全面升级适配 `agent-wechat` 1.2.0+ Native `/v1` API**：
  - 移除已废弃的 0.16.x 容器 `/api/*` 与 `/api/ws/events` 接口
  - 新增 `/v1/accounts` 自动发现与 `account_id` 绑定机制
  - 事件通道迁移至 `GET /v1/accounts/{account}/events/stream` (SSE) + `GET /v1/accounts/{account}/events` 增量游标轮询 + `POST /consumers/{consumer}/ack` 位点持久化
  - 入站消息改为基于 `message.committed` / `message.recalled` 事件驱动，支持原生 `@提及` (`NativeMentions`)、内嵌引用 (`quote`)、链接卡片 (`link`)、位置 (`location`) 与撤回抑制
  - 媒体下载迁移至 `/v1/accounts/{account}/messages/{id}/media` + `POST /media-transfers` 异步任务轮询 + `/media/{assetId}/content`，支持微信 `image/x-wxgf` 预览图降级获取
  - 出站消息迁移至 `POST /v1/accounts/{account}/uploads` (multipart) + `POST /v1/accounts/{account}/sends`（强制携带 `Idempotency-Key` 并在超时后通过 `by-idempotency-key` 恢复查询，避免重复发送）
  - 支持跨服务器远程部署：`/wxauth` 改为通过远端 `/v1/accounts/{account}/auth/login` 与 `/operations/{id}/qr` 直接拉取 PNG 登录二维码，不再依赖本机安装 `wx` CLI（完美兼容 CentOS Stream 9 `glibc=2.32` 远程连接 Ubuntu 24.04 `agent-wechat`）
  - 新增 `/wxstatus` 指令用于查看远端 `agent-wechat` 守护进程版本与账号在线状态
  - 平台配置新增 `account_id`、`verify_tls`、`read_policy`、`require_group_mention` 及私聊/群聊访问控制策略

## 0.3.18 - 2026-03-27

- 修复“掉线重连后回灌离线消息”的问题
- 在鉴权从非 `logged_in` 恢复到 `logged_in` 时，先执行会话基线同步（将 `last_seen_id` 对齐到当前最新）
- 基线同步期间临时抑制入站事件处理，避免并发窗口内把离线期间历史消息再次转发到 AstrBot
