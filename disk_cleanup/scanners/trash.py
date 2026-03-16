"""Scanner for macOS Trash."""

from pathlib import Path

from disk_cleanup.config import Config, HOME
from disk_cleanup.scanners import Category, CleanupItem, RiskLevel
from disk_cleanup.utils import dir_size


def scan_trash(config: Config) -> list[CleanupItem]:
    """Check how much space Trash is using."""
    items: list[CleanupItem] = []
    trash = HOME / ".Trash"

    if not trash.exists():
        return items

    size = dir_size(trash)
    if size > 1 * 1024 * 1024:  # More than 1 MB
        items.append(CleanupItem(
            path=trash,
            size_bytes=size,
            category=Category.TRASH,
            risk=RiskLevel.SAFE,
            reason="macOS Trash — already deleted by user",
            is_directory=True,
        ))

    return items
