"""Scanner for screenshots and screen recordings scattered around the system."""

import fnmatch
from pathlib import Path

from disk_cleanup.config import Config, HOME, SCREENSHOT_PATTERNS
from disk_cleanup.scanners import Category, CleanupItem, RiskLevel
from disk_cleanup.utils import get_file_age_days, is_excluded

# Common places screenshots end up
SCREENSHOT_LOCATIONS = [
    HOME / "Desktop",
    HOME / "Documents",
    HOME / "Downloads",
    HOME / "Pictures",
]


def scan_screenshots(config: Config) -> list[CleanupItem]:
    """Find screenshots and screen recordings."""
    items: list[CleanupItem] = []
    seen: set[Path] = set()

    for location in SCREENSHOT_LOCATIONS:
        if not location.exists() or is_excluded(location, config):
            continue

        try:
            for entry in location.iterdir():
                if not entry.is_file() or entry.is_symlink():
                    continue
                if entry in seen:
                    continue

                name = entry.name
                is_screenshot = any(
                    fnmatch.fnmatch(name, pattern)
                    for pattern in SCREENSHOT_PATTERNS
                )

                if is_screenshot:
                    try:
                        size = entry.stat().st_size
                        age = get_file_age_days(entry)
                        seen.add(entry)

                        # Recent screenshots are medium risk, old ones are low
                        risk = RiskLevel.LOW if age > 30 else RiskLevel.MEDIUM
                        items.append(CleanupItem(
                            path=entry,
                            size_bytes=size,
                            category=Category.SCREENSHOTS,
                            risk=risk,
                            reason=f"Screenshot, {age:.0f} days old, in {location.name}/",
                            metadata={"age_days": age, "location": str(location)},
                        ))
                    except OSError:
                        pass
        except PermissionError:
            pass

    return items
