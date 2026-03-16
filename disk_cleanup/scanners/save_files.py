"""Scanner for large application save/project files.

Targets heavyweight creative apps like Photoshop, Blender, Logic Pro,
Final Cut, Premiere, After Effects, etc. whose project and scratch files
can quietly consume tens of gigabytes.
"""

from pathlib import Path

from disk_cleanup.config import Config, HOME
from disk_cleanup.scanners import Category, CleanupItem, RiskLevel
from disk_cleanup.utils import dir_size, get_file_age_days, is_excluded


# ------------------------------------------------------------------
#  Per-app save file / scratch locations
#  (path, label, extensions-or-None, is_directory)
# ------------------------------------------------------------------

# Extension sets for file-based scanning
PHOTOSHOP_EXTS = frozenset({".psd", ".psb", ".psdt"})
BLENDER_EXTS = frozenset({".blend", ".blend1", ".blend2"})
ILLUSTRATOR_EXTS = frozenset({".ai", ".ait"})
INDESIGN_EXTS = frozenset({".indd", ".indt", ".idml"})
PREMIERE_EXTS = frozenset({".prproj", ".prel"})
AFTER_EFFECTS_EXTS = frozenset({".aep", ".aet"})
LOGIC_EXTS = frozenset({".logicx",})
GARAGEBAND_EXTS = frozenset({".band",})
SKETCH_EXTS = frozenset({".sketch",})
FIGMA_EXTS = frozenset({".fig",})
PROCREATE_EXTS = frozenset({".procreate",})
DAVINCI_EXTS = frozenset({".drp",})
MAYA_EXTS = frozenset({".ma", ".mb"})
CINEMA4D_EXTS = frozenset({".c4d",})
UNITY_EXTS = frozenset({".unity",})

ALL_PROJECT_EXTS: dict[str, str] = {}
for ext in PHOTOSHOP_EXTS:
    ALL_PROJECT_EXTS[ext] = "Photoshop"
for ext in BLENDER_EXTS:
    ALL_PROJECT_EXTS[ext] = "Blender"
for ext in ILLUSTRATOR_EXTS:
    ALL_PROJECT_EXTS[ext] = "Illustrator"
for ext in INDESIGN_EXTS:
    ALL_PROJECT_EXTS[ext] = "InDesign"
for ext in PREMIERE_EXTS:
    ALL_PROJECT_EXTS[ext] = "Premiere Pro"
for ext in AFTER_EFFECTS_EXTS:
    ALL_PROJECT_EXTS[ext] = "After Effects"
for ext in LOGIC_EXTS:
    ALL_PROJECT_EXTS[ext] = "Logic Pro"
for ext in GARAGEBAND_EXTS:
    ALL_PROJECT_EXTS[ext] = "GarageBand"
for ext in SKETCH_EXTS:
    ALL_PROJECT_EXTS[ext] = "Sketch"
for ext in FIGMA_EXTS:
    ALL_PROJECT_EXTS[ext] = "Figma"
for ext in PROCREATE_EXTS:
    ALL_PROJECT_EXTS[ext] = "Procreate"
for ext in DAVINCI_EXTS:
    ALL_PROJECT_EXTS[ext] = "DaVinci Resolve"
for ext in MAYA_EXTS:
    ALL_PROJECT_EXTS[ext] = "Maya"
for ext in CINEMA4D_EXTS:
    ALL_PROJECT_EXTS[ext] = "Cinema 4D"
for ext in UNITY_EXTS:
    ALL_PROJECT_EXTS[ext] = "Unity"


# Directories that are scratch/cache for creative apps (safe to delete)
APP_SCRATCH_DIRS = [
    (HOME / "Library" / "Application Support" / "Adobe" / "Common" / "Media Cache Files",
     "Adobe Media Cache", RiskLevel.SAFE),
    (HOME / "Library" / "Application Support" / "Adobe" / "Common" / "Media Cache",
     "Adobe Media Cache", RiskLevel.SAFE),
    (HOME / "Library" / "Application Support" / "Adobe" / "Common" / "Peak Files",
     "Adobe Audio Peak cache", RiskLevel.SAFE),
    (HOME / "Library" / "Caches" / "com.adobe.crashreporter",
     "Adobe crash reports", RiskLevel.SAFE),
    (HOME / "Library" / "Application Support" / "Blender",
     "Blender app data (scripts, addons, cache)", RiskLevel.LOW),
    (HOME / "Library" / "Application Support" / "Adobe" / "Adobe Photoshop 2025" / "AutoRecover",
     "Photoshop auto-recover scratch", RiskLevel.LOW),
    (HOME / "Library" / "Application Support" / "Adobe" / "Adobe Photoshop 2024" / "AutoRecover",
     "Photoshop auto-recover scratch", RiskLevel.LOW),
    (HOME / "Movies" / "Final Cut Backups",
     "Final Cut Pro backups", RiskLevel.LOW),
    (HOME / "Movies" / "Motion Templates.localized",
     "Motion templates", RiskLevel.LOW),
]

# Where to look for project files
PROJECT_SCAN_DIRS = [
    HOME / "Desktop",
    HOME / "Documents",
    HOME / "Downloads",
    HOME / "Movies",
    HOME / "Music",
]

# Minimum size to report (50 MB for project files, 100 MB for dirs)
MIN_FILE_SIZE = 50 * 1024 * 1024
MIN_DIR_SIZE = 100 * 1024 * 1024
MAX_DEPTH = 5


def scan_save_files(config: Config) -> list[CleanupItem]:
    """Find large creative-app project files and scratch directories."""
    items: list[CleanupItem] = []

    # 1. Scan known scratch/cache directories
    for path, label, risk in APP_SCRATCH_DIRS:
        if not path.exists() or is_excluded(path, config):
            continue
        size = dir_size(path)
        if size >= MIN_DIR_SIZE:
            items.append(CleanupItem(
                path=path,
                size_bytes=size,
                category=Category.SAVE_FILES,
                risk=risk,
                reason=f"{label} — can be regenerated",
                is_directory=True,
                metadata={"type": "scratch"},
            ))

    # 2. Scan common directories for large project files
    for root_dir in PROJECT_SCAN_DIRS:
        if not root_dir.exists():
            continue
        _scan_dir_for_projects(root_dir, config, items, depth=0)

    return items


def _scan_dir_for_projects(
    directory: Path,
    config: Config,
    items: list[CleanupItem],
    depth: int,
) -> None:
    """Recursively scan for large project files."""
    if depth > MAX_DEPTH or is_excluded(directory, config):
        return

    try:
        for entry in directory.iterdir():
            if entry.is_symlink() or is_excluded(entry, config):
                continue

            if entry.is_file():
                ext = entry.suffix.lower()
                app_name = ALL_PROJECT_EXTS.get(ext)
                if not app_name:
                    continue

                try:
                    size = entry.stat().st_size
                except OSError:
                    continue

                if size < MIN_FILE_SIZE:
                    continue

                age = get_file_age_days(entry)
                # Old project files are lower risk (less likely still in use)
                if age > 365:
                    risk = RiskLevel.LOW
                    age_note = f", {age:.0f} days old — likely unused"
                elif age > 90:
                    risk = RiskLevel.MEDIUM
                    age_note = f", {age:.0f} days old"
                else:
                    risk = RiskLevel.HIGH
                    age_note = ", recently modified"

                items.append(CleanupItem(
                    path=entry,
                    size_bytes=size,
                    category=Category.SAVE_FILES,
                    risk=risk,
                    reason=f"{app_name} project file ({ext}){age_note}",
                    metadata={"app": app_name, "age_days": age},
                ))

            elif entry.is_dir() and not entry.name.startswith("."):
                # Logic Pro / GarageBand projects are directories
                ext = entry.suffix.lower()
                app_name = ALL_PROJECT_EXTS.get(ext)
                if app_name:
                    try:
                        size = dir_size(entry)
                    except PermissionError:
                        continue
                    if size >= MIN_FILE_SIZE:
                        age = get_file_age_days(entry)
                        if age > 365:
                            risk = RiskLevel.LOW
                            age_note = f", {age:.0f} days old — likely unused"
                        elif age > 90:
                            risk = RiskLevel.MEDIUM
                            age_note = f", {age:.0f} days old"
                        else:
                            risk = RiskLevel.HIGH
                            age_note = ", recently modified"

                        items.append(CleanupItem(
                            path=entry,
                            size_bytes=size,
                            category=Category.SAVE_FILES,
                            risk=risk,
                            reason=f"{app_name} project ({ext}){age_note}",
                            is_directory=True,
                            metadata={"app": app_name, "age_days": age},
                        ))
                else:
                    _scan_dir_for_projects(entry, config, items, depth + 1)
    except PermissionError:
        pass
