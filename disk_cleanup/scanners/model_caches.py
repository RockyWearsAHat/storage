"""Scanner for AI/ML model caches and heavyweight data directories."""

from pathlib import Path

from disk_cleanup.config import Config, HOME
from disk_cleanup.scanners import Category, CleanupItem, RiskLevel
from disk_cleanup.utils import dir_size, is_excluded


# Known AI/ML model cache locations with labels
MODEL_CACHE_DIRS = [
    (HOME / ".cache" / "huggingface", "Hugging Face model cache", RiskLevel.LOW),
    (HOME / ".cache" / "torch", "PyTorch model cache", RiskLevel.LOW),
    (HOME / ".cache" / "clip", "CLIP model cache", RiskLevel.LOW),
    (HOME / ".cache" / "diffusers", "Diffusers model cache", RiskLevel.LOW),
    (HOME / ".ollama", "Ollama LLM models", RiskLevel.LOW),
    (HOME / ".cache" / "lm-studio", "LM Studio models", RiskLevel.LOW),
    (HOME / ".cache" / "whisper", "Whisper model cache", RiskLevel.LOW),
    (HOME / ".cache" / "puppeteer", "Puppeteer browser binaries", RiskLevel.SAFE),
    (HOME / ".cache" / "ms-playwright", "Playwright browser binaries", RiskLevel.SAFE),
    (HOME / ".cache" / "uv", "uv package cache", RiskLevel.SAFE),
    (HOME / ".cache" / "pip", "pip download cache", RiskLevel.SAFE),
    (HOME / ".cache" / "yarn", "Yarn cache", RiskLevel.SAFE),
    (HOME / ".cache" / "node", "Node.js cache", RiskLevel.SAFE),
    (HOME / ".cache" / "go-build", "Go build cache", RiskLevel.SAFE),
    (HOME / ".cache" / "coursier", "Scala/Coursier cache", RiskLevel.SAFE),
    (HOME / ".cache" / "cypress", "Cypress test runner", RiskLevel.SAFE),
    (HOME / ".npm", "npm cache", RiskLevel.SAFE),
    (HOME / ".gradle", "Gradle cache", RiskLevel.SAFE),
    (HOME / ".m2", "Maven cache", RiskLevel.SAFE),
    (HOME / ".nuget", "NuGet cache", RiskLevel.SAFE),
    (HOME / ".cargo" / "registry", "Cargo registry cache", RiskLevel.SAFE),
    (HOME / "Library" / "pnpm", "pnpm cache", RiskLevel.SAFE),
    (HOME / ".objaverse", "Objaverse 3D model cache", RiskLevel.LOW),
]

# Steam and game data
GAME_DIRS = [
    (HOME / "Library" / "Application Support" / "Steam", "Steam games & data", RiskLevel.MEDIUM),
]

# VS Code extension/app data
VSCODE_DIRS = [
    (HOME / "Library" / "Application Support" / "Code", "VS Code app data (extensions, state)", RiskLevel.MEDIUM),
    (HOME / ".vscode", "VS Code config & extensions", RiskLevel.MEDIUM),
    (HOME / ".cursor", "Cursor editor data", RiskLevel.MEDIUM),
]

MIN_SIZE = 100 * 1024 * 1024  # 100 MB

# Fingerprinting: roots and walk depth for incremental scan detection
# Include ~/.cache (catch-all) and major known root dirs
SCAN_ROOTS = (
    [p for p, _, _ in MODEL_CACHE_DIRS]
    + [p for p, _, _ in GAME_DIRS]
    + [p for p, _, _ in VSCODE_DIRS]
    + [HOME / ".cache"]  # catch-all for unknown caches
)
SCAN_DEPTH = 0  # we only stat known dirs + iterdir ~/.cache (shallow)


def scan_model_caches(config: Config) -> list[CleanupItem]:
    """Find AI/ML model caches, package manager caches, and heavy app data."""
    items: list[CleanupItem] = []

    for path, label, risk in MODEL_CACHE_DIRS + GAME_DIRS + VSCODE_DIRS:
        if not path.exists() or is_excluded(path, config):
            continue
        size = dir_size(path)
        if size >= MIN_SIZE:
            items.append(CleanupItem(
                path=path,
                size_bytes=size,
                category=Category.MODEL_CACHES,
                risk=risk,
                reason=f"{label} — re-downloaded when needed",
                is_directory=True,
                metadata={"type": label},
            ))

    # Scan ~/.cache for anything else large we missed
    dot_cache = HOME / ".cache"
    if dot_cache.exists() and not is_excluded(dot_cache, config):
        known_names = {p.name for p, _, _ in MODEL_CACHE_DIRS if p.parent == dot_cache}
        try:
            for entry in dot_cache.iterdir():
                if entry.name in known_names or not entry.is_dir():
                    continue
                size = dir_size(entry)
                if size >= MIN_SIZE:
                    items.append(CleanupItem(
                        path=entry,
                        size_bytes=size,
                        category=Category.MODEL_CACHES,
                        risk=RiskLevel.LOW,
                        reason=f"Cache directory ({entry.name})",
                        is_directory=True,
                    ))
        except PermissionError:
            pass

    return items
