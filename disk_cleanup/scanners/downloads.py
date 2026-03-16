"""Scanner for old downloads."""

from pathlib import Path

from disk_cleanup.config import Config, HOME
from disk_cleanup.scanners import Category, CleanupItem, RiskLevel
from disk_cleanup.utils import get_file_age_days, is_excluded

# Installers and disk images are almost always safe to remove
INSTALLER_EXTENSIONS = frozenset({
    ".dmg", ".pkg", ".iso", ".app", ".mpkg",
})

ARCHIVE_EXTENSIONS = frozenset({
    ".zip", ".gz", ".tar", ".tgz", ".bz2", ".xz", ".rar", ".7z",
})

MIN_SIZE = 1 * 1024 * 1024  # 1 MB


def scan_downloads(config: Config) -> list[CleanupItem]:
    """Find old or unnecessary files in Downloads."""
    items: list[CleanupItem] = []
    downloads = HOME / "Downloads"

    if not downloads.exists() or is_excluded(downloads, config):
        return items

    try:
        for entry in downloads.iterdir():
            if entry.name.startswith(".") or is_excluded(entry, config):
                continue

            try:
                if entry.is_symlink():
                    continue

                age_days = get_file_age_days(entry)

                if entry.is_file():
                    size = entry.stat().st_size
                    if size < MIN_SIZE:
                        continue

                    ext = entry.suffix.lower()

                    # Installers/disk images — always flag
                    if ext in INSTALLER_EXTENSIONS:
                        items.append(CleanupItem(
                            path=entry,
                            size_bytes=size,
                            category=Category.DOWNLOADS,
                            risk=RiskLevel.SAFE,
                            reason=f"Installer/disk image, {age_days:.0f} days old",
                            metadata={"age_days": age_days, "type": "installer"},
                        ))
                    # Archives — flag if old
                    elif ext in ARCHIVE_EXTENSIONS and age_days > config.download_age_days / 2:
                        items.append(CleanupItem(
                            path=entry,
                            size_bytes=size,
                            category=Category.DOWNLOADS,
                            risk=RiskLevel.LOW,
                            reason=f"Archive, {age_days:.0f} days old",
                            metadata={"age_days": age_days, "type": "archive"},
                        ))
                    # Everything else — flag if old
                    elif age_days > config.download_age_days:
                        risk = RiskLevel.LOW if age_days > config.download_age_days * 2 else RiskLevel.MEDIUM
                        items.append(CleanupItem(
                            path=entry,
                            size_bytes=size,
                            category=Category.DOWNLOADS,
                            risk=risk,
                            reason=f"Old download ({ext or 'no ext'}), {age_days:.0f} days old",
                            metadata={"age_days": age_days, "type": "old_file"},
                        ))

                elif entry.is_dir():
                    # Flag old directories in Downloads
                    if age_days > config.download_age_days:
                        from disk_cleanup.utils import dir_size
                        size = dir_size(entry)
                        if size >= MIN_SIZE:
                            items.append(CleanupItem(
                                path=entry,
                                size_bytes=size,
                                category=Category.DOWNLOADS,
                                risk=RiskLevel.MEDIUM,
                                reason=f"Old download folder, {age_days:.0f} days old",
                                is_directory=True,
                                metadata={"age_days": age_days, "type": "old_dir"},
                            ))
            except OSError:
                pass
    except PermissionError:
        pass

    return items
