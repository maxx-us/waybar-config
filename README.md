# Waybar configuration

Run the single installer from this directory:

```sh
./install.sh
```

It offers a core profile (clock, workspaces, language picker) or the full
configuration. It installs to `$XDG_CONFIG_HOME/waybar`, or `~/.config/waybar`
when `XDG_CONFIG_HOME` is unset. Existing files are backed up before
replacement. For a test or alternate location, pass `--config-dir /absolute/path`.

The installer detects `pacman`, `apt`, or `dnf` and asks before installing
profile packages. You can skip the selector with `--profile core` or
`--profile full`, and use `--install-deps` or `--skip-deps` for package handling.

The language module reads layouts from Niri and opens a GTK layer-shell popup
above the clicked module. The core profile installs Waybar, Niri, GTK 3, and
gtk-layer-shell. The full profile also installs common utilities for the
bundled modules. The optional display module is hidden when `niri-displays` is
unavailable; distro-specific GTK layer-shell packages and the native taskbar
module may still require manual setup.