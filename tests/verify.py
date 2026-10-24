"""
netboost 引擎完整验证套件
=========================

用本地限速 HTTP 服务器验证：
  1. 多连接加速比（每连接限速 → 聚合应接近 N 倍）
  2. 多连接下载文件完整性（sha256 必须一致）
  3. 断点续传（stop 后重下，字节无空洞无重复）
  4. 服务器不支持 Range（忽略 Range 回 200）时退化为单连接
  5. 404 抛出 DownloadError
  6. 连接中途被强行断开的自动重试
  7. 暂停 / 恢复
  8. 边界：1KB 小文件、0 字节文件、中文文件名（RFC5987）
  9. 全局限速生效

运行：python tests/verify.py
"""

from __future__ import annotations

import hashlib
import http.server
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import traceback

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import DownloadError, Downloader, format_size, format_speed  # noqa: E402

PER_CONN_BPS = 256 * 1024        # 每连接限速 256KB/s
RESULTS: list[tuple[str, bool, str]] = []


def make_data(n: int, seed0: int = 20240601) -> bytes:
    """确定性伪随机数据，保证每次运行内容一致。"""
    out = bytearray()
    seed = seed0
    while len(out) < n:
        seed = (seed * 1103515245 + 12345) & 0xFFFFFFFF
        out.extend(seed.to_bytes(4, "little"))
    return bytes(out[:n])


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# --------------------------------------------------------------------------
# 测试服务器
# --------------------------------------------------------------------------

class ServerConfig:
    def __init__(self):
        self.data = b""
        self.per_conn_bps = PER_CONN_BPS
        self.support_range = True
        self.filename = "test.bin"
        self.rfc5987 = False
        self.abort_after = 0          # >0 时每条连接传这么多字节就断开
        self.etag = ""
        self.status = 200
        self._abort_counter = 0
        self.lock = threading.Lock()

    def should_abort(self) -> bool:
        """仅对前若干条连接模拟中途断线。"""
        if self.abort_after <= 0:
            return False
        with self.lock:
            self._abort_counter += 1
            return self._abort_counter <= 3


CFG = ServerConfig()


class QuietServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # 客户端主动断开（限速测试/中止测试）属于预期行为，不打印堆栈
        import sys as _sys
        exc = _sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError,
                            ValueError, OSError)):
            return
        super().handle_error(request, client_address)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        try:
            self._serve()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
            return

    def _serve(self):
        cfg = CFG
        if cfg.status != 200:
            self.send_response(cfg.status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        data = cfg.data
        total = len(data)
        rng = self.headers.get("Range")
        start, end, partial = 0, total - 1, False

        if rng and cfg.support_range and rng.lower().startswith("bytes="):
            spec = rng[6:].strip()
            if "-" in spec:
                a, b = spec.split("-", 1)
                try:
                    start = int(a)
                    end = min(int(b) if b else total - 1, total - 1)
                except ValueError:
                    start, end = 0, total - 1
                else:
                    if total > 0 and start <= end:
                        partial = True
                    elif total > 0:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{total}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return

        body = data[start:end + 1] if total else b""
        self.send_response(206 if partial else 200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes" if cfg.support_range else "none")
        if cfg.etag:
            self.send_header("ETag", cfg.etag)
        if cfg.rfc5987:
            self.send_header(
                "Content-Disposition",
                "attachment; filename*=UTF-8''%E6%B5%8B%E8%AF%95%E6%96%87%E4%BB%B6.bin")
        else:
            self.send_header("Content-Disposition", f'attachment; filename="{cfg.filename}"')
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.end_headers()

        if not body:
            return

        abort = cfg.should_abort()
        abort_at = cfg.abort_after if abort else 0
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
            if abort_at and sent >= abort_at:
                # 强制截断：不写完 Content-Length 就断开，模拟网络中断
                self.close_connection = True
                return
            expected = sent / max(1, cfg.per_conn_bps)
            actual = time.monotonic() - t0
            if expected > actual:
                time.sleep(expected - actual)


class TestServer:
    def __enter__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.close()
        self.srv = QuietServer(("127.0.0.1", self.port), Handler)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.port}/file.bin"

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()
        return False


def reset_cfg(**kw):
    CFG.__init__()
    for k, v in kw.items():
        setattr(CFG, k, v)


def check(name: str, fn):
    print(f"\n── {name} " + "─" * max(0, 60 - len(name)))
    try:
        detail = fn() or ""
        RESULTS.append((name, True, detail))
        print(f"   PASS  {detail}")
    except Exception as e:  # noqa: BLE001
        RESULTS.append((name, False, f"{e}"))
        print(f"   FAIL  {e}")
        traceback.print_exc()


def tmpdir():
    return tempfile.mkdtemp(prefix="netboost-verify-")


# --------------------------------------------------------------------------
# 各项测试
# --------------------------------------------------------------------------

def t_acceleration():
    """每连接限速 256KB/s，8 连接应显著加速。"""
    size = 6 * 1024 * 1024
    reset_cfg(data=make_data(size), per_conn_bps=PER_CONN_BPS, filename="accel.bin")
    speeds = {}
    with TestServer() as url:
        for conns in (1, 8):
            d = Downloader(url, out_dir=tmpdir(), connections=conns,
                           max_connections=conns, adaptive=False, progress_interval=0.1)
            d.probe()
            assert d.total == size, f"大小探测错误 {d.total} != {size}"
            r = d.run()
            speeds[conns] = r.avg_speed
            shutil.rmtree(os.path.dirname(r.path), ignore_errors=True)
    ratio = speeds[8] / speeds[1]
    detail = (f"1 连接 {format_speed(speeds[1])} → 8 连接 {format_speed(speeds[8])}"
              f"，加速 {ratio:.2f}x")
    assert ratio >= 3.0, f"加速比仅 {ratio:.2f}x（要求 ≥3x）"
    return detail


def t_integrity():
    """多连接下载结果必须与源文件逐字节一致。"""
    size = 5 * 1024 * 1024
    data = make_data(size, seed0=777)
    reset_cfg(data=data, filename="integrity.bin")
    root = tmpdir()
    with TestServer() as url:
        d = Downloader(url, out_dir=root, connections=8, max_connections=16,
                       adaptive=True, progress_interval=0.1)
        d.probe()
        r = d.run()
        got = sha256_file(r.path)
        want = hashlib.sha256(data).hexdigest()
        assert got == want, f"sha256 不一致\n want={want}\n got ={got}"
        assert os.path.getsize(r.path) == size, "文件大小不匹配"
        assert not r.partial, "标记为未完成"
        detail = f"sha256 一致，峰值连接 {r.peak_conns if hasattr(r, 'peak_conns') else r.peak_connections}"
    shutil.rmtree(root, ignore_errors=True)
    return detail


def t_resume():
    """下载一半 stop，再重新下载，验证无空洞无重复。"""
    size = 6 * 1024 * 1024
    data = make_data(size, seed0=4242)
    reset_cfg(data=data, per_conn_bps=PER_CONN_BPS, filename="resume.bin")
    root = tmpdir()
    with TestServer() as url:
        d1 = Downloader(url, out_dir=root, connections=8, max_connections=8,
                        adaptive=False, progress_interval=0.05)
        d1.probe()

        def stopper(dl):
            if dl.downloaded() >= size * 0.4:
                dl.stop()

        r1 = d1.run(on_progress=stopper)
        assert r1.partial, "第一阶段应标记为未完成"
        first_done = d1.downloaded()
        assert 0 < first_done < size, f"第一阶段进度异常 {first_done}"

        # 状态文件应存在
        state = d1.path + ".netboost.json"
        assert os.path.exists(state), "状态文件未生成"

        d2 = Downloader(url, out_dir=root, connections=8, max_connections=8,
                        adaptive=False, progress_interval=0.05)
        d2.probe()
        r2 = d2.run()
        got = sha256_file(r2.path)
        want = hashlib.sha256(data).hexdigest()
        assert got == want, "续传后 sha256 不一致（存在空洞或重复写入）"
        assert r2.resumed, "未识别为续传"
        assert not r2.partial, "续传后仍未完成"
        detail = f"续传起点 {format_size(first_done)}，resumed={r2.resumed}，文件校验一致"
    shutil.rmtree(root, ignore_errors=True)
    return detail


def t_no_range():
    """服务器忽略 Range，应退化为单连接且结果正确。"""
    size = 2 * 1024 * 1024
    data = make_data(size, seed0=99)
    reset_cfg(data=data, support_range=False, per_conn_bps=1 << 30,
              filename="norange.bin")
    root = tmpdir()
    with TestServer() as url:
        d = Downloader(url, out_dir=root, connections=8, max_connections=16,
                       adaptive=True, progress_interval=0.05)
        d.probe()
        assert not d.supports_ranges, "应判定为不支持分段"
        r = d.run()
        got = sha256_file(r.path)
        want = hashlib.sha256(data).hexdigest()
        assert got == want, "单连接下载内容错误"
        assert r.peak_connections == 1, f"应只用 1 个连接，实际 {r.peak_connections}"
        detail = f"正确退化单连接，峰值连接 {r.peak_connections}"
    shutil.rmtree(root, ignore_errors=True)
    return detail


def t_404():
    """404 必须抛 DownloadError，不能静默成功。"""
    reset_cfg(status=404)
    with TestServer() as url:
        d = Downloader(url, out_dir=tmpdir())
        try:
            d.probe()
        except DownloadError as e:
            return f"正确抛出 DownloadError：{e}"
        raise AssertionError("404 未抛异常")


def t_abort_retry():
    """连接中途被强行断开，应能重试并最终成功。"""
    size = 3 * 1024 * 1024
    data = make_data(size, seed0=555)
    reset_cfg(data=data, per_conn_bps=64 * 1024,
              abort_after=48 * 1024, filename="abort.bin")
    root = tmpdir()
    with TestServer() as url:
        d = Downloader(url, out_dir=root, connections=4, max_connections=4,
                       adaptive=False, retries=8, progress_interval=0.05)
        d.probe()
        r = d.run()
        got = sha256_file(r.path)
        want = hashlib.sha256(data).hexdigest()
        assert got == want, "断连重试后文件损坏"
        detail = "前 3 条连接被强制断开，重试后文件完整"
    shutil.rmtree(root, ignore_errors=True)
    return detail


def t_pause_resume():
    """下载中暂停，再恢复，最终文件正确。"""
    size = 3 * 1024 * 1024
    data = make_data(size, seed0=321)
    reset_cfg(data=data, per_conn_bps=96 * 1024, filename="pause.bin")
    root = tmpdir()
    with TestServer() as url:
        d = Downloader(url, out_dir=root, connections=6, max_connections=6,
                       adaptive=False, progress_interval=0.05)
        d.probe()
        paused_once = {"v": False}

        def hook(dl):
            if not paused_once["v"] and dl.downloaded() > size * 0.2:
                paused_once["v"] = True
                dl.pause()
                threading.Timer(0.6, dl.resume_paused).start()

        r = d.run(on_progress=hook)
        got = sha256_file(r.path)
        want = hashlib.sha256(data).hexdigest()
        assert got == want, "暂停/恢复后文件损坏"
        assert paused_once["v"], "暂停未触发"
        detail = "暂停后恢复，文件校验一致"
    shutil.rmtree(root, ignore_errors=True)
    return detail


def t_small_file():
    """1KB 小文件（小于分片阈值）应正确下载。"""
    data = make_data(1024, seed0=7)
    reset_cfg(data=data, per_conn_bps=1 << 30, filename="small.bin")
    root = tmpdir()
    with TestServer() as url:
        d = Downloader(url, out_dir=root, connections=8, max_connections=16,
                       progress_interval=0.05)
        d.probe()
        r = d.run()
        got = sha256_file(r.path)
        assert got == hashlib.sha256(data).hexdigest(), "小文件内容错误"
        detail = f"1KB 文件正确，分片数 {len(d.chunks)}"
    shutil.rmtree(root, ignore_errors=True)
    return detail


def t_zero_file():
    """0 字节文件不应崩溃。"""
    reset_cfg(data=b"", per_conn_bps=1 << 30, filename="empty.bin")
    root = tmpdir()
    with TestServer() as url:
        d = Downloader(url, out_dir=root, connections=4, max_connections=4,
                       progress_interval=0.05)
        d.probe()
        assert d.total == 0, f"总大小应为 0，实际 {d.total}"
        r = d.run()
        assert os.path.getsize(r.path) == 0, "应生成 0 字节文件"
        detail = "0 字节文件处理正常"
    shutil.rmtree(root, ignore_errors=True)
    return detail


def t_cjk_filename():
    """RFC5987 中文文件名应正确解码。"""
    data = make_data(256 * 1024, seed0=11)
    reset_cfg(data=data, per_conn_bps=1 << 30, rfc5987=True)
    root = tmpdir()
    with TestServer() as url:
        d = Downloader(url, out_dir=root, connections=4, max_connections=4,
                       progress_interval=0.05)
        d.probe()
        assert d.filename == "测试文件.bin", f"文件名解码错误：{d.filename!r}"
        r = d.run()
        assert os.path.basename(r.path) == "测试文件.bin", f"保存路径错误 {r.path}"
        assert sha256_file(r.path) == hashlib.sha256(data).hexdigest(), "内容错误"
        detail = f"中文文件名正确：{d.filename}"
    shutil.rmtree(root, ignore_errors=True)
    return detail


def t_rate_limit():
    """全局限速应生效：限 1MB/s 时下载 2MB 至少约 1.5 秒。"""
    size = 2 * 1024 * 1024
    reset_cfg(data=make_data(size, seed0=88), per_conn_bps=1 << 30, filename="rate.bin")
    root = tmpdir()
    limit = 1024 * 1024
    with TestServer() as url:
        d = Downloader(url, out_dir=root, connections=8, max_connections=8,
                       adaptive=False, rate_limit=limit, progress_interval=0.05)
        d.probe()
        r = d.run()
        avg = r.avg_speed
        assert avg <= limit * 1.35, f"限速未生效：实际 {format_speed(avg)} > 限 {format_speed(limit)}"
        detail = f"限速 {format_speed(limit)}，实测平均 {format_speed(avg)}"
    shutil.rmtree(root, ignore_errors=True)
    return detail


def t_etag_md5():
    """ETag 为 32 位 MD5 时应完成校验。"""
    data = make_data(1024 * 1024, seed0=1234)
    md5 = hashlib.md5(data).hexdigest()
    reset_cfg(data=data, per_conn_bps=1 << 30, filename="md5.bin", etag=f'"{md5}"')
    root = tmpdir()
    with TestServer() as url:
        d = Downloader(url, out_dir=root, connections=4, max_connections=4,
                       progress_interval=0.05, verify_md5=True)
        d.probe()
        r = d.run()
        assert r.md5_verified is True, f"应为 True，实际 {r.md5_verified}"
        detail = "ETag=MD5 校验通过"
    shutil.rmtree(root, ignore_errors=True)
    return detail


def main():
    print("=" * 70)
    print("netboost 引擎验证套件")
    print(f"每连接限速基准：{format_speed(PER_CONN_BPS)}")
    print("=" * 70)

    tests = [
        ("1. 多连接加速比", t_acceleration),
        ("2. 多连接完整性", t_integrity),
        ("3. 断点续传", t_resume),
        ("4. 不支持 Range 退化", t_no_range),
        ("5. 404 错误处理", t_404),
        ("6. 断连自动重试", t_abort_retry),
        ("7. 暂停 / 恢复", t_pause_resume),
        ("8. 1KB 小文件", t_small_file),
        ("9. 0 字节文件", t_zero_file),
        ("10. 中文文件名", t_cjk_filename),
        ("11. 全局限速", t_rate_limit),
        ("12. ETag MD5 校验", t_etag_md5),
    ]
    for name, fn in tests:
        check(name, fn)

    print("\n" + "=" * 70)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"结果：{passed}/{len(RESULTS)} 通过")
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print("=" * 70)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
