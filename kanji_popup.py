#!/usr/bin/python3
"""Kanji popup: show the highlighted kanji's card from tiengnhatdongian.com.

Bound to a GNOME hotkey. The first run stays in the background; later runs
hand over to it through D-Bus, so the card opens quickly.

Usage:
    kanji_popup.py                 look up the highlighted text
    kanji_popup.py --kanji 日本    look up the given text
    kanji_popup.py --background    start quietly, without a lookup (used at login)
    kanji_popup.py --quit          stop the background process
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

APP_ID = "com.traphan.KanjiPopup"
SITE_HOST = "www.tiengnhatdongian.com"
PAGE_URL = "https://" + SITE_HOST + "/kanji/giai-nghia-kanji-{}/"
PLACEHOLDER_IMAGE = "giai_nghia_kanji.png"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}
TIMEOUT = (5, 15)
CACHE_DIR = (
    Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "kanji-popup"
)
MAX_KANJI = 10
WINDOW_WIDTH = 420
WINDOW_HEIGHT = 600
IMAGE_WIDTH = 368
IMAGE_HEIGHT = 270

KANJI_RE = re.compile(
    "[㐀-䶿一-鿿豈-﫿\U00020000-\U0002ffff]"
)

PALETTES = {
    True: """
@define-color kp_bg #26252b;
@define-color kp_header #302f36;
@define-color kp_fg #eeedf2;
@define-color kp_accent #a566bd;
""",
    False: """
@define-color kp_bg #fbfafc;
@define-color kp_header #eeebf2;
@define-color kp_fg #29272e;
@define-color kp_accent #8e4ba8;
""",
}

CSS = """
.kanji-window {
    color: @kp_fg;
    background-color: @kp_bg;
    border-radius: 0 0 24px 24px;
}
.kanji-window decoration {
    border-radius: 24px;
    background-color: @kp_bg;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35), 0 0 0 1px rgba(0, 0, 0, 0.12);
}
.kanji-window headerbar {
    color: @kp_fg;
    background-image: none;
    background-color: @kp_header;
    border: none;
    box-shadow: none;
    border-radius: 24px 24px 0 0;
}
.kanji-window headerbar .title { color: @kp_fg; }
.kanji-window.maximized,
.kanji-window.maximized decoration,
.kanji-window.maximized headerbar { border-radius: 0; }
.kanji-window headerbar button:not(.traffic) {
    color: @kp_fg;
    min-width: 28px;
    min-height: 28px;
    padding: 2px;
    border: none;
    border-radius: 12px;
    background-image: none;
    background-color: transparent;
    box-shadow: none;
}
.kanji-window headerbar button:not(.traffic):hover {
    background-color: alpha(@kp_fg, 0.12);
}
.kanji-window headerbar button:disabled { color: alpha(@kp_fg, 0.3); }
.kanji-window scrolledwindow,
.kanji-window viewport,
.kanji-window overshoot,
.kanji-window undershoot { background: none; border: none; box-shadow: none; }
.kanji-window scrollbar { background: none; border: none; }
.kanji-window scrollbar slider {
    min-width: 6px;
    border: none;
    border-radius: 999px;
    background-color: alpha(@kp_fg, 0.35);
}
.kanji-big {
    font-family: "Noto Serif CJK JP", "Noto Sans CJK JP", serif;
    font-size: 72px;
    padding: 6px 16px;
    border-radius: 24px;
    background-color: alpha(@kp_accent, 0.18);
}
.kanji-reading {
    font-size: 17px;
    font-weight: bold;
    padding: 6px 14px;
    border-radius: 999px;
    color: #ffffff;
    background-color: @kp_accent;
}
.section-title {
    font-size: 12px;
    font-weight: bold;
    color: alpha(@kp_fg, 0.65);
}
.picture {
    background-color: #ffffff;
    border-radius: 20px;
    border: 1px solid alpha(@kp_fg, 0.08);
}
.tip {
    padding: 9px 14px;
    border-radius: 16px;
    background-color: alpha(@kp_fg, 0.07);
}
.traffic {
    min-width: 14px;
    min-height: 14px;
    padding: 0;
    margin: 0;
    border: none;
    border-radius: 50%;
    background-image: none;
    box-shadow: none;
    outline: none;
}
.traffic label {
    font-size: 10px;
    font-weight: bold;
    color: transparent;
}
.traffic:hover label { color: rgba(0, 0, 0, 0.65); }
.traffic-close { background-color: #ff5f57; }
.traffic-minimize { background-color: #febc2e; }
.traffic-maximize { background-color: #28c840; }
.traffic-close:hover { background-color: #ff453a; }
.traffic-minimize:hover { background-color: #ffb000; }
.traffic-maximize:hover { background-color: #1fb636; }
"""

SETTINGS_PATH = (
    Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    / "kanji-popup"
    / "settings.json"
)


class NotFound(Exception):
    """The site has no page for this kanji."""


# --- Settings --------------------------------------------------------------


def load_dark_mode():
    """Return the saved mode, or the system's preference if none is saved."""
    try:
        return bool(json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))["dark"])
    except (OSError, ValueError, KeyError, TypeError):
        interface = Gio.Settings.new("org.gnome.desktop.interface")
        return interface.get_string("color-scheme") == "prefer-dark"


def save_dark_mode(dark):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(SETTINGS_PATH, json.dumps({"dark": dark}).encode("utf-8"))


# --- Selection -------------------------------------------------------------


def _paste(*flags):
    wl_paste = shutil.which("wl-paste")
    if wl_paste is None:
        return ""
    try:
        result = subprocess.run(
            [wl_paste, "--no-newline", "--type", "text", *flags],
            capture_output=True,
            timeout=3,
        )
    except subprocess.TimeoutExpired:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8", "ignore")


def read_selection():
    """Return the highlighted text, or the clipboard if nothing is highlighted."""
    return _paste("--primary") or _paste()


def extract_kanji(text):
    """Return the unique kanji in text, in order, capped at MAX_KANJI."""
    return list(dict.fromkeys(KANJI_RE.findall(text)))[:MAX_KANJI]


# --- Fetching and cache ----------------------------------------------------


def page_url(kanji):
    return PAGE_URL.format(urllib.parse.quote(kanji))


def parse_page(html, kanji):
    """Extract a kanji's details from its page; raises ValueError on a new layout."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    reading = soup.select_one("h3.txt-kanji")
    if reading is None:
        raise ValueError("page layout not recognised")

    meaning = ""
    description = soup.find("meta", property="og:description")
    if description and description.get("content"):
        meaning = description["content"].split(" : ", 1)[-1].strip()

    tips = []
    for paragraph in soup.select("#remember p.mean"):
        for line_break in paragraph.find_all("br"):
            line_break.replace_with("\n")
        tip = paragraph.get_text().strip()
        if tip:
            tips.append(tip)

    image_url = None
    image = soup.select_one("img.kanji-img-thumb")
    if image and image.get("src"):
        candidate = urllib.parse.urljoin(page_url(kanji), image["src"])
        parts = urllib.parse.urlsplit(candidate)
        if (
            PLACEHOLDER_IMAGE not in candidate
            and parts.scheme == "https"
            and parts.hostname == SITE_HOST
        ):
            image_url = candidate

    return {
        "kanji": kanji,
        "reading": " ".join(reading.get_text().split()),
        "meaning": meaning,
        "tips": tips,
        "image_url": image_url,
    }


def _write_atomic(path, data):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def lookup(kanji):
    """Return a kanji's details, from the cache when possible.

    The entry's "image_path" is None when the site has no real picture.
    Raises NotFound, requests.RequestException or ValueError.
    """
    import requests

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{ord(kanji):x}"
    entry_path = CACHE_DIR / f"{stem}.json"
    image_path = CACHE_DIR / f"{stem}.img"

    try:
        entry = json.loads(entry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        entry = None
    if entry is not None and (not entry["image_url"] or image_path.exists()):
        entry["image_path"] = str(image_path) if entry["image_url"] else None
        return entry

    response = requests.get(page_url(kanji), headers=HEADERS, timeout=TIMEOUT)
    if response.status_code == 404:
        raise NotFound(kanji)
    response.raise_for_status()
    entry = parse_page(response.text, kanji)

    if entry["image_url"]:
        picture = requests.get(entry["image_url"], headers=HEADERS, timeout=TIMEOUT)
        picture.raise_for_status()
        _write_atomic(image_path, picture.content)

    _write_atomic(entry_path, json.dumps(entry, ensure_ascii=False).encode("utf-8"))
    entry["image_path"] = str(image_path) if entry["image_url"] else None
    return entry


# --- Widgets ---------------------------------------------------------------


def _label(text, css_class=None):
    label = Gtk.Label(label=text)
    label.set_xalign(0)
    label.set_line_wrap(True)
    label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.set_max_width_chars(44)
    if css_class:
        label.get_style_context().add_class(css_class)
    return label


def _chip(text, css_class):
    """A label that hugs its text, so its CSS background doesn't fill the row."""
    label = _label(text, css_class)
    label.set_halign(Gtk.Align.START)
    label.set_valign(Gtk.Align.CENTER)
    return label


def _traffic_button(css_class, glyph, tooltip, on_click):
    button = Gtk.Button(label=glyph)
    button.set_valign(Gtk.Align.CENTER)
    button.set_can_focus(False)
    button.set_tooltip_text(tooltip)
    context = button.get_style_context()
    context.add_class("traffic")
    context.add_class(css_class)
    button.connect("clicked", on_click)
    return button


def _card_box():
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    box.set_border_width(14)
    return box


def build_card(entry):
    """Build the card for a looked-up kanji."""
    box = _card_box()

    header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
    header.pack_start(_chip(entry["kanji"], "kanji-big"), False, False, 0)
    header.pack_start(_chip(entry["reading"], "kanji-reading"), False, False, 0)
    box.pack_start(header, False, False, 0)

    if entry.get("image_path"):
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                entry["image_path"], IMAGE_WIDTH, IMAGE_HEIGHT, True
            )
        except GLib.Error:
            pixbuf = None
        if pixbuf is not None:
            picture = Gtk.Box()
            picture.get_style_context().add_class("picture")
            image = Gtk.Image.new_from_pixbuf(pixbuf)
            image.set_margin_top(10)
            image.set_margin_bottom(10)
            picture.pack_start(image, True, True, 0)
            box.pack_start(picture, False, False, 0)

    if entry["meaning"]:
        box.pack_start(_label("NGHĨA", "section-title"), False, False, 0)
        box.pack_start(_label(entry["meaning"]), False, False, 0)

    if entry["tips"]:
        box.pack_start(_label("MẸO NHỚ NHANH", "section-title"), False, False, 0)
        for tip in entry["tips"]:
            box.pack_start(_label(tip, "tip"), False, False, 0)

    return box


def build_message(kanji, message, loading=False):
    """Build a placeholder card for the loading and error states."""
    box = _card_box()
    box.pack_start(_chip(kanji, "kanji-big"), False, False, 0)
    if loading:
        spinner = Gtk.Spinner()
        spinner.start()
        box.pack_start(spinner, False, False, 0)
    box.pack_start(_label(message), False, False, 0)
    return box


# --- Application -----------------------------------------------------------


class KanjiApp(Gtk.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.window = None
        self.scroller = None
        self.previous_button = None
        self.next_button = None
        self.items = []
        self.index = 0
        self.token = 0
        self.close_on_blur = False
        self.theme_button = None
        self.css_provider = None
        self.dark = True

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()
        self.dark = load_dark_mode()
        self.css_provider = Gtk.CssProvider()
        self._apply_theme()
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            self.css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _apply_theme(self):
        self.css_provider.load_from_data((PALETTES[self.dark] + CSS).encode("utf-8"))

    def _update_theme_button(self):
        icon = "weather-clear-symbolic" if self.dark else "weather-clear-night-symbolic"
        self.theme_button.set_image(
            Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
        )
        self.theme_button.set_tooltip_text(
            "Switch to light mode" if self.dark else "Switch to dark mode"
        )

    def _toggle_theme(self, _button):
        self.dark = not self.dark
        self._apply_theme()
        self._update_theme_button()
        save_dark_mode(self.dark)

    def do_command_line(self, command_line):
        args = command_line.get_arguments()[1:]
        if args == ["--quit"]:
            self.quit()
            return 0
        if args == ["--background"]:
            return 0
        if args and not (len(args) == 2 and args[0] == "--kanji"):
            command_line.printerr_literal(__doc__)
            return 2
        self.token += 1
        text = args[1] if args else None
        threading.Thread(
            target=self._read_and_open, args=(self.token, text), daemon=True
        ).start()
        return 0

    def _read_and_open(self, token, text):
        # The selection is read before the window exists, so the window can't
        # interfere with whichever app currently owns the highlighted text.
        kanji = extract_kanji(read_selection() if text is None else text)
        if kanji:
            GLib.idle_add(self._open, token, kanji)
        else:
            notifier = shutil.which("notify-send")
            if notifier:
                subprocess.run(
                    [notifier, "-t", "2000", "Kanji popup", "No kanji in the selection"],
                    check=False,
                )

    def _open(self, token, kanji):
        if token == self.token:
            # A new toplevel each time, because GNOME gives keyboard focus to
            # new windows but only flashes "is ready" for existing ones.
            if self.window is not None:
                self.window.destroy()
            self.items = kanji
            self.index = 0
            self._create_window()
            self._show_current()
        return GLib.SOURCE_REMOVE

    def _create_window(self):
        window = Gtk.ApplicationWindow(application=self)
        window.get_style_context().add_class("kanji-window")
        window.set_default_size(WINDOW_WIDTH, self._window_height())
        window.connect("key-press-event", self._on_key)
        window.connect("focus-in-event", self._on_focus_in)
        window.connect("focus-out-event", self._on_focus_out)
        window.connect("destroy", self._on_destroy)

        bar = Gtk.HeaderBar(show_close_button=False)
        traffic = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        traffic.add(
            _traffic_button(
                "traffic-close", "×", "Close", lambda _b: self._close(window)
            )
        )
        traffic.add(
            _traffic_button(
                "traffic-minimize", "−", "Minimize", lambda _b: self._minimize(window)
            )
        )
        traffic.add(
            _traffic_button(
                "traffic-maximize",
                "+",
                "Maximize",
                lambda _b: self._toggle_maximize(window),
            )
        )
        bar.pack_start(traffic)

        self.previous_button = Gtk.Button.new_from_icon_name(
            "go-previous-symbolic", Gtk.IconSize.BUTTON
        )
        self.previous_button.connect("clicked", lambda _b: self._move(-1))
        self.next_button = Gtk.Button.new_from_icon_name(
            "go-next-symbolic", Gtk.IconSize.BUTTON
        )
        self.next_button.connect("clicked", lambda _b: self._move(1))
        open_button = Gtk.Button.new_from_icon_name(
            "web-browser-symbolic", Gtk.IconSize.BUTTON
        )
        open_button.set_tooltip_text("Open full page")
        open_button.connect("clicked", self._open_in_browser)
        bar.pack_end(open_button)
        self.theme_button = Gtk.Button()
        self.theme_button.set_can_focus(False)
        self.theme_button.connect("clicked", self._toggle_theme)
        self._update_theme_button()
        bar.pack_end(self.theme_button)
        if len(self.items) > 1:
            bar.pack_end(self.next_button)
            bar.pack_end(self.previous_button)
        window.set_titlebar(bar)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        window.add(self.scroller)

        self.window = window
        self.close_on_blur = False
        window.show_all()

    @staticmethod
    def _window_height():
        monitor = Gdk.Display.get_default().get_monitor(0)
        if monitor is None:
            return WINDOW_HEIGHT
        return min(WINDOW_HEIGHT, monitor.get_workarea().height - 80)

    def _set_content(self, widget):
        child = self.scroller.get_child()
        if child is not None:
            self.scroller.remove(child)
        self.scroller.add(widget)
        self.scroller.show_all()

    def _show_current(self):
        self.token += 1
        kanji = self.items[self.index]
        self.window.get_titlebar().set_title(
            f"{kanji}  ({self.index + 1}/{len(self.items)})"
        )
        self.previous_button.set_sensitive(self.index > 0)
        self.next_button.set_sensitive(self.index < len(self.items) - 1)
        self._set_content(build_message(kanji, "Loading…", loading=True))
        threading.Thread(
            target=self._load, args=(self.token, kanji), daemon=True
        ).start()

    def _load(self, token, kanji):
        try:
            entry, message = lookup(kanji), None
        except NotFound:
            entry, message = None, "This kanji isn't on the site."
        except ValueError:
            entry, message = None, "The site's page layout has changed."
        except Exception:  # network and disk errors; shown on the card
            entry, message = None, "Couldn't load this kanji. Check your connection."
        GLib.idle_add(self._show_result, token, kanji, entry, message)

    def _show_result(self, token, kanji, entry, message):
        if token == self.token and self.window is not None:
            self._set_content(
                build_card(entry) if entry else build_message(kanji, message)
            )
        return GLib.SOURCE_REMOVE

    def _move(self, step):
        target = self.index + step
        if 0 <= target < len(self.items):
            self.index = target
            self._show_current()

    def _open_in_browser(self, _button):
        Gio.AppInfo.launch_default_for_uri(page_url(self.items[self.index]), None)

    def _close(self, window):
        if window is self.window:
            window.destroy()
        return GLib.SOURCE_REMOVE

    def _minimize(self, window):
        # Minimizing loses focus, which would otherwise close the card.
        # Focus returning on restore turns click-away closing back on.
        self.close_on_blur = False
        window.iconify()

    @staticmethod
    def _toggle_maximize(window):
        if window.is_maximized():
            window.unmaximize()
        else:
            window.maximize()

    def _on_key(self, window, event):
        if event.keyval == Gdk.KEY_Escape:
            self._close(window)
        elif event.keyval == Gdk.KEY_Left:
            self._move(-1)
        elif event.keyval == Gdk.KEY_Right:
            self._move(1)
        else:
            return False
        return True

    def _on_focus_in(self, _window, _event):
        self.close_on_blur = True
        return False

    def _on_focus_out(self, window, _event):
        if self.close_on_blur:
            GLib.idle_add(self._close, window)
        return False

    def _on_destroy(self, window):
        if window is self.window:
            self.window = None


if __name__ == "__main__":
    raise SystemExit(KanjiApp().run(sys.argv))
