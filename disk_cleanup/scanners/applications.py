"""Scanner for installed applications — flags large .app bundles."""

from pathlib import Path

from disk_cleanup.config import Config, HOME
from disk_cleanup.scanners import Category, CleanupItem, RiskLevel
from disk_cleanup.utils import dir_size, is_excluded


APP_DIRS = [
    Path("/Applications"),
    HOME / "Applications",
]

# Threshold: flag apps larger than this
MIN_SIZE = 500 * 1024 * 1024  # 500 MB

# System apps that should never be flagged
SYSTEM_APPS = frozenset({
    "Safari.app", "Mail.app", "Messages.app", "FaceTime.app",
    "Calendar.app", "Photos.app", "Music.app", "TV.app", "News.app",
    "Stocks.app", "Maps.app", "Notes.app", "Reminders.app",
    "Finder.app", "System Preferences.app", "System Settings.app",
    "App Store.app", "Preview.app", "TextEdit.app", "QuickTime Player.app",
    "Automator.app", "Terminal.app", "Activity Monitor.app",
    "Disk Utility.app", "Console.app", "Keychain Access.app",
    "Font Book.app", "Migration Assistant.app", "Bluetooth File Exchange.app",
    "Screenshot.app", "Siri.app", "Home.app", "Shortcuts.app",
    "Clock.app", "Weather.app", "Freeform.app", "Books.app",
})


def scan_applications(config: Config) -> list[CleanupItem]:
    """Find large installed applications."""
    items: list[CleanupItem] = []

    for app_dir in APP_DIRS:
        if not app_dir.exists() or is_excluded(app_dir, config):
            continue

        try:
            for entry in app_dir.iterdir():
                if not entry.name.endswith(".app"):
                    continue
                if entry.name in SYSTEM_APPS:
                    continue
                if entry.is_symlink() or is_excluded(entry, config):
                    continue

                try:
                    size = dir_size(entry)
                except PermissionError:
                    continue

                if size < MIN_SIZE:
                    continue

                # Apps you installed yourself are medium risk — you might still use them
                risk = RiskLevel.MEDIUM

                # Build a readable name: "Xcode.app" → "Xcode"
                app_name = entry.stem

                items.append(CleanupItem(
                    path=entry,
                    size_bytes=size,
                    category=Category.APPLICATIONS,
                    risk=risk,
                    reason=f"{app_name} — large application",
                    is_directory=True,
                    metadata={"app_name": app_name},
                ))
        except PermissionError:
            pass

    return items
