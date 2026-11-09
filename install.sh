#!/bin/sh
# Adds (or with --uninstall removes) the GNOME hotkey and login autostart for
# the kanji popup. Other custom shortcuts are left untouched.
set -eu

DIR=$(cd "$(dirname "$0")" && pwd)
HOTKEY='<Primary>z'
AUTOSTART=$HOME/.config/autostart/kanji-popup.desktop
LIST_SCHEMA=org.gnome.settings-daemon.plugins.media-keys
SLOT=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/kanji-popup/
SLOT_SCHEMA=$LIST_SCHEMA.custom-keybinding:$SLOT

update_list() {
    new=$(gsettings get "$LIST_SCHEMA" custom-keybindings | python3 -c '
import ast, sys
raw = sys.stdin.read().strip().removeprefix("@as").strip()
slots = [s for s in ast.literal_eval(raw) if s != sys.argv[2]]
if sys.argv[1] == "add":
    slots.append(sys.argv[2])
print(slots)
' "$1" "$SLOT")
    gsettings set "$LIST_SCHEMA" custom-keybindings "$new"
}

if [ "${1:-}" = "--uninstall" ]; then
    update_list remove
    gsettings reset "$SLOT_SCHEMA" name
    gsettings reset "$SLOT_SCHEMA" command
    gsettings reset "$SLOT_SCHEMA" binding
    rm -f "$AUTOSTART"
    /usr/bin/python3 "$DIR/kanji_popup.py" --quit || true
    echo "Hotkey and autostart removed."
    exit 0
fi

gsettings set "$SLOT_SCHEMA" name 'Kanji popup'
gsettings set "$SLOT_SCHEMA" command "/usr/bin/python3 $DIR/kanji_popup.py"
gsettings set "$SLOT_SCHEMA" binding "$HOTKEY"
update_list add

mkdir -p "$(dirname "$AUTOSTART")"
cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=Kanji popup
Comment=Show a kanji card for the highlighted text
Exec=/usr/bin/python3 $DIR/kanji_popup.py --background
Terminal=false
X-GNOME-Autostart-enabled=true
EOF

echo "Hotkey installed: Ctrl+Z"
echo "Autostart installed: $AUTOSTART"
