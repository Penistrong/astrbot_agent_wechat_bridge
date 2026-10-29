from src.agent_wechat_client import WeChatClient, encode_resource_id


def test_encode_resource_id_escapes_slashes() -> None:
    raw_id = "message/message_0.db/Msg_123/45"
    assert encode_resource_id(raw_id) == "message%2Fmessage_0.db%2FMsg_123%2F45"


def test_build_events_stream_url_and_strip_v1_suffix() -> None:
    client = WeChatClient(
        "https://192.168.1.20:7443/v1/",
        token="secret",
        account_id="acct_01",
    )
    assert client.base_url == "https://192.168.1.20:7443"
    assert (
        client.build_events_stream_url(after="42")
        == "https://192.168.1.20:7443/v1/accounts/acct_01/events/stream?after=42"
    )


def test_resolve_account_id_picks_online_authenticated_account(monkeypatch) -> None:
    client = WeChatClient("http://127.0.0.1:6174", token="secret")
    monkeypatch.setattr(
        client,
        "list_accounts",
        lambda: [
            {"id": "offline_acct", "authenticated": False, "connection": "disconnected"},
            {"id": "online_acct", "authenticated": True, "connection": "online"},
        ],
    )
    assert client.resolve_account_id() == "online_acct"
    assert client.account_id == "online_acct"


def test_send_message_text_uses_v1_sends_with_idempotency_key(monkeypatch) -> None:
    client = WeChatClient(
        "http://127.0.0.1:6174",
        token="secret",
        account_id="acct_main",
    )
    captured: dict[str, object] = {}

    def fake_post(path, body=None, *, headers=None, timeout=None):
        captured["path"] = path
        captured["body"] = body
        captured["headers"] = headers
        captured["timeout"] = timeout
        return {
            "id": "send_1",
            "accountId": "acct_main",
            "destination": "wxid_x",
            "state": "accepted",
        }

    monkeypatch.setattr(client, "_post", fake_post)
    payload = {
        "chatId": "wxid_x",
        "text": "hello v1",
        "idempotencyKey": "fixed-key-001",
    }
    result = client.send_message(payload, timeout=30.0)

    assert result["success"] is True
    assert captured["path"] == "/v1/accounts/acct_main/sends"
    assert captured["body"] == {"threadId": "wxid_x", "text": "hello v1"}
    assert captured["headers"] == {"Idempotency-Key": "fixed-key-001"}
    assert captured["timeout"] == 30.0


def test_send_message_image_uploads_then_submits_media_job(monkeypatch) -> None:
    client = WeChatClient(
        "http://127.0.0.1:6174",
        token="secret",
        account_id="acct_main",
    )
    uploaded: dict[str, object] = {}
    sent: dict[str, object] = {}

    def fake_upload(data, filename, mime_type="application/octet-stream", account_id=None, *, timeout=None):
        uploaded["data"] = data
        uploaded["filename"] = filename
        uploaded["mime_type"] = mime_type
        return {"id": "src_123"}

    def fake_post(path, body=None, *, headers=None, timeout=None):
        sent["path"] = path
        sent["body"] = body
        sent["headers"] = headers
        return {"id": "job_1", "state": "accepted", "kind": "send"}

    monkeypatch.setattr(client, "upload_media", fake_upload)
    monkeypatch.setattr(client, "_post", fake_post)

    result = client.send_message(
        {
            "chatId": "filehelper",
            "image": {"data": "aW1n", "mimeType": "image/png"},
            "idempotencyKey": "img-key-1",
        }
    )

    assert result["success"] is True
    assert uploaded["data"] == b"img"
    assert uploaded["mime_type"] == "image/png"
    assert sent["path"] == "/v1/accounts/acct_main/sends"
    assert sent["body"] == {
        "threadId": "filehelper",
        "kind": "image",
        "sourceId": "src_123",
        "standardSourceId": None,
        "thumbnailSourceId": None,
        "playSeconds": None,
        "durationMs": None,
    }


def test_download_message_media_handles_transfer_and_wxgf_preview(monkeypatch) -> None:
    client = WeChatClient(
        "http://127.0.0.1:6174",
        token="secret",
        account_id="acct_main",
    )
    calls = {"list": 0, "transfer": 0, "content": []}

    def fake_list_media(message_id, account_id=None):
        calls["list"] += 1
        if calls["list"] == 1:
            return [{"id": "asset_1", "state": "missing", "variant": "original"}]
        return [
            {
                "id": "asset_1",
                "state": "available",
                "variant": "original",
                "actualVariant": "original",
                "filename": "photo.wxgf",
                "preview": {"filename": "photo.still.jpg", "contentType": "image/jpeg"},
            }
        ]

    def fake_transfer(message_id, quality="best", asset_id=None, account_id=None):
        calls["transfer"] += 1
        return {"id": "job_dl_1", "state": "completed"}

    def fake_get_content(asset_id, *, representation="original", account_id=None, timeout=60.0):
        calls["content"].append(representation)
        if representation == "original":
            return b"wxgf_raw", "image/x-wxgf", {}
        return b"jpeg_preview_bytes", "image/jpeg", {}

    monkeypatch.setattr(client, "list_message_media", fake_list_media)
    monkeypatch.setattr(client, "start_media_transfer", fake_transfer)
    monkeypatch.setattr(client, "get_media_content", fake_get_content)

    downloaded = client.download_message_media("msg_1", msg_type=3)
    assert downloaded == (b"jpeg_preview_bytes", "image/jpeg", "photo.still.jpg")
    assert calls["transfer"] == 1
    assert calls["content"] == ["original", "preview"]

