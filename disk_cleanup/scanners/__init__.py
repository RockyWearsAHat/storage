"""Scanner result types and base interface."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class RiskLevel(Enum):
    """How risky it is to delete this item."""
    SAFE = "safe"           # Caches, temp files — always regenerated
    LOW = "low"             # Old downloads, screenshots — probably unneeded
    MEDIUM = "medium"       # Large files, old repos — review recommended
    HIGH = "high"           # Anything that might contain unique data


class Category(Enum):
    CACHES = "caches"
    LOGS = "logs"
    DOWNLOADS = "downloads"
    SCREENSHOTS = "screenshots"
    DEV_ARTIFACTS = "dev_artifacts"
    GIT_REPOS = "git_repos"
    XCODE = "xcode"
    LARGE_FILES = "large_files"
    DUPLICATES = "duplicates"
    TRASH = "trash"
    MAIL_DOWNLOADS = "mail_downloads"
    BREW = "brew"
    MODEL_CACHES = "model_caches"
    MESSAGES = "messages"
    CONTAINERS = "containers"
    APPLICATIONS = "applications"
    SAVE_FILES = "save_files"


CATEGORY_LABELS = {
    Category.CACHES: "Application Caches",
    Category.LOGS: "Log Files & Crash Reports",
    Category.DOWNLOADS: "Old Downloads",
    Category.SCREENSHOTS: "Screenshots & Screen Recordings",
    Category.DEV_ARTIFACTS: "Developer Build Artifacts",
    Category.GIT_REPOS: "Git Repos (backed up on GitHub)",
    Category.XCODE: "Xcode Data",
    Category.LARGE_FILES: "Large Files",
    Category.DUPLICATES: "Duplicate Files",
    Category.TRASH: "Trash",
    Category.MAIL_DOWNLOADS: "Mail Attachment Downloads",
    Category.BREW: "Homebrew Cache",
    Category.MODEL_CACHES: "AI/ML Models & Heavy Caches",
    Category.MESSAGES: "Messages & Chat Data",
    Category.CONTAINERS: "App Containers & Sandboxed Data",
    Category.APPLICATIONS: "Large Applications",
    Category.SAVE_FILES: "Creative App Save/Project Files",
}


@dataclass
class CleanupItem:
    """A single file or directory flagged for potential cleanup."""
    path: Path
    size_bytes: int
    category: Category
    risk: RiskLevel
    reason: str
    is_directory: bool = False
    metadata: dict = field(default_factory=dict)

    @property
    def size_display(self) -> str:
        from disk_cleanup.utils import format_size
        return format_size(self.size_bytes)


@dataclass
class ScanResult:
    """Aggregated results from all scanners."""
    items: list[CleanupItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    scan_time_seconds: float = 0.0

    @property
    def total_size(self) -> int:
        return sum(item.size_bytes for item in self.items)

    def by_category(self) -> dict[Category, list[CleanupItem]]:
        result: dict[Category, list[CleanupItem]] = {}
        for item in self.items:
            result.setdefault(item.category, []).append(item)
        return result

    def by_risk(self) -> dict[RiskLevel, list[CleanupItem]]:
        result: dict[RiskLevel, list[CleanupItem]] = {}
        for item in self.items:
            result.setdefault(item.risk, []).append(item)
        return result
