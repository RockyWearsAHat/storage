"""Scanner for application and system caches."""

from pathlib import Path

from disk_cleanup.config import Config, HOME
from disk_cleanup.scanners import Category, CleanupItem, RiskLevel
from disk_cleanup.utils import dir_size, is_excluded


# Known safe-to-delete cache directories
CACHE_DIRS = [
    HOME / "Library" / "Caches",
]

# Fingerprinting: roots and walk depth for incremental scan detection
SCAN_ROOTS = [HOME / "Library" / "Caches"]
SCAN_DEPTH = 1

# Specific large cache subdirs worth calling out individually
NOTABLE_CACHES = [
    ("com.apple.Safari", "Safari Cache"),
    ("com.google.Chrome", "Chrome Cache"),
    ("com.microsoft.VSCode", "VS Code Cache"),
    ("com.spotify.client", "Spotify Cache"),
    ("com.docker.docker", "Docker Cache"),
    ("Firefox", "Firefox Cache"),
    ("Slack", "Slack Cache"),
    ("discord", "Discord Cache"),
    ("pip", "pip Cache"),
    ("yarn", "Yarn Cache"),
    ("com.apple.dt.Xcode", "Xcode Cache"),
]

# Minimum cache size worth reporting (5 MB)
MIN_REPORT_SIZE = 5 * 1024 * 1024


def scan_caches(config: Config) -> list[CleanupItem]:
    """Find application caches that can safely be removed."""
    items: list[CleanupItem] = []

    caches_dir = HOME / "Library" / "Caches"
    if not caches_dir.exists():
        return items

    if is_excluded(caches_dir, config):
        return items

    notable_names = {name for name, _ in NOTABLE_CACHES}
    found_notable = set()

    # Scan for notable caches first
    for name, label in NOTABLE_CACHES:
        cache_path = caches_dir / name
        if cache_path.exists() and cache_path.is_dir():
            size = dir_size(cache_path)
            if size >= MIN_REPORT_SIZE:
                items.append(CleanupItem(
                    path=cache_path,
                    size_bytes=size,
                    category=Category.CACHES,
                    risk=RiskLevel.SAFE,
                    reason=f"{label} — regenerated automatically",
                    is_directory=True,
                ))
                found_notable.add(name)

    # Scan remaining cache directories
    try:
        for entry in caches_dir.iterdir():
            if not entry.is_dir() or entry.name in found_notable:
                continue
            if entry.name.startswith("."):
                continue
            try:
                size = dir_size(entry)
                if size >= MIN_REPORT_SIZE:
                    items.append(CleanupItem(
                        path=entry,
                        size_bytes=size,
                        category=Category.CACHES,
                        risk=RiskLevel.SAFE,
                        reason=f"App cache ({entry.name}) — regenerated automatically",
                        is_directory=True,
                    ))
            except PermissionError:
                pass
    except PermissionError:
        pass

    return items
