"""验证打包后的 GUI exe 能真正启动并显示窗口（而不是静默崩溃）。"""
import os
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from PIL import ImageGrab  # noqa: E402

HERE = r"C:\Users\lx676\Documents\deepseek-harness\default-workspace\netboost"
EXE = os.path.join(HERE, "dist", "netboost下载器.exe")


def window_title_exists(title_part: str) -> bool:
    import ctypes
    from ctypes import wintypes

    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        n = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if n > 0:
            buf = ctypes.create_unicode_buffer(n + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, n + 1)
            if title_part in buf.value:
                found.append((hwnd, buf.value))
        return True

    ctypes.windll.user32.EnumWindows(cb, 0)
    return found[0] if found else None


def main():
    print("exe 存在:", os.path.exists(EXE), "大小 MB:", round(os.path.getsize(EXE) / 1048576, 1))
    proc = subprocess.Popen([EXE])
    print("已启动 PID:", proc.pid)

    hit = None
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        hit = window_title_exists("netboost")
        if hit:
            break
        if proc.poll() is not None:
            print("FAIL: 进程已退出，返回码", proc.returncode)
            return 1
        time.sleep(0.4)

    if not hit:
        print("FAIL: 20 秒内未出现窗口")
        proc.kill()
        return 1

    hwnd, title = hit
    print("窗口出现, hwnd=", hwnd, "标题=", title)

    # 用 PrintWindow 直接抓窗口内容，不受层叠顺序/前台焦点影响
    shot = capture_window(hwnd, os.path.join(HERE, "exe-screenshot.png"))
    print("截图:", shot)

    # 清理
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
    print("exe GUI 验证通过")
    return 0


def capture_window(hwnd, out_path):
    """PrintWindow + PW_RENDERFULLCONTENT，直接抓取指定窗口的位图。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    user32.ShowWindow(hwnd, 5)      # SW_SHOW
    user32.BringWindowToTop(hwnd)
    time.sleep(0.8)

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w <= 0 or h <= 0:
        raise RuntimeError(f"窗口尺寸异常 {w}x{h}")

    hdc = user32.GetWindowDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(hdc)
    bitmap = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem_dc, bitmap)

    PW_RENDERFULLCONTENT = 0x00000002
    ok = user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = w
    bi.biHeight = -h          # 负数 = 自上而下
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0

    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem_dc, bitmap, 0, h, buf, ctypes.byref(bi), 0)

    from PIL import Image
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    img.save(out_path)

    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hwnd, hdc)
    return out_path


if __name__ == "__main__":
    raise SystemExit(main())
