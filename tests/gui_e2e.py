"""GUI 端到端测试：起本地服务器 → 驱动 GUI 点按钮下载 → 校验结果。"""
import hashlib
import http.server
import os
import sys
import tempfile
import threading
import time
import tkinter as tk

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as gui_app  # noqa: E402

DATA = bytes((i * 7 + 13) % 256 for i in range(3 * 1024 * 1024))
SHA = hashlib.sha256(DATA).hexdigest()
PER_CONN = 200 * 1024


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
        self.send_header("Content-Disposition", 'attachment; filename="gui-test.bin"')
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(DATA)}")
        self.end_headers()
        t0, sent = time.monotonic(), 0
        for i in range(0, len(body), 16 * 1024):
            try:
                self.wfile.write(body[i:i + 16 * 1024]); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            sent += 16 * 1024
            exp, act = sent / PER_CONN, time.monotonic() - t0
            if exp > act:
                time.sleep(exp - act)


def main():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    url = f"http://127.0.0.1:{port}/gui-test.bin"
    outdir = tempfile.mkdtemp()

    win = gui_app.App()
    win.dir_var.set(outdir)
    win.conn_var.set(8)
    win.maxc_var.set(8)
    win.url_var.set(url)
    win.on_start()

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        win.update()
        rows = list(win.rows.values())
        if rows:
            dl = rows[0].dl
            if dl.total and dl.downloaded() >= dl.total:
                break
            if rows[0].last_status == "失败":
                print("FAIL: 下载失败", win.status_var.get())
                win.destroy(); return 1
        time.sleep(0.05)

    # 再刷几帧让 UI 落定
    for _ in range(10):
        win.update(); time.sleep(0.03)

    win.tree.selection_set(list(win.rows.keys()))
    win.update()
    print("状态栏:", win.status_var.get())
    print("表格行:", win.tree.item(list(win.rows.keys())[0], "values"))

    path = os.path.join(outdir, "gui-test.bin")
    ok = os.path.exists(path) and hashlib.sha256(open(path, "rb").read()).hexdigest() == SHA
    print("文件校验:", "OK" if ok else "FAIL")

    shot = os.path.join(HERE, "gui-screenshot.png")
    try:
        from PIL import ImageGrab  # 可能没装
    except ImportError:
        ImageGrab = None
    win.destroy()
    srv.shutdown()

    if not ok:
        return 1
    print("GUI 端到端测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
