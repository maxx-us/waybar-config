#!/usr/bin/env bash
# Waybar scroll volume step.
# NOTE: directions are kept as in your original (scroll-up -> "up" -> volume down).
SINK=@DEFAULT_AUDIO_SINK@

# throttle: ignore events within 80 ms
f=/tmp/waybar-vol-throttle
now=$(date +%s%N)
last=$(cat "$f" 2>/dev/null || echo 0)
(( now - last < 60000000 )) && exit 0
echo "$now" > "$f"

STEP=1

case $1 in
    down) wpctl set-volume -l 1.0 "$SINK" "${STEP}%+" ;;
    up)   wpctl set-volume "$SINK" "${STEP}%-" ;;
esac