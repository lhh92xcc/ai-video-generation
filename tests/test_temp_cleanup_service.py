from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services import temp_cleanup_service
from app.services.temp_cleanup_service import TemporaryDirectoryCleanupService


def fixed_now() -> datetime:
    return datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def mark_old(path: Path, now: datetime) -> None:
    timestamp = (now - timedelta(days=3)).timestamp()
    path.touch()
    path.touch()
    path.stat()
    import os

    os.utime(path, (timestamp, timestamp))


def test_cleanup_deletes_old_files_and_keeps_recent_files(tmp_path: Path) -> None:
    now = fixed_now()
    work_root = tmp_path / "work"
    storage_root = tmp_path / "artifacts"
    work_root.mkdir()
    storage_root.mkdir()
    old_file = work_root / "old.tmp"
    recent_file = work_root / "recent.tmp"
    mark_old(old_file, now)
    recent_file.write_bytes(b"keep")

    service = TemporaryDirectoryCleanupService(
        (str(work_root),),
        storage_base_path=str(storage_root),
        max_age_hours=24,
        min_free_gb=0,
    )
    report = service.cleanup(now)

    assert not old_file.exists()
    assert recent_file.exists()
    assert report.deleted_files == 1
    assert report.deleted_bytes == 0
    assert report.skipped_files == 1
    assert report.scanned_roots == (str(work_root.resolve()),)
    assert report.low_disk is False


def test_cleanup_never_deletes_artifact_storage(tmp_path: Path) -> None:
    now = fixed_now()
    storage_root = tmp_path / "artifacts"
    storage_root.mkdir()
    artifact = storage_root / "rendered-video.mp4"
    mark_old(artifact, now)

    service = TemporaryDirectoryCleanupService(
        (str(storage_root),),
        storage_base_path=str(storage_root),
        max_age_hours=24,
        min_free_gb=0,
    )
    report = service.cleanup(now)

    assert artifact.exists()
    assert report.deleted_files == 0
    assert report.scanned_roots == ()
    assert any("Artifact storage" in error for error in report.errors)


def test_cleanup_reports_low_disk_and_before_after_space(monkeypatch, tmp_path: Path) -> None:
    now = fixed_now()
    work_root = tmp_path / "renders"
    storage_root = tmp_path / "artifacts"
    work_root.mkdir()
    storage_root.mkdir()
    old_file = work_root / "old.render"
    mark_old(old_file, now)
    free_bytes = iter((100, 250, 250))
    monkeypatch.setattr(temp_cleanup_service, "_free_bytes", lambda _path: next(free_bytes))

    service = TemporaryDirectoryCleanupService(
        (str(work_root),),
        storage_base_path=str(storage_root),
        max_age_hours=24,
        min_free_gb=1,
    )
    report = service.cleanup(now)

    assert report.free_bytes_before == 100
    assert report.free_bytes_after == 250
    assert report.min_free_bytes == 1024**3
    assert report.low_disk is True
    assert len(report.warnings) == 2
    assert report.deleted_files == 1
