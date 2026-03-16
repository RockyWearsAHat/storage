"""Full disk usage mapper — shows where ALL space is being used."""

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from disk_cleanup.config import HOME


@dataclass
class DirNode:
    """A directory and its disk usage."""
    path: Path
    size_bytes: int = 0
    children: list["DirNode"] = field(default_factory=list)
    error: str | None = None

    @property
    def name(self) -> str:
        return self.path.name or str(self.path)

    @property
    def display_path(self) -> str:
        try:
            return f"~/{self.path.relative_to(HOME)}"
        except ValueError:
            return str(self.path)


def map_disk(root: Path = Path("/"), depth: int = 2, min_size_mb: float = 100) -> DirNode:
    """Map disk usage from a root directory using `du` for speed.

    Args:
        root: Starting directory to map.
        depth: How many levels deep to scan.
        min_size_mb: Only include directories larger than this.
    """
    root_node = DirNode(path=root)

    # Use du for the fast top-level scan
    root_node.size_bytes = _du_bytes(root)
    root_node.children = _scan_level(root, depth, int(min_size_mb * 1024 * 1024))

    return root_node


def map_home(depth: int = 3, min_size_mb: float = 50) -> DirNode:
    """Map the home directory in detail."""
    return map_disk(HOME, depth, min_size_mb)


def map_system_overview() -> list[DirNode]:
    """Get a high-level view of where space is used across the whole system.

    Returns top-level breakdown: /Users, /Applications, /Library, /System, etc.
    """
    nodes = []
    top_dirs = [
        Path("/Users"),
        Path("/Applications"),
        Path("/Library"),
        Path("/System"),
        Path("/usr"),
        Path("/opt"),
        Path("/private/var"),
    ]

    for d in top_dirs:
        if d.exists():
            size = _du_bytes(d)
            if size > 0:
                nodes.append(DirNode(path=d, size_bytes=size))

    nodes.sort(key=lambda n: n.size_bytes, reverse=True)
    return nodes


def drill_into(path: Path, min_size_mb: float = 50) -> DirNode:
    """Drill into a specific directory to see its children.

    Used for interactive exploration.
    """
    node = DirNode(path=path)
    node.size_bytes = _du_bytes(path)
    node.children = _scan_level(path, depth=1, min_bytes=int(min_size_mb * 1024 * 1024))
    return node


def _scan_level(directory: Path, depth: int, min_bytes: int) -> list[DirNode]:
    """Scan one level of children with their sizes."""
    if depth <= 0:
        return []

    children = []

    try:
        entries = sorted(directory.iterdir())
    except PermissionError:
        return []
    except OSError:
        return []

    for entry in entries:
        if not entry.is_dir() or entry.is_symlink():
            # Count files at this level as an aggregate
            continue

        # Skip pseudo-filesystems and known noise
        name = entry.name
        if name in {".fseventsd", ".Spotlight-V100", ".vol", "Volumes"}:
            continue

        size = _du_bytes(entry)
        if size >= min_bytes:
            node = DirNode(path=entry, size_bytes=size)
            if depth > 1:
                node.children = _scan_level(entry, depth - 1, min_bytes)
            children.append(node)

    # Calculate size consumed by files directly in this directory (not in subdirs)
    try:
        file_size = 0
        for entry in directory.iterdir():
            if entry.is_file() and not entry.is_symlink():
                try:
                    file_size += entry.stat().st_size
                except OSError:
                    pass
        if file_size >= min_bytes:
            children.append(DirNode(
                path=directory / "[files in this directory]",
                size_bytes=file_size,
            ))
    except (PermissionError, OSError):
        pass

    children.sort(key=lambda n: n.size_bytes, reverse=True)
    return children


def _du_bytes(path: Path) -> int:
    """Get directory size in bytes using macOS `du` (fast, handles permissions)."""
    try:
        result = subprocess.run(
            ["du", "-sk", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 or result.stdout.strip():
            # du -sk outputs kilobytes
            line = result.stdout.strip().split("\n")[-1] if result.stdout.strip() else ""
            if line:
                kb = int(line.split("\t")[0])
                return kb * 1024
    except (subprocess.TimeoutExpired, ValueError, IndexError):
        pass
    return 0


def get_full_disk_breakdown(progress_callback=None) -> dict:
    """Comprehensive disk usage breakdown.

    Returns a structured breakdown of where ALL disk space is going.
    """
    result = {
        "system_overview": [],
        "home_detail": None,
        "total_accounted": 0,
    }

    # 1. System-level overview
    if progress_callback:
        progress_callback("system volumes")
    system_nodes = map_system_overview()
    result["system_overview"] = system_nodes
    result["total_accounted"] = sum(n.size_bytes for n in system_nodes)

    # 2. Detailed home directory scan
    if progress_callback:
        progress_callback("home directory (detailed)")
    home_node = map_home(depth=3, min_size_mb=50)
    result["home_detail"] = home_node

    return result
