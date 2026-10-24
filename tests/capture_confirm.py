"""截图：确认框（自动识别结果）与设置页。"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image  # noqa: E402

import app as gui_app  # noqa: E402
from detect import detect_from_url  # noqa: E402


def capture(hwnd, out):
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.ShowWindow(hwnd, 5)
    user32.BringWindowToTop(hwnd)
    time.sleep(0.7)
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, 0x00000002)

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    bi = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB").save(out)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)


def find(title_part):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            n = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if n:
                b = ctypes.create_unicode_buffer(n + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, b, n + 1)
                if b.value.strip() == title_part:
                    found.append(hwnd)
        return True

    ctypes.windll.user32.EnumWindows(cb, 0)
    return found


def main():
    win = gui_app.App()
    win.geometry("1120x880+30+20")
    win.update()

    # 确认框：视频
    url = "https://example.com/Interstellar.2014.2160p.mkv"
    det = detect_from_url(url)
    dlg = gui_app.ConfirmDialog(
        win, url, {"detection": det, "total": 8 * 1024 ** 3,
                   "out_dir": gui_app.category_dir(win.cfg, det.category)})
    for _ in range(15):
        win.update()
        time.sleep(0.05)
    hits = find("确认下载")
    print("确认框:", hits)
    if hits:
        capture(hits[0], os.path.join(HERE, "confirm-screenshot.png"))
        print("已保存 confirm-screenshot.png")
    dlg.destroy()
    win.update()

    # 设置页
    win.on_settings()
    for _ in range(15):
        win.update()
        time.sleep(0.05)
    hits = [h for h in find("设置")]
    print("设置窗:", hits)
    if hits:
        capture(hits[0], os.path.join(HERE, "settings-screenshot.png"))
        print("已保存 settings-screenshot.png")
        # 切到浏览器页
        for w in win.winfo_children():
            for nb in w.winfo_children():
                if nb.winfo_class() == "TNotebook":
                    nb.select(1)
                    for _ in range(12):
                        win.update()
                        time.sleep(0.05)
                    capture(hits[0], os.path.join(HERE, "browser-screenshot.png"))
                    print("已保存 browser-screenshot.png")

    win.destroy()


if __name__ == "__main__":
    main()
