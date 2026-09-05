"""Conservative cleanup for disposable Worker directories.

Artifact storage is intentionally excluded.  Only explicitly configured
temporary roots are considered, and every deleted file must be older than the
configured age.  This makes the operation safe to expose as a manual action
from the remote queue page.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CleanupReport:
    scanned_roots: tuple[str, ...]
    deleted_files: int
    deleted_bytes: int
    skipped_files: int
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    free_bytes_before: int
    free_bytes_after: int
    min_free_bytes: int
    low_disk: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "scanned_roots": list(self.scanned_roots),
            "deleted_files": self.deleted_files,
            "deleted_bytes": self.deleted_bytes,
            "skipped_files": self.skipped_files,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "free_bytes_before": self.free_bytes_before,
            "free_bytes_after": self.free_bytes_after,
            "min_free_bytes": self.min_free_bytes,
            "low_disk": self.low_disk,
        }


class TemporaryDirectoryCleanupService:
    """Delete old files from a whitelist of disposable directories."""

    def __init__(
        self,
        roots: tuple[str, ...],
        *,
        storage_base_path: str,
        max_age_hours: float = 24,
        max_files_per_run: int = 500,
        min_free_gb: float = 10,
    ) -> None:
        self.roots = tuple(root for root in roots if root.strip())
        self.storage_base_path = Path(storage_base_path).resolve()
        self.max_age = timedelta(hours=max(0.01, max_age_hours))
        self.max_files_per_run = max(1, max_files_per_run)
        self.min_free_bytes = max(0, int(min_free_gb * 1024**3))

    def cleanup(self, now: datetime | None = None) -> CleanupReport:
        current_time = now or datetime.now(timezone.utc)
        cutoff = current_time.timestamp() - self.max_age.total_seconds()
        scanned: list[str] = []
        errors: list[str] = []
        warnings: list[str] = []
        candidates: list[tuple[float, Path, int]] = []
        skipped = 0
        usage_path = self._usage_path()
        free_bytes_before = _free_bytes(usage_path)
        low_disk_before = (
            self.min_free_bytes > 0 and free_bytes_before < self.min_free_bytes
        )
        if low_disk_before:
            warnings.append(
                "可用磁盘低于清理阈值，将持续删除白名单目录中的过期文件直到达到阈值或达到本次上限"
            )

        for raw_root in self.roots:
            try:
                root = Path(raw_root).expanduser().resolve()
                self._validate_root(root)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if not root.exists():
                continue
            scanned.append(str(root))
            try:
                for path in root.rglob("*"):
                    if path.is_symlink() or not path.is_file():
                        continue
                    try:
                        stat = path.stat()
                    except OSError as exc:
                        errors.append(f"{path}: {exc}")
                        continue
                    if stat.st_mtime >= cutoff:
                        skipped += 1
                        continue
                    candidates.append((stat.st_mtime, path, stat.st_size))
            except OSError as exc:
                errors.append(f"{root}: {exc}")

        candidates.sort(key=lambda item: item[0])
        deleted_files = 0
        deleted_bytes = 0
        for _, path, size in candidates[: self.max_files_per_run]:
            try:
                path.unlink()
            except OSError as exc:
                errors.append(f"{path}: {exc}")
                continue
            deleted_files += 1
            deleted_bytes += size
            if low_disk_before and _free_bytes(usage_path) >= self.min_free_bytes:
                break

        for raw_root in scanned:
            root = Path(raw_root)
            try:
                directories = sorted(
                    (path for path in root.rglob("*") if path.is_dir() and not path.is_symlink()),
                    key=lambda path: len(path.parts),
                    reverse=True,
                )
                for directory in directories:
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
            except OSError as exc:
                errors.append(f"{root}: {exc}")

        free_bytes = _free_bytes(usage_path)
        low_disk_after = self.min_free_bytes > 0 and free_bytes < self.min_free_bytes
        if low_disk_after:
            warnings.append(
                "清理后可用磁盘仍低于阈值，请在 Windows 主机上释放更多磁盘空间"
            )
        return CleanupReport(
            scanned_roots=tuple(scanned),
            deleted_files=deleted_files,
            deleted_bytes=deleted_bytes,
            skipped_files=skipped,
            errors=tuple(errors[:50]),
            warnings=tuple(warnings[:20]),
            free_bytes_before=free_bytes_before,
            free_bytes_after=free_bytes,
            min_free_bytes=self.min_free_bytes,
            low_disk=low_disk_after,
        )

    def _usage_path(self) -> Path:
        """Choose an existing filesystem path without widening cleanup scope."""

        for raw_root in self.roots:
            candidate = Path(raw_root).expanduser().resolve()
            while not candidate.exists() and candidate != candidate.parent:
                candidate = candidate.parent
            if candidate.exists():
                return candidate
        return Path.cwd()

    def _validate_root(self, root: Path) -> None:
        if root == Path("/") or root == Path.cwd().resolve():
            raise ValueError(f"Refusing to clean broad root: {root}")
        if root == self.storage_base_path or self.storage_base_path in root.parents:
            raise ValueError(f"Refusing to clean Artifact storage path: {root}")
        if root.name not in {"work", "renders", "musetalk", "tmp", "temp"}:
            raise ValueError(
                f"Cleanup root must be an explicit disposable directory (work/renders/musetalk): {root}"
            )


def _free_bytes(path: Path) -> int:
    try:
        return int(shutil.disk_usage(path).free)
    except OSError:
        return 0
