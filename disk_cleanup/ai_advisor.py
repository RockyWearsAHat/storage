"""AI Advisor — generates structured analysis for GitHub Copilot integration."""

import json
from datetime import datetime, timezone

from disk_cleanup.config import Config
from disk_cleanup.scanners import CATEGORY_LABELS, Category, CleanupItem, RiskLevel, ScanResult
from disk_cleanup.utils import format_size, get_disk_usage


def generate_analysis(result: ScanResult, config: Config) -> dict:
    """Generate a structured analysis report from scan results.

    This report is designed to be pasted into GitHub Copilot Chat
    for AI-powered cleanup recommendations.
    """
    disk = get_disk_usage()

    by_category = result.by_category()
    by_risk = result.by_risk()

    # Build category summaries
    category_summaries = []
    for cat, items in sorted(by_category.items(), key=lambda x: sum(i.size_bytes for i in x[1]), reverse=True):
        total = sum(i.size_bytes for i in items)
        category_summaries.append({
            "category": CATEGORY_LABELS.get(cat, cat.value),
            "item_count": len(items),
            "total_size": format_size(total),
            "total_bytes": total,
            "risk_breakdown": _risk_breakdown(items),
            "top_items": [
                {
                    "path": str(item.path),
                    "size": format_size(item.size_bytes),
                    "risk": item.risk.value,
                    "reason": item.reason,
                }
                for item in sorted(items, key=lambda x: x.size_bytes, reverse=True)[:5]
            ],
        })

    # Build risk summary
    risk_summary = {}
    for risk in RiskLevel:
        items = by_risk.get(risk, [])
        total = sum(i.size_bytes for i in items)
        risk_summary[risk.value] = {
            "item_count": len(items),
            "total_size": format_size(total),
            "total_bytes": total,
        }

    # Quick wins — safe items sorted by size
    safe_items = by_risk.get(RiskLevel.SAFE, [])
    quick_wins = [
        {
            "path": str(item.path),
            "size": format_size(item.size_bytes),
            "category": CATEGORY_LABELS.get(item.category, item.category.value),
            "reason": item.reason,
        }
        for item in sorted(safe_items, key=lambda x: x.size_bytes, reverse=True)[:10]
    ]

    # Git repos needing attention
    git_warnings = [
        {
            "path": str(item.path),
            "size": format_size(item.size_bytes),
            "reason": item.reason,
            "remote": item.metadata.get("remote_url", ""),
        }
        for item in result.items
        if item.category == Category.GIT_REPOS and item.metadata.get("has_warning")
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disk_overview": {
            "total": format_size(disk["total"]),
            "used": format_size(disk["used"]),
            "free": format_size(disk["free"]),
            "percent_used": f"{disk['percent_used']:.1f}%",
        },
        "scan_summary": {
            "total_items": len(result.items),
            "total_reclaimable": format_size(result.total_size),
            "total_reclaimable_bytes": result.total_size,
            "scan_time": f"{result.scan_time_seconds:.1f}s",
            "errors": result.errors,
        },
        "risk_summary": risk_summary,
        "quick_wins": quick_wins,
        "categories": category_summaries,
        "git_warnings": git_warnings,
        "recommendations": _generate_recommendations(result, disk),
    }


def generate_copilot_prompt(analysis: dict) -> str:
    """Generate a prompt you can paste directly into GitHub Copilot Chat."""
    total = analysis["scan_summary"]["total_reclaimable"]
    free = analysis["disk_overview"]["free"]
    pct = analysis["disk_overview"]["percent_used"]

    prompt = f"""I ran a disk cleanup scan on my Mac. Here are the results:

**Disk:** {pct} used, {free} free
**Reclaimable:** {total} across {analysis["scan_summary"]["total_items"]} items

**Quick Wins (safe to delete):**
"""
    for win in analysis["quick_wins"][:8]:
        prompt += f"- {win['size']} — {win['reason']} ({win['path']})\n"

    if analysis["git_warnings"]:
        prompt += "\n**Git Repos with Uncommitted Changes (DO NOT DELETE):**\n"
        for warn in analysis["git_warnings"]:
            prompt += f"- {warn['path']} — {warn['reason']}\n"

    prompt += f"""
**Risk Breakdown:**
"""
    for risk, info in analysis["risk_summary"].items():
        if info["item_count"] > 0:
            prompt += f"- {risk}: {info['item_count']} items, {info['total_size']}\n"

    prompt += """
Based on this scan, please:
1. Recommend which categories I should clean first for maximum space savings
2. Flag anything that looks risky to delete
3. Suggest any additional macOS cleanup steps I might be missing
4. Tell me if any of the "safe" items might actually need a second look
"""

    return prompt


def format_report_text(analysis: dict) -> str:
    """Format the analysis as a human-readable text report."""
    lines = []
    lines.append("=" * 60)
    lines.append("  DISK CLEANUP ANALYSIS REPORT")
    lines.append("=" * 60)
    lines.append("")

    # Disk overview
    d = analysis["disk_overview"]
    lines.append(f"  Disk: {d['percent_used']} used  |  {d['free']} free  |  {d['total']} total")
    lines.append(f"  Reclaimable: {analysis['scan_summary']['total_reclaimable']}")
    lines.append("")

    # Risk summary
    lines.append("  RISK BREAKDOWN")
    lines.append("  " + "-" * 40)
    for risk, info in analysis["risk_summary"].items():
        if info["item_count"] > 0:
            lines.append(f"  {risk:>8}: {info['item_count']:>4} items  |  {info['total_size']}")
    lines.append("")

    # Quick wins
    if analysis["quick_wins"]:
        lines.append("  TOP QUICK WINS (safe to delete)")
        lines.append("  " + "-" * 40)
        for win in analysis["quick_wins"][:8]:
            lines.append(f"  {win['size']:>10}  {win['reason']}")
            lines.append(f"             {win['path']}")
        lines.append("")

    # Warnings
    if analysis["git_warnings"]:
        lines.append("  ⚠ GIT REPOS WITH UNCOMMITTED CHANGES")
        lines.append("  " + "-" * 40)
        for warn in analysis["git_warnings"]:
            lines.append(f"  {warn['size']:>10}  {warn['path']}")
            lines.append(f"             {warn['reason']}")
        lines.append("")

    # Recommendations
    if analysis["recommendations"]:
        lines.append("  RECOMMENDATIONS")
        lines.append("  " + "-" * 40)
        for i, rec in enumerate(analysis["recommendations"], 1):
            lines.append(f"  {i}. {rec}")
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


def _risk_breakdown(items: list[CleanupItem]) -> dict[str, int]:
    breakdown: dict[str, int] = {}
    for item in items:
        breakdown[item.risk.value] = breakdown.get(item.risk.value, 0) + 1
    return breakdown


def _generate_recommendations(result: ScanResult, disk: dict) -> list[str]:
    recs = []
    by_cat = result.by_category()
    by_risk = result.by_risk()

    safe_total = sum(i.size_bytes for i in by_risk.get(RiskLevel.SAFE, []))
    if safe_total > 1024 * 1024 * 1024:
        recs.append(
            f"Start with safe items — {format_size(safe_total)} can be reclaimed with zero risk."
        )

    if Category.TRASH in by_cat:
        trash_size = sum(i.size_bytes for i in by_cat[Category.TRASH])
        if trash_size > 500 * 1024 * 1024:
            recs.append(f"Empty Trash to reclaim {format_size(trash_size)} immediately.")

    if Category.DEV_ARTIFACTS in by_cat:
        dev_size = sum(i.size_bytes for i in by_cat[Category.DEV_ARTIFACTS])
        if dev_size > 2 * 1024 * 1024 * 1024:
            recs.append(
                f"Developer artifacts total {format_size(dev_size)} — "
                "consider running 'npm prune' or removing unused node_modules."
            )

    if Category.XCODE in by_cat:
        xcode_size = sum(i.size_bytes for i in by_cat[Category.XCODE])
        if xcode_size > 5 * 1024 * 1024 * 1024:
            recs.append(
                f"Xcode data totals {format_size(xcode_size)} — "
                "DerivedData is always safe to delete."
            )

    if Category.DOWNLOADS in by_cat:
        installers = [
            i for i in by_cat[Category.DOWNLOADS]
            if i.metadata.get("type") == "installer"
        ]
        if installers:
            inst_size = sum(i.size_bytes for i in installers)
            recs.append(
                f"{len(installers)} installer/disk images ({format_size(inst_size)}) "
                "in Downloads — these are almost always safe to remove after installation."
            )

    pct = disk["percent_used"]
    if pct > 90:
        recs.append("URGENT: Disk is over 90% full. Prioritize safe deletions immediately.")
    elif pct > 80:
        recs.append("Disk is over 80% full. Consider cleaning up to maintain performance.")

    if not recs:
        recs.append("Disk looks healthy! Run periodic scans to keep it that way.")

    return recs
