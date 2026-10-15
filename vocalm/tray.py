"""The tray icon: Vocalm's home when there's no window. Left-click opens the dashboard; the icon turns red while
you're loud and grey while paused, so you can check at a glance even with the bar hidden.

pystray runs its own message loop on a daemon thread. Menu clicks become commands for the main thread; nothing
here touches Tk.
"""
import threading
import webbrowser

from . import APP_NAME, autostart
from .icons import logo_image


class Tray:
    def __init__(self, core):
        self.core = core
        self.icon = None
        self.images = {}

    def url(self):
        return f"http://127.0.0.1:{self.core.port}/"

    def _image(self, state):
        if state not in self.images:
            self.images[state] = logo_image(64, state)
        return self.images[state]

    def start(self):
        try:
            import pystray
        except ImportError:
            print("pystray isn't installed; running without a tray icon")
            return
        core, put = self.core, self.core.commands.put
        item, menu = pystray.MenuItem, pystray.Menu

        def toggle_autostart(icon, _):
            try:
                autostart.disable() if autostart.enabled() else autostart.enable()
            except Exception as e:
                print("Couldn't change start with Windows:", e)

        self.icon = pystray.Icon("Vocalm", self._image("calm"), APP_NAME, menu(
            item("Open dashboard", lambda: webbrowser.open(self.url()), default=True),
            menu.SEPARATOR,
            item("Pause monitoring", lambda: put(("pause", not core.paused)), checked=lambda _: core.paused),
            item("Show bar on screen", lambda: put(("overlay", not core.cfg.overlay_visible)),
                 checked=lambda _: core.cfg.overlay_visible),
            item("Move bar", lambda: put(("move",))),
            menu.SEPARATOR,
            item("Start with Windows", toggle_autostart, checked=lambda _: autostart.enabled(),
                 visible=autostart.supported()),
            menu.SEPARATOR,
            item(f"Quit {APP_NAME}", lambda: put(("quit",))),
        ))
        core.listeners.append(self.on_state)
        core.quit_hooks.append(self.stop)
        threading.Thread(target=self.icon.run, name="tray", daemon=True).start()

    def on_state(self, state):
        """Called by the main thread when you go calm / loud / paused. Every change is shown (the bar's own
        hysteresis already stops it flickering), so the icon can never be left stuck on the wrong colour."""
        if self.icon is None:
            return
        try:
            self.icon.icon = self._image(state)
            self.icon.title = {"paused": f"{APP_NAME}: paused", "loud": f"{APP_NAME}: you're loud"}.get(
                state, f"{APP_NAME}: listening")
        except Exception as e:
            print("Tray update failed:", repr(e))

    def stop(self):
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
