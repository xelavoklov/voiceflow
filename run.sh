#!/bin/bash
# Launch VoiceFlow. Optional settings go to ~/.config/voiceflow/env (see README).
cd "$(dirname "$0")"
[ -f "$HOME/.config/voiceflow/env" ] && . "$HOME/.config/voiceflow/env"
if [ -n "$VOICEFLOW_NO_PROXY" ]; then
    unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
fi
exec /usr/bin/python3 app.py "$@"
