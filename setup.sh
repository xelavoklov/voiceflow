#!/bin/bash
# One-shot setup for Ubuntu (GNOME/Wayland): system packages, uv, uinput access,
# the transcription environment and an application-menu launcher.
#   ./setup.sh [--yes] [--no-cuda] [--dry-run]
set -e
cd "$(dirname "$0")"

YES=0; CUDA=auto; DRY=0
for arg in "$@"; do
    case "$arg" in
        -y|--yes) YES=1 ;;
        --no-cuda) CUDA=no ;;
        --dry-run) DRY=1 ;;
        -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
        *) echo "Unknown option: $arg"; exit 1 ;;
    esac
done

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
run()  { if [ "$DRY" = 1 ]; then echo "[dry-run] $*"; else "$@"; fi; }
ask()  { [ "$YES" = 1 ] && return 0; read -r -p "$1 [Y/n] " a; [[ -z "$a" || "$a" =~ ^[Yy] ]]; }

command -v apt-get >/dev/null || { echo "This script supports Debian/Ubuntu only (apt-get not found)."; exit 1; }
[ "$XDG_SESSION_TYPE" = "wayland" ] || echo "Note: VoiceFlow is built for GNOME on Wayland; you are on '${XDG_SESSION_TYPE:-unknown}'."

say "System packages"
PKGS="python3-gi python3-gi-cairo gir1.2-gtk-3.0 pipewire-bin wl-clipboard libnotify-bin"
MISSING=""
for p in $PKGS; do dpkg -s "$p" >/dev/null 2>&1 || MISSING="$MISSING $p"; done
if [ -n "$MISSING" ]; then
    echo "Installing:$MISSING (sudo required)"
    run sudo apt-get update -qq
    run sudo apt-get install -y $MISSING
else
    echo "All present."
fi

say "uv (Python environment manager)"
if command -v uv >/dev/null || [ -x "$HOME/.local/bin/uv" ]; then
    echo "Found."
else
    if ask "uv is not installed. Install it with the official installer (https://astral.sh/uv)?"; then
        if [ "$DRY" = 1 ]; then echo "[dry-run] curl -LsSf https://astral.sh/uv/install.sh | sh"; else curl -LsSf https://astral.sh/uv/install.sh | sh; fi
    else
        echo "uv is required."; exit 1
    fi
fi
export PATH="$HOME/.local/bin:$PATH"

say "Access to /dev/uinput (needed to press Ctrl+V)"
if [ -w /dev/uinput ]; then
    echo "Already writable."
elif ask "Install the udev rule (sudo) so your user can create a virtual keyboard?"; then
    run sudo cp contrib/70-voiceflow-uinput.rules /etc/udev/rules.d/
    run sudo udevadm control --reload
    run sudo udevadm trigger /dev/uinput
    echo "If pasting does not work right away, log out and back in once."
fi

say "Transcription environment"
CUDA_FLAG=""
if [ "$CUDA" = auto ] && command -v nvidia-smi >/dev/null; then
    if ldconfig -p 2>/dev/null | grep -q "libcublas.so.12" && ldconfig -p 2>/dev/null | grep -q "libcudnn.so.9"; then
        echo "NVIDIA GPU with cuBLAS 12 / cuDNN 9 found: GPU will be used."
    elif ask "NVIDIA GPU detected. Download CUDA libraries from pip (~1 GB) for GPU transcription?"; then
        CUDA_FLAG="--cuda"
    fi
fi
run ./install.sh $CUDA_FLAG

say "Application menu launcher"
run ./install-desktop.sh

say "Done"
echo "Start it from the application menu (search for VoiceFlow) or run ./run.sh"
echo "The first launch downloads the speech model (~1.6 GB for large-v3-turbo; pick a smaller one with a right click)."
