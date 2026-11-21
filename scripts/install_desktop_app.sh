#!/usr/bin/env bash
# Adds "Clipping Studio" to the Ubuntu app menu and puts an icon on the Desktop.
# Run from anywhere:  bash scripts/install_desktop_app.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_ID="clipping-studio"

# Use the project's virtual environment when there is one.
if [ -x "$REPO_DIR/.venv/bin/python" ]; then
  PY="$REPO_DIR/.venv/bin/python"
elif [ -x "$REPO_DIR/venv/bin/python" ]; then
  PY="$REPO_DIR/venv/bin/python"
else
  PY="$(command -v python3)"
fi

if ! "$PY" -c "import tkinter" 2>/dev/null; then
  echo "Clipping Studio needs Tk for its window. Installing python3-tk (asks for your password)..."
  sudo apt-get install -y python3-tk
  if ! "$PY" -c "import tkinter" 2>/dev/null; then
    echo "Tk still can't be loaded by $PY."
    echo "If this Python is not Ubuntu's own (e.g. pyenv/conda), install Tk for it, or recreate .venv with /usr/bin/python3."
    exit 1
  fi
fi

APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$APPS_DIR"
DESKTOP_FILE="$APPS_DIR/$APP_ID.desktop"

cat > "$DESKTOP_FILE" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Clipping Studio
Comment=Turn long videos into Shorts
Exec="$PY" "$REPO_DIR/clipping_studio.py"
Path=$REPO_DIR
Icon=$REPO_DIR/assets/app/icon.png
Terminal=false
Categories=AudioVideo;Video;
StartupWMClass=ClippingStudio
DESKTOP
chmod +x "$DESKTOP_FILE"

DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$DESKTOP_DIR" ]; then
  cp "$DESKTOP_FILE" "$DESKTOP_DIR/$APP_ID.desktop"
  chmod +x "$DESKTOP_DIR/$APP_ID.desktop"
  # Ubuntu asks you to "Allow Launching" unless the icon is marked trusted.
  gio set "$DESKTOP_DIR/$APP_ID.desktop" metadata::trusted true 2>/dev/null || true
fi

update-desktop-database "$APPS_DIR" 2>/dev/null || true

echo "Done. Open 'Clipping Studio' from the app menu or the Desktop icon."
echo "If the Desktop icon shows a warning, right-click it and choose 'Allow Launching'."
echo "You can also start it from a terminal:  $PY $REPO_DIR/clipping_studio.py"
