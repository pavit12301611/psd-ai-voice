#!/usr/bin/env bash
# ============================================================================
# PSD AI Voice Assistant — one-shot installer & runner for Fedora Workstation 44
#
#   ./run.sh                 first-run setup (if needed) then start
#   ./run.sh --setup         install system deps + venv + speech model only
#   ./run.sh --text          text REPL (no mic/HUD) — good for debugging
#   ./run.sh --once "..."    process a single utterance and exit
#   ./run.sh --no-hud        headless voice mode
#   ./run.sh --mute          voice mode without spoken audio
#   ./run.sh --autostart     install GNOME autostart entry, then start
#   ./run.sh --dev           reinstall Python deps then start
#   ./run.sh -y ...          non-interactive (assume yes to dnf/group changes)
#
# Everything lives under the repo: .venv/, data/, config/.
# ============================================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

VENV="$APP_DIR/.venv"
PY="$VENV/bin/python"
PIP="$VENV/bin/pip"
REQ="$APP_DIR/requirements.txt"
SETUP_STAMP="$APP_DIR/.setup_complete"
VOSK_MODEL_DIR="$APP_DIR/data/models/vosk"
VOSK_MODEL_NAME="vosk-model-small-en-us-0.15"
VOSK_MODEL_URL="https://alphacephei.com/vosk/models/${VOSK_MODEL_NAME}.zip"

ASSUME_YES=0
DO_SETUP_ONLY=0
DO_DEV=0
DO_AUTOSTART=0
PASSTHROUGH=()

for arg in "$@"; do
  case "$arg" in
    -y|--yes)        ASSUME_YES=1 ;;
    --setup)         DO_SETUP_ONLY=1 ;;
    --dev)           DO_DEV=1 ;;
    --autostart)     DO_AUTOSTART=1 ;;
    -h|--help)       sed -n '2,20p' "$0"; exit 0 ;;
    *)               PASSTHROUGH+=("$arg") ;;
  esac
done

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
info()  { printf '  \033[36m→\033[0m %s\n' "$*"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()   { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

ask() { # ask "question"
  if [ "$ASSUME_YES" -eq 1 ]; then return 0; fi
  read -r -p "  $1 [y/N] " reply
  [[ "$reply" =~ ^[Yy] ]]
}

# ----------------------------------------------------------------------------
bold "PSD AI Voice Assistant — Fedora setup"
# ----------------------------------------------------------------------------

# --- OS check (warning only so containers/derivatives still work) ----------
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  if [[ "${ID:-}" != "fedora" ]]; then
    warn "This script targets Fedora Workstation 44 (detected: ${PRETTY_NAME:-unknown})."
    warn "Continuing anyway — package names may differ."
  else
    info "OS: ${PRETTY_NAME}"
  fi
fi

# --- 1. System packages ------------------------------------------------------
DNF_PKGS=(
  python3 python3-pip python3-virtualenv python3-devel
  gcc                  # needed if pip must compile evdev (pynput dependency)
  python3-tkinter
  python3-gobject      # PyGObject — the AI orb (GTK3)
  python3-cairo        # cairo bindings for the orb gradient
  gtk3
  xorg-x11-server-Xwayland  # orb positioning on GNOME Wayland
  portaudio          # sounddevice backend
  espeak-ng          # TTS voices
  pulseaudio-utils   # pactl volume control
  xdg-utils gio2     # gio is part of glib2; listed via fallback below
  libnotify          # notify-send — agent answers display as notifications
  python3-evdev      # Wayland cursor-shake + hotkey detection (also satisfies pynput)
  unzip curl
)
# `gio` ships in glib2 — normalise the list:
DNF_PKGS=("${DNF_PKGS[@]/gio2/glib2}")

MISSING=()
if command -v dnf >/dev/null 2>&1; then
  for pkg in "${DNF_PKGS[@]}"; do
    rpm -q "$pkg" >/dev/null 2>&1 || MISSING+=("$pkg")
  done
  if [ "${#MISSING[@]}" -gt 0 ]; then
    info "Missing packages: ${MISSING[*]}"
    if ask "Install them with dnf? (needs sudo)"; then
      sudo dnf install -y "${MISSING[@]}" || die "dnf install failed"
      ok "System packages installed"
    else
      warn "Skipping packages — the assistant may not have audio/TTS."
    fi
  else
    ok "System packages present"
  fi
else
  warn "dnf not found — install manually: ${DNF_PKGS[*]}"
fi

# --- 2. input group (native Wayland cursor-shake) ----------------------------
if [ -e /dev/input/event0 ] && ! groups | grep -qw input; then
  if ask "Add your user to the 'input' group for cursor-shake on Wayland? (re-login needed once)"; then
    sudo usermod -aG input "$USER" || warn "usermod failed — HUD Talk button still works."
    warn "Log out and back in once for the input group to apply."
  fi
fi

# --- 3. Python virtualenv ----------------------------------------------------
need_venv=0
[ -x "$PY" ] || need_venv=1
[ "$DO_DEV" -eq 1 ] && need_venv=1
[ "$DO_SETUP_ONLY" -eq 1 ] && [ ! -x "$PY" ] && need_venv=1

if [ "$need_venv" -eq 1 ]; then
  info "Creating virtualenv…"
  if command -v virtualenv >/dev/null 2>&1; then
    # --system-site-packages: picks up dnf's python3-evdev / python3-tkinter
    virtualenv --system-site-packages -p python3 "$VENV" >/dev/null
  else
    python3 -m venv --system-site-packages "$VENV"
  fi
  [ -x "$PY" ] || die "could not create virtualenv"
  ok "Virtualenv ready"
fi

if [ "$DO_DEV" -eq 1 ] || [ ! -f "$SETUP_STAMP" ] || [ "$DO_SETUP_ONLY" -eq 1 ]; then
  info "Installing Python dependencies…"
  "$PIP" install --upgrade pip >/dev/null
  if ! "$PIP" install -r "$REQ"; then
    # pynput pulls in `evdev`, which needs Python headers/gcc. Fall back to a
    # wheel-only install; Wayland shake detection uses dnf's python3-evdev anyway.
    warn "Full pip install failed — retrying without compiled extras…"
    grep -v '^pynput' "$REQ" > "$REQ.partial" || true
    "$PIP" install -r "$REQ.partial" || die "pip install failed"
    rm -f "$REQ.partial"
    "$PIP" install --no-deps pynput || warn "pynput skipped (HUD Talk button still works)"
    "$PIP" install python-xlib six || true
  fi
  touch "$SETUP_STAMP"
  ok "Python dependencies ready"
fi

# --- 4. Vosk speech model ------------------------------------------------------
mkdir -p "$VOSK_MODEL_DIR"
if [ ! -d "$VOSK_MODEL_DIR/$VOSK_MODEL_NAME" ]; then
  info "Downloading offline speech model (~40 MB)…"
  tmpzip="$(mktemp --suffix=.zip)"
  if curl -fL --retry 3 -o "$tmpzip" "$VOSK_MODEL_URL"; then
    unzip -qo "$tmpzip" -d "$VOSK_MODEL_DIR"
    rm -f "$tmpzip"
    ok "Speech model installed"
  else
    rm -f "$tmpzip"
    warn "Model download failed — voice mode will ask you to retry --setup."
  fi
fi

# --- 5. Runtime data + config ---------------------------------------------------
mkdir -p data/agent_inbox data/agent_outbox data/calendar
[ -f config/config.yaml ] || die "config/config.yaml missing"

# --- 6. Optional GNOME autostart ---------------------------------------------------
if [ "$DO_AUTOSTART" -eq 1 ]; then
  AUTOSTART_DIR="$HOME/.config/autostart"
  mkdir -p "$AUTOSTART_DIR"
  cat > "$AUTOSTART_DIR/psd-ai-voice.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=PSD AI Voice Assistant
Comment=Always-ready voice assistant (cursor-shake activation)
Exec=$APP_DIR/run.sh --quiet-start
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
  ok "Autostart entry installed → $AUTOSTART_DIR/psd-ai-voice.desktop"
fi

if [ "$DO_SETUP_ONLY" -eq 1 ]; then
  bold "Setup complete. Start with: ./run.sh"
  exit 0
fi

# --- 7. Launch ---------------------------------------------------------------------
bold "Starting PSD Voice…"
# --quiet-start is just passthrough noise suppression for autostart
EXEC_ARGS=()
for a in "${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}"; do
  [ "$a" = "--quiet-start" ] || EXEC_ARGS+=("$a")
done

exec "$PY" -m assistant "${EXEC_ARGS[@]+"${EXEC_ARGS[@]}"}"
