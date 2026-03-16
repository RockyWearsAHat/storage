"""Scanner for git repositories — identifies repos backed up on GitHub."""

import subprocess
from pathlib import Path

from disk_cleanup.config import Config, HOME
from disk_cleanup.scanners import Category, CleanupItem, RiskLevel
from disk_cleanup.utils import dir_size, is_excluded

# Same roots as dev_artifacts
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

MAX_DEPTH = 5


def scan_git_repos(config: Config) -> list[CleanupItem]:
    """Find git repos and check if they're fully backed up to a remote."""
    items: list[CleanupItem] = []
    seen: set[Path] = set()

    def find_repos(directory: Path, depth: int = 0):
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

                if (entry / ".git").exists():
                    seen.add(resolved)
                    _analyze_repo(entry, items, config)
                elif not entry.name.startswith("."):
                    if entry.name not in {"Library", "Applications", "node_modules",
                                          "Music", "Movies", "Pictures", ".Trash"}:
                        find_repos(entry, depth + 1)
        except PermissionError:
            pass

    for root in SCAN_ROOTS:
        if root.exists():
            find_repos(root)

    return items


def _analyze_repo(repo_path: Path, items: list[CleanupItem], config: Config):
    """Analyze a single git repo for backup status."""
    info = _get_repo_info(repo_path)

    if info is None:
        return

    has_remote = bool(info.get("remotes"))
    is_clean = info.get("is_clean", False)
    is_pushed = info.get("is_pushed", False)
    remote_url = info.get("remote_url", "")
    is_github = "github.com" in remote_url

    # Only report repos that are fully backed up
    if has_remote and is_clean and is_pushed:
        size = dir_size(repo_path)
        if size < 10 * 1024 * 1024:  # Skip tiny repos
            return

        source = "GitHub" if is_github else "remote"
        items.append(CleanupItem(
            path=repo_path,
            size_bytes=size,
            category=Category.GIT_REPOS,
            risk=RiskLevel.LOW,
            reason=f"Fully backed up to {source} — all changes pushed",
            is_directory=True,
            metadata={
                "remote_url": remote_url,
                "is_github": is_github,
                "is_clean": is_clean,
                "is_pushed": is_pushed,
                "branch": info.get("branch", "unknown"),
            },
        ))
    elif has_remote and not is_clean:
        # Has uncommitted changes — flag as informational
        size = dir_size(repo_path)
        if size < 50 * 1024 * 1024:
            return

        items.append(CleanupItem(
            path=repo_path,
            size_bytes=size,
            category=Category.GIT_REPOS,
            risk=RiskLevel.HIGH,
            reason=f"Has remote but UNCOMMITTED CHANGES — do not delete",
            is_directory=True,
            metadata={
                "remote_url": remote_url,
                "is_github": is_github,
                "is_clean": False,
                "is_pushed": is_pushed,
                "has_warning": True,
            },
        ))


def _get_repo_info(repo_path: Path) -> dict | None:
    """Get git status information for a repository."""
    try:
        def git(*args: str) -> str:
            result = subprocess.run(
                ["git", *args],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout.strip()

        # Get remotes
        remotes = git("remote").splitlines()

        # Get current branch
        branch = git("rev-parse", "--abbrev-ref", "HEAD")

        # Check if working directory is clean
        status = git("status", "--porcelain")
        is_clean = len(status) == 0

        # Get remote URL
        remote_url = ""
        if remotes:
            remote_url = git("remote", "get-url", remotes[0])

        # Check if current branch is pushed (no unpushed commits)
        is_pushed = False
        if remotes and branch != "HEAD":
            tracking = git("rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}")
            if tracking:
                ahead = git("rev-list", "--count", f"{tracking}..HEAD")
                is_pushed = ahead == "0"

        return {
            "remotes": remotes,
            "branch": branch,
            "is_clean": is_clean,
            "remote_url": remote_url,
            "is_pushed": is_pushed,
        }
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
