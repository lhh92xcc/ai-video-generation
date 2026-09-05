from __future__ import annotations

import asyncio

import httpx

from app.domain.models import SubtitleASRGenerationRequest
from app.providers.aliyun_dashscope_asr import AliyunDashScopeASRProvider
from app.providers.errors_asr import SubtitleASRProviderError


def test_aliyun_dashscope_asr_uploads_polls_and_parses_sentence_timestamps() -> None:
    async def exercise() -> None:
        poll_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal poll_count
            if request.method == "GET" and request.url.path == "/api/v1/uploads":
                assert request.url.params["action"] == "getPolicy"
                assert request.url.params["model"] == "paraformer-v2"
                assert request.headers["authorization"] == "Bearer aliyun-key"
                return httpx.Response(
                    200,
                    request=request,
                    json={
                        "data": {
                            "upload_host": "https://upload.example.test/oss",
                            "upload_dir": "dashscope-instant/test",
                            "oss_access_key_id": "temporary-access",
                            "signature": "temporary-signature",
                            "policy": "temporary-policy",
                            "x_oss_object_acl": "private",
                            "x_oss_forbid_overwrite": "true",
                        }
                    },
                )
            if request.method == "POST" and request.url.host == "upload.example.test":
                assert request.url.path == "/oss"
                assert request.headers["content-type"].startswith("multipart/form-data;")
                assert b"temporary-signature" in request.content
                assert b"narration-" in request.content
                assert b"audio/mpeg" in request.content
                return httpx.Response(200, request=request)
            if request.method == "POST" and request.url.path == "/api/v1/services/audio/asr/transcription":
                assert request.headers["x-dashscope-async"] == "enable"
                assert request.headers["x-dashscope-ossresourceresolve"] == "enable"
                assert request.headers["authorization"] == "Bearer aliyun-key"
                assert request.content
                assert b'"model":"paraformer-v2"' in request.content
                assert b'"language_hints":["zh"]' in request.content
                assert b'"timestamp_alignment_enabled":true' in request.content
                return httpx.Response(
                    200,
                    request=request,
                    json={"output": {"task_id": "task-123"}},
                )
            if request.method == "POST" and request.url.path == "/api/v1/tasks/task-123":
                poll_count += 1
                if poll_count == 1:
                    return httpx.Response(
                        200,
                        request=request,
                        json={"output": {"task_status": "RUNNING"}},
                    )
                return httpx.Response(
                    200,
                    request=request,
                    json={
                        "output": {
                            "task_status": "SUCCEEDED",
                            "results": [
                                {
                                    "subtask_status": "SUCCEEDED",
                                    "transcription_url": "https://result.example.test/result.json",
                                }
                            ],
                        }
                    },
                )
            if request.method == "GET" and request.url.host == "result.example.test":
                assert "authorization" not in request.headers
                assert "content-type" not in request.headers
                return httpx.Response(
                    200,
                    request=request,
                    json={
                        "transcript": "第一句测试。第二句字幕。",
                        "sentences": [
                            {"begin_time": 0, "end_time": 1600, "text": "第一句测试。"},
                            {"begin_time": 1600, "end_time": 3900, "text": "第二句字幕。"},
                        ],
                        "words": [
                            {"begin_time": 0, "end_time": 800, "text": "第一句"},
                            {"begin_time": 800, "end_time": 1600, "text": "测试。"},
                        ],
                    },
                )
            raise AssertionError(f"unexpected request: {request.method} {request.url}")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = AliyunDashScopeASRProvider(
            base_url="https://dashscope.aliyuncs.com",
            api_key="aliyun-key",
            model="paraformer-v2",
            timeout_seconds=10,
            client=client,
        )
        result = await provider.transcribe(
            SubtitleASRGenerationRequest(
                audio_bytes=b"mp3-bytes",
                mime_type="audio/mpeg",
                language="zh-CN",
                audio_duration_seconds=4.0,
            )
        )

        assert poll_count == 2
        assert result.provider == "aliyun_dashscope_asr"
        assert result.precision == "segment_asr"
        assert [cue.text for cue in result.cues] == ["第一句测试。", "第二句字幕。"]
        assert result.cues[0].end_seconds == 1.6
        assert result.cues[1].start_seconds == 1.6
        assert result.metadata["segment_timestamps"] is True
        assert result.metadata["word_timestamps"] is False
        assert result.metadata["word_count"] == 2
        await provider.close()
        await client.aclose()

    asyncio.run(exercise())


def test_aliyun_dashscope_asr_parses_word_timestamps_when_sentences_absent() -> None:
    result = AliyunDashScopeASRProvider._parse_result(
        {
            "words": [
                {"begin_time": 0, "end_time": 400, "text": "你"},
                {"begin_time": 400, "end_time": 800, "text": "好"},
                {"begin_time": 800, "end_time": 1200, "text": "。"},
            ]
        }
    )

    cues, precision, word_count = result
    assert precision == "word_boundary"
    assert word_count == 3
    assert len(cues) == 1
    assert cues[0].text == "你好。"
    assert cues[0].start_seconds == 0
    assert cues[0].end_seconds == 1.2


def test_aliyun_dashscope_asr_parses_transcripts_container() -> None:
    cues, precision, word_count = AliyunDashScopeASRProvider._parse_result(
        {
            "properties": {"audio_format": "mp3"},
            "transcripts": [
                {
                    "channel_id": 0,
                    "text": "这是测试。",
                    "sentences": [
                        {
                            "sentence_id": 1,
                            "begin_time": 120,
                            "end_time": 980,
                            "text": "这是测试。",
                            "words": [
                                {"begin_time": 120, "end_time": 310, "text": "这"},
                                {"begin_time": 310, "end_time": 980, "text": "是测试。"},
                            ],
                        }
                    ],
                }
            ],
        }
    )

    assert precision == "segment_asr"
    assert word_count == 2
    assert len(cues) == 1
    assert cues[0].text == "这是测试。"
    assert cues[0].start_seconds == 0.12
    assert cues[0].end_seconds == 0.98


def test_aliyun_dashscope_asr_maps_auth_error() -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, request=request, json={"message": "forbidden"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = AliyunDashScopeASRProvider(
            base_url="https://dashscope.aliyuncs.com",
            api_key="aliyun-key",
            model="paraformer-v2",
            timeout_seconds=10,
            client=client,
        )
        try:
            await provider.transcribe(
                SubtitleASRGenerationRequest(
                    audio_bytes=b"mp3-bytes",
                    mime_type="audio/mpeg",
                    language="zh-CN",
                    audio_duration_seconds=1.0,
                )
            )
        except SubtitleASRProviderError as exc:
            assert exc.code == "ASR_PROVIDER_AUTH_FAILED"
        else:
            raise AssertionError("expected an authentication error")
        await provider.close()
        await client.aclose()

    asyncio.run(exercise())
