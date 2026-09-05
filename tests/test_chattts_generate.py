from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "chattts_generate.py"
_SPEC = importlib.util.spec_from_file_location("chattts_generate", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class _FakeArray:
    def __init__(self, values: list[float]) -> None:
        self._values = values
        self.size = len(values)

    def reshape(self, *_shape: int) -> "_FakeArray":
        return self

    def tolist(self) -> list[float]:
        return self._values


class _FakeNumpy:
    float32 = object()

    @staticmethod
    def asarray(values: object, dtype: object = None) -> _FakeArray:
        del dtype
        if isinstance(values, _FakeArray):
            return values
        return _FakeArray([float(value) for value in values])  # type: ignore[union-attr]


def test_chattts_runner_requests_one_continuous_waveform() -> None:
    class FakeChat:
        def __init__(self) -> None:
            self.call: tuple[tuple[object, ...], dict[str, object]] | None = None

        def infer(self, *args: object, **kwargs: object) -> list[object]:
            self.call = (args, kwargs)
            return [_FakeArray([0.1, 0.2, 0.3])]

    chat = FakeChat()
    waveform = _MODULE._synthesize_continuous_waveform(
        chat,
        "完整场景文本，保留标点并一次合成。",
        object(),
        _FakeNumpy,
    )

    assert waveform.tolist() == pytest.approx([0.1, 0.2, 0.3])
    assert chat.call is not None
    args, kwargs = chat.call
    assert args == (["完整场景文本，保留标点并一次合成。"],)
    assert kwargs["skip_refine_text"] is False
    assert kwargs["split_text"] is False


def test_chattts_runner_rejects_multiple_waveforms() -> None:
    class FakeChat:
        def infer(self, *args: object, **kwargs: object) -> list[object]:
            return [_FakeArray([1.0, 1.0]), _FakeArray([1.0, 1.0])]

    with pytest.raises(SystemExit, match="exactly one continuous waveform"):
        _MODULE._synthesize_continuous_waveform(
            FakeChat(),
            "完整场景文本。",
            object(),
            _FakeNumpy,
        )


def test_chattts_runner_keeps_long_text_in_natural_sentence_chunks() -> None:
    chunks = _MODULE._split_narration_chunks(
        "第一句内容很长很长很长很长很长。"
        "第二句内容很长很长很长很长很长。"
        "第三句内容很长很长很长很长很长。"
        "第四句内容很长很长很长很长很长。",
        32,
    )

    original = (
        "第一句内容很长很长很长很长很长。"
        "第二句内容很长很长很长很长很长。"
        "第三句内容很长很长很长很长很长。"
        "第四句内容很长很长很长很长很长。"
    )
    assert "".join(chunks) == original
    assert all(len(chunk) <= 32 for chunk in chunks)
    assert len(chunks) == 2


def test_chattts_runner_never_stitches_long_text_waveforms() -> None:
    text = "第一句。第二句。第三句。"

    class FakeChat:
        def __init__(self) -> None:
            self.calls = 0

        def infer(self, *args: object, **kwargs: object) -> list[object]:
            del args, kwargs
            self.calls += 1
            return [_FakeArray([0.1, 0.2, 0.3])]

    chat = FakeChat()
    waveform, chunks = _MODULE._synthesize_long_text(
        chat,
        text,
        object(),
        _FakeNumpy,
        chunk_max_characters=4,
        crossfade_samples=120,
    )

    assert chat.calls == 1
    assert chunks == [text]
    assert waveform.tolist() == pytest.approx([0.1, 0.2, 0.3])


def test_chattts_provider_reads_continuous_metadata_without_phrase_timings(tmp_path: Path) -> None:
    from app.providers.chattts import ChatTTSProvider

    metadata_path = tmp_path / "speech.json"
    metadata_path.write_text(
        '{"segmentation":"continuous_text","synthesis_mode":"single_waveform"}',
        encoding="utf-8",
    )

    metadata = ChatTTSProvider._read_pacing_metadata(metadata_path)

    assert metadata["segmentation"] == "continuous_text"
    assert metadata["synthesis_mode"] == "single_waveform"
    assert "segments" not in metadata


def test_chattts_provider_reads_chunk_crossfade_metadata(tmp_path: Path) -> None:
    from app.providers.chattts import ChatTTSProvider

    metadata_path = tmp_path / "speech.json"
    metadata_path.write_text(
        '{"segmentation":"continuous_text_chunks",'
        '"synthesis_mode":"chunked_crossfade",'
        '"chunk_count":4,"crossfade_ms":120,'
        '"waveform_join_strategy":"equal_power_crossfade"}',
        encoding="utf-8",
    )

    metadata = ChatTTSProvider._read_pacing_metadata(metadata_path)

    assert metadata["synthesis_mode"] == "chunked_crossfade"
    assert metadata["chunk_count"] == 4
    assert metadata["crossfade_ms"] == 120
    assert metadata["waveform_join_strategy"] == "equal_power_crossfade"
