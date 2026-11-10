#!/usr/bin/env bash
# Waybar media module: shows the current song, or the last played one when nothing is running.
# Needs: playerctl
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/waybar-media-last"
MAXLEN=40
mkdir -p "$(dirname "$STATE")"

# If Firefox/Chrome tabs steal the display, ignore them: PC=(playerctl -i firefox,chromium)
PC=(playerctl)

esc()  { local s=$1; s=${s//&/"&amp;"}; s=${s//</"&lt;"}; s=${s//>/"&gt;"}; printf '%s' "$s"; }   # pango markup
jstr() { local s=$1; s=${s//\\/\\\\}; s=${s//\"/\\\"}; s=${s//$'\n'/\\n}; printf '%s' "$s"; }  # JSON string
emit() { printf '{"text":"%s","tooltip":"%s","class":"%s"}\n' "$(jstr "$1")" "$(jstr "$2")" "$3"; }
urldec() { local s=$1; s=${s//\\/\\\\}; printf '%b' "${s//%/\\x}"; }   # %20 -> space

TIPCACHE="$(dirname "$STATE")/waybar-media-tooltip"
TIP_NAMELEN=44

# Hover text: 1 previous, current, 2 next songs of the folder (same order click-to-resume uses).
# Cached per song, so the folder is only scanned when the song changes.
queue_tip() {   # file:// url -> pango text on stdout; returns 1 if unavailable
  local url=$1 path dir i idx k name out cached
  local -a files
  cached=$(head -n1 "$TIPCACHE" 2>/dev/null)
  if [[ $cached == "$url" ]]; then tail -n +2 "$TIPCACHE"; return 0; fi

  path=$(urldec "${url#file://}"); dir=${path%/*}
  [[ -f $path ]] || return 1
  mapfile -d '' -t files < <(
    find "$dir" -maxdepth 1 -type f \( -iname '*.mp3' -o -iname '*.flac' -o -iname '*.ogg' \
      -o -iname '*.opus' -o -iname '*.m4a' -o -iname '*.wav' -o -iname '*.aac' -o -iname '*.wma' \) \
      -print0 | sort -zV
  )
  idx=-1
  for i in "${!files[@]}"; do [[ ${files[$i]} == "$path" ]] && { idx=$i; break; }; done
  (( idx < 0 )) && return 1

  out="<i>Playlist · $((idx + 1)) / ${#files[@]}</i>"
  for k in $((idx - 1)) "$idx" $((idx + 1)) $((idx + 2)); do
    (( k < 0 || k >= ${#files[@]} )) && continue
    name=${files[$k]##*/}; name=${name%.*}
    (( ${#name} > TIP_NAMELEN )) && name="${name:0:TIP_NAMELEN-1}…"
    name=$(esc "$name")
    if   (( k < idx )); then out+=$'\n'"󰒮  $name"
    elif (( k == idx )); then out+=$'\n'"󰐊  <b>$name</b>"
    else                      out+=$'\n'"󰒭  $name"
    fi
  done
  printf '%s\n%s\n' "$url" "$out" > "$TIPCACHE"
  printf '%s\n' "$out"
}


status=$("${PC[@]}" status 2>/dev/null)
artist="" title="" url=""

case $status in
  Playing|Paused)
    artist=$("${PC[@]}" metadata artist 2>/dev/null)
    title=$("${PC[@]}" metadata title 2>/dev/null)
    url=$("${PC[@]}" metadata xesam:url 2>/dev/null)
    # only remember local files (those can be reopened in VLC)
    if [[ $url == file://* ]]; then
      new=$(printf '%s\n%s\n%s\n' "$artist" "$title" "$url")
      [[ $new == "$(cat "$STATE" 2>/dev/null)" ]] || printf '%s\n' "$new" > "$STATE"
    fi
    ;;
  *)
    status=Stopped
    { IFS= read -r artist; IFS= read -r title; IFS= read -r url; } 2>/dev/null < "$STATE"
    ;;
esac

if [[ -z $title && -z $url ]]; then
  emit "󰎆  Nothing played yet" "Nothing played yet" empty
  exit 0
fi

# fall back to the file name if the tag is empty
if [[ -z $title ]]; then
  title=$(urldec "${url##*/}"); title=${title%.*}
elif [[ $title != *" "* && $title =~ %[0-9A-Fa-f]{2} ]]; then
  title=$(urldec "$title")     # some players hand over the still-encoded file name
fi

case $status in
  Playing) icon=''; cls=playing ;;
  *)       icon=''; cls=${status,,} ;;   # paused / stopped
esac

label=$title
[[ -n $artist ]] && label="$artist – $title"
(( ${#label} > MAXLEN )) && label="${label:0:MAXLEN-1}…"

# hover text: the surrounding songs if we can, else just the song
tip=""
[[ $url == file://* ]] && tip=$(queue_tip "$url")
if [[ -z $tip ]]; then
  tip=$(esc "$title"); [[ -n $artist ]] && tip=$(esc "$artist – $title")
fi
[[ $status == Stopped ]] && tip+=$'\n<i>last played – click to resume in VLC</i>'

emit "$icon $(esc "$label")" "$tip" "$cls"