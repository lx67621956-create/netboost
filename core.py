"""
netboost 核心下载引擎
=====================

设计目标：把"单连接限速"的服务器跑满，而不是绕过任何账号/会员限制。

原理
----
1. 先用 Range 请求探测：文件总大小、服务器是否支持分段传输（206）。
2. 支持分段时，把文件切成许多小块（chunk），由多个连接并发拉取，每个连接
   只负责自己的字节区间 —— 服务器若按"连接"限速，聚合速度即 N 倍。
3. 每块实时记录已写入字节，中断后可从断点继续（真正的断点续传）。
4. 自动增压：先开少量连接，按测量到的聚合速度决定是否继续加连接，加到
   "再加也不提升"为止，避免无意义地把连接数开到最大。
5. 全局令牌桶限速（可选），用于不想占满带宽时。

不支持分段（服务器忽略 Range）时自动退化为单连接顺序下载，并明确提示。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import ssl
import sys
import threading
import time
import random
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

__all__ = ["Downloader", "DownloadResult", "DownloadError", "format_size", "format_speed", "format_eta"]

VERSION = "1.0.0"

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

CHUNK_MIN = 512 * 1024          # 最小分片 512KB
CHUNK_MAX = 16 * 1024 * 1024    # 最大分片 16MB
SPLIT_THRESHOLD = 2 * 1024 * 1024  # 反复失败的块缩小到该阈值以下不再切


class DownloadError(Exception):
    """无法继续的致命错误。"""


class _Aborted(Exception):
    """用户中止（stop / pause），不算错误。"""


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def format_size(n: float) -> str:
    if n is None:
        return "?"
    n = float(n)
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024.0
        i += 1
    return f"{n:.2f} {units[i]}" if i else f"{int(n)} B"


def format_speed(bps: float) -> str:
    if bps is None:
        return "?"
    return format_size(bps) + "/s"


def format_eta(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _sanitize_filename(name: str, fallback: str = "download.bin") -> str:
    if not name:
        return fallback
    name = unquote(name).strip().replace("\\", "/").split("/")[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(" .")
    return name or fallback


def _filename_from_disposition(value: str) -> Optional[str]:
    if not value:
        return None
    # RFC5987: filename*=UTF-8''xxx
    m = re.search(r"filename\*\s*=\s*([^']*)'[^']*'([^;]+)", value, re.I)
    if m:
        enc = m.group(1).strip().lower() or "utf-8"
        raw = m.group(2).strip().strip('"')
        try:
            return _sanitize_filename(base64.b64decode(raw).decode("utf-8", "replace")) \
                if enc == "base64" else _sanitize_filename(unquote(raw, encoding="utf-8", errors="replace"))
        except Exception:
            pass
    m = re.search(r'filename\s*=\s*"([^"]*)"', value, re.I)
    if m:
        return _sanitize_filename(m.group(1))
    m = re.search(r"filename\s*=\s*([^;]+)", value, re.I)
    if m:
        return _sanitize_filename(m.group(1).strip())
    return None


def _parse_content_range(value: str) -> Optional[tuple]:
    """'bytes 0-1/12345' -> (start, end, total)"""
    m = re.match(r"\s*bytes\s+(\d+)-(\d+)/(\d+|\*)", value or "", re.I)
    if not m:
        return None
    total = None if m.group(3) == "*" else int(m.group(3))
    return int(m.group(1)), int(m.group(2)), total


def _parse_int(value: Optional[str]) -> Optional[int]:
    try:
        return int(str(value).strip()) if value is not None else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# 全局令牌桶限速
# --------------------------------------------------------------------------

class _RateLimiter:
    """简单的全局令牌桶：多连接共享同一个速率上限。"""

    def __init__(self, bytes_per_second: float):
        self.rate = float(bytes_per_second)
        self.capacity = max(self.rate * 0.25, 64 * 1024)
        self.tokens = self.capacity
        self.last = time.monotonic()
        self.lock = threading.Lock()

    def consume(self, n: int) -> None:
        n = min(n, self.capacity)
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
                self.last = now
                if self.tokens >= n:
                    self.tokens -= n
                    return
                need = (n - self.tokens) / self.rate
            time.sleep(min(need, 0.25))


# --------------------------------------------------------------------------
# 分片
# --------------------------------------------------------------------------

@dataclass
class Chunk:
    start: int                 # 文件内绝对起始偏移
    end: int                   # 含末端的绝对结束偏移
    done: int = 0              # 已写入字节数（相对本片起点）

    @property
    def size(self) -> int:
        return self.end - self.start + 1

    @property
    def remaining(self) -> int:
        return self.size - self.done

    @property
    def finished(self) -> bool:
        return self.done >= self.size


@dataclass
class DownloadResult:
    path: str
    total: int
    downloaded: int
    elapsed: float
    peak_connections: int
    resumed: bool = False
    md5_verified: Optional[bool] = None
    partial: bool = False       # True 表示被中止 / 未完成

    @property
    def avg_speed(self) -> float:
        return self.downloaded / self.elapsed if self.elapsed > 0 else 0.0


ProgressFn = Callable[["Downloader"], None]


# --------------------------------------------------------------------------
# 下载器
# --------------------------------------------------------------------------

class Downloader:
    """一个文件的多连接下载任务。

    典型用法::

        d = Downloader("https://example.com/big.iso", out_dir="downloads",
                       connections=8, max_connections=32)
        d.probe()
        print(d.total, d.filename)
        result = d.run(lambda dl: print(dl.progress_line()))
    """

    def __init__(
        self,
        url: str,
        dest: Optional[str] = None,
        out_dir: str = ".",
        connections: int = 8,
        max_connections: int = 32,
        chunk_size: Optional[int] = None,
        headers: Optional[dict] = None,
        user_agent: str = DEFAULT_UA,
        referer: Optional[str] = None,
        timeout: float = 30.0,
        retries: int = 6,
        rate_limit: Optional[float] = None,
        adaptive: bool = True,
        ramp_delay: float = 1.5,
        verify_md5: bool = True,
        insecure: bool = False,
        resume: bool = True,
        progress_interval: float = 0.15,
        log: Optional[Callable[[str], None]] = None,
    ):
        self.url = url
        self.dest = dest
        self.out_dir = out_dir
        self.connections = max(1, int(connections))
        self.max_connections = max(self.connections, int(max_connections))
        self.chunk_size_opt = chunk_size
        self.headers = dict(headers or {})
        self.user_agent = user_agent
        self.referer = referer
        self.timeout = timeout
        self.retries = max(1, int(retries))
        self.rate_limit = rate_limit
        self.adaptive = adaptive
        self.ramp_delay = ramp_delay
        self.verify_md5 = verify_md5
        self.insecure = insecure
        self.resume = resume
        self.progress_interval = progress_interval
        self._log = log or (lambda msg: None)

        # 探测结果
        self.final_url: Optional[str] = None
        self.filename: str = ""
        self.total: Optional[int] = None
        self.supports_ranges: bool = False
        self.etag: Optional[str] = None
        self.last_modified: Optional[str] = None
        self.content_type: Optional[str] = None
        self.probed = False

        # 运行状态
        self.path: Optional[str] = None
        self.state_path: Optional[str] = None
        self.chunks: list[Chunk] = []
        self._queue: list[int] = []
        self._lock = threading.RLock()
        self._stop = False
        self._paused = False
        self._workers: list[threading.Thread] = []
        self._launched = 0
        self._active = 0
        self._fatal: Optional[str] = None
        self._written = 0            # 已写入的总字节（含本轮之前）
        self._session_start_bytes = 0
        self._limiter = _RateLimiter(rate_limit) if rate_limit else None
        self._opener = None
        self._t0 = 0.0
        self._peak_conns = 0
        self._speed = 0.0
        self._resumed = False
        self._last_state_save = 0.0
        self._grow_blocked = False
        self._samples: list[tuple[float, int]] = []
        self._best_speed = 0.0

    # ------------------------------------------------------------------ 探测

    def _build_opener(self):
        handlers = [HTTPRedirectHandler()]
        ctx = ssl._create_unverified_context() if self.insecure else ssl.create_default_context()
        from urllib.request import HTTPSHandler, HTTPHandler
        handlers.append(HTTPSHandler(context=ctx))
        self._opener = build_opener(*handlers)

    def _request(self, method: str = "GET", extra: Optional[dict] = None) -> Request:
        req = Request(self.url, method=method)
        req.add_header("User-Agent", self.user_agent)
        req.add_header("Accept", "*/*")
        req.add_header("Accept-Encoding", "identity")  # 关键：禁止压缩，保证 Range 字节精确
        if self.referer:
            req.add_header("Referer", self.referer)
        for k, v in self.headers.items():
            req.add_header(k, v)
        for k, v in (extra or {}).items():
            req.add_header(k, v)
        return req

    def probe(self) -> "Downloader":
        """探测大小 / 是否支持 Range / 文件名，不下载正文。"""
        if self._opener is None:
            self._build_opener()
        err = None
        # 先尝试 Range: bytes=0-0，可同时拿到 206 与总长度
        for attempt in range(3):
            try:
                req = self._request(extra={"Range": "bytes=0-0"})
                resp = self._opener.open(req, timeout=self.timeout)
                try:
                    status = getattr(resp, "status", None) or resp.getcode()
                    self.final_url = resp.geturl()
                    self.etag = resp.headers.get("ETag")
                    self.last_modified = resp.headers.get("Last-Modified")
                    self.content_type = resp.headers.get("Content-Type")
                    cd = resp.headers.get("Content-Disposition") or ""
                    name = _filename_from_disposition(cd)

                    cr = _parse_content_range(resp.headers.get("Content-Range") or "")
                    clen = _parse_int(resp.headers.get("Content-Length"))
                    ar = (resp.headers.get("Accept-Ranges") or "").lower()

                    if status == 206 and cr and cr[2] is not None:
                        self.total = cr[2]
                        self.supports_ranges = True
                    elif status == 200 and clen is not None and clen > 0:
                        # 服务器忽略了 Range（回了整文件 200）
                        self.total = clen
                        self.supports_ranges = False
                    elif status == 200 and ar == "bytes":
                        # 少见：200 但声明接受 Range；再看 Content-Length
                        self.total = clen
                        self.supports_ranges = bool(clen and clen > 0)
                    else:
                        self.total = clen
                        self.supports_ranges = status == 206

                    if not name:
                        path = urlsplit(self.final_url or self.url).path
                        name = _sanitize_filename(path.rsplit("/", 1)[-1])
                    self.filename = name or "download.bin"
                    self.probed = True
                    return self
                finally:
                    try:
                        resp.close()
                    except Exception:
                        pass
            except HTTPError as e:
                err = e
                if e.code in (401, 403, 404, 410):
                    raise DownloadError(f"服务器返回 HTTP {e.code}（链接可能失效或需要登录/Referer）") from e
                if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise DownloadError(f"HTTP {e.code}: {e.reason}") from e
            except (URLError, TimeoutError, OSError) as e:
                err = e
                if attempt < 2:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise DownloadError(f"网络错误：{e}") from e
        raise DownloadError(f"探测失败：{err}")

    # ------------------------------------------------------------------ 规划

    def _plan_chunks(self, done_map: dict) -> None:
        total = self.total
        assert total is not None
        # 分片大小：留出足够多的块让连接数有活干，又不要太碎
        size = self.chunk_size_opt
        if not size:
            target_pieces = max(4, self.max_connections * 3)
            size = max(CHUNK_MIN, min(CHUNK_MAX, total // target_pieces))
        size = max(64 * 1024, int(size))

        chunks: list[Chunk] = []
        pos = 0
        while pos < total:
            end = min(total - 1, pos + size - 1)
            c = Chunk(start=pos, end=end)
            saved = done_map.get(pos)
            if saved is not None:
                c.done = max(0, min(saved, c.size))
            chunks.append(c)
            pos = end + 1
        if not chunks:  # 0 字节文件
            chunks = [Chunk(start=0, end=0)]
        self.chunks = chunks
        self._queue = [i for i, c in enumerate(chunks) if not c.finished]
        self._written = sum(c.done for c in chunks)

    def _chunk_key(self, c: Chunk) -> str:
        return f"{c.start}-{c.end}"

    # ------------------------------------------------------------------ 状态文件

    def _state_dict(self) -> dict:
        return {
            "version": 1,
            "url": self.url,
            "final_url": self.final_url,
            "total": self.total,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "filename": self.filename,
            "supports_ranges": self.supports_ranges,
            "chunks": [{"start": c.start, "end": c.end, "done": c.done} for c in self.chunks],
            "updated": time.time(),
        }

    def _save_state(self, force: bool = False) -> None:
        if not self.state_path or not self.supports_ranges:
            return
        now = time.monotonic()
        if not force and now - self._last_state_save < 1.0:
            return
        self._last_state_save = now
        try:
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state_dict(), f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    def _load_state(self) -> Optional[dict]:
        if not self.state_path or not os.path.exists(self.state_path):
            return None
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(st, dict) or st.get("version") != 1:
            return None
        if self.total is not None and st.get("total") != self.total:
            return None
        if self.etag and st.get("etag") and self.etag != st["etag"]:
            return None
        return st

    # ------------------------------------------------------------------ 单块下载

    def _fetch_chunk(self, chunk: Chunk, handle) -> None:
        """把一个 chunk 剩余部分拉到文件里；失败会自动退回队列重试。"""
        attempt = 0
        fatal_attempts = 0
        while chunk.remaining > 0:
            if self._stop:
                raise _Aborted()
            if self._paused:
                raise _Aborted()
            start_abs = chunk.start + chunk.done
            end_abs = chunk.end
            range_header = f"bytes={start_abs}-{end_abs}"
            try:
                req = self._request(extra={"Range": range_header})
                resp = self._opener.open(req, timeout=self.timeout)
                try:
                    status = getattr(resp, "status", None) or resp.getcode()
                    if status != 206:
                        # 服务器中途不再支持 Range：致命
                        raise DownloadError(
                            f"服务器未按 Range 返回（HTTP {status}），无法多连接下载")
                    cr = _parse_content_range(resp.headers.get("Content-Range") or "")
                    if cr and cr[0] != start_abs:
                        raise DownloadError(f"Content-Range 不匹配：{cr[0]} != {start_abs}")
                    want = end_abs - start_abs + 1
                    got = 0
                    # 每次读 64KB：既保证吞吐，又让 stop/pause 能在 ~0.25s 内生效
                    while got < want:
                        if self._stop or self._paused:
                            raise _Aborted()
                        block = resp.read(min(64 * 1024, want - got))
                        if not block:
                            break
                        if self._limiter is not None:
                            self._limiter.consume(len(block))
                        handle.seek(start_abs + got)
                        handle.write(block)
                        got += len(block)
                        with self._lock:
                            chunk.done += len(block)
                            self._written += len(block)
                    if got < want:
                        raise IOError(f"连接提前断开（{got}/{want} 字节）")
                    attempt = 0
                finally:
                    try:
                        resp.close()
                    except Exception:
                        pass
                return
            except _Aborted:
                raise
            except DownloadError:
                raise
            except (HTTPError, URLError, TimeoutError, OSError, IOError) as e:
                attempt += 1
                fatal_attempts += 1
                code = getattr(e, "code", None)
                if code in (401, 403, 404, 410):
                    raise DownloadError(f"服务器返回 HTTP {code}，下载被拒绝") from e
                if attempt > self.retries:
                    # 反复失败：把剩余部分对半切开，减小单次请求压力
                    if chunk.remaining > SPLIT_THRESHOLD and fatal_attempts <= self.retries * 4:
                        with self._lock:
                            cut = chunk.start + chunk.done + chunk.remaining // 2
                            if cut <= chunk.end:
                                tail = Chunk(start=cut, end=chunk.end)
                                self.chunks.append(tail)
                                self._queue.append(len(self.chunks) - 1)
                                chunk.end = cut - 1
                        attempt = 0
                        continue
                    raise DownloadError(f"下载失败重试已用尽：{e}") from e
                delay = min(8.0, 0.4 * (2 ** (attempt - 1))) + random.uniform(0, 0.3)
                time.sleep(delay)

    # ------------------------------------------------------------------ 工作线程

    def _next_chunk_index(self) -> Optional[int]:
        while self._queue:
            idx = self._queue.pop(0)
            c = self.chunks[idx]
            if not c.finished:
                return idx
        return None

    def _worker(self) -> None:
        handle = None
        try:
            handle = open(self.path, "r+b")
            while not self._stop:
                with self._lock:
                    if self._stop:
                        break
                    if self._paused:
                        # 暂停：本线程退出，resume() 会重新拉起
                        break
                    idx = self._next_chunk_index()
                    if idx is None:
                        break
                    chunk = self.chunks[idx]
                try:
                    self._fetch_chunk(chunk, handle)
                except _Aborted:
                    # 把没做完的块放回队列（done 已记录，下次从这里继续）
                    with self._lock:
                        if not chunk.finished:
                            self._queue.insert(0, idx)
                    raise
                except DownloadError as e:
                    with self._lock:
                        if not chunk.finished and not self._stop:
                            self._queue.insert(0, idx)
                        if self._fatal is None:
                            self._fatal = str(e)
                    return
        except _Aborted:
            pass
        except Exception as e:  # 兜底，避免线程静默死亡
            with self._lock:
                if self._fatal is None:
                    self._fatal = repr(e)
        finally:
            with self._lock:
                self._active -= 1
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass

    def _launch_workers(self, n: int) -> None:
        for _ in range(n):
            with self._lock:
                if self._stop or self._paused:
                    return
                if not self._queue and not any(not c.finished for c in self.chunks):
                    return
                self._active += 1
                self._launched += 1
                self._peak_conns = max(self._peak_conns, self._active)
            t = threading.Thread(target=self._worker, name=f"netboost-{self._launched}", daemon=True)
            self._workers.append(t)
            t.start()

    # ------------------------------------------------------------------ 进度

    def downloaded(self) -> int:
        with self._lock:
            return self._written

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def active_connections(self) -> int:
        with self._lock:
            return self._active

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def failed(self) -> Optional[str]:
        return self._fatal

    def progress_line(self, width: int = 34) -> str:
        total = self.total or 0
        done = self.downloaded()
        pct = (done / total * 100.0) if total else 0.0
        filled = int(width * pct / 100.0)
        bar = "█" * filled + "─" * (width - filled)
        remain = max(0, total - done) if total else 0
        eta = (remain / self._speed) if self._speed > 0.5 and remain else None
        tag = "暂停" if self._paused else "下载中"
        return (f"{bar} {pct:5.1f}%  {format_size(done)}/{format_size(total) if total else '?'}"
                f"  {format_speed(self._speed)}  连接 {self.active_connections}  "
                f"剩余 {format_eta(eta)}  [{tag}]")

    # ------------------------------------------------------------------ 控制

    def pause(self) -> None:
        with self._lock:
            self._paused = True
        self._save_state(force=True)

    def resume_paused(self) -> None:
        with self._lock:
            if not self._paused:
                return
            self._paused = False
        self._launch_workers(max(1, self.connections))

    def stop(self) -> None:
        with self._lock:
            self._stop = True
        self._save_state(force=True)

    # ------------------------------------------------------------------ 主流程

    def _prepare(self) -> None:
        if not self.probed:
            self.probe()
        if not self.filename:
            self.filename = "download.bin"
        os.makedirs(self.out_dir or ".", exist_ok=True)
        self.path = self.dest or os.path.join(self.out_dir, self.filename)
        self.path = os.path.abspath(self.path)
        self.state_path = self.path + ".netboost.json"

        done_map: dict[int, int] = {}
        if self.resume and self.supports_ranges:
            st = self._load_state()
            if st:
                for c in st.get("chunks") or []:
                    try:
                        done_map[int(c["start"])] = int(c["done"])
                    except (KeyError, TypeError, ValueError):
                        continue
                self._resumed = bool(done_map)

        self._plan_chunks(done_map)

        # 文件准备：支持分段时预分配并保留已有数据；否则清空重来
        exists = os.path.exists(self.path)
        if self.supports_ranges:
            mode = "r+b" if exists else "wb"
            with open(self.path, mode) as f:
                f.truncate(self.total or 0)
            # 若没有可信任的状态文件但有旧文件，则从头来
            if exists and not done_map:
                with open(self.path, "wb") as f:
                    f.truncate(self.total or 0)
        else:
            # 单连接：无法续传，直接覆盖
            if self.total is None:
                # 未知长度：直接流式写，路径先清空
                open(self.path, "wb").close()
            else:
                with open(self.path, "wb") as f:
                    f.truncate(self.total)
            self.chunks = [Chunk(start=0, end=(self.total - 1) if self.total else 0)]
            self._queue = [0]
            self._written = 0

    def _stream_single(self) -> None:
        """不支持 Range 时的退化路径：单连接从头下。"""
        req = self._request()
        resp = self._opener.open(req, timeout=self.timeout)
        with self._lock:
            self._peak_conns = max(self._peak_conns, 1)
        try:
            with open(self.path, "wb") as f:
                while True:
                    if self._stop or self._paused:
                        raise _Aborted()
                    block = resp.read(64 * 1024)
                    if not block:
                        break
                    if self._limiter is not None:
                        self._limiter.consume(len(block))
                    f.write(block)
                    with self._lock:
                        self._written += len(block)
                        if self.chunks:
                            self.chunks[0].done = self._written
        finally:
            try:
                resp.close()
            except Exception:
                pass

    def run(self, on_progress: Optional[ProgressFn] = None) -> DownloadResult:
        """执行下载。on_progress 会在监控循环里被周期调用（可直接打印/更新 UI）。"""
        if self._opener is None:
            self._build_opener()
        self._prepare()
        self._stop = False
        self._paused = False
        self._fatal = None
        self._t0 = time.monotonic()
        self._session_start_bytes = self._written
        self._peak_conns = 0
        self._launched = 0
        self._active = 0
        self._workers = []
        self._samples = []
        self._best_speed = 0.0
        self._grow_blocked = False
        start_bytes = self._written

        try:
            if not self.supports_ranges:
                self._log("服务器不支持分段传输，退化为单连接下载（无法多连接加速）")
                if self.total is None:
                    self._log("也无法获知文件大小，无法显示进度")
                self._stream_single()
            else:
                self._log(f"分段下载：{len(self.chunks)} 个分片，起始 {self.connections} 连接，"
                          f"上限 {self.max_connections}")
                self._launch_workers(self.connections)
                self._monitor(on_progress)
                # 等待所有 worker 退出
                while True:
                    alive = [t for t in self._workers if t.is_alive()]
                    if not alive:
                        break
                    if self._stop:
                        for t in alive:
                            t.join(timeout=0.2)
                        break
                    alive[0].join(timeout=0.1)
        except _Aborted:
            pass
        except KeyboardInterrupt:
            self.stop()
        finally:
            with self._lock:
                self._stop = True
            for t in list(self._workers):
                t.join(timeout=2.0)
            self._save_state(force=True)

        elapsed = max(1e-6, time.monotonic() - self._t0)
        downloaded = self._written - start_bytes
        total = self.total
        partial = False
        if self._fatal:
            raise DownloadError(self._fatal)
        if self.supports_ranges and total is not None:
            if self._written < total:
                partial = True
            else:
                partial = False
        if self._stop and self.supports_ranges and total is not None and self._written < total:
            partial = True

        md5_ok: Optional[bool] = None
        if not partial and self.verify_md5 and total:
            md5_ok = self._verify_md5()

        res = DownloadResult(
            path=self.path, total=int(self.total or self._written),
            downloaded=int(downloaded), elapsed=elapsed,
            peak_connections=self._peak_conns, resumed=self._resumed,
            md5_verified=md5_ok, partial=partial,
        )
        if partial:
            self._log(f"未完成：已保存 {format_size(self._written)}，状态已记录，可续传")
        return res

    def _monitor(self, on_progress: Optional[ProgressFn]) -> None:
        last_t = time.monotonic()
        last_b = self.downloaded()
        next_ramp = last_t + self.ramp_delay
        while True:
            time.sleep(self.progress_interval)
            now = time.monotonic()
            with self._lock:
                stop = self._stop or self._paused
                fatal = self._fatal
            cur = self.downloaded()
            dt = now - last_t
            if dt >= 0.2:
                self._speed = (cur - last_b) / dt
                last_t, last_b = now, cur
            if on_progress is not None:
                try:
                    on_progress(self)
                except Exception:
                    pass

            if fatal:
                return
            if stop:
                return
            # 全部完成？
            with self._lock:
                if not self._queue and all(c.finished for c in self.chunks):
                    # 等残留 worker 收尾
                    if all(not c.finished for c in self.chunks) or not self._queue:
                        if self._written >= (self.total or 0):
                            return
                pending = [c for c in self.chunks if not c.finished]
            if not pending:
                return

            # 自动增压
            if (self.adaptive and not self._grow_blocked
                    and now >= next_ramp and self.max_connections > self.connections):
                next_ramp = now + self.ramp_delay
                self._samples.append((now, int(self._speed)))
                productive_pending = sum(1 for c in pending if c.remaining > 512 * 1024)
                if self._speed > self._best_speed * 1.10:
                    self._best_speed = self._speed
                    if self._active < self.max_connections:
                        add = max(1, min(4, self.max_connections - self._active))
                        if productive_pending >= self._active:
                            self._log(f"速度提升到 {format_speed(self._speed)}，增加 {add} 个连接")
                            self._launch_workers(add)
                elif len(self._samples) >= 3:
                    self._grow_blocked = True
                    self._log(f"再加连接已无明显提升（{format_speed(self._speed)}），停止增压")

    def _verify_md5(self) -> Optional[bool]:
        etag = (self.etag or "").strip().strip('"')
        if re.fullmatch(r"[0-9a-fA-F]{32}", etag):
            want = etag.lower()
        else:
            return None
        h = hashlib.md5()
        try:
            with open(self.path, "rb") as f:
                for block in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(block)
        except OSError:
            return None
        ok = (h.hexdigest() == want)
        if ok:
            self._log("MD5 校验通过（与服务器 ETag 一致）")
        else:
            self._log("⚠ MD5 与服务器 ETag 不一致，文件可能损坏")
        return ok


# --------------------------------------------------------------------------
# 便捷函数
# --------------------------------------------------------------------------

def download(url: str, out_dir: str = ".", on_progress: Optional[ProgressFn] = None, **kwargs) -> DownloadResult:
    d = Downloader(url, out_dir=out_dir, **kwargs)
    d.probe()
    return d.run(on_progress)
