"""Scan result caching — persist/load scan data between sessions.

Supports incremental rescanning: each category's root directories are
fingerprinted (recursive dir mtime) at scan time. On the next scan,
only categories whose directory trees have changed are re-scanned —
everything else is reused from cache. Think `git status`.
"""

import json
import os
import time
from pathlib import Path

from disk_cleanup.config import HOME
from disk_cleanup.scanners import Category, CleanupItem, RiskLevel, ScanResult

CACHE_DIR = HOME / ".storage"
CACHE_FILE = CACHE_DIR / "last_scan.json"
CACHE_MAX_AGE = 3600  # 1 hour — after this, results are "stale"


def _get_scanner_registry():
    """Lazy import to avoid circular dependency at module level."""
    from disk_cleanup.scanner import SCANNER_ROOTS, SCANNER_DEPTHS
    return SCANNER_ROOTS, SCANNER_DEPTHS


# ── Recursive directory fingerprinting ──────────────────────────────────────

# Cap fingerprint depth — we don't need to walk as deep as the scanner.
# Depth 2 catches changes to children and grandchildren, which covers
# the vast majority of real-world edits. Full-depth walking is too slow
# for directories like ~/Documents at depth 5-6. Users can `storage scan --full`
# if they need to force a complete rescan.
_FINGERPRINT_MAX_DEPTH = 2


def _fingerprint_roots(roots: list[Path], max_depth: int = 0) -> str:
    """Fingerprint a set of root directories by recursively walking subdirs.

    Returns a single hash string (max mtime across the tree). For depth=0,
    this just stats the root dirs themselves. For depth>0, it walks
    subdirectories up to max_depth levels, collecting the maximum mtime
    across all directories. This catches nested changes that don't
    propagate to the parent dir's mtime on macOS.

    Only stats *directories* (not individual files) to keep this fast.
    """
    max_mtime = 0.0
    effective_depth = min(max_depth, _FINGERPRINT_MAX_DEPTH)

    for root in roots:
        if not root.exists():
            continue
        try:
            max_mtime = max(max_mtime, os.stat(root).st_mtime)
        except OSError:
            continue

        if effective_depth > 0:
            max_mtime = max(max_mtime, _walk_dir_mtimes(root, effective_depth))

    return str(max_mtime)


def _walk_dir_mtimes(directory: Path, remaining_depth: int) -> float:
    """Recursively collect the max mtime of subdirectories."""
    max_mt = 0.0
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    try:
                        max_mt = max(max_mt, entry.stat(follow_symlinks=False).st_mtime)
                    except OSError:
                        continue
                    if remaining_depth > 1:
                        max_mt = max(max_mt, _walk_dir_mtimes(
                            Path(entry.path), remaining_depth - 1
                        ))
    except (OSError, PermissionError):
        pass
    return max_mt


def _fingerprint_all() -> dict[str, str]:
    """Fingerprint roots for every category.

    Returns {category_value: fingerprint_string}.
    """
    roots_map, depths_map = _get_scanner_registry()
    return {
        cat.value: _fingerprint_roots(roots, depths_map.get(cat, 0))
        for cat, roots in roots_map.items()
    }


def save_scan(result: ScanResult, categories: list[Category] | None = None) -> None:
    """Persist scan results to disk with per-category fingerprints.

    If `categories` is given, only those categories' fingerprints and items
    are updated — the rest are preserved from the existing cache (merge).
    """
    CACHE_DIR.mkdir(exist_ok=True)

    # If partial scan, merge with existing cache
    if categories:
        existing, _ = load_scan()
        existing_data = _load_raw_cache()
        if existing and existing_data:
            # Keep items from categories NOT in this scan
            rescanned_cats = {c.value for c in categories}
            kept_items = [i for i in existing.items if i.category.value not in rescanned_cats]
            result = ScanResult(
                items=kept_items + result.items,
                errors=existing.errors + result.errors,
                scan_time_seconds=result.scan_time_seconds,
            )
            # Merge fingerprints: update only rescanned categories
            old_fp = existing_data.get("fingerprints", {})
            roots_map, depths_map = _get_scanner_registry()
            new_fp = {
                cat.value: _fingerprint_roots(
                    roots_map.get(cat, []),
                    depths_map.get(cat, 0),
                )
                for cat in categories
            }
            old_fp.update(new_fp)
            fingerprints = old_fp
        else:
            fingerprints = _fingerprint_all()
    else:
        fingerprints = _fingerprint_all()

    # Sort by size
    result.items.sort(key=lambda x: x.size_bytes, reverse=True)

    data = {
        "timestamp": time.time(),
        "scan_time_seconds": result.scan_time_seconds,
        "errors": result.errors,
        "fingerprints": fingerprints,
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


def _load_raw_cache() -> dict | None:
    """Load the raw JSON cache dict (for internal merging)."""
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


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


def stale_categories() -> list[Category]:
    """Compare cached fingerprints against current directory mtimes.

    Returns a list of categories whose root directories have changed
    since the last scan — these need rescanning. If no cache exists,
    returns ALL categories.

    When a manifest exists, uses finer-grained per-path staleness
    detection and new-path discovery.  Falls back to the original
    directory-fingerprint approach when no manifest is available.
    """
    raw = _load_raw_cache()
    if not raw or "fingerprints" not in raw:
        roots_map, _ = _get_scanner_registry()
        return list(roots_map.keys())

    # Try manifest-based staleness first
    from disk_cleanup.manifest import load_manifest, stale_manifest_paths, discover_new_paths

    manifest = load_manifest()
    if manifest:
        stale: list[Category] = []
        new_paths = discover_new_paths()
        roots_map, _ = _get_scanner_registry()

        for cat in roots_map:
            if stale_manifest_paths(cat) or cat in new_paths:
                stale.append(cat)
        return stale

    # Fallback: original fingerprint comparison
    old_fps = raw["fingerprints"]
    stale = []

    roots_map, depths_map = _get_scanner_registry()
    for cat, roots in roots_map.items():
        old_fp = old_fps.get(cat.value)
        new_fp = _fingerprint_roots(roots, depths_map.get(cat, 0))

        if old_fp != new_fp:
            stale.append(cat)

    return stale


def scan_freshness() -> dict:
    """Return a summary of cache freshness for display.

    Returns:
        {
            "has_cache": bool,
            "age": float | None,
            "age_display": str,
            "total_categories": int,
            "stale_categories": list[str],
            "fresh_categories": list[str],
            "stale_count": int,
            "is_fully_fresh": bool,
        }
    """
    result, age = load_scan()
    roots_map, _ = _get_scanner_registry()
    if result is None:
        return {
            "has_cache": False,
            "age": None,
            "age_display": "no scan data",
            "total_categories": len(roots_map),
            "stale_categories": [c.value for c in roots_map],
            "fresh_categories": [],
            "stale_count": len(roots_map),
            "is_fully_fresh": False,
        }

    stale = stale_categories()
    all_cats = list(roots_map.keys())
    fresh = [c for c in all_cats if c not in stale]

    return {
        "has_cache": True,
        "age": age,
        "age_display": format_age(age) if age else "unknown",
        "total_categories": len(all_cats),
        "stale_categories": [c.value for c in stale],
        "fresh_categories": [c.value for c in fresh],
        "stale_count": len(stale),
        "is_fully_fresh": len(stale) == 0,
    }


def invalidate_categories(categories: list[Category]) -> None:
    """Mark specific categories as stale by zeroing their fingerprints.

    More surgical than invalidate_scan() — preserves cache for unaffected categories.
    """
    raw = _load_raw_cache()
    if not raw or "fingerprints" not in raw:
        return

    for cat in categories:
        if cat.value in raw["fingerprints"]:
            # Zero out the fingerprint so it's detected as stale
            raw["fingerprints"][cat.value] = {}

    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(raw, f)
    except OSError:
        pass


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
