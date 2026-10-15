"""The Windows plumbing: overlay window styles, monitors, global hotkeys, one-copy-only, and which app is in front.

Everything here degrades to a harmless no-op off Windows, so the rest of the app (and the tests) can import it
anywhere.
"""
import ctypes
import os
import sys
import threading

IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:
    from ctypes import wintypes

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020   # clicks fall through to the game
WS_EX_TOOLWINDOW = 0x00000080    # keeps it out of Alt-Tab and the taskbar
WS_EX_LAYERED = 0x00080000       # per-window transparency
WS_EX_NOACTIVATE = 0x08000000    # can never take focus

HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000

# id: (virtual key, repeats, label). Arrow keys repeat so holding one glides the bar.
HOTKEYS = {
    "quit": (0x51, False, "Ctrl+Alt+Q"),
    "pause": (0x4D, False, "Ctrl+Alt+M"),
    "move": (0x42, False, "Ctrl+Alt+B"),
    "left": (0x25, True, "Ctrl+Alt+Left"),
    "up": (0x26, True, "Ctrl+Alt+Up"),
    "right": (0x27, True, "Ctrl+Alt+Right"),
    "down": (0x28, True, "Ctrl+Alt+Down"),
}


# ---------- process ----------

def set_dpi_awareness():
    """Per-monitor DPI awareness, so window positions are real pixels on every monitor."""
    if not IS_WINDOWS:
        return
    for attempt in (lambda: ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)),
                    lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2),
                    lambda: ctypes.windll.user32.SetProcessDPIAware()):
        try:
            if attempt():
                return
        except (AttributeError, OSError):
            continue


_MUTEX = None  # held for the life of the process


def claim_single_instance(name="Local\\Vocalm-singleton"):
    """False if another copy is already running.

    Global hotkeys belong to whichever program registers them first, so a second copy would lose Ctrl+Alt+Q and
    leave an overlay you can't close. One copy at a time.
    """
    global _MUTEX
    if not IS_WINDOWS:
        return True
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return True
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    _MUTEX = handle
    return True


def message_box(title, text, error=True):
    """A plain Windows dialog, for problems at startup when there's no console and no dashboard yet."""
    if IS_WINDOWS:
        try:
            ctypes.windll.user32.MessageBoxW(None, str(text), str(title), 0x10 if error else 0x40)
            return
        except OSError:
            pass
    print(f"{title}: {text}", file=sys.stderr)


# ---------- overlay windows ----------

def _user32():
    user32 = ctypes.windll.user32
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_uint]
    user32.GetParent.restype = wintypes.HWND
    user32.GetParent.argtypes = [wintypes.HWND]
    return user32


def hwnd_of(window):
    """The real top-level window handle behind a Tk window."""
    window.update_idletasks()
    return _user32().GetParent(window.winfo_id()) or window.winfo_id()


def exstyle(window):
    if not IS_WINDOWS:
        return 0
    return _user32().GetWindowLongPtrW(hwnd_of(window), GWL_EXSTYLE)


def make_overlay(window):
    """Click-through, never focusable, never in Alt-Tab. The difference between an overlay and a window that
    steals your mouse mid-fight."""
    if not IS_WINDOWS:
        return
    user32 = _user32()
    hwnd = hwnd_of(window)
    style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE,
                             style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)


def raise_topmost(window):
    """Put the window at the front of the always-on-top band without activating it.

    Tk's -topmost sets the flag; SetWindowPos pushes us back above other always-on-top windows (the Discord
    overlay, Afterburner...), which otherwise win just by being raised later.
    """
    try:
        window.attributes("-topmost", True)
    except Exception:
        pass
    if not IS_WINDOWS:
        return True
    return bool(_user32().SetWindowPos(hwnd_of(window), wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                                       SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE))


# ---------- screens and the cursor ----------

def cursor_position():
    if not IS_WINDOWS:
        return None
    pt = wintypes.POINT()
    return (pt.x, pt.y) if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)) else None


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", ctypes.c_long * 4),
                ("rcWork", ctypes.c_long * 4), ("dwFlags", ctypes.c_ulong)]


def _monitor_near(x, y, width, height):
    """(whole monitor, work area) as (left, top, right, bottom) rectangles, for the monitor nearest a rectangle.

    Clamping to one monitor (not the bounding box of all of them) means a saved position can never land in the
    dead space between two monitors of different sizes, or on a monitor that's since been unplugged.
    """
    fallback = (0, 0, 1920, 1080)
    if not IS_WINDOWS:
        return fallback, fallback
    user32 = ctypes.windll.user32
    user32.MonitorFromRect.restype = wintypes.HANDLE
    user32.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)]
    rect = wintypes.RECT(int(x), int(y), int(x + width), int(y + height))
    monitor = user32.MonitorFromRect(ctypes.byref(rect), 2)  # MONITOR_DEFAULTTONEAREST
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return fallback, fallback
    return tuple(info.rcMonitor), tuple(info.rcWork)


def monitor_near(x, y, width, height):
    """The whole monitor, taskbar included: where the bar may go."""
    return _monitor_near(x, y, width, height)[0]


def work_area_near(x, y, width, height):
    """The monitor minus its taskbar: where cards and default corners go."""
    return _monitor_near(x, y, width, height)[1]


def primary_work_area():
    return work_area_near(0, 0, 1, 1)


# ---------- global hotkeys ----------

def run_hotkeys(on_hotkey, on_failed):
    """Register the hotkeys and pump their messages forever (call on a daemon thread).

    on_hotkey(name) runs on this thread for every press; on_failed([labels]) once if any key was already taken
    by another program, so the app can say so instead of Ctrl+Alt+Q silently doing nothing.
    """
    if not IS_WINDOWS:
        return
    user32 = ctypes.windll.user32
    names, failed = {}, []
    for number, (name, (vk, repeats, label)) in enumerate(HOTKEYS.items(), start=1):
        mods = MOD_CONTROL | MOD_ALT | (0 if repeats else MOD_NOREPEAT)
        if user32.RegisterHotKey(None, number, mods, vk):
            names[number] = name
        else:
            failed.append(label)
    if failed:
        on_failed(failed)
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY and msg.wParam in names:
            on_hotkey(names[msg.wParam])


def start_hotkeys(on_hotkey, on_failed):
    thread = threading.Thread(target=run_hotkeys, args=(on_hotkey, on_failed), name="hotkeys", daemon=True)
    thread.start()
    return thread


# ---------- which app is in front ----------

GENERIC_NAMES = {"", "unrealgame", "bootstrappackagedgame", "game", "launcher", "application", "client", "main",
                 "unity", "player", "win64", "shipping"}
NOT_APPS = {"explorer.exe", "searchhost.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe",
            "lockapp.exe", "applicationframehost.exe"}


def _version_dll():
    version = ctypes.windll.version
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.POINTER(wintypes.UINT)]
    return version


def _version_string(path, key):
    """A string from an .exe's version resources, e.g. FileDescription "Counter-Strike 2"."""
    version = _version_dll()
    size = version.GetFileVersionInfoSizeW(path, None)
    if not size:
        return ""
    buf = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(path, 0, size, buf):
        return ""
    ptr, length = ctypes.c_void_p(), wintypes.UINT()
    langs = []
    if version.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(length)):
        pairs = ctypes.cast(ptr, ctypes.POINTER(wintypes.WORD * 2))
        for i in range(length.value // 4):
            langs.append("%04x%04x" % (pairs[i][0], pairs[i][1]))
    for lang in langs + ["040904b0", "040904e4", "000004b0"]:
        if version.VerQueryValueW(buf, f"\\StringFileInfo\\{lang}\\{key}", ctypes.byref(ptr), ctypes.byref(length)):
            if length.value:
                return ctypes.wstring_at(ptr, length.value - 1).strip()
    return ""


def tidy_name(name):
    """ "Battlefield™ 6" -> "Battlefield 6", "League of Legends (TM) Client" -> "League of Legends Client",
    "Overwatch Application" -> "Overwatch"."""
    for mark in ("(TM)", "(R)", "(C)", "™", "®", "©"):
        name = name.replace(mark, "")
    name = " ".join(name.split())
    if name.lower().endswith(" application") and len(name) > len(" application"):
        name = name[: -len(" application")]
    return name.strip(" -:")


def friendly_app_name(exe_path):
    """What to call an app: its description ("Counter-Strike 2", "Discord"), else its product name, else the
    .exe name tidied up ("VALORANT-Win64-Shipping.exe" -> "VALORANT")."""
    stem = os.path.splitext(os.path.basename(exe_path))[0]
    for key in ("FileDescription", "ProductName"):
        try:
            name = tidy_name(_version_string(exe_path, key))
        except OSError:
            name = ""
        if name and name.lower() not in GENERIC_NAMES and len(name) <= 60:
            return name
    for suffix in ("-Win64-Shipping", "-WinGDK-Shipping", "_x64", "-x64", "64", "_dx12", "_dx11", "_Shipping"):
        if stem.endswith(suffix) and len(stem) > len(suffix):
            stem = stem[: -len(suffix)]
    return stem


class ForegroundWatcher:
    """Names the app in front, cheaply: one lookup per new process, cached by process id.

    Only the program's name is ever read, never window titles, so nothing you're browsing or typing is recorded.
    """

    def __init__(self, own_pid=None):
        self.own_pid = own_pid or os.getpid()
        self.cache = {}

    def current(self):
        """The app's friendly name, "Desktop", or None when it can't tell (keep using the previous one)."""
        if not IS_WINDOWS:
            return None
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                        ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value or pid.value == self.own_pid:
            return None
        if pid.value in self.cache:
            return self.cache[pid.value]
        name = "Unknown app"
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            try:
                buf, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
                if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    exe = buf.value
                    name = "Desktop" if os.path.basename(exe).lower() in NOT_APPS else friendly_app_name(exe)
            finally:
                kernel32.CloseHandle(handle)
        if len(self.cache) > 256:
            self.cache.clear()
        self.cache[pid.value] = name
        return name
