"""
netboost 配置存取
=================

保存分类下载目录、浏览器接管开关等设置。
配置位置（按优先级）：
  1. 环境变量 NETBOOST_CONFIG 指定的文件
  2. %APPDATA%\\netboost\\config.json
  3. 用户主目录 .netboost\\config.json
"""

from __future__ import annotations

import json
import os

from detect import CATEGORIES, DEFAULT_SUBDIR

__all__ = [
    "CATEGORIES", "load_config", "save_config", "config_path",
    "category_dir", "default_config", "DOWNLOADS",
]

DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")


def default_config() -> dict:
    """默认：每个分类在「下载」目录下各建一个子文件夹。"""
    return {
        "version": 2,
        "dirs": {
            key: os.path.join(DOWNLOADS, DEFAULT_SUBDIR[key])
            for key in CATEGORIES
        },
        # 关掉 = 完全不影响系统，双击 exe 也不会改任何注册表
        "browser": {
            "associate": False,       # 是否接管浏览器下载（需用户主动开启）
            "protocol": True,         # netboost:// 协议（开启后才注册）
            "clipboard_watch": False, # 监视剪贴板里的链接
            "last_registered": False, # 记录上次注册状态，用于启动时清理
        },
        "ui": {
            "confirm_before_download": True,   # 下载前弹确认框
            "open_folder_after": False,        # 完成后打开文件夹
        },
    }


def config_path() -> str:
    env = os.environ.get("NETBOOST_CONFIG")
    if env:
        return os.path.abspath(env)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "netboost", "config.json")
    return os.path.join(os.path.expanduser("~"), ".netboost", "config.json")


def _merge_dict(base: dict, override: dict) -> None:
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge_dict(base[k], v)
        elif v is not None:
            base[k] = v


def load_config() -> dict:
    """读取配置；缺失或损坏时返回默认值（不抛异常，保证能启动）。"""
    cfg = default_config()
    path = config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return cfg
    if not isinstance(data, dict):
        return cfg

    dirs = data.get("dirs")
    if isinstance(dirs, dict):
        for key in CATEGORIES:
            value = dirs.get(key)
            if isinstance(value, str) and value.strip():
                cfg["dirs"][key] = os.path.expandvars(os.path.expanduser(value.strip()))
    for section in ("browser", "ui"):
        if isinstance(data.get(section), dict):
            _merge_dict(cfg[section], data[section])
    return cfg


def save_config(cfg: dict) -> bool:
    """原子写入配置；失败返回 False（例如没有写权限）。"""
    path = config_path()
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def category_dir(cfg: dict, key: str) -> str:
    """取某个分类的目录，缺失时回退到该分类默认值。"""
    value = (cfg.get("dirs") or {}).get(key)
    if isinstance(value, str) and value.strip():
        return os.path.expandvars(os.path.expanduser(value.strip()))
    return os.path.join(DOWNLOADS, DEFAULT_SUBDIR.get(key, "其他"))
