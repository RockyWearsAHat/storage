"""Path locking — persistent, absolute protection for user-specified paths.

A locked path is completely invisible to the storage system. It will not
be scanned, queried, listed, analysed, deleted, or modified in any way
by any operation in this program.  The lock persists across cache rescans,
manifest updates, and program restarts.  Only an explicit unlock by the
user removes the protection.

Lock store: ~/.storage/locks.json
"""

import json
import os
import tempfile
import time
from pathlib import Path

from disk_cleanup.config import HOME

LOCKS_DIR = HOME / ".storage"
LOCKS_FILE = LOCKS_DIR / "locks.json"


# ── Core API ────────────────────────────────────────────────────────────────

def is_locked(path: Path) -> bool:
    """Return True if *path* (or any ancestor) is locked.

    A lock on /foo/bar also protects /foo/bar/baz — anything under a
    locked directory is untouchable.
    """
    locks = load_locks()
    if not locks:
        return False

    resolved = path.resolve()
    for entry in locks:
        locked_path = Path(entry["path"])
        if resolved == locked_path or _is_subpath(resolved, locked_path):
            return True
    return False


def lock_path(path_str: str, *, reason: str = "") -> tuple[bool, str]:
    """Add a path to the lock list.

    Returns (success, message).
    """
    p = Path(path_str).expanduser().resolve()

    if not p.exists():
        return False, f"Path does not exist: {p}"

    locks = load_locks()

    # Check if already locked (exact match or ancestor)
    for entry in locks:
        existing = Path(entry["path"])
        if p == existing:
            return False, f"Already locked: {p}"
        if _is_subpath(p, existing):
            return False, f"Already covered by lock on: {existing}"

    locks.append({
        "path": str(p),
        "locked_at": time.time(),
        "reason": reason,
    })
    _save_locks(locks)
    return True, f"Locked: {p}"


def unlock_path(path_str: str) -> tuple[bool, str]:
    """Remove a path from the lock list.

    Returns (success, message).
    """
    p = Path(path_str).expanduser().resolve()
    locks = load_locks()

    for i, entry in enumerate(locks):
        if Path(entry["path"]) == p:
            locks.pop(i)
            _save_locks(locks)
            return True, f"Unlocked: {p}"

    return False, f"Not locked: {p}"


def list_locks() -> list[dict]:
    """Return all current locks with metadata."""
    return load_locks()


def load_locks() -> list[dict]:
    """Load the lock list from disk. Returns [] if missing or corrupt."""
    if not LOCKS_FILE.exists():
        return []
    try:
        with open(LOCKS_FILE, "r") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        return data
    except (json.JSONDecodeError, OSError):
        return []


def filter_locked(items: list, *, path_attr: str = "path") -> list:
    """Remove any items whose path (or ancestor) is locked.

    Works with any list of objects that have a `path` attribute (Path or str).
    For plain Path lists, pass path_attr=None and items should be Path objects.
    """
    unlocked, _ = partition_locked(items, path_attr=path_attr)
    return unlocked


def partition_locked(
    items: list, *, path_attr: str = "path",
) -> tuple[list, list]:
    """Split items into (unlocked, locked) lists.

    Both lists preserve original items unchanged.
    Works with any list of objects that have a `path` attribute (Path or str).
    For plain Path lists, pass path_attr=None and items should be Path objects.
    """
    locks = load_locks()
    if not locks:
        return items, []

    lock_paths = [Path(e["path"]) for e in locks]

    def _blocked(item_path: Path) -> bool:
        resolved = item_path.resolve()
        for lp in lock_paths:
            if resolved == lp or _is_subpath(resolved, lp):
                return True
        return False

    unlocked = []
    locked = []
    for item in items:
        if path_attr is None:
            p = item
        else:
            p = getattr(item, path_attr)
        if isinstance(p, str):
            p = Path(p)
        if _blocked(p):
            locked.append(item)
        else:
            unlocked.append(item)
    return unlocked, locked


# ── Internal helpers ────────────────────────────────────────────────────────

def _is_subpath(child: Path, parent: Path) -> bool:
    """True if child is under parent."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _save_locks(locks: list[dict]) -> None:
    """Atomically persist the lock list."""
    LOCKS_DIR.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=LOCKS_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(locks, f, indent=2)
        os.replace(tmp, LOCKS_FILE)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
