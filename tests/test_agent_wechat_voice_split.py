import pytest

# src.agent_wechat_event 在模块顶层 import astrbot，裸环境（只有插件依赖）
# 收不进来；装了 AstrBot 的环境才跑这组用例。
pytest.importorskip("astrbot")

from src import agent_wechat_event as ev  # noqa: E402


def test_missing_file_is_returned_as_is(tmp_path) -> None:
    missing = str(tmp_path / "nope.wav")
    assert ev._split_voice_for_wechat(missing) == [missing]


def test_short_voice_is_not_split(tmp_path, monkeypatch) -> None:
    voice = tmp_path / "a.wav"
    voice.write_bytes(b"x" * 2000)
    monkeypatch.setattr(ev, "_probe_duration", lambda path: 12.0)
    assert ev._split_voice_for_wechat(str(voice)) == [str(voice)]


def test_silence_cuts_uses_midpoints(monkeypatch) -> None:
    class Result:
        stderr = (
            "silence_start: 1.0\nsilence_end: 2.0\n"
            "silence_start: 5.4\nsilence_end: 6.4\n"
        )

    monkeypatch.setattr(ev.subprocess, "run", lambda *a, **k: Result())
    assert ev._silence_cuts("x.wav") == [1.5, 5.9]


def test_probe_duration_returns_zero_when_ffprobe_missing(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(ev.subprocess, "run", boom)
    assert ev._probe_duration("x.wav") == 0.0
