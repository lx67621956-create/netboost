"""
功能测试：自动识别 → 自动目录 → 确认框 → 下载。以及设置/浏览器接管的可逆性。
"""
import hashlib
import http.server
import os
import shutil
import sys
import tempfile
import threading
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as gui_app  # noqa: E402
import browser as browser_mod  # noqa: E402
import config  # noqa: E402
from detect import detect_from_url  # noqa: E402

FILES = {
    "/movie.mp4": (b"M" * (1024 * 512), "movie.mp4", "video/mp4"),
    "/song.mp3": (b"S" * (1024 * 256), "song.mp3", "audio/mpeg"),
    "/report.docx": (b"D" * (1024 * 128), "report.docx",
                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "/setup.exe": (b"E" * (1024 * 256), "setup.exe", "application/x-msdownload"),
    "/blob.bin": (b"O" * (1024 * 128), "blob.bin", "application/octet-stream"),
    "/sub.chs.srt": (b"1\n00:00:01,000 --> 00:00:02,000\nhi\n", "sub.chs.srt", "text/plain"),
}

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        data, name, ctype = FILES.get(self.path, (b"", "x.bin", "application/octet-stream"))
        rng = self.headers.get("Range")
        start, end, partial = 0, max(0, len(data) - 1), False
        if rng and rng.startswith("bytes=") and data:
            a, b = rng[6:].split("-", 1)
            start = int(a)
            end = min(int(b) if b else len(data) - 1, len(data) - 1)
            partial = True
        body = data[start:end + 1] if data else b""
        self.send_response(206 if partial else 200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        if partial and data:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass


def wait_complete(win, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        win.update()
        rows = list(win.rows.values())
        if rows and all(
            (r.dl.total and r.dl.downloaded() >= r.dl.total) or r.last_status == "失败"
            for r in rows
        ):
            for _ in range(30):
                win.update()
                time.sleep(0.05)
            return True
        time.sleep(0.05)
    return False


def main():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"

    root = tempfile.mkdtemp(prefix="netboost-auto-")
    os.environ["NETBOOST_CONFIG"] = os.path.join(root, "config.json")
    dirs = {k: os.path.join(root, config.DEFAULT_SUBDIR[k]) for k in config.CATEGORIES}

    print("=== 1. 类型识别 ===")
    cases = [
        (f"{base}/movie.mp4", "video"),
        (f"{base}/song.mp3", "music"),
        (f"{base}/report.docx", "document"),
        (f"{base}/setup.exe", "program"),
        (f"{base}/blob.bin", "other"),
    ]
    for url, want in cases:
        got = detect_from_url(url).category
        # blob.bin 在表里 -> 无归类，其他
        check(f"{os.path.basename(url)} → {config.CATEGORIES[want]}",
              got == want, f"实际 {got}")

    print("\n=== 2. 自动识别 + 自动目录 + 确认下载 ===")
    win = gui_app.App()
    win.update()
    win.cfg["dirs"] = dict(dirs)
    win.cfg["ui"]["confirm_before_download"] = False   # 测试里跳过弹窗
    config.save_config(win.cfg)

    for url, want in cases:
        win.url_var.set(url)
        n_before = len(win.rows)
        win.on_start()
        # 等探测+自动开始
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and len(win.rows) == n_before:
            win.update()
            time.sleep(0.05)
        if len(win.rows) == n_before:
            check(f"{os.path.basename(url)} 自动开始", False, "超时")
            continue
        # 队列里 pending 的确认逻辑
        win.update()
        row = list(win.rows.values())[-1]
        check(f"{os.path.basename(url)} → 分类 {config.CATEGORIES[want]}",
              row.category == want, f"实际 {row.category}")
        check(f"{os.path.basename(url)} → 目录正确",
              os.path.normpath(row.dl.out_dir) == os.path.normpath(dirs[want]),
              row.dl.out_dir)
        wait_complete(win)
        check(f"{os.path.basename(url)} 下载文件存在",
              os.path.exists(row.dl.path), row.dl.path)

    print("\n=== 3. 确认框里的自动目录 ===")
    win.cfg["ui"]["confirm_before_download"] = True
    win.url_var.set(f"{base}/song.mp3")
    # 直接构造 ConfirmDialog 验证自动目录
    dlg = gui_app.ConfirmDialog(
        win, f"{base}/song.mp3",
        {"detection": detect_from_url(f"{base}/song.mp3"),
         "total": 1024, "out_dir": dirs["music"]})
    win.update()
    check("确认框默认目录 = 音乐目录",
          os.path.normpath(dlg.dir_var.get()) == os.path.normpath(dirs["music"]),
          dlg.dir_var.get())
    check("确认框类型 = 音乐", dlg.cat_var.get() == "music", dlg.cat_var.get())
    check("音乐不显示字幕栏", not dlg.sub_box.winfo_ismapped())
    dlg.destroy()
    win.update()

    dlg2 = gui_app.ConfirmDialog(
        win, f"{base}/movie.mp4",
        {"detection": detect_from_url(f"{base}/movie.mp4"),
         "total": 1024, "out_dir": dirs["video"]})
    win.update()
    check("视频显示字幕栏", bool(dlg2.sub_box.winfo_ismapped()))
    check("确认框默认目录 = 视频目录",
          os.path.normpath(dlg2.dir_var.get()) == os.path.normpath(dirs["video"]),
          dlg2.dir_var.get())
    # 手动改类型 -> 目录跟着变
    dlg2._set_cat("program")
    win.update()
    check("改类型为程序后目录跟随",
          os.path.normpath(dlg2.dir_var.get()) == os.path.normpath(dirs["program"]),
          dlg2.dir_var.get())
    dlg2.destroy()

    print("\n=== 4. 浏览器协议：可逆性（关键）===")
    if browser_mod.is_windows():
        before = browser_mod.protocol_registered()
        ok, msg = browser_mod.register_protocol()
        check("注册协议", ok and browser_mod.protocol_registered(), msg)
        ok2, msg2 = browser_mod.unregister_protocol()
        check("还原协议（回到注册前状态）",
              ok2 and browser_mod.protocol_registered() == before, msg2)
        # 再注册再还原一次，确认可反复
        browser_mod.register_protocol()
        browser_mod.unregister_protocol()
        check("可反复注册/还原", not browser_mod.protocol_registered())
    else:
        print("  跳过（非 Windows）")

    print("\n=== 5. 浏览器探测（只读）===")
    found = browser_mod.detect_browsers()
    check("能列出浏览器（不修改任何配置）", isinstance(found, list),
          f"检测到 {len(found)} 个: {[b['short'] for b in found]}")

    win.destroy()
    srv.shutdown()

    print("\n" + "=" * 64)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"结果：{passed}/{len(RESULTS)} 通过")
    for name, ok, _ in RESULTS:
        if not ok:
            print(f"  [FAIL] {name}")
    print("=" * 64)
    os.environ.pop("NETBOOST_CONFIG", None)
    shutil.rmtree(root, ignore_errors=True)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
