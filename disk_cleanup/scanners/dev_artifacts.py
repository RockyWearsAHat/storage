"""Scanner for developer build artifacts (node_modules, __pycache__, .venv, etc.)."""

from pathlib import Path

from disk_cleanup.config import Config, HOME
from disk_cleanup.scanners import Category, CleanupItem, RiskLevel
from disk_cleanup.utils import dir_size, is_excluded

# (directory name, label, risk) — these are always regenerable from source
DEV_ARTIFACT_DIRS = [
    ("node_modules", "Node.js dependencies", RiskLevel.SAFE),
    ("__pycache__", "Python bytecode cache", RiskLevel.SAFE),
    (".pytest_cache", "pytest cache", RiskLevel.SAFE),
    (".mypy_cache", "mypy cache", RiskLevel.SAFE),
    (".ruff_cache", "ruff cache", RiskLevel.SAFE),
    (".tox", "tox environments", RiskLevel.SAFE),
    (".venv", "Python virtual environment", RiskLevel.LOW),
    ("venv", "Python virtual environment", RiskLevel.LOW),
    (".env", "Python virtual environment", RiskLevel.MEDIUM),  # Could be env vars
    ("target", "Rust/Java build output", RiskLevel.SAFE),
    ("build", "Build output", RiskLevel.LOW),
    ("dist", "Distribution output", RiskLevel.LOW),
    (".next", "Next.js build cache", RiskLevel.SAFE),
    (".nuxt", "Nuxt build cache", RiskLevel.SAFE),
    (".turbo", "Turbo build cache", RiskLevel.SAFE),
    (".gradle", "Gradle cache", RiskLevel.SAFE),
    (".angular", "Angular cache", RiskLevel.SAFE),
    ("Pods", "CocoaPods dependencies", RiskLevel.SAFE),
    (".dart_tool", "Dart tool cache", RiskLevel.SAFE),
    (".pub-cache", "Dart pub cache", RiskLevel.SAFE),
    ("vendor", "Vendor dependencies", RiskLevel.LOW),
    ("coverage", "Test coverage data", RiskLevel.SAFE),
    (".coverage", "Coverage data file", RiskLevel.SAFE),
    ("htmlcov", "HTML coverage reports", RiskLevel.SAFE),
]

# Minimum size worth reporting
MIN_SIZE = 10 * 1024 * 1024  # 10 MB

# Scan roots — common places where dev projects live
SCAN_ROOTS = [
    HOME / "Desktop",
    HOME / "Documents",
    HOME / "Projects",
    HOME / "projects",
    HOME / "Developer",
    HOME / "dev",
    HOME / "src",
    HOME / "Code",
    HOME / "code",
    HOME / "repos",
    HOME / "github",
    HOME / "workspace",
    HOME / "work",
]

# Max depth to search for artifact directories
MAX_DEPTH = 6


def scan_dev_artifacts(config: Config) -> list[CleanupItem]:
    """Find developer build artifacts that can be regenerated."""
    items: list[CleanupItem] = []
    seen: set[Path] = set()
    artifact_names = {name for name, _, _ in DEV_ARTIFACT_DIRS}
    artifact_map = {name: (label, risk) for name, label, risk in DEV_ARTIFACT_DIRS}

    def scan_dir(directory: Path, depth: int = 0):
        if depth > MAX_DEPTH:
            return
        if is_excluded(directory, config):
            return

        try:
            for entry in directory.iterdir():
                if not entry.is_dir() or entry.is_symlink():
                    continue

                resolved = entry.resolve()
                if resolved in seen:
                    continue

                if entry.name in artifact_names:
                    seen.add(resolved)
                    size = dir_size(entry)
                    if size >= MIN_SIZE:
                        label, risk = artifact_map[entry.name]
                        # Check if this is in a git repo for context
                        in_git = _is_in_git_repo(entry.parent)
                        reason = f"{label} — regenerable"
                        if in_git:
                            reason += " (in git repo)"

                        items.append(CleanupItem(
                            path=entry,
                            size_bytes=size,
                            category=Category.DEV_ARTIFACTS,
                            risk=risk,
                            reason=reason,
                            is_directory=True,
                            metadata={"in_git_repo": in_git, "artifact_type": entry.name},
                        ))
                elif entry.name.startswith("."):
                    # Don't recurse into hidden dirs (other than the artifact ones)
                    continue
                else:
                    # Recurse, but skip known non-project dirs
                    if entry.name not in {"Library", "Applications", "Music", "Movies", "Pictures"}:
                        scan_dir(entry, depth + 1)
        except PermissionError:
            pass

    for root in SCAN_ROOTS:
        if root.exists():
            scan_dir(root)

    return items


def _is_in_git_repo(directory: Path) -> bool:
    """Check if a directory is inside a git repository."""
    current = directory
    for _ in range(10):
        if (current / ".git").exists():
            return True
        parent = current.parent
        if parent == current:
            break
        current = parent
    return False
