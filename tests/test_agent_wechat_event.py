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
        def __init__(self, id=None, **_):
            self.id = id

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

    api_mod.logger = types.SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        exception=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )
    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.MessageChain = MessageChain
    components_mod.At = At
    components_mod.AtAll = AtAll
    components_mod.File = File
    components_mod.Image = Image
    components_mod.Node = Node
    components_mod.Nodes = Nodes
    components_mod.Plain = Plain
    components_mod.Record = Record
    components_mod.Reply = Reply
    components_mod.Share = Share
    components_mod.Video = Video
    platform_mod.Group = Group
    platform_mod.MessageMember = MessageMember

    astrbot.api = api_mod
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api_mod
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.message_components"] = components_mod
    sys.modules["astrbot.api.platform"] = platform_mod


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
