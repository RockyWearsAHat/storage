"""Core scanning engine — orchestrates all category scanners."""

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
