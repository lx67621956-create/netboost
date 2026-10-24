"""
netboost 文件类型自动识别
========================

用户不需要手动选分类：下载器自己判断目标文件属于哪一类，
自动选好该类别的默认文件夹，用户只需点「确认」或自己另选目录。

五个分类（按用户要求）：
  video     视频   —— mp4 / mkv / avi / mov ...
  music     音乐   —— mp3 / flac / wav ...
  document  文档   —— docx / pdf / xlsx / pptx ...
  program   程序   —— exe / msi / apk / zip ...
  other     其他   —— 无法归类的

判定优先级（从强到弱）：
  1. 文件名扩展名（最可靠）
  2. Content-Type / MIME（服务器声明）
  3. URL 路径里的扩展名
  4. 探测后的 Content-Disposition 文件名（最终兜底，在 probe 之后调用）

识别结果附带「依据」和「把握程度」，界面会把它显示给用户看。
"""

from __future__ import annotations

import mimetypes
import os
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

__all__ = [
    "CATEGORIES", "Detection", "detect", "detect_from_headers",
    "detect_from_url", "category_label", "category_icon", "ext_of",
    "is_subtitle",
]

# 分类键 -> 显示名
CATEGORIES = {
    "video": "视频",
    "music": "音乐",
    "document": "文档",
    "program": "程序",
    "other": "其他",
}

# 默认子目录名
DEFAULT_SUBDIR = {
    "video": "视频",
    "music": "音乐",
    "document": "文档",
    "program": "程序",
    "other": "其他",
}

ICONS = {
    "video": "▶",
    "music": "♪",
    "document": "▤",
    "program": "⚙",
    "other": "■",
}

# ---------------------------------------------------------------- 扩展名表

_VIDEO = {
    "mp4", "mkv", "avi", "mov", "wmv", "flv", "m4v", "ts", "mpg", "mpeg",
    "rmvb", "rm", "webm", "m2ts", "mts", "vob", "3gp", "3g2", "asf", "divx",
    "f4v", "ogv", "m4p", "m2v", "mpv", "qt", "y4m", "h264", "h265", "hevc",
    "vp9", "av1",
}

_MUSIC = {
    "mp3", "flac", "wav", "aac", "m4a", "ogg", "wma", "ape", "opus", "alac",
    "mid", "midi", "aiff", "aif", "amr", "ac3", "dts", "mka", "ra", "au",
    "mp2", "mpc", "tta", "wv", "dsf", "dff", "spx", "caf",
}

_DOCUMENT = {
    "doc", "docx", "xls", "xlsx", "xlsm", "ppt", "pptx", "pdf", "txt", "rtf",
    "odt", "ods", "odp", "csv", "md", "markdown", "epub", "mobi", "azw",
    "azw3", "djvu", "chm", "tex", "log", "json", "xml", "html", "htm",
    "pages", "numbers", "key", "vsd", "vsdx", "one", "msg", "eml", "wps",
    "et", "dps", "ofd",
}

_PROGRAM = {
    "exe", "msi", "msix", "appx", "appxbundle", "msixbundle", "dmg", "pkg",
    "deb", "rpm", "apk", "ipa", "jar", "war", "bat", "cmd", "ps1", "sh",
    "appimage", "run", "cab", "wim", "vhd", "vhdx",
    # 压缩包通常就是安装包 / 软件下载包
    "zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "zst", "lzma",
    "arp", "lzh", "z",
    "iso",  # 光盘镜像：多为软件安装盘，归程序类更常用
}

# 这些不算程序：.bin/.dat/.iso 等多为数据或镜像，归入「其他」更稳妥
_OTHER_AMBIGUOUS = {
    "bin", "dat", "iso", "img", "raw", "bak", "part", "crdownload", "tmp",
}

SUBTITLE_EXTS = {
    "srt", "ass", "ssa", "vtt", "sub", "idx", "smi", "sami", "lrc", "sup",
    "mpl2", "tmp_sub",
}

# 非图片/非音视频素材
_OTHER = {
    "jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "ico", "tif", "tiff",
    "psd", "ai", "eps", "heic", "raw", "cr2", "nef", "torrent", "ini",
    "cfg", "conf", "dat", "db", "sqlite", "bak", "tmp", "part", "crdownload",
    "bin", "iso", "img",
}


def ext_of(filename: str) -> str:
    """取小写扩展名（不含点）；没有扩展名返回空串。"""
    if not filename:
        return ""
    base = filename.split("?")[0].split("#")[0]
    ext = os.path.splitext(base)[1]
    return ext[1:].lower() if ext else ""


def is_subtitle(filename: str) -> bool:
    return ext_of(filename) in SUBTITLE_EXTS


def category_label(key: str) -> str:
    return CATEGORIES.get(key, "其他")


def category_icon(key: str) -> str:
    return ICONS.get(key, "■")


def _category_for_ext(ext: str) -> str | None:
    if not ext:
        return None
    if ext in _VIDEO:
        return "video"
    if ext in _MUSIC:
        return "music"
    if ext in _DOCUMENT:
        return "document"
    if ext in _PROGRAM:
        return "program"
    if ext in SUBTITLE_EXTS:
        return "video"      # 字幕跟着视频走
    if ext in _OTHER:
        return "other"
    return None


# MIME -> 分类（只在扩展名缺失或无法判断时使用）
_MIME_MAP = {
    "video": "video",
    "audio": "music",
    "image": "other",
}

_MIME_SPECIAL = {
    "application/pdf": "document",
    "application/msword": "document",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "document",
    "application/vnd.ms-excel": "document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "document",
    "application/vnd.ms-powerpoint": "document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "document",
    "text/plain": "document",
    "text/html": "document",
    "text/csv": "document",
    "application/rtf": "document",
    "application/epub+zip": "document",
    "application/x-msdownload": "program",
    "application/x-msi": "program",
    "application/vnd.microsoft.portable-executable": "program",
    "application/x-msdos-program": "program",
    "application/vnd.android.package-archive": "program",
    "application/x-7z-compressed": "program",
    "application/zip": "program",
    "application/x-rar-compressed": "program",
    "application/x-tar": "program",
    "application/gzip": "program",
    "application/x-debian-package": "program",
    "application/x-apple-diskimage": "program",
    "application/octet-stream": "other",
}


def _category_for_mime(mime: str) -> str | None:
    if not mime:
        return None
    mime = mime.split(";")[0].strip().lower()
    if mime in _MIME_SPECIAL:
        cat = _MIME_SPECIAL[mime]
        return None if cat == "other" else cat
    major = mime.split("/")[0]
    if major in _MIME_MAP:
        cat = _MIME_MAP[major]
        return None if cat == "other" else cat
    return None


# ---------------------------------------------------------------- 结果对象

@dataclass
class Detection:
    category: str = "other"
    reason: str = ""            # 判定依据，给用户看
    confidence: str = "low"     # high / medium / low
    ext: str = ""
    filename: str = ""

    @property
    def label(self) -> str:
        return category_label(self.category)

    @property
    def icon(self) -> str:
        return category_icon(self.category)

    @property
    def confident(self) -> bool:
        return self.confidence in ("high", "medium")

    def describe(self) -> str:
        if self.ext:
            return f"{self.label}文件（.{self.ext}）"
        return f"{self.label}文件"


# ---------------------------------------------------------------- 判定入口

def _filename_from_url(url: str) -> str:
    try:
        path = urlsplit(url).path
    except ValueError:
        return ""
    if not path:
        return ""
    name = unquote(path.rstrip("/").rsplit("/", 1)[-1])
    return name if "." in name else ""


def detect_from_url(url: str) -> Detection:
    """仅凭 URL 判断（不需要联网）。"""
    name = _filename_from_url(url)
    ext = ext_of(name)
    cat = _category_for_ext(ext)
    if cat:
        return Detection(category=cat, ext=ext, filename=name,
                         reason=f"链接文件名扩展名 .{ext}", confidence="high")
    return Detection(category="other", ext=ext, filename=name,
                     reason="链接里看不出文件类型", confidence="low")


def detect_from_headers(url: str, filename: str = "", content_type: str = "") -> Detection:
    """用服务器返回的文件名 / Content-Type 判断（probe 之后调用）。"""
    ext = ext_of(filename)
    cat = _category_for_ext(ext)
    if cat:
        return Detection(category=cat, ext=ext, filename=filename,
                         reason=f"文件名扩展名 .{ext}", confidence="high")

    cat = _category_for_mime(content_type)
    if cat:
        short = (content_type or "").split(";")[0].strip()
        return Detection(category=cat, ext=ext, filename=filename,
                         reason=f"服务器类型 {short}", confidence="medium")

    # 退回 URL
    got = detect_from_url(url)
    if got.category != "other":
        if not filename:
            got.filename = got.filename
        return got

    # mimetypes 兜底
    if ext:
        guess, _ = mimetypes.guess_type("x." + ext)
        cat = _category_for_mime(guess or "")
        if cat:
            return Detection(category=cat, ext=ext, filename=filename,
                             reason=f"推测 .{ext} 属于{category_label(cat)}", confidence="low")

    return Detection(category="other", ext=ext, filename=filename,
                     reason="无法判断类型，放入「其他」", confidence="low")


def detect(url: str, filename: str = "", content_type: str = "") -> Detection:
    """统一入口：有服务器信息就用，没有就只看 URL。"""
    if filename or content_type:
        return detect_from_headers(url, filename, content_type)
    return detect_from_url(url)
