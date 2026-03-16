"""Scan result caching — persist/load scan data between sessions."""

import json
import time
from pathlib import Path

from disk_cleanup.config import HOME
from disk_cleanup.scanners import Category, CleanupItem, RiskLevel, ScanResult

CACHE_DIR = HOME / ".storage"
CACHE_FILE = CACHE_DIR / "last_scan.json"
CACHE_MAX_AGE = 3600  # 1 hour — after this, results are "stale"


def save_scan(result: ScanResult) -> None:
    """Persist scan results to disk."""
    CACHE_DIR.mkdir(exist_ok=True)
    data = {
        "timestamp": time.time(),
        "scan_time_seconds": result.scan_time_seconds,
        "errors": result.errors,
        "items": [
            {
                "path": str(item.path),
                "size_bytes": item.size_bytes,
                "category": item.category.value,
                "risk": item.risk.value,
                "reason": item.reason,
                "is_directory": item.is_directory,
                "metadata": item.metadata,
            }
            for item in result.items
        ],
    }
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)


def load_scan() -> tuple[ScanResult | None, float | None]:
    """Load cached scan results.

    Returns (result, age_seconds) or (None, None) if no cache.
    """
    if not CACHE_FILE.exists():
        return None, None

    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)

        age = time.time() - data["timestamp"]

        items = []
        for raw in data["items"]:
            items.append(CleanupItem(
                path=Path(raw["path"]),
                size_bytes=raw["size_bytes"],
                category=Category(raw["category"]),
                risk=RiskLevel(raw["risk"]),
                reason=raw["reason"],
                is_directory=raw.get("is_directory", False),
                metadata=raw.get("metadata", {}),
            ))

        result = ScanResult(
            items=items,
            errors=data.get("errors", []),
            scan_time_seconds=data.get("scan_time_seconds", 0),
        )
        return result, age

    except (json.JSONDecodeError, KeyError, ValueError):
        return None, None


def is_stale(age_seconds: float | None) -> bool:
    """Check if cached results are too old to be useful."""
    if age_seconds is None:
        return True
    return age_seconds > CACHE_MAX_AGE


def format_age(age_seconds: float) -> str:
    """Human-readable age string."""
    if age_seconds < 60:
        return "just now"
    elif age_seconds < 3600:
        return f"{int(age_seconds / 60)}m ago"
    elif age_seconds < 86400:
        return f"{int(age_seconds / 3600)}h ago"
    else:
        return f"{int(age_seconds / 86400)}d ago"


def invalidate_scan() -> None:
    """Remove the cached scan file so subsequent queries trigger a fresh scan."""
    try:
        CACHE_FILE.unlink(missing_ok=True)
    except OSError:
        pass
