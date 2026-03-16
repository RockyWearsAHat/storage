"""Configuration, constants, and safety settings."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


HOME = Path.home()
CONFIG_PATH = HOME / ".disk_cleanup.json"

# Directories that must NEVER be deleted or modified
PROTECTED_PATHS = frozenset({
    HOME / "Documents",
    HOME / "Pictures",
    HOME / "Music",
    HOME / "Movies",
    HOME / ".ssh",
    HOME / ".gnupg",
    HOME / ".config",
    HOME / ".zshrc",
    HOME / ".bashrc",
    HOME / ".bash_profile",
    HOME / ".zprofile",
    HOME / ".gitconfig",
    Path("/System"),
    Path("/usr"),
    Path("/bin"),
    Path("/sbin"),
    Path("/Applications"),
    Path("/Library"),
})

# File extensions that are almost always safe to remove from caches/temp
SAFE_CACHE_EXTENSIONS = frozenset({
    ".tmp", ".temp", ".cache", ".log", ".old", ".bak",
    ".dSYM", ".o", ".pyc", ".pyo",
})

# Screenshot patterns (macOS defaults)
SCREENSHOT_PATTERNS = [
    "Screenshot *.png",
    "Screen Shot *.png",
    "Screenshot *.jpg",
    "Screen Recording *.mov",
    "Bildschirmfoto *.png",  # German
    "Capture d'écran *.png",  # French
]


@dataclass
class Config:
    """User-configurable cleanup settings."""

    # Age in days — downloads older than this are flagged
    download_age_days: int = 90

    # Files larger than this (in MB) are flagged
    large_file_threshold_mb: int = 500

    # Extra directories to protect (user-specified)
    extra_protected: list[str] = field(default_factory=list)

    # Directories to skip during scanning
    scan_exclusions: list[str] = field(default_factory=list)

    # Whether to use Trash (True) or permanent delete (False)
    use_trash: bool = True

    # Max depth for recursive scans
    max_scan_depth: int = 10

    @classmethod
    def load(cls) -> "Config":
        """Load config from ~/.disk_cleanup.json, or return defaults."""
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r") as f:
                    data = json.load(f)
                return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            except (json.JSONDecodeError, TypeError):
                pass
        return cls()

    def save(self) -> None:
        """Persist current config to disk."""
        with open(CONFIG_PATH, "w") as f:
            json.dump(self.__dict__, f, indent=2)

    @property
    def protected_paths(self) -> frozenset[Path]:
        """All protected paths including user-specified ones."""
        extra = frozenset(Path(p).expanduser() for p in self.extra_protected)
        return PROTECTED_PATHS | extra

    @property
    def exclusion_paths(self) -> frozenset[Path]:
        return frozenset(Path(p).expanduser() for p in self.scan_exclusions)
