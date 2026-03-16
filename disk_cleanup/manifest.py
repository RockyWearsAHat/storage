"""Adaptive manifest — learns which paths to check from previous scans.

After the first scan, the manifest records every discovered path grouped by
category plus the parent directories ("watched parents") where new items
might appear.  On subsequent scans the manifest provides finer-grained
staleness checking (per-path mtime) and discovers new directories that
weren't present during the last scan.

The manifest is optional.  If missing or corrupt the system falls back
to the static SCANNER_ROOTS exactly as before.
"""

import json
import os
import tempfile
import time
from pathlib import Path

from disk_cleanup.config import HOME
from disk_cleanup.scanners import Category, ScanResult

MANIFEST_DIR = HOME / ".storage"
MANIFEST_FILE = MANIFEST_DIR / "manifest.json"

MANIFEST_VERSION = 1


# ── Load / Save ─────────────────────────────────────────────────────────────

def load_manifest() -> dict | None:
    """Load the manifest from ~/.storage/manifest.json.

    Returns None if the file is missing, unreadable, or has an
    incompatible version.
    """
    if not MANIFEST_FILE.exists():
        return None
    try:
        with open(MANIFEST_FILE, "r") as f:
            data = json.load(f)
        if data.get("version") != MANIFEST_VERSION:
            return None
        return data
    except (json.JSONDecodeError, OSError, KeyError):
        return None


def save_manifest(manifest: dict) -> None:
    """Atomically write the manifest to disk."""
    MANIFEST_DIR.mkdir(exist_ok=True)
    manifest["updated"] = time.time()

    # Write to a temp file in the same directory, then rename for atomicity.
    fd, tmp = tempfile.mkstemp(dir=MANIFEST_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(manifest, f, indent=2)
        os.replace(tmp, MANIFEST_FILE)
    except OSError:
        # Clean up the temp file on failure
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ── Build / Update ──────────────────────────────────────────────────────────

def _stat_path(p: Path) -> dict:
    """Stat a single path and return a manifest entry."""
    try:
        st = p.stat()
        return {
            "path": str(p),
            "mtime": st.st_mtime,
            "size_bytes": st.st_size if p.is_file() else 0,
            "exists": True,
        }
    except OSError:
        return {
            "path": str(p),
            "mtime": 0.0,
            "size_bytes": 0,
            "exists": False,
        }


def build_manifest_from_scan(result: ScanResult) -> dict:
    """Build a fresh manifest from a full scan result.

    Extracts unique parent directories from every item (grouped by
    category) and records the static SCANNER_ROOTS as watched_parents.
    """
    from disk_cleanup.scanner import SCANNER_ROOTS

    now = time.time()
    paths: dict[str, list[dict]] = {}
    watched: dict[str, list[str]] = {}

    # Group unique parent dirs by category
    parents_by_cat: dict[str, set[str]] = {}
    for item in result.items:
        cat = item.category.value
        parent = str(item.path.parent)
        parents_by_cat.setdefault(cat, set()).add(parent)

    for cat_val, parent_set in parents_by_cat.items():
        entries = []
        for p_str in sorted(parent_set):
            entries.append(_stat_path(Path(p_str)))
        paths[cat_val] = entries

    # Record SCANNER_ROOTS as watched parents
    for cat, roots in SCANNER_ROOTS.items():
        root_strs = [str(r) for r in roots if r.exists()]
        if root_strs:
            watched[cat.value] = root_strs

    manifest = {
        "version": MANIFEST_VERSION,
        "created": now,
        "updated": now,
        "last_full_discovery": now,
        "paths": paths,
        "watched_parents": watched,
    }
    return manifest


def update_manifest_after_scan(
    manifest: dict,
    result: ScanResult,
    categories: list[Category],
) -> dict:
    """Merge new scan results into an existing manifest.

    Only the given categories are refreshed.  Paths that no longer
    exist are pruned.  Newly discovered paths are added.
    """
    paths = manifest.setdefault("paths", {})

    # Collect new parents from the scan result (only for rescanned cats)
    rescanned_cats = {c.value for c in categories}
    new_parents: dict[str, set[str]] = {}
    for item in result.items:
        cat = item.category.value
        if cat in rescanned_cats:
            new_parents.setdefault(cat, set()).add(str(item.path.parent))

    # Merge: replace entries for rescanned categories, keep others as-is
    for cat_val in rescanned_cats:
        parent_set = new_parents.get(cat_val, set())
        # Also keep existing entries that still exist
        for entry in paths.get(cat_val, []):
            parent_set.add(entry["path"])
        # Re-stat everything
        entries = []
        for p_str in sorted(parent_set):
            p = Path(p_str)
            if p.exists():
                entries.append(_stat_path(p))
        paths[cat_val] = entries

    # Prune entries in non-rescanned categories that no longer exist
    for cat_val in list(paths.keys()):
        if cat_val in rescanned_cats:
            continue
        paths[cat_val] = [e for e in paths[cat_val] if Path(e["path"]).exists()]

    manifest["paths"] = paths
    return manifest


# ── Query ───────────────────────────────────────────────────────────────────

def get_scan_targets(category: Category) -> list[Path]:
    """Return known paths for a category from the manifest.

    Falls back to SCANNER_ROOTS if no manifest or no entries for
    the category.
    """
    from disk_cleanup.scanner import SCANNER_ROOTS

    manifest = load_manifest()
    if manifest:
        entries = manifest.get("paths", {}).get(category.value, [])
        if entries:
            return [Path(e["path"]) for e in entries if e.get("exists", True)]

    return list(SCANNER_ROOTS.get(category, []))


def stale_manifest_paths(category: Category) -> list[Path]:
    """Return manifest paths whose mtime has changed since last recorded.

    If the manifest is missing or has no data for this category,
    returns an empty list (caller should fall back to full-category check).
    """
    manifest = load_manifest()
    if not manifest:
        return []

    entries = manifest.get("paths", {}).get(category.value, [])
    if not entries:
        return []

    stale: list[Path] = []
    for entry in entries:
        p = Path(entry["path"])
        if not p.exists():
            # Path gone — category needs rescan to prune it
            stale.append(p)
            continue
        try:
            current_mtime = p.stat().st_mtime
        except OSError:
            stale.append(p)
            continue
        if current_mtime != entry.get("mtime", 0.0):
            stale.append(p)

    return stale


def discover_new_paths() -> dict[Category, list[Path]]:
    """Scan watched_parents for directories not yet in the manifest.

    This is cheap — just an iterdir() on each watched parent.
    Returns newly discovered paths grouped by category.

    A child directory is considered "new" only if it is NOT equal to, or
    a subdirectory of, any already-known manifest path for this category.
    This avoids false positives when a watched parent is itself a known
    path (e.g. ~/Library/Caches is both tracked and watched).
    """
    manifest = load_manifest()
    if not manifest:
        return {}

    watched = manifest.get("watched_parents", {})
    known_paths = manifest.get("paths", {})

    new: dict[Category, list[Path]] = {}

    for cat_val, parent_dirs in watched.items():
        try:
            cat = Category(cat_val)
        except ValueError:
            continue

        # Build the set of known Path objects for containment checks
        known_set = set()
        for e in known_paths.get(cat_val, []):
            known_set.add(Path(e["path"]))

        for parent_str in parent_dirs:
            parent = Path(parent_str)
            if not parent.is_dir():
                continue
            try:
                for child in parent.iterdir():
                    if not child.is_dir():
                        continue
                    # "New" means not equal to, and not under, any known path
                    if child in known_set:
                        continue
                    if any(child == kp or _is_subpath(child, kp) or _is_subpath(kp, child)
                           for kp in known_set):
                        continue
                    new.setdefault(cat, []).append(child)
            except (OSError, PermissionError):
                continue

    return new


def _is_subpath(path: Path, parent: Path) -> bool:
    """Check if path is under parent (without resolving symlinks)."""
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
