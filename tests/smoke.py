"""快速冒烟测试：本地限速服务器 + 多连接下载 + 完整性校验。"""
import hashlib
import http.server
import os
import socket
import sys
import tempfile
import threading
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import Downloader, format_size, format_speed  # noqa

SIZE = 4 * 1024 * 1024
PER_CONN_BPS = 256 * 1024  # 每连接 256KB/s


def make_data(n):
    # 确定性伪随机
    out = bytearray()
    seed = 12345
    while len(out) < n:
        seed = (seed * 1103515245 + 12345) & 0xFFFFFFFF
        out.extend(seed.to_bytes(4, "little"))
    return bytes(out[:n])


DATA = make_data(SIZE)
SHA = hashlib.sha256(DATA).hexdigest()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        rng = self.headers.get("Range")
        start, end = 0, len(DATA) - 1
        partial = False
        if rng and rng.startswith("bytes="):
            spec = rng[6:]
            if "-" in spec:
                a, b = spec.split("-", 1)
                start = int(a)
                end = int(b) if b else len(DATA) - 1
                end = min(end, len(DATA) - 1)
                partial = True
        body = DATA[start:end + 1]
        self.send_response(206 if partial else 200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", '"%s"' % SHA[:32])
        self.send_header("Content-Disposition", 'attachment; filename="test.bin"')
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(DATA)}")
        self.end_headers()
        # 按连接限速
        chunk = 16 * 1024
        t0 = time.monotonic()
        sent = 0
        for i in range(0, len(body), chunk):
            piece = body[i:i + chunk]
            try:
                self.wfile.write(piece)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            sent += len(piece)
            expected = sent / PER_CONN_BPS
            actual = time.monotonic() - t0
            if expected > actual:
                time.sleep(expected - actual)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main():
    port = free_port()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/test.bin"
    print(f"服务器 {url}  文件 {format_size(SIZE)}  每连接限速 {format_speed(PER_CONN_BPS)}")

    results = {}
    for conns in (1, 8):
        d = Downloader(url, out_dir=tempfile.mkdtemp(), connections=conns,
                       max_connections=conns, adaptive=False, progress_interval=0.1)
        d.probe()
        assert d.total == SIZE, d.total
        assert d.supports_ranges, "应支持 Range"
        t0 = time.monotonic()
        res = d.run()
        el = time.monotonic() - t0
        with open(res.path, "rb") as f:
            got = hashlib.sha256(f.read()).hexdigest()
        ok = got == SHA
        print(f"  {conns:2d} 连接 → {el:5.2f}s  {format_speed(res.avg_speed)}  "
              f"峰值连接 {res.peak_connections}  校验 {'OK' if ok else 'FAIL'}")
        results[conns] = (res.avg_speed, ok)
        assert ok, "文件完整性校验失败!"

    ratio = results[8][0] / results[1][0]
    print(f"\n加速比：{ratio:.2f}x  （理论上限约 8x，受分片与调度开销影响）")
    assert ratio >= 3, f"加速比过低：{ratio:.2f}"
    print("冒烟测试全部通过")
    srv.shutdown()


if __name__ == "__main__":
    main()
