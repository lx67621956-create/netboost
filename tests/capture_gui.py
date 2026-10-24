"""GUI 截图：验证字号、分类切换、字幕栏显隐。"""
import http.server
import os
import sys
import tempfile
import threading
import time
import ctypes
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

DATA = bytes((i * 11 + 7) % 256 for i in range(16 * 1024 * 1024))
PER_CONN = 700 * 1024


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        rng = self.headers.get("Range")
        start, end, partial = 0, len(DATA) - 1, False
        if rng and rng.startswith("bytes="):
            spec = rng[6:]
            if "-" in spec:
                a, b = spec.split("-", 1)
                start = int(a)
                end = min(int(b) if b else len(DATA) - 1, len(DATA) - 1)
                partial = True
        body = DATA[start:end + 1]
        self.send_response(206 if partial else 200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        name = "Interstellar.2014.1080p.mkv" if "movie" in self.path else "song.mp3"
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(DATA)}")
        self.end_headers()
        t0, sent = time.monotonic(), 0
        for i in range(0, len(body), 32 * 1024):
            try:
                self.wfile.write(body[i:i + 32 * 1024])
                self.wfile.flush()
            except Exception:
                return
            sent += 32 * 1024
            exp, act = sent / PER_CONN, time.monotonic() - t0
            if exp > act:
                time.sleep(exp - act)


def capture_window(hwnd, out_path):
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.ShowWindow(hwnd, 5)
    user32.BringWindowToTop(hwnd)
    time.sleep(0.8)
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
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
    Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB").save(out_path)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return out_path


def find_window(title_part):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            n = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if n > 0:
                b = ctypes.create_unicode_buffer(n + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, b, n + 1)
                if title_part in b.value:
                    found.append((hwnd, b.value))
        return True

    ctypes.windll.user32.EnumWindows(cb, 0)
    return found[0] if found else None


def main():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    outdir = tempfile.mkdtemp()

    win = gui_app.App()
    win.geometry("1120x800+60+40")
    win.update()
    win.dir_var.set(outdir)

    # 电影分类，2 个任务
    win.apply_category("movie")
    for _ in range(2):
        win.url_var.set(f"http://127.0.0.1:{port}/movie.f.bin")
        win.sub_var.set(f"http://127.0.0.1:{port}/Interstellar.chs.srt")
        win.on_start()
        win.update()
        time.sleep(0.2)

    t_end = time.monotonic() + 6
    while time.monotonic() < t_end:
        win.update()
        time.sleep(0.03)

    win.update_idletasks()
    hit = find_window("netboost")
    print("窗口:", hit)
    if hit:
        path = capture_window(hit[0], os.path.join(HERE, "gui-screenshot.png"))
        print("截图:", path)

    for row in win.rows.values():
        row.dl.stop()
    time.sleep(0.5)
    win.update()
    win.destroy()
    srv.shutdown()


if __name__ == "__main__":
    main()
