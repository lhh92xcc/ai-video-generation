from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # API tests must never depend on the network.  The local portfolio profile
    # can still opt into Edge TTS explicitly; this fixture only isolates the
    # default in-process test application from a user's shell configuration.
    monkeypatch.setenv("AI_VIDEO_TTS_PROVIDER", "mock")
    with TestClient(create_app()) as test_client:
        yield test_client
