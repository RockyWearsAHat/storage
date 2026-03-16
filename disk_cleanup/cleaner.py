"""Safe deletion engine with confirmation and undo support."""

from pathlib import Path

from disk_cleanup.config import Config
from disk_cleanup.locks import is_locked
from disk_cleanup.scanners import CleanupItem, RiskLevel
from disk_cleanup.utils import format_size, is_protected, move_to_trash, permanent_delete


def delete_item(item: CleanupItem, config: Config, permanent: bool = False) -> tuple[bool, str]:
    """Delete a single cleanup item.

    Returns (success, message).
    """
    path = item.path

    # Safety checks
    if is_protected(path, config):
        return False, f"BLOCKED: {path} is a protected path"

    if is_locked(path):
        return False, f"BLOCKED: {path} is \U0001f512 LOCKED by user"

    if not path.exists():
        return False, f"Already gone: {path}"

    # Extra safety for high-risk items
    if item.risk == RiskLevel.HIGH:
        return False, f"BLOCKED: {path} is high-risk — manual review required"

    # Perform deletion
    if permanent or not config.use_trash:
        success = permanent_delete(path)
        action = "permanently deleted"
    else:
        success = move_to_trash(path)
        action = "moved to Trash"

    if success:
        return True, f"{action}: {path} ({format_size(item.size_bytes)})"
    else:
        return False, f"Failed to delete: {path}"


def delete_items(
    items: list[CleanupItem],
    config: Config,
    permanent: bool = False,
    progress_callback=None,
) -> tuple[int, int, int]:
    """Delete multiple items. Returns (success_count, fail_count, bytes_freed)."""
    success_count = 0
    fail_count = 0
    bytes_freed = 0

    for i, item in enumerate(items):
        success, msg = delete_item(item, config, permanent)
        if success:
            success_count += 1
            bytes_freed += item.size_bytes
        else:
            fail_count += 1

        if progress_callback:
            progress_callback(i + 1, len(items), success, msg)

    return success_count, fail_count, bytes_freed
