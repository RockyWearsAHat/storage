"""Scanner for log files and crash reports."""

from pathlib import Path

from disk_cleanup.config import Config, HOME
from disk_cleanup.scanners import Category, CleanupItem, RiskLevel
from disk_cleanup.utils import dir_size, get_file_age_days, is_excluded


LOG_DIRS = [
    (HOME / "Library" / "Logs", "User Application Logs"),
    (HOME / "Library" / "Logs" / "DiagnosticReports", "Crash Reports"),
]

MIN_REPORT_SIZE = 1 * 1024 * 1024  # 1 MB


def scan_logs(config: Config) -> list[CleanupItem]:
    """Find log files and crash reports."""
    items: list[CleanupItem] = []

    # Scan user logs directory
    logs_dir = HOME / "Library" / "Logs"
    if logs_dir.exists() and not is_excluded(logs_dir, config):
        # Individual log subdirectories
        try:
            for entry in logs_dir.iterdir():
                if is_excluded(entry, config):
                    continue
                if entry.is_dir():
                    size = dir_size(entry)
                    if size >= MIN_REPORT_SIZE:
                        items.append(CleanupItem(
                            path=entry,
                            size_bytes=size,
                            category=Category.LOGS,
                            risk=RiskLevel.SAFE,
                            reason=f"Log directory ({entry.name})",
                            is_directory=True,
                        ))
                elif entry.is_file() and entry.suffix in (".log", ".txt"):
                    try:
                        size = entry.stat().st_size
                        if size >= MIN_REPORT_SIZE:
                            age = get_file_age_days(entry)
                            items.append(CleanupItem(
                                path=entry,
                                size_bytes=size,
                                category=Category.LOGS,
                                risk=RiskLevel.SAFE,
                                reason=f"Log file, {age:.0f} days old",
                            ))
                    except OSError:
                        pass
        except PermissionError:
            pass

    # Crash reports
    crash_dir = HOME / "Library" / "Logs" / "DiagnosticReports"
    if crash_dir.exists() and not is_excluded(crash_dir, config):
        size = dir_size(crash_dir)
        if size >= MIN_REPORT_SIZE:
            items.append(CleanupItem(
                path=crash_dir,
                size_bytes=size,
                category=Category.LOGS,
                risk=RiskLevel.SAFE,
                reason="Crash reports & diagnostic data",
                is_directory=True,
            ))

    return items
