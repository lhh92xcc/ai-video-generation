from pathlib import Path


def test_macos_comfyui_setup_keeps_mps_available_by_default() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "setup-comfyui-mac.sh"
    ).read_text(encoding="utf-8")

    assert "COMFYUI_TORCH_INDEX_URL" in script
    assert "COMFYUI_REQUIRE_MPS" in script
    assert "download.pytorch.org/whl/nightly/cpu" not in script
    assert "torch.backends.mps.is_available()" in script
