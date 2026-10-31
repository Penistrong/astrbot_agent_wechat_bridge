from src.agent_wechat_autojoin import AutoJoinGate, looks_like_question

UMO = "agent_wechat:GroupMessage:123@chatroom"
BASE = dict(
    base_possibility=0.3,
    min_hotness=3,
    cooldown_seconds=90,
    self_silence_seconds=45,
)


def _gate(count: int) -> AutoJoinGate:
    gate = AutoJoinGate()
    for _ in range(count):
        gate.observe(UMO)
    return gate


def test_looks_like_question() -> None:
    assert looks_like_question("给出点意见呗")
    assert looks_like_question("这游戏好玩吗")
    assert looks_like_question("你怎么看")
    assert not looks_like_question("哈哈哈")
    assert not looks_like_question("我去买饭了")
    assert not looks_like_question("   ")


def test_cold_group_stays_quiet() -> None:
    ok, reason = _gate(2).decide(UMO, **BASE)
    assert not ok
    assert "冷场" in reason


def test_question_breaks_through_cold_group() -> None:
    ok, reason = _gate(1).decide(UMO, question=True, **BASE)
    assert ok
    assert "提问" in reason


def test_cooldown_blocks_repeat(monkeypatch) -> None:
    # 概率封顶 0.6，不固定 random 会偶发掷不中
    monkeypatch.setattr("src.agent_wechat_autojoin.random.random", lambda: 0.0)
    gate = _gate(6)
    params = dict(BASE, base_possibility=1.0, self_silence_seconds=0)
    first, _ = gate.decide(UMO, **params)
    assert first
    second, reason = gate.decide(UMO, **params)
    assert not second
    assert "冷却" in reason


def test_self_silence_blocks() -> None:
    gate = _gate(6)
    gate.mark_self_spoke(UMO)
    ok, reason = gate.decide(UMO, **BASE)
    assert not ok
    assert "自己刚说过话" in reason


def test_hotness_scales_probability(monkeypatch) -> None:
    monkeypatch.setattr("src.agent_wechat_autojoin.random.random", lambda: 0.2)
    ok, reason = _gate(6).decide(UMO, **BASE)
    assert ok
    assert "p=0.30" in reason

    monkeypatch.setattr("src.agent_wechat_autojoin.random.random", lambda: 0.5)
    not_ok, _ = _gate(6).decide(UMO, **BASE)
    assert not not_ok


def test_probability_is_capped(monkeypatch) -> None:
    monkeypatch.setattr("src.agent_wechat_autojoin.random.random", lambda: 0.0)
    ok, reason = _gate(40).decide(
        UMO, base_possibility=1.0, min_hotness=3, cooldown_seconds=0, self_silence_seconds=0
    )
    assert ok
    assert "p=0.60" in reason
