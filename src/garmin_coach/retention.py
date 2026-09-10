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
    """Preview or delete report files strictly older than the given number of days."""
    if root.is_symlink():
        raise ValueError("reports root must not be a symbolic link")
    result = PruneResult()
    if not root.exists():
        return result
    names: tuple[str, ...] = ("report.md", "hrv_band.png", "acwr.png")
    if not keep_digest:
        names += ("digest.json", "snapshot.json")
    for path in sorted(root.iterdir()):
        try:
            day = dt.date.fromisoformat(path.name)
        except ValueError:
            continue
        if path.name != day.isoformat() or path.is_symlink() or not path.is_dir():
            continue
        age = (today - day).days
        if age <= older_than:
            continue
        files = _report_files(path, names, result)
        if files:
            result.folders.append(ReportFolder(path, age, files))
            if confirm:
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
    return result


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
