"""Scanner for Homebrew caches and old formula versions."""

from pathlib import Path

from disk_cleanup.config import Config, HOME
from disk_cleanup.scanners import Category, CleanupItem, RiskLevel
from disk_cleanup.utils import dir_size, is_excluded


BREW_CACHE_DIRS = [
    HOME / "Library" / "Caches" / "Homebrew",
    Path("/usr/local/Cellar"),  # Intel Mac
]


def scan_brew(config: Config) -> list[CleanupItem]:
    """Find Homebrew caches."""
    items: list[CleanupItem] = []

    brew_cache = HOME / "Library" / "Caches" / "Homebrew"
    if brew_cache.exists() and not is_excluded(brew_cache, config):
        size = dir_size(brew_cache)
        if size > 50 * 1024 * 1024:
            items.append(CleanupItem(
                path=brew_cache,
                size_bytes=size,
                category=Category.BREW,
                risk=RiskLevel.SAFE,
                reason="Homebrew download cache — re-downloaded when needed",
                is_directory=True,
            ))

    return items
