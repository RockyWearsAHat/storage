"""Query engine — Copilot-facing interface for interrogating scan data.

All output is designed for Copilot Chat to parse and relay to the user.
Every function is read-only and never modifies anything on disk.
"""

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from disk_cleanup.cache import load_scan
from disk_cleanup.config import HOME, Config
from disk_cleanup.locks import is_locked, partition_locked
from disk_cleanup.scanners import CATEGORY_LABELS, Category, CleanupItem, RiskLevel, ScanResult
from disk_cleanup.utils import format_size, get_disk_usage


# ── Query scan results ─────────────────────────────────────────────

def query_scan(
    *,
    category: str | None = None,
    risk: str | None = None,
    older_than_days: float | None = None,
    newer_than_days: float | None = None,
    larger_than_mb: float | None = None,
    smaller_than_mb: float | None = None,
    path_contains: str | None = None,
    sort_by: str = "size",
    limit: int = 50,
    as_json: bool = False,
) -> str:
    """Query cached scan data with filters. Returns formatted text or JSON."""
    result, age = load_scan()
    if result is None:
        return "ERROR: No scan data found. Run `storage scan` first."

    items = list(result.items)

    # ── Apply filters ──────────────────────────────────────────────
    if category:
        cat_match = _resolve_category(category)
        if cat_match is None:
            valid = ", ".join(c.value for c in Category)
            return f"ERROR: Unknown category '{category}'. Valid: {valid}"
        items = [i for i in items if i.category == cat_match]

    if risk:
        risk_match = _resolve_risk(risk)
        if risk_match is None:
            valid = ", ".join(r.value for r in RiskLevel)
            return f"ERROR: Unknown risk level '{risk}'. Valid: {valid}"
        items = [i for i in items if i.risk == risk_match]

    if older_than_days is not None:
        items = [i for i in items if _file_age_days(i.path) > older_than_days]

    if newer_than_days is not None:
        items = [i for i in items if 0 < _file_age_days(i.path) <= newer_than_days]

    if larger_than_mb is not None:
        threshold = int(larger_than_mb * 1024 * 1024)
        items = [i for i in items if i.size_bytes >= threshold]

    if smaller_than_mb is not None:
        threshold = int(smaller_than_mb * 1024 * 1024)
        items = [i for i in items if i.size_bytes <= threshold]

    if path_contains:
        needle = path_contains.lower()
        items = [i for i in items if needle in str(i.path).lower()]

    # ── Sort ───────────────────────────────────────────────────────
    if sort_by == "size":
        items.sort(key=lambda x: x.size_bytes, reverse=True)
    elif sort_by == "age":
        items.sort(key=lambda x: _file_age_days(x.path), reverse=True)
    elif sort_by == "name":
        items.sort(key=lambda x: str(x.path).lower())
    elif sort_by == "risk":
        order = {RiskLevel.HIGH: 0, RiskLevel.MEDIUM: 1, RiskLevel.LOW: 2, RiskLevel.SAFE: 3}
        items.sort(key=lambda x: order.get(x.risk, 99))

    total_before_limit = len(items)
    items = items[:limit]

    # Determine which items are locked (for display marking)
    _, locked_set = partition_locked(items)
    locked_paths = {str(i.path.resolve()) for i in locked_set}

    # ── Format ─────────────────────────────────────────────────────
    if as_json:
        return _format_json(items, total_before_limit, age, locked_paths=locked_paths)
    return _format_text(items, total_before_limit, age, locked_paths=locked_paths)


def query_summary(as_json: bool = False) -> str:
    """Return a high-level summary of scan data: totals by category and risk."""
    result, age = load_scan()
    if result is None:
        return "ERROR: No scan data found. Run `storage scan` first."

    disk = get_disk_usage()
    by_cat = result.by_category()
    by_risk = result.by_risk()

    if as_json:
        data = {
            "disk": {
                "total": format_size(disk["total"]),
                "used": format_size(disk["used"]),
                "free": format_size(disk["free"]),
                "percent_used": round(disk["percent_used"], 1),
            },
            "scan_age_seconds": round(age, 0) if age else None,
            "total_items": len(result.items),
            "total_reclaimable": format_size(result.total_size),
            "total_reclaimable_bytes": result.total_size,
            "by_category": {
                cat.value: {
                    "label": CATEGORY_LABELS.get(cat, cat.value),
                    "count": len(items),
                    "size": format_size(sum(i.size_bytes for i in items)),
                    "size_bytes": sum(i.size_bytes for i in items),
                }
                for cat, items in sorted(by_cat.items(), key=lambda x: sum(i.size_bytes for i in x[1]), reverse=True)
            },
            "by_risk": {
                risk.value: {
                    "count": len(items),
                    "size": format_size(sum(i.size_bytes for i in items)),
                    "size_bytes": sum(i.size_bytes for i in items),
                }
                for risk, items in by_risk.items()
            },
        }
        return json.dumps(data, indent=2)

    lines = []
    lines.append(f"Disk: {format_size(disk['used'])} used / {format_size(disk['total'])} total ({disk['percent_used']:.1f}%) — {format_size(disk['free'])} free")
    lines.append(f"Scan: {len(result.items)} items, {format_size(result.total_size)} reclaimable")
    lines.append("")
    lines.append("By category:")
    for cat, items in sorted(by_cat.items(), key=lambda x: sum(i.size_bytes for i in x[1]), reverse=True):
        total = sum(i.size_bytes for i in items)
        label = CATEGORY_LABELS.get(cat, cat.value)
        lines.append(f"  {format_size(total):>10}  {label} ({len(items)} items)")
    lines.append("")
    lines.append("By risk:")
    for risk in RiskLevel:
        items = by_risk.get(risk, [])
        if items:
            total = sum(i.size_bytes for i in items)
            lines.append(f"  {format_size(total):>10}  {risk.value} ({len(items)} items)")

    # Show locked items summary
    _, locked_items = partition_locked(list(result.items))
    if locked_items:
        locked_total = sum(i.size_bytes for i in locked_items)
        lines.append("")
        lines.append(f"\U0001f512 Locked ({len(locked_items)} items, {format_size(locked_total)}):")
        by_lock_cat: dict[Category, list] = {}
        for li in locked_items:
            by_lock_cat.setdefault(li.category, []).append(li)
        for cat, litems in sorted(by_lock_cat.items(), key=lambda x: sum(i.size_bytes for i in x[1]), reverse=True):
            ltotal = sum(i.size_bytes for i in litems)
            label = CATEGORY_LABELS.get(cat, cat.value)
            lines.append(f"  {format_size(ltotal):>10}  {label} ({len(litems)} items) — protected, no actions available")

    return "\n".join(lines)


# ── File/directory info ────────────────────────────────────────────

def path_info(target: str, as_json: bool = False) -> str:
    """Deep inspection of a specific file or directory.

    Returns size, dates (modified, accessed, created, last-opened),
    type, and whether it appears in the scan results.
    """
    path = Path(target).expanduser().resolve()
    if not path.exists():
        return f"ERROR: Path does not exist: {path}"

    # Block interaction with locked paths — show size + lock status only
    if is_locked(path):
        try:
            if path.is_dir():
                size = _du_bytes(path)
            else:
                size = path.stat().st_size
        except OSError:
            size = 0
        msg = (
            f"\U0001f512 LOCKED: {path}\n"
            f"Size: {format_size(size)}\n"
            f"This path is protected by a user lock. No interaction is available.\n"
            f"To unlock: storage unlock {path}"
        )
        if as_json:
            import json as _json
            return _json.dumps({"path": str(path), "locked": True, "size": format_size(size), "size_bytes": size}, indent=2)
        return msg

    try:
        stat = path.stat()
    except OSError as e:
        return f"ERROR: Cannot stat {path}: {e}"

    info: dict = {
        "path": str(path),
        "exists": True,
        "is_directory": path.is_dir(),
        "is_symlink": path.is_symlink(),
    }

    # Size
    if path.is_dir():
        info["size"] = _du_bytes(path)
    else:
        info["size"] = stat.st_size
    info["size_display"] = format_size(info["size"])

    # Timestamps
    info["modified"] = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    info["accessed"] = datetime.fromtimestamp(stat.st_atime, tz=timezone.utc).isoformat()
    if hasattr(stat, "st_birthtime"):
        info["created"] = datetime.fromtimestamp(stat.st_birthtime, tz=timezone.utc).isoformat()

    # macOS last-opened via mdls (Spotlight metadata)
    last_opened = _mdls_last_used(path)
    if last_opened:
        info["last_opened"] = last_opened

    # File type info
    if path.is_file():
        info["extension"] = path.suffix
        content_type = _mdls_content_type(path)
        if content_type:
            info["content_type"] = content_type

    # Check if it's in scan results
    result, _ = load_scan()
    if result:
        matches = [i for i in result.items if i.path.resolve() == path]
        if matches:
            item = matches[0]
            info["in_scan"] = True
            info["scan_category"] = CATEGORY_LABELS.get(item.category, item.category.value)
            info["scan_risk"] = item.risk.value
            info["scan_reason"] = item.reason
        else:
            info["in_scan"] = False

    if as_json:
        return json.dumps(info, indent=2)

    # Human-readable
    lines = []
    lines.append(f"Path: {info['path']}")
    lines.append(f"Type: {'directory' if info['is_directory'] else 'file'}")
    if info.get("extension"):
        lines.append(f"Extension: {info['extension']}")
    if info.get("content_type"):
        lines.append(f"Content type: {info['content_type']}")
    lines.append(f"Size: {info['size_display']}")
    lines.append(f"Modified: {info['modified']}")
    lines.append(f"Accessed: {info['accessed']}")
    if info.get("created"):
        lines.append(f"Created: {info['created']}")
    if info.get("last_opened"):
        lines.append(f"Last opened: {info['last_opened']}")
    if info.get("in_scan"):
        lines.append(f"Scan category: {info['scan_category']}")
        lines.append(f"Scan risk: {info['scan_risk']}")
        lines.append(f"Scan reason: {info['scan_reason']}")
    elif info.get("in_scan") is False:
        lines.append("Not flagged in scan results")
    return "\n".join(lines)


# ── Helpers ────────────────────────────────────────────────────────

def _resolve_category(name: str) -> Category | None:
    name = name.lower().strip()
    for cat in Category:
        if cat.value == name:
            return cat
    return None


def _resolve_risk(name: str) -> RiskLevel | None:
    name = name.lower().strip()
    for risk in RiskLevel:
        if risk.value == name:
            return risk
    return None


def _file_age_days(path: Path) -> float:
    try:
        mtime = path.stat().st_mtime
        age = datetime.now(timezone.utc) - datetime.fromtimestamp(mtime, tz=timezone.utc)
        return age.total_seconds() / 86400
    except OSError:
        return -1


def _du_bytes(path: Path) -> int:
    """Fast directory size via du."""
    try:
        out = subprocess.run(
            ["du", "-sk", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return int(out.stdout.split()[0]) * 1024
    except (subprocess.TimeoutExpired, ValueError, IndexError):
        return 0


def _mdls_last_used(path: Path) -> str | None:
    """Get kMDItemLastUsedDate from Spotlight metadata."""
    try:
        out = subprocess.run(
            ["mdls", "-name", "kMDItemLastUsedDate", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        line = out.stdout.strip()
        if "(null)" not in line and "=" in line:
            return line.split("=", 1)[1].strip()
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _mdls_content_type(path: Path) -> str | None:
    """Get kMDItemContentType from Spotlight metadata."""
    try:
        out = subprocess.run(
            ["mdls", "-name", "kMDItemContentType", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        line = out.stdout.strip()
        if "(null)" not in line and "=" in line:
            val = line.split("=", 1)[1].strip().strip('"')
            return val
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _format_json(items: list[CleanupItem], total_count: int, age: float | None, *, locked_paths: set[str] | None = None) -> str:
    lp = locked_paths or set()
    data = {
        "total_matching": total_count,
        "showing": len(items),
        "scan_age_seconds": round(age, 0) if age else None,
        "items": [
            {
                "path": str(item.path),
                "size": format_size(item.size_bytes),
                "size_bytes": item.size_bytes,
                "category": item.category.value,
                "category_label": CATEGORY_LABELS.get(item.category, item.category.value),
                "risk": item.risk.value,
                "reason": item.reason,
                "is_directory": item.is_directory,
                "age_days": round(_file_age_days(item.path), 1),
                "locked": str(item.path.resolve()) in lp,
            }
            for item in items
        ],
    }
    return json.dumps(data, indent=2)


def _format_text(items: list[CleanupItem], total_count: int, age: float | None, *, locked_paths: set[str] | None = None) -> str:
    if not items:
        return "No items match the query."

    lp = locked_paths or set()
    lines = []
    if total_count > len(items):
        lines.append(f"Showing {len(items)} of {total_count} matching items:")
    else:
        lines.append(f"{total_count} matching items:")
    lines.append("")

    for item in items:
        try:
            display_path = f"~/{item.path.relative_to(HOME)}"
        except ValueError:
            display_path = str(item.path)

        age_days = _file_age_days(item.path)
        age_str = f"{age_days:.0f}d old" if age_days > 0 else ""

        is_item_locked = str(item.path.resolve()) in lp
        lock_prefix = "\U0001f512 " if is_item_locked else "  "
        lock_suffix = "  [LOCKED]" if is_item_locked else ""

        lines.append(
            f"{lock_prefix}{format_size(item.size_bytes):>10}  [{item.risk.value:>6}]  {display_path}{lock_suffix}"
        )
        lines.append(
            f"             {item.reason}  {age_str}"
        )

    total_size = sum(i.size_bytes for i in items)
    lines.append("")
    lines.append(f"Total: {format_size(total_size)} across {len(items)} items")
    return "\n".join(lines)
