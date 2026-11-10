#!/usr/bin/env bash

sink='@DEFAULT_AUDIO_SINK@'
output=$(wpctl get-volume "$sink" 2>/dev/null) || exit 1
volume=$(awk '/Volume:/ {printf "%d", $2 * 100 + 0.5}' <<<"$output")
[[ -n $volume ]] || exit 1

icon='󰕾'
if [[ $output == *MUTED* ]]; then
    icon='󰝟'
fi

printf '{"text":"%s %s%%","tooltip":"Volume: %s%%"}\n' "$icon" "$volume" "$volume"
