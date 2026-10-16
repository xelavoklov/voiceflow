#!/bin/bash
# Добавляет VoiceFlow в меню приложений (ярлык + иконка) для текущего пользователя.
set -e
cd "$(dirname "$0")"
ROOT="$PWD"
install -Dm644 assets/voiceflow.svg "$HOME/.local/share/icons/hicolor/scalable/apps/voiceflow.svg"
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/voiceflow.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=VoiceFlow
Comment=Диктовка в любое поле ввода
Exec=$ROOT/run.sh
Icon=voiceflow
Terminal=false
Categories=Utility;
StartupWMClass=voiceflow
EOF
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
echo "Готово: VoiceFlow появится в меню приложений."
