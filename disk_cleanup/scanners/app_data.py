"""Scanner for app data: Messages attachments, sandboxed containers, etc."""

from pathlib import Path

from disk_cleanup.config import Config, HOME
from disk_cleanup.scanners import Category, CleanupItem, RiskLevel
from disk_cleanup.utils import dir_size, is_excluded

MIN_SIZE = 100 * 1024 * 1024  # 100 MB


def scan_messages(config: Config) -> list[CleanupItem]:
    """Find Messages app data (attachments, chat history)."""
    items: list[CleanupItem] = []

    messages_dir = HOME / "Library" / "Messages"
    if messages_dir.exists() and not is_excluded(messages_dir, config):
        # Attachments subdirectory is usually the bulk of it
        attachments = messages_dir / "Attachments"
        if attachments.exists():
            size = dir_size(attachments)
            if size >= MIN_SIZE:
                items.append(CleanupItem(
                    path=attachments,
                    size_bytes=size,
                    category=Category.MESSAGES,
                    risk=RiskLevel.MEDIUM,
                    reason="iMessage attachments (photos, videos sent/received)",
                    is_directory=True,
                ))

        # Overall Messages folder size (for reporting)
        total_size = dir_size(messages_dir)
        non_attach = total_size - (dir_size(attachments) if attachments.exists() else 0)
        if non_attach >= MIN_SIZE:
            items.append(CleanupItem(
                path=messages_dir,
                size_bytes=non_attach,
                category=Category.MESSAGES,
                risk=RiskLevel.HIGH,
                reason="iMessage database & other data — deleting may lose chat history",
                is_directory=True,
            ))

    return items


def scan_containers(config: Config) -> list[CleanupItem]:
    """Find App Sandbox containers using significant space."""
    items: list[CleanupItem] = []

    containers_dir = HOME / "Library" / "Containers"
    if not containers_dir.exists() or is_excluded(containers_dir, config):
        return items

    try:
        for entry in containers_dir.iterdir():
            if not entry.is_dir():
                continue
            size = dir_size(entry)
            if size >= MIN_SIZE:
                # Try to get a readable app name from the bundle ID
                name = _bundle_to_name(entry.name)
                items.append(CleanupItem(
                    path=entry,
                    size_bytes=size,
                    category=Category.CONTAINERS,
                    risk=RiskLevel.MEDIUM,
                    reason=f"Sandboxed app data for {name}",
                    is_directory=True,
                    metadata={"bundle_id": entry.name, "app_name": name},
                ))
    except PermissionError:
        pass

    return items


def _bundle_to_name(bundle_id: str) -> str:
    """Convert a bundle ID to a human-readable name, best effort."""
    known = {
        "com.apple.mail": "Apple Mail",
        "com.apple.Safari": "Safari",
        "com.apple.Notes": "Notes",
        "com.apple.iChat": "Messages",
        "com.apple.MobileSMS": "Messages",
        "com.apple.Photos": "Photos",
        "com.apple.Preview": "Preview",
        "com.apple.finder": "Finder",
        "com.apple.TextEdit": "TextEdit",
        "com.docker.docker": "Docker",
        "com.spotify.client": "Spotify",
        "com.tinyspeck.slackmacgap": "Slack",
        "com.hnc.Discord": "Discord",
        "com.google.Chrome": "Chrome",
        "org.mozilla.firefox": "Firefox",
        "com.microsoft.teams2": "Microsoft Teams",
        "com.microsoft.Outlook": "Outlook",
        "com.microsoft.Word": "Word",
        "com.microsoft.Excel": "Excel",
        "com.linear": "Linear",
        "com.isaacmarovitz.Whisky": "Whisky (Windows compat)",
    }
    if bundle_id in known:
        return known[bundle_id]

    # Extract last component as a rough name
    parts = bundle_id.split(".")
    if len(parts) >= 2:
        return parts[-1]
    return bundle_id
