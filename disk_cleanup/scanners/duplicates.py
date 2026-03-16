"""Scanner for duplicate files."""

import os
from collections import defaultdict
from pathlib import Path

from disk_cleanup.config import Config, HOME
from disk_cleanup.scanners import Category, CleanupItem, RiskLevel
from disk_cleanup.utils import file_hash, is_excluded

SCAN_DIRS = [
    HOME / "Desktop",
    HOME / "Documents",
    HOME / "Downloads",
]

# Only check files above this size for duplicates (skip tiny files)
MIN_SIZE = 1 * 1024 * 1024  # 1 MB
MAX_DEPTH = 4

# Fingerprinting: roots and walk depth for incremental scan detection
SCAN_ROOTS = list(SCAN_DIRS)
SCAN_DEPTH = MAX_DEPTH


def scan_duplicates(config: Config) -> list[CleanupItem]:
    """Find duplicate files by content hash."""
    items: list[CleanupItem] = []

    # Phase 1: Group files by size (fast pre-filter)
    size_groups: dict[int, list[Path]] = defaultdict(list)

    def collect_files(directory: Path, depth: int = 0):
        if depth > MAX_DEPTH or is_excluded(directory, config):
            return
        try:
            for entry in directory.iterdir():
                if entry.is_symlink() or is_excluded(entry, config):
                    continue
                if entry.is_file():
                    try:
                        size = entry.stat().st_size
                        if size >= MIN_SIZE:
                            size_groups[size].append(entry)
                    except OSError:
                        pass
                elif entry.is_dir() and not entry.name.startswith("."):
                    collect_files(entry, depth + 1)
        except PermissionError:
            pass

    for root in SCAN_DIRS:
        if root.exists():
            collect_files(root)

    # Phase 2: Hash files that share the same size
    hash_groups: dict[str, list[tuple[Path, int]]] = defaultdict(list)

    for size, paths in size_groups.items():
        if len(paths) < 2:
            continue

        for path in paths:
            h = file_hash(path)
            if h:
                hash_groups[h].append((path, size))

    # Phase 3: Report duplicates (keep the oldest, flag the rest)
    for h, group in hash_groups.items():
        if len(group) < 2:
            continue

        # Sort by modification time — keep the oldest
        group.sort(key=lambda x: _mtime(x[0]))

        original = group[0]
        for dup_path, dup_size in group[1:]:
            items.append(CleanupItem(
                path=dup_path,
                size_bytes=dup_size,
                category=Category.DUPLICATES,
                risk=RiskLevel.LOW,
                reason=f"Duplicate of {original[0].name} (in {original[0].parent.name}/)",
                metadata={"original": str(original[0]), "hash": h},
            ))

    return items


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return float("inf")
