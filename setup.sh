#!/usr/bin/env bash
# Quotely — one-shot setup script (macOS / Linux)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
EXT="$ROOT/extension"
DATA="$ROOT/data"

# Isolate from any global PYTHONPATH
unset PYTHONPATH

echo ""
echo "╔══════════════════════════════════╗"
echo "║        Quotely — Setup           ║"
echo "╚══════════════════════════════════╝"
echo ""

# ---------------------------------------------------------------------------
# 1. Python 3.10+
# ---------------------------------------------------------------------------
PYTHON=""
for cmd in python3 python; do
  if command -v "$cmd" &>/dev/null; then
    PYVER=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
    MAJOR=$(echo "$PYVER" | cut -d. -f1)
    MINOR=$(echo "$PYVER" | cut -d. -f2)
    if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 10 ]; then
      PYTHON="$cmd"
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  echo "[ERROR] Python 3.10+ not found."
  echo "        Install from https://python.org and re-run this script."
  exit 1
fi
echo "[OK] Python $("$PYTHON" --version)"

# ---------------------------------------------------------------------------
# 2. Create Python venv
# ---------------------------------------------------------------------------
PYBIN="$BACKEND/.venv/bin/python3"
PIP="$BACKEND/.venv/bin/pip"

if [ ! -d "$BACKEND/.venv" ]; then
  echo "[...] Creating virtual environment..."
  "$PYTHON" -m venv "$BACKEND/.venv"
fi
echo "[OK] Virtual environment ready."

# ---------------------------------------------------------------------------
# 3. Install dependencies
# ---------------------------------------------------------------------------
echo "[...] Installing Python packages (first time: ~300 MB, a few minutes)..."
"$PIP" install --upgrade pip --quiet
"$PIP" install -r "$BACKEND/requirements.txt" --quiet
echo "[OK] Python packages installed."

# ---------------------------------------------------------------------------
# 4. Pre-download embedding model (~90 MB, once)
# ---------------------------------------------------------------------------
echo "[...] Downloading embedding model all-MiniLM-L6-v2 (~90 MB)..."
"$PYBIN" -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
echo "[OK] Embedding model ready."

# ---------------------------------------------------------------------------
# 5. Data directories
# ---------------------------------------------------------------------------
mkdir -p "$DATA/papers" "$DATA/db"
echo "[OK] Data folders: $DATA/papers  (drop your articles here)"

# ---------------------------------------------------------------------------
# 6. Build VS Code extension
# ---------------------------------------------------------------------------
if command -v node &>/dev/null; then
  echo "[...] Installing Node.js dependencies..."
  cd "$EXT" && npm install --silent
  echo "[...] Compiling TypeScript..."
  npm run compile --silent

  if npx vsce --version &>/dev/null 2>&1; then
    echo "[...] Packaging extension..."
    npm run package 2>/dev/null || true
    VSIX=$(ls -t "$EXT"/*.vsix 2>/dev/null | head -1)
    if [ -n "$VSIX" ]; then
      echo "[OK] Extension: $VSIX"
      if command -v code &>/dev/null; then
        code --install-extension "$VSIX" --force
        echo "[OK] Extension installed in VS Code."
      else
        echo "[INFO] Install manually: Cmd+Shift+P → 'Extensions: Install from VSIX' → $VSIX"
      fi
    fi
  fi
else
  echo "[WARN] Node.js not found — skipping extension build."
  echo "       Install from https://nodejs.org then re-run, or install the .vsix manually."
fi

# ---------------------------------------------------------------------------
# 7. Write quotely.projectPath to VS Code user settings
# ---------------------------------------------------------------------------
echo "[...] Configuring VS Code settings..."
"$PYBIN" - "$ROOT" << 'PYEOF'
import json, sys, os, platform

project_path = sys.argv[1]

p = platform.system()
if p == "Darwin":
    settings_file = os.path.expanduser("~/Library/Application Support/Code/User/settings.json")
elif p == "Linux":
    settings_file = os.path.expanduser("~/.config/Code/User/settings.json")
else:
    settings_file = os.path.expanduser("~/.config/Code/User/settings.json")

os.makedirs(os.path.dirname(settings_file), exist_ok=True)
try:
    with open(settings_file, "r", encoding="utf-8") as f:
        settings = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    settings = {}

settings["quotely.projectPath"] = project_path
with open(settings_file, "w", encoding="utf-8") as f:
    json.dump(settings, f, indent=2, ensure_ascii=False)
print(f"[OK] quotely.projectPath = {project_path}")
PYEOF

cd "$ROOT"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Quotely setup complete!                                     ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  1. Reload VS Code (Cmd+Shift+P → 'Reload Window')           ║"
echo "║                                                              ║"
echo "║  2. Drop PDFs / DOCX / TEX files into:                      ║"
printf "║     %-61s║\n" "$DATA/papers/"
echo "║                                                              ║"
echo "║  3. Open a .tex or .md file — backend starts automatically   ║"
echo "║     Type \\cite{ or press Cmd+Shift+C for suggestions         ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
