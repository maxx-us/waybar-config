#!/usr/bin/env bash
f=/tmp/waybar-bright-throttle
now=$(date +%s%N)
last=$(cat "$f" 2>/dev/null || echo 0)
(( now - last < 50000000 )) && exit 0   # ignore events within 100 ms
echo "$now" > "$f"

case $1 in
    up)   brightnessctl set +1% ;;
    down) brightnessctl -n1 set 1%- ;;
esac