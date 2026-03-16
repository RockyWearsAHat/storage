"""Core scanning engine — orchestrates all category scanners.

Supports incremental rescanning: only categories whose root directories
have changed since the last scan are re-run. Fresh categories are
loaded from cache.
"""

import time
from typing import Callable

from disk_cleanup.config import Config
from disk_cleanup.scanners import Category, CleanupItem, ScanResult

from disk_cleanup.scanners.caches import scan_caches
from disk_cleanup.scanners.logs import scan_logs
from disk_cleanup.scanners.downloads import scan_downloads
from disk_cleanup.scanners.screenshots import scan_screenshots
from disk_cleanup.scanners.dev_artifacts import scan_dev_artifacts
from disk_cleanup.scanners.git_repos import scan_git_repos
from disk_cleanup.scanners.xcode import scan_xcode
from disk_cleanup.scanners.large_files import scan_large_files
from disk_cleanup.scanners.duplicates import scan_duplicates
from disk_cleanup.scanners.trash import scan_trash
from disk_cleanup.scanners.brew import scan_brew
from disk_cleanup.scanners.model_caches import scan_model_caches
from disk_cleanup.scanners.app_data import scan_messages, scan_containers
from disk_cleanup.scanners.applications import scan_applications
from disk_cleanup.scanners.save_files import scan_save_files

# Import roots+depth from each scanner module
from disk_cleanup.scanners import caches as _m_caches
from disk_cleanup.scanners import logs as _m_logs
from disk_cleanup.scanners import downloads as _m_downloads
from disk_cleanup.scanners import screenshots as _m_screenshots
from disk_cleanup.scanners import dev_artifacts as _m_dev
from disk_cleanup.scanners import git_repos as _m_git
from disk_cleanup.scanners import xcode as _m_xcode
from disk_cleanup.scanners import large_files as _m_large
from disk_cleanup.scanners import duplicates as _m_dupes
from disk_cleanup.scanners import trash as _m_trash
from disk_cleanup.scanners import brew as _m_brew
from disk_cleanup.scanners import model_caches as _m_models
from disk_cleanup.scanners import app_data as _m_app
from disk_cleanup.scanners import applications as _m_apps
from disk_cleanup.scanners import save_files as _m_save


# Registry of all scanners
SCANNERS: dict[Category, Callable[[Config], list[CleanupItem]]] = {
    Category.TRASH: scan_trash,
    Category.CACHES: scan_caches,
    Category.LOGS: scan_logs,
    Category.DOWNLOADS: scan_downloads,
    Category.SCREENSHOTS: scan_screenshots,
    Category.DEV_ARTIFACTS: scan_dev_artifacts,
    Category.GIT_REPOS: scan_git_repos,
    Category.XCODE: scan_xcode,
    Category.LARGE_FILES: scan_large_files,
    Category.DUPLICATES: scan_duplicates,
    Category.BREW: scan_brew,
    Category.MODEL_CACHES: scan_model_caches,
    Category.MESSAGES: scan_messages,
    Category.CONTAINERS: scan_containers,
    Category.APPLICATIONS: scan_applications,
    Category.SAVE_FILES: scan_save_files,
}

# Root paths + walk depth per category — used by cache.py for fingerprinting.
# Each scanner module exports SCAN_ROOTS (list[Path]) and SCAN_DEPTH (int).
from pathlib import Path

SCANNER_ROOTS: dict[Category, list[Path]] = {
    Category.CACHES:        _m_caches.SCAN_ROOTS,
    Category.LOGS:          _m_logs.SCAN_ROOTS,
    Category.DOWNLOADS:     _m_downloads.SCAN_ROOTS,
    Category.SCREENSHOTS:   _m_screenshots.SCAN_ROOTS,
    Category.DEV_ARTIFACTS: _m_dev.SCAN_ROOTS,
    Category.GIT_REPOS:     _m_git.SCAN_ROOTS,
    Category.XCODE:         _m_xcode.SCAN_ROOTS,
    Category.LARGE_FILES:   _m_large.SCAN_ROOTS,
    Category.DUPLICATES:    _m_dupes.SCAN_ROOTS,
    Category.TRASH:         _m_trash.SCAN_ROOTS,
    Category.BREW:          _m_brew.SCAN_ROOTS,
    Category.MODEL_CACHES:  _m_models.SCAN_ROOTS,
    Category.MESSAGES:      _m_app.SCAN_ROOTS_MESSAGES,
    Category.CONTAINERS:    _m_app.SCAN_ROOTS_CONTAINERS,
    Category.APPLICATIONS:  _m_apps.SCAN_ROOTS,
    Category.SAVE_FILES:    _m_save.SCAN_ROOTS,
}

SCANNER_DEPTHS: dict[Category, int] = {
    Category.CACHES:        _m_caches.SCAN_DEPTH,
    Category.LOGS:          _m_logs.SCAN_DEPTH,
    Category.DOWNLOADS:     _m_downloads.SCAN_DEPTH,
    Category.SCREENSHOTS:   _m_screenshots.SCAN_DEPTH,
    Category.DEV_ARTIFACTS: _m_dev.SCAN_DEPTH,
    Category.GIT_REPOS:     _m_git.SCAN_DEPTH,
    Category.XCODE:         _m_xcode.SCAN_DEPTH,
    Category.LARGE_FILES:   _m_large.SCAN_DEPTH,
    Category.DUPLICATES:    _m_dupes.SCAN_DEPTH,
    Category.TRASH:         _m_trash.SCAN_DEPTH,
    Category.BREW:          _m_brew.SCAN_DEPTH,
    Category.MODEL_CACHES:  _m_models.SCAN_DEPTH,
    Category.MESSAGES:      _m_app.SCAN_DEPTH_MESSAGES,
    Category.CONTAINERS:    _m_app.SCAN_DEPTH_CONTAINERS,
    Category.APPLICATIONS:  _m_apps.SCAN_DEPTH,
    Category.SAVE_FILES:    _m_save.SCAN_DEPTH,
}


def run_scan(
    config: Config,
    categories: list[Category] | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> ScanResult:
    """Run all (or selected) scanners and return aggregated results."""
    result = ScanResult()
    start = time.time()

    scanners_to_run = SCANNERS
    if categories:
        scanners_to_run = {k: v for k, v in SCANNERS.items() if k in categories}

    for category, scanner_fn in scanners_to_run.items():
        if progress_callback:
            from disk_cleanup.scanners import CATEGORY_LABELS
            progress_callback(CATEGORY_LABELS.get(category, category.value))

        try:
            items = scanner_fn(config)
            result.items.extend(items)
        except Exception as e:
            result.errors.append(f"{category.value}: {e}")

    result.scan_time_seconds = time.time() - start

    # Sort by size descending
    result.items.sort(key=lambda x: x.size_bytes, reverse=True)

    return result


def run_incremental_scan(
    config: Config,
    progress_callback: Callable[[str], None] | None = None,
    status_callback: Callable[[int, int], None] | None = None,
) -> tuple[ScanResult, list[Category], list[Category]]:
    """Smart incremental scan — only rescan categories with changed root dirs.

    Returns (result, rescanned_categories, skipped_categories).
    The result contains fresh data for changed categories merged with
    cached data for unchanged ones.

    After scanning, the manifest is built (full scan) or updated (partial)
    to enable finer-grained staleness detection on the next run.
    """
    from disk_cleanup.cache import stale_categories, save_scan, load_scan
    from disk_cleanup.manifest import (
        load_manifest, build_manifest_from_scan,
        update_manifest_after_scan, discover_new_paths, save_manifest,
    )

    stale = stale_categories()

    # Discover new paths via the manifest and add their categories
    new_paths = discover_new_paths()
    for cat in new_paths:
        if cat not in stale:
            stale.append(cat)

    if status_callback:
        status_callback(len(stale), len(SCANNERS))

    if not stale:
        # Everything is fresh — just return cache
        cached, _ = load_scan()
        if cached:
            return cached, [], list(SCANNERS.keys())
        # Cache gone somehow — full scan
        stale = list(SCANNERS.keys())

    is_full_scan = set(stale) == set(SCANNERS.keys())

    # Run only stale scanners
    result = run_scan(config, categories=stale, progress_callback=progress_callback)

    # Save with merge (keeps fresh categories from cache, updates stale ones)
    save_scan(result, categories=stale)

    # Update the manifest
    manifest = load_manifest()
    if is_full_scan or manifest is None:
        # Reload the full merged result for manifest building
        merged, _ = load_scan()
        if merged:
            manifest = build_manifest_from_scan(merged)
        else:
            manifest = build_manifest_from_scan(result)
        save_manifest(manifest)
    else:
        manifest = update_manifest_after_scan(manifest, result, stale)
        save_manifest(manifest)

    # Reload the merged result
    merged, _ = load_scan()
    if merged:
        fresh = [c for c in SCANNERS if c not in stale]
        return merged, stale, fresh

    return result, stale, []
