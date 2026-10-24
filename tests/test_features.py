"""
功能测试：字幕联动（新版自动识别架构）
=====================================

新版流程是「自动识别 → 自动目录 → 确认 → 下载」，
分类不再由用户手动切换，所以这里直接测「视频分类 + 字幕」这条链路：

  1. 视频下载后，字幕与影片同名同目录
  2. 多语言字幕带语言后缀、不互相覆盖
  3. 本地字幕文件导入（ASS 不被误判成 SRT）
  4. 音乐分类不带字幕
  5. 分类目录各自正确
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
import config  # noqa: E402
from detect import detect_from_url  # noqa: E402

MOVIE = bytes((i * 23 + 11) % 256 for i in range(2 * 1024 * 1024))
MOVIE_SHA = hashlib.sha256(MOVIE).hexdigest()
SRT = b"""1
00:00:01,000 --> 00:00:03,000
Hello netboost.
"""
SRT_EN = b"""1
00:00:01,000 --> 00:00:03,000
English line.
"""
ASS = b"""[Script Info]
ScriptType: v4.00+

[V4+ Styles]

[Events]
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Chinese subtitle line
"""
SONG = bytes((i * 7 + 3) % 256 for i in range(512 * 1024))


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path
        if p.startswith("/movie"):
            data, name, ctype = MOVIE, "Interstellar.2014.1080p.mkv", "video/x-matroska"
        elif p.startswith("/song"):
            data, name, ctype = SONG, "song.mp3", "audio/mpeg"
        elif "eng" in p:
            data, name, ctype = SRT_EN, "Interstellar.eng.srt", "text/plain"
        elif "chs" in p:
            data, name, ctype = SRT, "Interstellar.chs.srt", "text/plain"
        else:
            data, name, ctype = ASS, "Interstellar.ass", "text/plain"

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


RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")


def wait_all(win, timeout=90):
    """等所有任务下载完 + 字幕线程收尾。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        win.update()
        rows = list(win.rows.values())
        if rows and all(
            (r.dl.total and r.dl.downloaded() >= r.dl.total) or r.last_status == "失败"
            for r in rows
        ):
            for _ in range(50):
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

    root = tempfile.mkdtemp(prefix="netboost-feat-")
    os.environ["NETBOOST_CONFIG"] = os.path.join(root, "config.json")
    video_dir = os.path.join(root, "视频")
    music_dir = os.path.join(root, "音乐")
    dirs = {"video": video_dir, "music": music_dir,
            "document": os.path.join(root, "文档"),
            "program": os.path.join(root, "程序"),
            "other": os.path.join(root, "其他")}

    win = gui_app.App()
    win.update()
    win.cfg["dirs"] = dict(dirs)
    win.cfg["ui"]["confirm_before_download"] = False
    config.save_config(win.cfg)

    print("=== 1. 分类目录 ===")
    for key, want in dirs.items():
        got = config.category_dir(win.cfg, key)
        check(f"{config.CATEGORIES[key]} 目录正确",
              os.path.normpath(got) == os.path.normpath(want), got)

    print("\n=== 2. 视频 + 中英双字幕 ===")
    win._start_download(base + "/movie.bin", "video", video_dir,
                        [f"{base}/Interstellar.chs.srt", f"{base}/Interstellar.eng.srt"])
    ok = wait_all(win)
    check("视频+字幕任务完成", ok)

    row = list(win.rows.values())[-1]
    path = row.dl.path or ""
    check("影片存进视频目录", os.path.dirname(path) == os.path.normpath(video_dir), path)
    check("影片完整性",
          os.path.exists(path) and hashlib.sha256(open(path, "rb").read()).hexdigest() == MOVIE_SHA)
    check("处理了 2 条字幕", len(row.sub_results) == 2, str(row.sub_results))

    if len(row.sub_results) >= 2:
        names = [os.path.basename(p) for p in row.sub_results]
        check("两条字幕都与影片同名（前缀一致）",
              all(n.startswith("Interstellar.2014.1080p") for n in names), str(names))
        check("语言后缀不同、不互相覆盖", len(set(names)) == 2, str(names))
        check("字幕位于影片同目录",
              all(os.path.dirname(os.path.normpath(p)) == os.path.normpath(video_dir)
                  for p in row.sub_results))
        chs = [p for p in row.sub_results if "chs" in os.path.basename(p)]
        check("简体字幕内容正确",
              bool(chs) and b"Hello netboost" in open(chs[0], "rb").read())

    print("\n=== 3. 本地 ASS 字幕导入 ===")
    local_ass = os.path.join(root, "我的字幕.ass")
    with open(local_ass, "wb") as f:
        f.write(ASS)
    win._start_download(base + "/movie2.bin", "video", video_dir, [f'"{local_ass}"'])
    ok2 = wait_all(win)
    check("本地字幕任务完成", ok2)
    row2 = list(win.rows.values())[-1]
    check("本地字幕已导入", len(row2.sub_results) == 1, str(row2.sub_results))
    if row2.sub_results:
        sp = row2.sub_results[0]
        check("导入字幕扩展名为 .ass", sp.endswith(".ass"), os.path.basename(sp))
        check("ASS 内容未被误判/损坏", b"ScriptType" in open(sp, "rb").read())
        check("导入字幕与影片同名",
              os.path.basename(sp).startswith("Interstellar.2014.1080p"), os.path.basename(sp))

    print("\n=== 4. 音乐分类不带字幕 ===")
    win._start_download(base + "/song.bin", "music", music_dir, [])
    ok3 = wait_all(win)
    check("音乐任务完成", ok3)
    row3 = list(win.rows.values())[-1]
    check("音乐任务无字幕", len(row3.sub_results) == 0)
    check("音乐存进音乐目录",
          os.path.dirname(row3.dl.path or "") == os.path.normpath(music_dir),
          row3.dl.path)
    check("音乐文件完整",
          os.path.exists(row3.dl.path) and open(row3.dl.path, "rb").read() == SONG)

    print("\n=== 5. 识别结果与目录一致（真实链路：probe 后用服务器文件名判断）===")
    # URL 本身是 /movie.bin（.bin 无法归类），真实流程会先 probe 拿到
    # Content-Disposition 里的 Interstellar.2014.1080p.mkv，再据此判断。
    cases = [
        ("movie.bin", "Interstellar.2014.1080p.mkv", "video/x-matroska", "video"),
        ("song.bin", "song.mp3", "audio/mpeg", "music"),
    ]
    for path, fname, ctype, want_key in cases:
        det = detect_from_url(base + "/" + path)
        check(f"仅看 URL（{path}）确实无法判断",
              det.category == "other", f"实际 {det.category}")

        det = __import__("detect").detect(base + "/" + path, fname, ctype)
        check(f"probe 后识别为 {config.CATEGORIES[want_key]}",
              det.category == want_key, f"{det.category} / {det.reason}")
        check(f"{path} 默认目录正确",
              os.path.normpath(config.category_dir(win.cfg, det.category))
              == os.path.normpath(dirs[want_key]))

    win.destroy()
    srv.shutdown()

    print("\n" + "=" * 62)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"结果：{passed}/{len(RESULTS)} 通过")
    for name, ok, _ in RESULTS:
        if not ok:
            print(f"  [FAIL] {name}")
    print("=" * 62)
    os.environ.pop("NETBOOST_CONFIG", None)
    shutil.rmtree(root, ignore_errors=True)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
