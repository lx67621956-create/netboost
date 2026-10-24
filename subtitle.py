"""
netboost 字幕处理
=================

“字幕跟电影放在一起”的落地逻辑：

1. 字幕 URL 下载后，按**影片同名**重命名：
      影片：Interstellar.2014.1080p.mkv
      字幕：Interstellar.2014.1080p.srt   ← 播放器自动挂载
2. 本地字幕文件直接复制到影片目录，同样改名。
3. 多语言字幕加语言后缀：
      Interstellar.2014.1080p.chs.srt
      Interstellar.2014.1080p.eng.srt

大多数播放器（PotPlayer / VLC / MPC-HC / 电视盒子）都会自动加载与影片同名的字幕。
"""

from __future__ import annotations

import os
import re
import shutil

from core import DownloadError, Downloader

__all__ = [
    "SUBTITLE_EXTS", "is_subtitle_name", "strip_video_ext",
    "subtitle_target", "download_subtitle", "import_local_subtitle",
    "LANG_HINTS",
]

SUBTITLE_EXTS = (".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".smi")

# 常见语言/字幕标记 -> 标准后缀
LANG_HINTS = [
    (("chs", "sc", "简体", "简中", "中文", "zh-cn", "zh_cn", "chi", "zho"), "chs"),
    (("cht", "tc", "繁体", "繁中", "zh-tw", "zh_tw"), "cht"),
    (("eng", "en", "english", "英文"), "eng"),
    (("jpn", "jp", "ja", "japanese", "日文"), "jpn"),
    (("kor", "kr", "ko", "korean", "韩文"), "kor"),
]

_VIDEO_EXTS = (
    ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".m4v", ".ts", ".mpg",
    ".mpeg", ".rmvb", ".rm", ".webm", ".m2ts", ".iso",
)


def is_subtitle_name(name: str) -> bool:
    return name.lower().endswith(SUBTITLE_EXTS)


def strip_video_ext(filename: str) -> str:
    """去掉视频扩展名，得到字幕的基准名。"""
    stem, ext = os.path.splitext(filename)
    if ext.lower() in _VIDEO_EXTS:
        return stem
    return filename if ext else stem


def guess_language(text: str) -> str | None:
    """从字幕文件名里猜语言标签。"""
    low = (text or "").lower()
    for keys, tag in LANG_HINTS:
        for k in keys:
            if k in low:
                return tag
    return None


def subtitle_target(video_filename: str, subtitle_ext: str,
                    language: str | None = None) -> str:
    """生成与影片同名的字幕文件名。"""
    base = strip_video_ext(video_filename)
    ext = subtitle_ext if subtitle_ext.startswith(".") else "." + subtitle_ext
    if 0 < len(ext) <= 6 and re.fullmatch(r"\.[A-Za-z0-9]+", ext):
        pass
    else:
        ext = ".srt"
    if language:
        return f"{base}.{language}{ext.lower()}"
    return f"{base}{ext.lower()}"


def _detect_ext_from_bytes(data: bytes) -> str:
    head = data[:512].lstrip(b"\xef\xbb\xbf").lstrip()
    low = head.lower()
    if low.startswith(b"[script info]") or b"dialogue:" in low:
        return ".ass"
    if low.startswith(b"webvtt"):
        return ".vtt"
    if low.startswith(b"<!doctype") or low.startswith(b"<html") or b"<sami" in low:
        return ".smi"
    if re.match(rb"^\s*\d+\s*\r?\n\d{2}:\d{2}:\d{2}", head):
        return ".srt"
    if b"-->" in low and b"<" in low:
        return ".vtt"
    return ".srt"


def download_subtitle(
    url: str,
    video_filename: str,
    movie_dir: str,
    language: str | None = None,
    log=None,
) -> str:
    """下载一条字幕，保存为与影片同名的字幕文件。返回最终路径。"""
    log = log or (lambda m: None)
    dl = Downloader(url, out_dir=movie_dir, connections=2, max_connections=4,
                    adaptive=False, retries=4, verify_md5=False)
    dl.probe()

    ext = os.path.splitext(dl.filename or "")[1].lower()
    if ext not in SUBTITLE_EXTS:
        ext = None

    # 先下到临时名，拿到内容后再判断真实格式
    tmp_dir = os.path.join(movie_dir, ".netboost-sub")
    os.makedirs(tmp_dir, exist_ok=True)
    dl.out_dir = tmp_dir
    dl.path = None
    dl.probed = False
    dl_retry = Downloader(url, out_dir=tmp_dir, connections=2, max_connections=4,
                          adaptive=False, retries=4, verify_md5=False)
    dl_retry.probe()
    result = dl_retry.run()

    try:
        with open(result.path, "rb") as f:
            head = f.read(2048)
        if not ext:
            ext = _detect_ext_from_bytes(head)
        lang = language or guess_language(os.path.basename(url)) \
            or guess_language(dl_retry.filename or "")
        final = os.path.join(movie_dir, subtitle_target(video_filename, ext, lang))
        shutil.move(result.path, final)
        log(f"字幕已保存：{os.path.basename(final)}")
        return final
    finally:
        try:
            for name in os.listdir(tmp_dir):
                os.remove(os.path.join(tmp_dir, name))
            os.rmdir(tmp_dir)
        except OSError:
            pass


def import_local_subtitle(local_path: str, video_filename: str, movie_dir: str,
                          language: str | None = None, log=None) -> str:
    """把本地字幕文件复制到影片目录并改成同名。"""
    log = log or (lambda m: None)
    if not os.path.isfile(local_path):
        raise DownloadError(f"字幕文件不存在：{local_path}")
    ext = os.path.splitext(local_path)[1].lower()
    if ext not in SUBTITLE_EXTS:
        ext = ".srt"
    lang = language or guess_language(os.path.basename(local_path))
    final = os.path.join(movie_dir, subtitle_target(video_filename, ext, lang))
    if os.path.abspath(local_path) == os.path.abspath(final):
        return final
    shutil.copy2(local_path, final)
    log(f"字幕已复制：{os.path.basename(final)}")
    return final
