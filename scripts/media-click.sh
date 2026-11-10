#!/usr/bin/env bash
# Click on the media module:
#   a player is playing/paused -> play/pause
#   otherwise                  -> open VLC on the last played song's folder, starting at that song
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/waybar-media-last"

case $(playerctl status 2>/dev/null) in
  Playing|Paused) exec playerctl play-pause ;;
esac

{ IFS= read -r _; IFS= read -r _; IFS= read -r url; } 2>/dev/null < "$STATE"
if [[ $url != file://* ]]; then
  notify-send -a Media "Nothing to resume" "Play a local song once first."
  exit 0
fi

# file:///home/me/Music/a%20b.mp3 -> /home/me/Music/a b.mp3
enc=${url#file://}
enc=${enc//\\/\\\\}
path=$(printf '%b' "${enc//%/\\x}")
dir=$(dirname "$path")

if [[ ! -f $path ]]; then
  notify-send -a Media "File not found" "$path"
  exit 0
fi

# every audio file in that folder, in natural order
mapfile -d '' -t files < <(
  find "$dir" -maxdepth 1 -type f \( -iname '*.mp3' -o -iname '*.flac' -o -iname '*.ogg' \
    -o -iname '*.opus' -o -iname '*.m4a' -o -iname '*.wav' -o -iname '*.aac' -o -iname '*.wma' \) \
    -print0 | sort -zV
)

# start at the saved song, then continue through the folder, wrapping around
idx=-1
for i in "${!files[@]}"; do
  [[ ${files[$i]} == "$path" ]] && { idx=$i; break; }
done
if (( idx >= 0 )); then
  queue=("${files[@]:idx}" "${files[@]:0:idx}")
else
  queue=("$path")
fi

setsid -f flatpak run org.videolan.VLC "${queue[@]}" >/dev/null 2>&1