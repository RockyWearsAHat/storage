# storage

AI-assisted disk management for macOS. Scans, maps, and cleans your disk from a single interactive terminal.

## Features

- **Smart Scanning**: Detects caches, logs, old downloads, screenshots, dev artifacts, AI model caches, Xcode data, large files, duplicates, and more
- **Full Disk Mapping**: See where _all_ your space is going — not just cleanable files
- **Git-Aware**: Identifies repos fully backed up to GitHub — safe to clean build artifacts
- **AI Advisor**: Generates structured reports and Copilot Chat prompts for deeper recommendations
- **Interactive REPL**: Explore scan results, drill into directories, and clean — all from one prompt
- **Safety First**: Dry-run by default, Trash instead of delete, protected paths enforced

## Quick Start

```bash
# Install
pip install -e .

# Launch interactive mode (default)
storage

# Or use direct commands
storage scan            # Scan for cleanable files
storage clean           # Interactive cleanup
storage map             # Map home directory usage
storage map ~/.cache    # Map a specific directory
storage whereis         # Full system-wide breakdown
storage overview        # Quick disk usage bar
storage analyze --copilot  # Generate a Copilot Chat prompt
```

## Interactive Mode

Just run `storage` with no arguments (or `storage -i`) to enter the REPL:

```
storage> scan              # Run a full scan
storage> status            # Disk overview + scan summary
storage> results           # Show scan results table
storage> show caches       # Details for a category
storage> categories        # List all categories
storage> map ~/Library     # Map a directory
storage> whereis           # System-wide breakdown
storage> clean safe        # Auto-clean zero-risk items
storage> clean caches      # Clean a specific category
storage> copilot           # Generate Copilot Chat prompt
storage> help              # All commands
storage> quit              # Exit
```

Scan results are cached (`~/.storage/last_scan.json`) so you can explore without rescanning every time.

## Categories

| Category        | What it finds                                            |
| --------------- | -------------------------------------------------------- |
| `caches`        | ~/Library/Caches, browser caches, app caches             |
| `logs`          | ~/Library/Logs, system logs, crash reports               |
| `downloads`     | Old files in ~/Downloads (configurable age)              |
| `screenshots`   | Screenshots on Desktop and common locations              |
| `dev_artifacts` | node_modules, \_\_pycache\_\_, .venv, build/, target/    |
| `git_repos`     | Repos fully pushed to GitHub (safe to clean artifacts)   |
| `xcode`         | DerivedData, archives, old simulators                    |
| `large_files`   | Files over a configurable size threshold                 |
| `duplicates`    | Files with identical content                             |
| `trash`         | ~/.Trash contents                                        |
| `model_caches`  | Hugging Face, Ollama, PyTorch, and other AI model caches |
| `app_data`      | ~/Library/Application Support bloat                      |
| `brew`          | Homebrew cache                                           |

## Safety

- **Dry-run by default** — nothing is deleted unless you explicitly confirm
- **Trash-first** — deletions move to macOS Trash (recoverable) unless you pass `--permanent`
- **Protected paths** — system files, running app data, and critical configs are never touched
- **Git safety** — only cleans build artifacts in repos where all changes are pushed upstream

## AI Integration

Generate a prompt for GitHub Copilot Chat with your scan data:

```bash
storage analyze --copilot
```

Or from within the interactive REPL:

```
storage> copilot       # From scan results
storage> copilot map   # From disk map
```

## Configuration

Edit `~/.disk_cleanup.json` to customize:

- Download age threshold (default: 90 days)
- Large file threshold (default: 500MB)
- Protected directories
- Scan exclusions
