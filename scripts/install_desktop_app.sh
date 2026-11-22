#!/usr/bin/env bash
# Adds "Clipping Studio" and "Clip Editor" to the Ubuntu app menu and puts
# their icons on the Desktop.
# Run from anywhere:  bash scripts/install_desktop_app.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Use the project's virtual environment when there is one.
if [ -x "$REPO_DIR/.venv/bin/python" ]; then
  PY="$REPO_DIR/.venv/bin/python"
elif [ -x "$REPO_DIR/venv/bin/python" ]; then
  PY="$REPO_DIR/venv/bin/python"
else
  PY="$(command -v python3)"
fi

if ! "$PY" -c "import tkinter" 2>/dev/null; then
  echo "Clipping Studio and Clip Editor need Tk for their windows. Installing python3-tk (asks for your password)..."
  sudo apt-get install -y python3-tk
  if ! "$PY" -c "import tkinter" 2>/dev/null; then
    echo "Tk still can't be loaded by $PY."
    echo "If this Python is not Ubuntu's own (e.g. pyenv/conda), install Tk for it, or recreate .venv with /usr/bin/python3."
    exit 1
  fi
fi

if ! command -v deno >/dev/null 2>&1 && [ ! -x "$HOME/.deno/bin/deno" ]; then
  echo "Tip: yt-dlp works best with a JavaScript runtime for YouTube. Install Deno once with:"
  echo "  curl -fsSL https://deno.land/install.sh | sh"
  echo "(then open a new terminal). Downloads still work without it, but some formats may be missing."
fi

# Clip Editor plays video inside its window with mpv (smooth on a CPU).
# Without it the editor still works, showing still pictures instead.
if ! "$PY" -c "import mpv" 2>/dev/null; then
  echo "Clip Editor plays videos with mpv. Installing mpv (asks for your password)..."
  sudo apt-get install -y mpv || true
  if ! "$PY" -m pip install --quiet python-mpv 2>/dev/null; then
    "$PY" -m pip install --quiet --user python-mpv 2>/dev/null \
      || echo "Could not install python-mpv for $PY. Clip Editor will show still pictures until it is installed."
  fi
fi

# Clip Editor draws emoji on pop-ups and the hook line with a colour emoji font.
if ! fc-list 2>/dev/null | grep -qi "Noto Color Emoji"; then
  echo "Installing the colour emoji font for Clip Editor (asks for your password)..."
  sudo apt-get install -y fonts-noto-color-emoji || echo "Could not install fonts-noto-color-emoji. Emoji will be left out of exports until it is installed."
fi

APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$APPS_DIR"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"

# make_launcher <id> <name> <comment> <script> <icon> <window class>
make_launcher() {
  local app_id="$1" name="$2" comment="$3" script="$4" icon="$5" wm_class="$6"
  local desktop_file="$APPS_DIR/$app_id.desktop"
  cat > "$desktop_file" <<DESKTOP
[Desktop Entry]
Type=Application
Name=$name
Comment=$comment
Exec="$PY" "$REPO_DIR/$script"
Path=$REPO_DIR
Icon=$REPO_DIR/$icon
Terminal=false
Categories=AudioVideo;Video;
StartupWMClass=$wm_class
DESKTOP
  chmod +x "$desktop_file"
  if [ -d "$DESKTOP_DIR" ]; then
    cp "$desktop_file" "$DESKTOP_DIR/$app_id.desktop"
    chmod +x "$DESKTOP_DIR/$app_id.desktop"
    # Ubuntu asks you to "Allow Launching" unless the icon is marked trusted.
    gio set "$DESKTOP_DIR/$app_id.desktop" metadata::trusted true 2>/dev/null || true
  fi
}

make_launcher clipping-studio "Clipping Studio" "Turn long videos into Shorts" \
  clipping_studio.py assets/app/icon.png ClippingStudio
make_launcher clip-editor "Clip Editor" "Pick your own moments and edit them into Shorts" \
  clip_editor.py assets/app/clip-editor.png ClipEditor

update-desktop-database "$APPS_DIR" 2>/dev/null || true

echo "Done. Open 'Clipping Studio' or 'Clip Editor' from the app menu or the Desktop icons."
echo "If the Desktop icon shows a warning, right-click it and choose 'Allow Launching'."
echo "You can also start them from a terminal:"
echo "  $PY $REPO_DIR/clipping_studio.py"
echo "  $PY $REPO_DIR/clip_editor.py"
