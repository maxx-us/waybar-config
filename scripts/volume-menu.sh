#!/usr/bin/env bash
# Copy the exact menu command from your wifi-menu.sh into this line,
# so the volume menu gets the same theme. Examples:
#   MENU=(rofi -dmenu -i -p "󰕾")
#   MENU=(wofi --dmenu -i)
#   MENU=(fuzzel --dmenu)
MENU=(rofi -dmenu -i -p "󰕾")

SINK=@DEFAULT_AUDIO_SINK@

bar() {
    local i s=""
    for ((i = 0; i < 10; i++)); do
        if ((i < $1 / 10)); then s+="█"; else s+="░"; fi
    done
    printf '%s' "$s"
}

while true; do
    out=$(wpctl get-volume "$SINK")
    vol=$(awk '{printf "%d", $2 * 100 + 0.5}' <<<"$out")
    icon="󰕾"
    [[ $out == *MUTED* ]] && icon="󰝟"

    entries="$icon  $(bar "$vol") ${vol}%
󰐕  Louder (+5%)
󰍴  Quieter (-5%)
󰖁  Mute / Unmute"
    for v in 100 90 80 70 60 50 40 30 20 10 0; do
        entries+=$'\n'"󰕾  $(bar "$v") ${v}%"
    done

    choice=$(printf '%s\n' "$entries" | "${MENU[@]}") || exit 0
    [[ -z $choice ]] && exit 0

    case $choice in
        *Louder*)  wpctl set-volume -l 1.0 "$SINK" 5%+ ;;
        *Quieter*) wpctl set-volume "$SINK" 5%- ;;
        *Mute*)    wpctl set-mute "$SINK" toggle ;;
        *"%")
            pct=${choice##* }
            wpctl set-mute "$SINK" 0
            wpctl set-volume -l 1.0 "$SINK" "${pct%\%}%"
            exit 0 ;;
    esac
done