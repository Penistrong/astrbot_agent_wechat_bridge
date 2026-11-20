# 更新日志

本项目遵循“每次版本更新都记录变更”的约定。

## 1.0.5 - 2026-11-20

- **修复群聊回复“张冠李戴”（发言人身份缺失、引用人裸 `wxid`、`@其他群友` 丢失）及表情引用报错问题**：
  - **群聊发言人身份自动兜底 (`ensure_group_sender_identity`)**：AstrBot 默认关闭「用户识别 (`provider_settings.identifier = false`)」时，多人群聊共享会话下当前触发消息不会携带发言人昵称，导致大模型将当前发言误判为群聊上下文中上一位发言的群友。现插件新增 `on_llm_request` 自动兜底注入 `<system_reminder>User ID: ..., Nickname: ...</system_reminder>`（若对应配置已开启 AstrBot 原生「用户识别」则自动跳过不重复注入）
  - **引用消息 (`Reply`) 发送者昵称解析**：修复群聊/私聊引用消息中 `Reply.sender_nickname` 直接使用裸 `wxid_...`（含引用机器人自身消息时显示裸 `wxid`）的问题，现统一解析为群名片/备注/昵称，并移除 `message_str` 中冗余的裸 `wxid` 引用前缀
  - **群聊 `@其他群友` 保留与 `At` 组件还原**：新增 `strip_leading_self_mentions`，仅剥离指向机器人自身的句首 `@提及`，完整保留对其他群成员的 `@提及` 并还原 `At(qq=..., name=...)` / `AtAll` 组件；修复纯 `@机器人` 空提及时残留 `@昵称` 文本的问题
  - **群成员缓存分页与单成员补查**：群成员列表支持自动分页预热，并在缓存未命中时通过 `/groups/{id}/members/{uid}` 按需补查新入群或大群成员昵称
  - **修复引用表情/不支持引用类型消息报 `HTTP 400 (unsupported_quote_type)`**：出站文本携带引用遇到 `unsupported_quote_type` / `invalid_quote` / `message_recalled` 时自动剥离引用降级为普通文本发送；同时将群聊未 `@机器人` 且无法拉取原图的加密商店表情降级为 `Face` 组件而非 `Plain("[表情]")`，避免随机主动回复对纯表情占位符反复回“嗯”并污染对话历史
- **优化跨平台掉线提醒 (`logout_notify_umos`) 校验与可视化选择体验**：
  - 将 `logout_notify_umos`、`logout_notify_max_count`、`logout_notify_interval_seconds` 及 `ensure_group_sender_identity` 直接挂载到 **「机器人 -> `agent_wechat` -> 接入设置」** 面板（同时向下兼容「插件 -> 配置」入口并自动迁移旧配置），无需再跨页面去插件列表里找配置
  - 为 `logout_notify_umos` 启用 AstrBot 原生 `"_special": "umo"` 可视化会话选择器，并明确提示需选择其他在线平台（如 QQ / Telegram / 飞书）的会话 UMO
  - 移除对裸 `wxid` 自动拼接 `agent_wechat:` 前缀的逻辑；当检测到 `logout_notify_umos` 指向当前已离线的 `agent_wechat` 平台或格式不完整时，直接拦截跳过并输出清晰的配置修正指引，避免向已掉线的微信平台发消息触发 `HTTP 409 (auth_required)`
  - 执行 `/wxauth` 广播登录二维码时自动排除当前发起指令的会话 UMO，避免同一聊天窗口重复收到两张二维码

## 1.0.4 - 2026-11-13

- **修复旧配置字符串 `"true"` 导致 WebUI 开关显示关闭但后端实际拦截未 `@` 群消息的问题**：
  - 修复 `require_group_mention` 在 `config.json` 中遗留为字符串 `"true"` 时，Vuetify `<v-switch>` 因严格全等判断渲染为关闭（灰色），而 Python `bool("true")` 计算为 `True` 导致普通群消息被静默丢弃的问题；启动及热重载时自动清洗持久化为真实布尔值
  - 优化 SSE 实时性与反代缓冲兜底：`response.iter_lines(chunk_size=1)` 禁用 `requests` 默认 512 字节行缓冲，并恢复定时增量游标对账轮询（配合 `event_lock` 与前置去重零重复），防止 SSE 连接半开或缓冲时消息延迟到达
  - 修复 `logout_notify_umos` 填写裸 `wxid` 时报错 `not enough values to unpack (expected 3, got 1)` 的问题，自动补全为 `agent_wechat:FriendMessage:wxid` 格式

## 1.0.3 - 2026-11-11

- **修复群聊图片多模态识别缺失、媒体延迟重试与表情降级问题**：
  - **支持引用图片/表情直接取图 (`49/57`)**：当群聊或私聊引用图片/表情提问时，自动下载被引用媒体或从本地近期缓存按 `serverId` 关联 `Image` 组件
  - **支持群聊「先发图、后 `@机器人`」跨消息关联**：微信图片消息本身无法附带 `@`，现自动缓存群成员近期发送的图片（即使开启 `require_group_mention=True` 也会预取缓存），当该成员在 90 秒内紧接着 `@机器人` 提问时自动挂载该图片进入多模态上下文
  - **媒体下载退避重试与多画质降级**：`download_message_media` 在 `best`/`original` 未就绪或失败时自动降级尝试 `standard`、`thumbnail` 及 `/messages/{id}/image` 缓存接口；`_download_v1_media` 增加退避重试与明确告警日志
  - **表情与失败媒体占位兜底**：针对微信商店/加密表情（`type=47`，CDN 域名不在 `agent-wechat` 白名单时返回 500）及下载失败媒体，自动降级为 `[表情]` / `[图片]` 文本占位符，避免整条消息因 `components` 为空被静默丢弃

## 1.0.2 - 2026-11-02

- **群聊唤醒、长语音切片与 WebUI 面板优化**：
  - 群聊仅在被 `@` 或长按引用机器人自身消息时才注入唤醒标记，避免 `require_group_mention=False` 时每条群消息都强制触发回复
  - 超长语音（>55s）支持通过 `ffmpeg silencedetect` 寻找换气静音中点自动切分发送，并自动清理临时切片文件
  - 新增插件与平台适配器图标 `logo.png`（修复 WebUI 机器人列表与详情页图标破损显示 `bot` 的问题）
  - 修正平台适配器 `CONFIG_METADATA` 与 `I18N_RESOURCES` 结构，使 WebUI 正确渲染中文配置项标题、说明、布尔开关、下拉选择与列表组件

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
