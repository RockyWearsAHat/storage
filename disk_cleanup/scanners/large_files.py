"""Scanner for unusually large files."""

from pathlib import Path

from disk_cleanup.config import Config, HOME
from disk_cleanup.scanners import Category, CleanupItem, RiskLevel
from disk_cleanup.utils import get_file_age_days, is_excluded

SCAN_DIRS = [
    HOME / "Desktop",
    HOME / "Documents",
    HOME / "Downloads",
    HOME / "Movies",
    HOME / "Music",
]

# Never flag these
SKIP_EXTENSIONS = frozenset({
    ".photoslibrary", ".musiclibrary",
})

MAX_DEPTH = 5

# Fingerprinting: roots and walk depth for incremental scan detection
SCAN_ROOTS = list(SCAN_DIRS)
SCAN_DEPTH = MAX_DEPTH


def scan_large_files(config: Config) -> list[CleanupItem]:
    """Find files larger than the configured threshold."""
    items: list[CleanupItem] = []
    threshold = config.large_file_threshold_mb * 1024 * 1024

    def scan(directory: Path, depth: int = 0):
        if depth > MAX_DEPTH or is_excluded(directory, config):
            return

        try:
            for entry in directory.iterdir():
                if entry.is_symlink() or is_excluded(entry, config):
                    continue

                if entry.is_file():
                    try:
                        size = entry.stat().st_size
                        if size >= threshold and entry.suffix.lower() not in SKIP_EXTENSIONS:
                            age = get_file_age_days(entry)
                            risk = RiskLevel.MEDIUM if age > 180 else RiskLevel.HIGH

                            items.append(CleanupItem(
                                path=entry,
                                size_bytes=size,
                                category=Category.LARGE_FILES,
                                risk=risk,
                                reason=f"Large file ({entry.suffix or 'no ext'}), {age:.0f} days old",
                                metadata={"age_days": age},
                            ))
                    except OSError:
                        pass
                elif entry.is_dir() and not entry.name.startswith("."):
                    scan(entry, depth + 1)
        except PermissionError:
            pass

    for root in SCAN_DIRS:
        if root.exists():
            scan(root)

    return items
