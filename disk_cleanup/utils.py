"""Shared utilities for disk cleanup."""

import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from disk_cleanup.config import Config


def format_size(size_bytes: int) -> str:
    """Human-readable file size."""
    if size_bytes < 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def get_file_age_days(path: Path) -> float:
    """Days since the file was last modified."""
    try:
        mtime = path.stat().st_mtime
        age = datetime.now(timezone.utc) - datetime.fromtimestamp(mtime, tz=timezone.utc)
        return age.total_seconds() / 86400
    except OSError:
        return 0


def dir_size(path: Path) -> int:
    """Total size of a directory in bytes."""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except PermissionError:
        pass
    return total


def file_hash(path: Path, chunk_size: int = 65536) -> str:
    """SHA-256 hash of a file's contents."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                h.update(data)
    except OSError:
        return ""
    return h.hexdigest()


def is_protected(path: Path, config: Config) -> bool:
    """Check if a path is protected from deletion."""
    resolved = path.resolve()
    for protected in config.protected_paths:
        try:
            if resolved == protected.resolve() or resolved == protected.resolve().parent:
                return True
        except OSError:
            pass
    return False


def is_excluded(path: Path, config: Config) -> bool:
    """Check if a path is in the exclusion list."""
    resolved = path.resolve()
    for excl in config.exclusion_paths:
        try:
            if resolved == excl.resolve() or resolved.is_relative_to(excl.resolve()):
                return True
        except (OSError, ValueError):
            pass
    return False


def move_to_trash(path: Path) -> bool:
    """Move a file/directory to macOS Trash using Finder (recoverable)."""
    try:
        script = f'''
        tell application "Finder"
            delete POSIX file "{path.resolve()}"
        end tell
        '''
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, timeout=30, check=True,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def permanent_delete(path: Path) -> bool:
    """Permanently delete a file or directory."""
    try:
        if path.is_dir():
            import shutil
            shutil.rmtree(path)
        else:
            path.unlink()
        return True
    except OSError:
        return False


def empty_trash() -> tuple[bool, int]:
    """Empty the macOS Trash. Returns (success, bytes_freed)."""
    trash_dir = Path.home() / ".Trash"
    if not trash_dir.exists():
        return True, 0

    # Measure size before
    total_size = 0
    try:
        for entry in trash_dir.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                try:
                    total_size += entry.stat().st_size
                except OSError:
                    pass
    except PermissionError:
        pass

    if total_size == 0:
        return True, 0

    # Use Finder AppleScript to empty trash (handles SIP-protected items)
    try:
        script = 'tell application "Finder" to empty trash'
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, timeout=120, check=True,
        )
        return True, total_size
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Fall back to manual deletion of contents
        import shutil
        freed = 0
        for child in list(trash_dir.iterdir()):
            try:
                child_size = child.stat().st_size if child.is_file() else dir_size(child)
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
                freed += child_size
            except OSError:
                pass
        return freed > 0, freed


def get_disk_usage() -> dict:
    """Get disk usage stats for the main volume."""
    stat = os.statvfs("/")
    total = stat.f_blocks * stat.f_frsize
    free = stat.f_bavail * stat.f_frsize
    used = total - free
    return {
        "total": total,
        "used": used,
        "free": free,
        "percent_used": (used / total * 100) if total > 0 else 0,
    }
