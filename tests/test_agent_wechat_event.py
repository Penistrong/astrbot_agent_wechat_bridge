import asyncio
import importlib
import sys
import types


def _install_astrbot_stubs() -> None:
    if "astrbot" in sys.modules:
        return

    astrbot = types.ModuleType("astrbot")
    api_mod = types.ModuleType("astrbot.api")
    event_mod = types.ModuleType("astrbot.api.event")
    components_mod = types.ModuleType("astrbot.api.message_components")
    platform_mod = types.ModuleType("astrbot.api.platform")

    class AstrMessageEvent:
        def __init__(
            self,
            message_str=None,
            message_obj=None,
            platform_meta=None,
            session_id=None,
        ):
            self.message_obj = message_obj

        async def send(self, message):
            return None

    class MessageChain:
        def __init__(self, chain=None):
            self.chain = list(chain or [])

        def message(self, text: str):
            self.chain.append(Plain(text=text))
            return self

        def file_image(self, path: str):
            self.chain.append(Image(file=path))
            return self

    class Plain:
        def __init__(self, text: str = "", **_):
            self.text = text

    class At:
        def __init__(self, qq=None, name=None, **_):
            self.qq = qq
            self.name = name

    class AtAll(At):
        def __init__(self, **_):
            super().__init__(qq="all", name="all", **_)

    class Image:
        def __init__(self, file=None, url=None, **_):
            self.file = file
            self.url = url

    class File:
        def __init__(self, name=None, file=None, url=None, **_):
            self.name = name
            self.file = file
            self.url = url

    class Record:
        def __init__(self, file=None, url=None, name=None, **_):
            self.file = file
            self.url = url
            self.name = name

    class Video:
        def __init__(self, file=None, url=None, name=None, **_):
            self.file = file
            self.url = url
            self.name = name

    class Reply:
        def __init__(
            self,
            id=None,
            chain=None,
            sender_id="",
            sender_nickname="",
            message_str="",
            text="",
            **_,
        ):
            self.id = id
            self.chain = list(chain or [])
            self.sender_id = sender_id
            self.sender_nickname = sender_nickname
            self.message_str = message_str
            self.text = text

    class Face:
        def __init__(self, id=0, **_):
            self.id = id

    class Location:
        def __init__(self, lat=0.0, lon=0.0, title="", content="", **_):
            self.lat = lat
            self.lon = lon
            self.title = title
            self.content = content

    class Share:
        def __init__(self, url="", title="", content="", image="", **_):
            self.url = url
            self.title = title
            self.content = content
            self.image = image

    class Node:
        def __init__(self, content=None, **_):
            self.content = list(content or [])

    class Nodes:
        def __init__(self, nodes=None, **_):
            self.nodes = list(nodes or [])

    class Group:
        def __init__(self, group_id):
            self.group_id = group_id
            self.group_name = None
            self.group_owner = None
            self.group_admins = None
            self.members = None
            self.member_count = None

    class MessageMember:
        def __init__(self, user_id="", nickname=None):
            self.user_id = user_id
            self.nickname = nickname

    class AstrBotMessage:
        def __init__(self):
            self.self_id = ""
            self.sender = None
            self.message = []
            self.message_str = ""
            self.raw_message = None
            self.timestamp = 0
            self.message_id = ""
            self.type = None
            self.group_id = ""
            self.group = None
            self.session_id = ""

    class MessageType:
        GROUP_MESSAGE = "GroupMessage"
        FRIEND_MESSAGE = "FriendMessage"

    class PlatformMetadata:
        def __init__(self, name="", description="", id="", support_streaming_message=False, **_):
            self.name = name
            self.description = description
            self.id = id
            self.support_streaming_message = support_streaming_message

    class Platform:
        def __init__(self, config_or_queue=None, event_queue=None):
            if event_queue is None:
                self.event_queue = config_or_queue
            else:
                self.config = config_or_queue
                self.event_queue = event_queue

        async def send_by_session(self, session, message_chain):
            return None

    def register_platform_adapter(*args, **kwargs):
        def _decorator(cls):
            return cls
        return _decorator

    class ProviderRequest:
        def __init__(self):
            self.extra_user_content_parts = []

    class Star:
        def __init__(self, context=None):
            self.context = context

    def register(*args, **kwargs):
        def _decorator(cls):
            return cls
        return _decorator

    class _Filter:
        @staticmethod
        def command(*args, **kwargs):
            return lambda fn: fn

        @staticmethod
        def on_llm_request(*args, **kwargs):
            return lambda fn: fn

    class TextPart:
        def __init__(self, text: str = ""):
            self.text = text

    core_mod = types.ModuleType("astrbot.core")
    core_platform_mod = types.ModuleType("astrbot.core.platform")
    core_ame_mod = types.ModuleType("astrbot.core.platform.astr_message_event")
    core_ame_mod.MessageSesion = object
    core_agent_mod = types.ModuleType("astrbot.core.agent")
    core_agent_msg_mod = types.ModuleType("astrbot.core.agent.message")
    core_agent_msg_mod.TextPart = TextPart
    provider_mod = types.ModuleType("astrbot.api.provider")
    provider_mod.ProviderRequest = ProviderRequest
    star_mod = types.ModuleType("astrbot.api.star")
    star_mod.Context = object
    star_mod.Star = Star
    star_mod.register = register

    api_mod.AstrBotConfig = dict
    api_mod.logger = types.SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        exception=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )
    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.MessageChain = MessageChain
    event_mod.filter = _Filter
    components_mod.At = At
    components_mod.AtAll = AtAll
    components_mod.Face = Face
    components_mod.File = File
    components_mod.Image = Image
    components_mod.Location = Location
    components_mod.Node = Node
    components_mod.Nodes = Nodes
    components_mod.Plain = Plain
    components_mod.Record = Record
    components_mod.Reply = Reply
    components_mod.Share = Share
    components_mod.Video = Video
    platform_mod.AstrBotMessage = AstrBotMessage
    platform_mod.Group = Group
    platform_mod.MessageMember = MessageMember
    platform_mod.MessageType = MessageType
    platform_mod.Platform = Platform
    platform_mod.PlatformMetadata = PlatformMetadata
    platform_mod.register_platform_adapter = register_platform_adapter

    astrbot.api = api_mod
    astrbot.core = core_mod
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api_mod
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.message_components"] = components_mod
    sys.modules["astrbot.api.platform"] = platform_mod
    sys.modules["astrbot.api.provider"] = provider_mod
    sys.modules["astrbot.api.star"] = star_mod
    sys.modules["astrbot.core"] = core_mod
    sys.modules["astrbot.core.platform"] = core_platform_mod
    sys.modules["astrbot.core.platform.astr_message_event"] = core_ame_mod
    sys.modules["astrbot.core.agent"] = core_agent_mod
    sys.modules["astrbot.core.agent.message"] = core_agent_msg_mod


def _load_event_module():
    _install_astrbot_stubs()
    if "src.agent_wechat_event" in sys.modules:
        return importlib.reload(sys.modules["src.agent_wechat_event"])
    return importlib.import_module("src.agent_wechat_event")


def test_build_send_payloads_expands_nodes_in_original_order():
    module = _load_event_module()
    chain = module.MessageChain(
        [
            module.Nodes(
                nodes=[
                    module.Node(content=[module.Plain(text="one")]),
                    module.Node(content=[module.Plain(text="two")]),
                ]
            )
        ]
    )

    payloads = asyncio.run(
        module.AgentWeChatMessageEvent._build_send_payloads("chat_x", chain)
    )

    assert payloads == [
        {"chatId": "chat_x", "text": "one"},
        {"chatId": "chat_x", "text": "two"},
    ]


def test_build_send_payloads_supports_quote_reply_and_group_mentions():
    module = _load_event_module()
    quote_chain = module.MessageChain(
        [
            module.Reply(id="msg_opaque_1|18446744073709551615"),
            module.Plain(text="quoted reply"),
        ]
    )
    quote_payloads = asyncio.run(
        module.AgentWeChatMessageEvent._build_send_payloads("wxid_peer", quote_chain)
    )
    assert quote_payloads == [
        {
            "chatId": "wxid_peer",
            "text": "quoted reply",
            "quote": {
                "messageId": "msg_opaque_1",
                "serverId": "18446744073709551615",
            },
        }
    ]

    mention_chain = module.MessageChain(
        [
            module.At(qq="wxid_alice", name="Alice"),
            module.Plain(text="please check"),
        ]
    )
    mention_payloads = asyncio.run(
        module.AgentWeChatMessageEvent._build_send_payloads(
            "123456@chatroom", mention_chain
        )
    )
    assert mention_payloads == [
        {
            "chatId": "123456@chatroom",
            "text": "please check",
            "mentions": {"userIds": ["wxid_alice"]},
        }
    ]


def test_build_send_payloads_expands_nodes_and_keeps_video_file_payload(monkeypatch):
    module = _load_event_module()
    monkeypatch.setattr(
        module,
        "_load_binary_from_path",
        lambda path, timeout=30, fallback_mime="application/octet-stream": (
            b"node-vid",
            "video/mp4",
            "node-video.mp4",
        ),
    )
    chain = module.MessageChain(
        [
            module.Nodes(
                nodes=[
                    module.Node(
                        content=[
                            module.Plain(text="From @AI测试群:\n\u200bhello"),
                            module.Video(file="file:////tmp/node-video.mp4"),
                        ]
                    )
                ]
            )
        ]
    )

    payloads = asyncio.run(
        module.AgentWeChatMessageEvent._build_send_payloads("chat_node_video", chain)
    )

    assert payloads == [
        {
            "chatId": "chat_node_video",
            "text": "From @AI测试群:\n\u200bhello",
        },
        {
            "chatId": "chat_node_video",
            "file": {
                "data": "bm9kZS12aWQ=",
                "filename": "node-video.mp4",
            },
        },
    ]


def test_build_send_payloads_expands_serialized_nodes_messages():
    module = _load_event_module()

    class SerializedNodes:
        async def to_dict(self):
            return {
                "messages": [
                    {
                        "type": "node",
                        "data": {
                            "content": [
                                {"type": "text", "data": {"text": "first"}},
                            ]
                        },
                    },
                    {
                        "type": "node",
                        "data": {
                            "content": [
                                {"type": "text", "data": {"text": "second"}},
                            ]
                        },
                    },
                ]
            }

    chain = module.MessageChain([SerializedNodes()])
    payloads = asyncio.run(
        module.AgentWeChatMessageEvent._build_send_payloads("chat_y", chain)
    )

    assert payloads == [
        {"chatId": "chat_y", "text": "first"},
        {"chatId": "chat_y", "text": "second"},
    ]


def test_build_send_payloads_ignores_serialized_reply_component():
    module = _load_event_module()

    class SerializedReply:
        async def to_dict(self):
            return {
                "type": "reply",
                "data": {
                    "id": "wechat:46094226262@chatroom:380",
                    "chain": [],
                    "sender_id": 0,
                    "sender_nickname": "",
                    "time": 0,
                    "message_str": "",
                    "text": "",
                    "qq": 0,
                    "seq": 0,
                },
            }

    chain = module.MessageChain([SerializedReply(), module.Plain(text="保留正文")])
    payloads = asyncio.run(
        module.AgentWeChatMessageEvent._build_send_payloads("chat_reply_skip", chain)
    )

    assert payloads == [{"chatId": "chat_reply_skip", "text": "保留正文"}]


def test_build_send_payloads_splits_text_before_image(monkeypatch):
    module = _load_event_module()
    monkeypatch.setattr(
        module,
        "_load_binary_from_path",
        lambda path, timeout=30, fallback_mime="application/octet-stream": (
            b"img",
            "image/jpeg",
            "a.jpg",
        ),
    )

    chain = module.MessageChain(
        [
            module.Plain(text="hello"),
            module.Image(file="/tmp/a.jpg"),
        ]
    )
    payloads = asyncio.run(
        module.AgentWeChatMessageEvent._build_send_payloads("chat_z", chain)
    )
    assert payloads == [
        {"chatId": "chat_z", "text": "hello"},
        {
            "chatId": "chat_z",
            "image": {
                "data": "aW1n",
                "mimeType": "image/jpeg",
            },
        },
    ]


def test_build_send_payloads_normalizes_webp_image_to_png(monkeypatch):
    module = _load_event_module()
    png_data = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xcf\xc0"
        b"\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    monkeypatch.setattr(
        module,
        "_load_binary_from_path",
        lambda path, timeout=30, fallback_mime="application/octet-stream": (
            png_data,
            "image/webp",
            "a.webp",
        ),
    )
    chain = module.MessageChain([module.Image(file="/tmp/a.webp")])
    payloads = asyncio.run(
        module.AgentWeChatMessageEvent._build_send_payloads("chat_webp", chain)
    )
    assert len(payloads) == 1
    assert payloads[0]["chatId"] == "chat_webp"
    assert payloads[0]["image"]["mimeType"] == "image/png"
    assert payloads[0]["image"]["data"].startswith("iVBORw0KGgo")


def test_adapter_resolves_quote_nickname_other_mentions_and_sticker_face(monkeypatch):
    _install_astrbot_stubs()
    adapter_mod = importlib.import_module("src.agent_wechat_platform_adapter")
    q = asyncio.Queue()
    adapter = adapter_mod.AgentWeChatPlatformAdapter(
        {"server_url": "http://127.0.0.1:6174", "require_group_mention": False},
        {},
        q,
    )
    adapter.self_id = "wxid_bot_self"
    adapter._add_self_alias("wxid_bot_self")

    monkeypatch.setattr(
        adapter.client,
        "list_group_members",
        lambda room_id, limit=200, cursor=None: {
            "items": [
                {"id": "wxid_bot_self", "roomDisplayName": "乌萨奇"},
                {"id": "wxid_awei", "roomDisplayName": "啊伟"},
                {"id": "wxid_c", "roomDisplayName": "C"},
                {"id": "wxid_setsuna", "roomDisplayName": "セツナ"},
            ],
            "nextCursor": None,
        },
    )
    monkeypatch.setattr(
        adapter.client,
        "get_thread",
        lambda tid, timeout=None: {"id": tid, "kind": "group", "displayName": "测试群"},
    )
    monkeypatch.setattr(
        adapter.client,
        "download_message_media",
        lambda *args, **kwargs: None,
    )

    # 1. 引用其他群友发言时，Reply.sender_nickname 应解析为群名片（而非裸 wxid）
    quoted_abm = asyncio.run(
        adapter._convert_native_message(
            {
                "id": "msg_q1",
                "serverId": "2001",
                "threadId": "43278484028@chatroom",
                "senderId": "wxid_setsuna",
                "type": 49,
                "appMessageType": 57,
                "body": "量化割死你",
                "mentions": {"state": "known", "self": False, "all": False, "userIds": []},
                "quote": {
                    "serverId": "1999",
                    "type": 1,
                    "senderId": "wxid_awei",
                    "embeddedText": "电信光猫里有一段代码",
                },
            }
        )
    )
    assert quoted_abm is not None
    assert quoted_abm.sender.nickname == "セツナ"
    reply_comp = next(c for c in quoted_abm.message if isinstance(c, adapter_mod.Reply))
    assert reply_comp.sender_nickname == "啊伟"
    assert quoted_abm.message_str == "量化割死你"

    # 2. 句首 @其他群友（未 @机器人）时，不应抹除 @C，且应附带 At(qq=wxid_c, name="C")
    mention_other_abm = asyncio.run(
        adapter._convert_native_message(
            {
                "id": "msg_m1",
                "serverId": "2002",
                "threadId": "43278484028@chatroom",
                "senderId": "wxid_setsuna",
                "type": 1,
                "body": "@C\u2005送我",
                "mentions": {
                    "state": "known",
                    "self": False,
                    "all": False,
                    "userIds": ["wxid_c"],
                },
            }
        )
    )
    assert mention_other_abm is not None
    assert mention_other_abm.message_str == "@C 送我"
    at_comps = [c for c in mention_other_abm.message if isinstance(c, adapter_mod.At)]
    assert len(at_comps) == 1
    assert at_comps[0].qq == "wxid_c"
    assert at_comps[0].name == "C"

    # 3. 纯 @机器人 空提及时，正文应清空为 ""，仅保留单个 At 组件
    empty_at_abm = asyncio.run(
        adapter._convert_native_message(
            {
                "id": "msg_m2",
                "serverId": "2003",
                "threadId": "43278484028@chatroom",
                "senderId": "wxid_awei",
                "type": 1,
                "body": "@乌萨奇\u2005",
                "mentions": {
                    "state": "known",
                    "self": True,
                    "all": False,
                    "userIds": ["wxid_bot_self"],
                },
            }
        )
    )
    assert empty_at_abm is not None
    assert empty_at_abm.message_str == ""
    assert len(empty_at_abm.message) == 1
    assert isinstance(empty_at_abm.message[0], adapter_mod.At)
    assert empty_at_abm.message[0].qq == "wxid_bot_self"
    assert empty_at_abm.message[0].name == "乌萨奇"

    # 4. 群聊未 @机器人 的加密商店表情下载失败时，降级为 Face 而非 Plain("[表情]")
    sticker_abm = asyncio.run(
        adapter._convert_native_message(
            {
                "id": "msg_s1",
                "serverId": "2004",
                "threadId": "43278484028@chatroom",
                "senderId": "wxid_c",
                "type": 47,
                "body": "",
                "mentions": {"state": "known", "self": False, "all": False, "userIds": []},
            }
        )
    )
    assert sticker_abm is not None
    assert sticker_abm.message_str == ""
    assert len(sticker_abm.message) == 1
    assert isinstance(sticker_abm.message[0], adapter_mod.Face)


def test_plugin_injects_group_sender_identity_only_when_identifier_disabled():
    _install_astrbot_stubs()
    root_main = importlib.import_module("main")

    class FakeContext:
        def __init__(self, identifier_enabled: bool):
            self._id_enabled = identifier_enabled

        def get_config(self, umo=None):
            return {"provider_settings": {"identifier": self._id_enabled}}

    class FakeEvent:
        unified_msg_origin = "agent_wechat:GroupMessage:43278484028@chatroom"

        def get_platform_name(self):
            return "agent_wechat"

        def get_message_type(self):
            return root_main.MessageType.GROUP_MESSAGE

        def get_sender_id(self):
            return "wxid_e73a6xlzbems22"

        def get_sender_name(self):
            return "啊伟"

    # 当 AstrBot identifier=False 时自动补齐当前发言人身份
    plugin = root_main.AgentWeChatBridgePlugin(FakeContext(False), {})
    req = root_main.ProviderRequest()
    asyncio.run(plugin.inject_group_sender_identity(FakeEvent(), req))
    assert len(req.extra_user_content_parts) == 1
    assert (
        req.extra_user_content_parts[0].text
        == "<system_reminder>User ID: wxid_e73a6xlzbems22, Nickname: 啊伟</system_reminder>"
    )

    # 再次调用不重复注入
    asyncio.run(plugin.inject_group_sender_identity(FakeEvent(), req))
    assert len(req.extra_user_content_parts) == 1

    # 当 AstrBot identifier=True 时跳过注入
    plugin_native_on = root_main.AgentWeChatBridgePlugin(FakeContext(True), {})
    req2 = root_main.ProviderRequest()
    asyncio.run(plugin_native_on.inject_group_sender_identity(FakeEvent(), req2))
    assert len(req2.extra_user_content_parts) == 0


def test_platform_config_exposes_logout_notify_umos_and_merges_with_plugin_config():
    _install_astrbot_stubs()
    adapter_mod = importlib.import_module("src.agent_wechat_platform_adapter")
    root_main = importlib.import_module("main")

    # 1. 确保「机器人 -> agent_wechat -> 接入设置」面板元数据与默认模板包含掉线提醒与群身份配置
    assert "logout_notify_umos" in adapter_mod.CONFIG_METADATA
    assert adapter_mod.CONFIG_METADATA["logout_notify_umos"].get("_special") == "umo"
    assert adapter_mod.DEFAULT_CONFIG.get("logout_notify_umos") == []
    assert adapter_mod.DEFAULT_CONFIG.get("logout_notify_max_count") == 3
    assert adapter_mod.DEFAULT_CONFIG.get("logout_notify_interval_seconds") == 900
    assert adapter_mod.DEFAULT_CONFIG.get("ensure_group_sender_identity") is True

    # 2. 验证机器人平台配置中的 logout_notify_umos 能触发跨平台掉线通知，且 initialize 自动迁移旧插件配置
    sent_umos: list[str] = []

    class FakePlatformManager:
        def __init__(self):
            self.platforms_config = [
                {
                    "id": "agent_wechat",
                    "type": "agent_wechat",
                    "enable": False,
                }
            ]
            self.platform_insts = []

    class FakeContext:
        def __init__(self):
            self.platform_manager = FakePlatformManager()

        async def send_message(self, umo, chain):
            sent_umos.append(umo)
            return True

    ctx = FakeContext()
    plugin = root_main.AgentWeChatBridgePlugin(
        ctx,
        {"logout_notify_umos": ["SnowLuma:FriendMessage:111111"]},
    )
    asyncio.run(plugin.initialize())
    assert ctx.platform_manager.platforms_config[0]["logout_notify_umos"] == [
        "SnowLuma:FriendMessage:111111"
    ]

    adapter = adapter_mod.AgentWeChatPlatformAdapter(
        {
            "id": "agent_wechat",
            "server_url": "http://127.0.0.1:6174",
            "logout_notify_umos": [
                "SnowLuma:FriendMessage:222222",
                "SnowLuma:FriendMessage:111111",
            ],
        },
        {},
        asyncio.Queue(),
    )
    asyncio.run(plugin._notify_logout("warn", adapter))
    assert sent_umos == [
        "SnowLuma:FriendMessage:222222",
        "SnowLuma:FriendMessage:111111",
    ]


