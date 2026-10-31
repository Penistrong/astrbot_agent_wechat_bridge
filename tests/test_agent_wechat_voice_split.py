import os
import tempfile

from tests.test_agent_wechat_event import _load_event_module


def test_missing_file_is_returned_as_is() -> None:
    ev = _load_event_module()
    with tempfile.TemporaryDirectory() as tmp_dir:
        missing = os.path.join(tmp_dir, "nope.wav")
        assert ev._split_voice_for_wechat(missing) == [missing]


def test_short_voice_is_not_split(monkeypatch) -> None:
    ev = _load_event_module()
    with tempfile.TemporaryDirectory() as tmp_dir:
        voice = os.path.join(tmp_dir, "a.wav")
        with open(voice, "wb") as f:
            f.write(b"x" * 2000)
        monkeypatch.setattr(ev, "_probe_duration", lambda path: 12.0)
        assert ev._split_voice_for_wechat(voice) == [voice]


def test_silence_cuts_uses_midpoints(monkeypatch) -> None:
    ev = _load_event_module()

    class Result:
        stderr = (
            "silence_start: 1.0\nsilence_end: 2.0\n"
            "silence_start: 5.4\nsilence_end: 6.4\n"
        )

    monkeypatch.setattr(ev.subprocess, "run", lambda *a, **k: Result())
    assert ev._silence_cuts("x.wav") == [1.5, 5.9]


def test_probe_duration_returns_zero_when_ffprobe_missing(monkeypatch) -> None:
    ev = _load_event_module()

    def boom(*args, **kwargs):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(ev.subprocess, "run", boom)
    assert ev._probe_duration("x.wav") == 0.0
