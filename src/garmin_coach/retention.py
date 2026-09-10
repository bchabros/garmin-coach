"""Deterministic retention for dated report artifacts, separate from coaching."""

from __future__ import annotations

import datetime as dt
import errno
import stat
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ReportFolder:
    """A dated folder and the report files selected by the age policy."""

    path: Path
    age_days: int
    files: dict[str, int]


@dataclass
class PruneResult:
    """Selected report files and the deletion count for one retention run."""

    folders: list[ReportFolder] = field(default_factory=list)
    deleted_files: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        """Return ok (0), partial deletion (1), or failed (2)."""
        return (1 if self.deleted_files else 2) if self.errors else 0


def prune_reports(
    root: Path,
    *,
    today: dt.date,
    older_than: int = 90,
    confirm: bool = False,
    keep_digest: bool = True,
) -> PruneResult:
    """Select expired report files and delete them only when explicitly confirmed.

    Args:
        root: Report root; the CLI fixes it to ./reports. Symlink roots are refused.
        today: Local calendar date against which folder names are aged.
        older_than: Exclusive age threshold in days.
        confirm: Whether to delete; False returns the selection without writes.
        keep_digest: Preserve digest.json and snapshot.json unless False.

    Returns:
        Selected folders, actual deletions and individual filesystem errors.

    Raises:
        ValueError: If the root is a symbolic link.
        OSError: If the report root cannot be inspected or listed.
    """
    if root.is_symlink():
        raise ValueError("reports root must not be a symbolic link")
    result = PruneResult()
    if not root.exists():
        return result
    names: tuple[str, ...] = ("report.md", "hrv_band.png", "acwr.png")
    if not keep_digest:
        names += ("digest.json", "snapshot.json")
    for path in sorted(root.iterdir()):
        age = _expired_folder_age(path, today, older_than, result)
        if age is None:
            continue
        files = _report_files(path, names, result)
        if files:
            result.folders.append(ReportFolder(path, age, files))
            if confirm:
                _delete_report_files(path, files, result)
    return result


def _expired_folder_age(
    path: Path, today: dt.date, older_than: int, result: PruneResult
) -> int | None:
    try:
        day = dt.date.fromisoformat(path.name)
    except ValueError:
        return None
    age = (today - day).days
    if path.name != day.isoformat() or age <= older_than:
        return None
    try:
        return age if stat.S_ISDIR(path.stat(follow_symlinks=False).st_mode) else None
    except OSError as exc:
        result.errors.append(f"{path}: {exc}")
        return None


def _delete_report_files(path: Path, files: dict[str, int], result: PruneResult) -> None:
    for name in files:
        try:
            (path / name).unlink()
            result.deleted_files += 1
        except OSError as exc:
            result.errors.append(f"{path / name}: {exc}")
    try:
        path.rmdir()
    except OSError as exc:
        if exc.errno != errno.ENOTEMPTY:
            result.errors.append(f"{path}: {exc}")


def _report_files(path: Path, names: tuple[str, ...], result: PruneResult) -> dict[str, int]:
    files = {}
    for name in names:
        try:
            info = (path / name).stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError as exc:
            result.errors.append(f"{path / name}: {exc}")
            continue
        if stat.S_ISREG(info.st_mode):
            files[name] = info.st_size
    return files
