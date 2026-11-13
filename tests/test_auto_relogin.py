import asyncio

from src.agent_wechat_platform_adapter import AgentWeChatPlatformAdapter


class _FakeClient:
    def __init__(self, states):
        self.states = list(states)
        self.login_calls = 0

    def start_login(self, **kwargs):
        self.login_calls += 1
        return {"id": "op-1", "state": self.states[0]}

    def get_operation(self, op_id):
        return {"id": op_id, "state": self.states.pop(0)}

    def cancel_operation(self, op_id):
        return {"id": op_id, "state": "cancelled"}


def _adapter(client):
    adapter = object.__new__(AgentWeChatPlatformAdapter)
    adapter.client = client
    adapter._auto_relogin_inflight = False
    adapter._last_auto_relogin_at = 0.0
    adapter.last_login_page_warn_at = 0.0
    return adapter


def test_saved_device_login_recovers_without_qr():
    client = _FakeClient(["starting", "succeeded"])
    assert asyncio.run(_adapter(client)._attempt_auto_relogin()) is True
    assert client.login_calls == 1


def test_failed_auto_login_allows_manual_qr_warning():
    client = _FakeClient(["starting", "failed"])
    assert asyncio.run(_adapter(client)._attempt_auto_relogin()) is False


def test_auto_login_is_rate_limited():
    client = _FakeClient(["succeeded"])
    adapter = _adapter(client)
    assert asyncio.run(adapter._attempt_auto_relogin()) is True
    assert asyncio.run(adapter._attempt_auto_relogin()) is False
    assert client.login_calls == 1
