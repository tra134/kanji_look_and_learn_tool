# Kanji Popup

Highlight a kanji anywhere on your Linux desktop, press a hotkey, and a small card shows its picture, Hán Việt reading, Vietnamese meaning and memory tips. Inspired by Kanji Look and Learn, with content from [tiengnhatdongian.com](https://www.tiengnhatdongian.com/).

## Requirements

GNOME on Wayland, plus these packages (Debian/Ubuntu names):

```sh
sudo apt install python3-gi gir1.2-gtk-3.0 python3-requests python3-bs4 wl-clipboard libnotify-bin fonts-noto-cjk
```

## Install

```sh
git clone https://github.com/tra134/kanji_look_and_learn_tool.git
cd kanji_look_and_learn_tool
./install.sh
```

This adds the `Ctrl+Z` hotkey and starts the tool at login. If you move the folder, run `./install.sh` again.

> **Note:** while this hotkey is set, `Ctrl+Z` opens the popup instead of Undo in every app. To use another key, change `HOTKEY` in `install.sh` and run it again, for example `HOTKEY='<Alt>z'`.

## Usage

Highlight text and press `Ctrl+Z`. No copying is needed. If several kanji are highlighted, each gets its own card, up to 10.

| Control | Action |
|---|---|
| `←` / `→` or arrow buttons | Previous / next kanji |
| `Esc`, red button, or click elsewhere | Close |
| Yellow button | Minimize |
| Green button | Maximize / restore |
| Sun / moon button | Switch light / dark mode |
| Globe button | Open the full page in your browser |

Kanji you've looked up are saved in `~/.cache/kanji-popup/`, so they open instantly and work offline.

## Uninstall

```sh
./install.sh --uninstall
```

## Credits

All kanji content and pictures belong to [tiengnhatdongian.com](https://www.tiengnhatdongian.com/). The tool fetches them only when you look up a kanji. Some kanji have no picture on the site, so their card shows text only.
