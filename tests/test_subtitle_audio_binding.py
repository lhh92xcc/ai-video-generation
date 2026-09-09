from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.providers.errors_asr import SubtitleASRInputError
from app.services.subtitle_service import SubtitleTaskService


@pytest.mark.parametrize(
    ('metadata_episode', 'source_episode', 'accepted'),
    [('target', 'target', True), (None, 'target', True), ('target', None, True),
     ('other', 'other', False), ('target', 'other', False),
     ('other', 'target', False), (None, None, False)],
)
def test_audio_episode_provenance_before_storage_read(metadata_episode, source_episode, accepted):
    async def exercise():
        project_id = uuid4()
        artifact = SimpleNamespace(
            task_id=uuid4(), project_id=project_id, type='audio_narration',
            metadata={'episode_id': metadata_episode, 'content_type': 'audio/wav',
                      'storage_key': 'audio/source.wav', 'duration_seconds': 3.0},
        )
        store = AsyncMock()
        store.get_artifact.return_value = artifact
        store.get_task.return_value = SimpleNamespace(input_data={'episode_id': source_episode})
        storage = AsyncMock()
        storage.get_bytes.return_value = b'audio'
        service = SubtitleTaskService(store, AsyncMock(), storage)
        args = (SimpleNamespace(project_id=project_id), {'audio_artifact_id': str(uuid4()), 'episode_id': 'target'})
        if accepted:
            result = await service._load_asr_audio(*args)
            assert result == (artifact, b'audio')
            storage.get_bytes.assert_awaited_once()
        else:
            with pytest.raises(SubtitleASRInputError) as caught:
                await service._load_asr_audio(*args)
            assert caught.value.code == 'SUBTITLE_ASR_AUDIO_EPISODE_MISMATCH'
            storage.get_bytes.assert_not_awaited()

    asyncio.run(exercise())
