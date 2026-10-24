"""截取设置对话框。"""
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


def capture(hwnd, out):
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.ShowWindow(hwnd, 5)
    user32.BringWindowToTop(hwnd)
    time.sleep(0.8)
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
                if title_part in b.value:
                    found.append((hwnd, b.value))
        return True

    ctypes.windll.user32.EnumWindows(cb, 0)
    return found


def main():
    win = gui_app.App()
    win.geometry("1120x880+50+20")
    win.update()
    win.on_settings()
    for _ in range(20):
        win.update()
        time.sleep(0.05)

    found = [f for f in find("设置 · 分类文件夹")]
    print("找到窗口:", found)
    for hwnd, title in found[:1]:
        out = os.path.join(HERE, "settings-screenshot.png")
        capture(hwnd, out)
        print("截图:", out, title)
    win.destroy()


if __name__ == "__main__":
    main()
