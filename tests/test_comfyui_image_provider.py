from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from app.domain.models import AssetType, ReferenceImageGenerationRequest
from app.providers.comfyui_image import ComfyUIImageGenerationProvider
from app.providers.errors import ImageProviderError


def image_request() -> ReferenceImageGenerationRequest:
    return ReferenceImageGenerationRequest(
        asset_id=uuid4(),
        asset_key=uuid4(),
        asset_type=AssetType.CHARACTER,
        asset_version=2,
        prompt="cinematic character reference",
        negative_prompt="blurry",
        width=512,
        height=768,
    )


def workflow_file(tmp_path: Path) -> Path:
    path = tmp_path / "workflow.json"
    path.write_text(
        json.dumps(
            {
                "1": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {
                        "text": "__AI_VIDEO_PROMPT__",
                        "negative": "__AI_VIDEO_NEGATIVE_PROMPT__",
                        "width": "__AI_VIDEO_WIDTH__",
                        "height": "__AI_VIDEO_HEIGHT__",
                        "seed": "__AI_VIDEO_SEED__",
                        "checkpoint": "__AI_VIDEO_CHECKPOINT__",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def test_comfyui_provider_submits_polls_and_downloads_image(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            payload = json.loads(request.content)
            inputs = payload["prompt"]["1"]["inputs"]
            assert inputs["text"] == "cinematic character reference"
            assert inputs["negative"] == "blurry"
            assert inputs["width"] == 512
            assert inputs["height"] == 768
            assert isinstance(inputs["seed"], int)
            return httpx.Response(200, json={"prompt_id": "prompt-1"})
        if request.url.path == "/history/prompt-1":
            return httpx.Response(
                200,
                json={
                    "prompt-1": {
                        "status": {"status_str": "success", "completed": True},
                        "outputs": {
                            "9": {
                                "images": [
                                    {"filename": "image.png", "subfolder": "", "type": "output"}
                                ]
                            }
                        },
                    }
                },
            )
        assert request.url.path == "/view"
        assert request.url.params["filename"] == "image.png"
        return httpx.Response(200, content=b"png-bytes", headers={"content-type": "image/png"})

    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = ComfyUIImageGenerationProvider(
            base_url="http://comfy.test",
            workflow_path=str(workflow_file(tmp_path)),
            model="sd_xl_turbo.safetensors",
            timeout_seconds=5,
            poll_interval_seconds=0.01,
            client=client,
        )
        result = await provider.generate_reference_image(image_request())
        await provider.close()
        await client.aclose()

        assert result.provider == "comfyui"
        assert result.image_base64 == base64.b64encode(b"png-bytes").decode("ascii")
        assert result.metadata["prompt_id"] == "prompt-1"

    asyncio.run(exercise())


def test_comfyui_provider_requires_prompt_placeholders(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"1": {"class_type": "SaveImage", "inputs": {}}}), encoding="utf-8")

    async def exercise() -> None:
        provider = ComfyUIImageGenerationProvider(workflow_path=str(invalid))
        with pytest.raises(ImageProviderError) as error:
            await provider.generate_reference_image(image_request())
        await provider.close()
        assert error.value.code == "IMAGE_WORKFLOW_INVALID"

    asyncio.run(exercise())


def test_comfyui_provider_accepts_flux_workflow_without_negative_prompt() -> None:
    workflow = {
        "1": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "__AI_VIDEO_PROMPT__"},
        }
    }
    rendered = ComfyUIImageGenerationProvider._replace_placeholders(
        workflow,
        {
            "__AI_VIDEO_PROMPT__": "one subject",
            "__AI_VIDEO_NEGATIVE_PROMPT__": "",
        },
    )
    assert rendered["1"]["inputs"]["text"] == "one subject"


def test_comfyui_provider_replaces_quality_parameters_as_numbers() -> None:
    workflow = {
        "1": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": "__AI_VIDEO_PROMPT__",
                "steps": "__AI_VIDEO_IMAGE_STEPS__",
                "guidance": "__AI_VIDEO_IMAGE_GUIDANCE__",
                "identity_weight": "__AI_VIDEO_IMAGE_IDENTITY_WEIGHT__",
            },
        }
    }
    rendered = ComfyUIImageGenerationProvider._replace_placeholders(
        workflow,
        {
            "__AI_VIDEO_PROMPT__": "one subject",
            "__AI_VIDEO_IMAGE_STEPS__": 4,
            "__AI_VIDEO_IMAGE_GUIDANCE__": 3.5,
            "__AI_VIDEO_IMAGE_IDENTITY_WEIGHT__": 0.9,
        },
    )
    inputs = rendered["1"]["inputs"]
    assert inputs["steps"] == 4
    assert inputs["guidance"] == 3.5
    assert inputs["identity_weight"] == 0.9


def test_comfyui_provider_rejects_non_object_prompt_response(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/prompt"
        return httpx.Response(200, json=["unexpected"])

    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = ComfyUIImageGenerationProvider(
            base_url="http://comfy.test",
            workflow_path=str(workflow_file(tmp_path)),
            client=client,
        )
        with pytest.raises(ImageProviderError) as error:
            await provider.generate_reference_image(image_request())
        await provider.close()
        await client.aclose()
        assert error.value.code == "IMAGE_PROVIDER_INVALID_RESPONSE"

    asyncio.run(exercise())
