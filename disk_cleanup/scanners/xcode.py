"""Scanner for Xcode-related data (DerivedData, archives, simulators)."""

from pathlib import Path

from disk_cleanup.config import Config, HOME
from disk_cleanup.scanners import Category, CleanupItem, RiskLevel
from disk_cleanup.utils import dir_size, get_file_age_days, is_excluded

MIN_SIZE = 50 * 1024 * 1024  # 50 MB

# Fingerprinting: roots and walk depth for incremental scan detection
SCAN_ROOTS = [
    HOME / "Library" / "Developer" / "Xcode" / "DerivedData",
    HOME / "Library" / "Developer" / "Xcode" / "Archives",
    HOME / "Library" / "Developer" / "Xcode" / "iOS DeviceSupport",
    HOME / "Library" / "Developer" / "Xcode" / "watchOS DeviceSupport",
    HOME / "Library" / "Developer" / "CoreSimulator" / "Devices",
    HOME / "Library" / "Developer" / "CoreSimulator" / "Caches",
]
SCAN_DEPTH = 1


def scan_xcode(config: Config) -> list[CleanupItem]:
    """Find Xcode-generated data that can be cleaned."""
    items: list[CleanupItem] = []

    # DerivedData — always safe to delete, regenerated on build
    derived = HOME / "Library" / "Developer" / "Xcode" / "DerivedData"
    if derived.exists() and not is_excluded(derived, config):
        try:
            for entry in derived.iterdir():
                if entry.is_dir():
                    size = dir_size(entry)
                    if size >= MIN_SIZE:
                        items.append(CleanupItem(
                            path=entry,
                            size_bytes=size,
                            category=Category.XCODE,
                            risk=RiskLevel.SAFE,
                            reason=f"Xcode DerivedData — regenerated on next build",
                            is_directory=True,
                        ))
        except PermissionError:
            pass

    # Archives — old build archives
    archives = HOME / "Library" / "Developer" / "Xcode" / "Archives"
    if archives.exists() and not is_excluded(archives, config):
        size = dir_size(archives)
        if size >= MIN_SIZE:
            items.append(CleanupItem(
                path=archives,
                size_bytes=size,
                category=Category.XCODE,
                risk=RiskLevel.MEDIUM,
                reason="Xcode build archives — may contain old app builds",
                is_directory=True,
            ))

    # iOS DeviceSupport — support files for connected devices
    device_support = HOME / "Library" / "Developer" / "Xcode" / "iOS DeviceSupport"
    if device_support.exists() and not is_excluded(device_support, config):
        size = dir_size(device_support)
        if size >= MIN_SIZE:
            items.append(CleanupItem(
                path=device_support,
                size_bytes=size,
                category=Category.XCODE,
                risk=RiskLevel.LOW,
                reason="iOS device support files — re-downloaded when needed",
                is_directory=True,
            ))

    # watchOS DeviceSupport
    watch_support = HOME / "Library" / "Developer" / "Xcode" / "watchOS DeviceSupport"
    if watch_support.exists() and not is_excluded(watch_support, config):
        size = dir_size(watch_support)
        if size >= MIN_SIZE:
            items.append(CleanupItem(
                path=watch_support,
                size_bytes=size,
                category=Category.XCODE,
                risk=RiskLevel.LOW,
                reason="watchOS device support files",
                is_directory=True,
            ))

    # CoreSimulator devices
    sim_devices = HOME / "Library" / "Developer" / "CoreSimulator" / "Devices"
    if sim_devices.exists() and not is_excluded(sim_devices, config):
        size = dir_size(sim_devices)
        if size >= MIN_SIZE:
            items.append(CleanupItem(
                path=sim_devices,
                size_bytes=size,
                category=Category.XCODE,
                risk=RiskLevel.LOW,
                reason="Simulator device data — recreated from Xcode",
                is_directory=True,
            ))

    # CoreSimulator caches
    sim_caches = HOME / "Library" / "Developer" / "CoreSimulator" / "Caches"
    if sim_caches.exists() and not is_excluded(sim_caches, config):
        size = dir_size(sim_caches)
        if size >= MIN_SIZE:
            items.append(CleanupItem(
                path=sim_caches,
                size_bytes=size,
                category=Category.XCODE,
                risk=RiskLevel.SAFE,
                reason="Simulator caches",
                is_directory=True,
            ))

    return items
