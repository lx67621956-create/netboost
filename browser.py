"""
netboost 浏览器 / 系统下载接管
=============================

设计原则（非常重要）：
  **只做可逆的、作用域最小的改动，并且保存原始值，随时能一键还原。**

做什么：
  1. 注册 `netboost://` 协议（HKCU\\Software\\Classes\\netboost）
     —— HKEY_CURRENT_USER 不需要管理员权限，卸载/还原时删掉即可。
  2. 可选：把浏览器里"点击下载链接"的行为交给 netboost。
     不同浏览器机制不同：
       * Chrome / Edge / 360 极速（Chromium 系）：没有官方接口改默认下载器，
         只能装扩展或改策略。这里不硬改，改为提供 `netboost://` 链接方式，
         并在界面里给出说明，避免动别人的配置。
       * Firefox：有 network.protocol-handler.* 偏好，但改 profile 风险高，不做。
     所以本模块的真实能力是：**注册协议 + 提供"设为默认"和"还原"开关**，
     并且能列出检测到的浏览器，告诉用户可用的接入方式。
  3. 检测已安装浏览器（只读注册表，不修改）。

不做什么：
  * 不劫持任何系统的 .exe / .zip 文件关联
  * 不修改 HKEY_LOCAL_MACHINE（不改全机器，只改当前用户）
  * 不碰浏览器的用户数据目录 / 配置文件
  * 不做任何卸载后无法还原的改动
"""

from __future__ import annotations

import os
import sys

__all__ = [
    "PROTOCOL", "is_windows", "protocol_registered",
    "register_protocol", "unregister_protocol",
    "detect_browsers", "open_in_browser_help",
]

PROTOCOL = "netboost"
_CLASSES = r"Software\Classes"
_KEY = rf"{_CLASSES}\{PROTOCOL}"

# 当前用户下的浏览器探测（只读）
_BROWSER_KEYS = [
    ("Google Chrome", r"Software\Google\Chrome\BLBeacon", "Chrome"),
    ("Microsoft Edge", r"Software\Microsoft\Edge\BLBeacon", "Edge"),
    ("360 极速浏览器", r"Software\360Chrome\Chrome\BLBeacon", "360极速"),
    ("360 安全浏览器", r"Software\360SE6\Install", "360安全"),
    ("Firefox", r"Software\Mozilla\Mozilla Firefox", "Firefox"),
    ("QQ 浏览器", r"Software\Tencent\QQBrowser", "QQ浏览器"),
    ("搜狗浏览器", r"Software\Sogou\SogouExplorer", "搜狗"),
    ("Opera", r"Software\Opera Software\Opera Stable", "Opera"),
    ("Brave", r"Software\BraveSoftware\Brave-Browser", "Brave"),
    ("Vivaldi", r"Software\Vivaldi", "Vivaldi"),
]


def is_windows() -> bool:
    return sys.platform.startswith("win")


# --------------------------------------------------------------------------
# 协议注册（仅 HKEY_CURRENT_USER，无需管理员，可完全撤销）
# --------------------------------------------------------------------------

def _winreg():
    import winreg  # 仅 Windows 可用
    return winreg


def _launcher_command() -> str:
    """生成协议调用的命令行：用当前程序打开传入的链接。"""
    if getattr(sys, "frozen", False):
        exe = sys.executable
        return f'"{exe}" "%1"'
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
    py = sys.executable
    pyw = py.replace("python.exe", "pythonw.exe")
    if os.path.exists(pyw):
        py = pyw
    return f'"{py}" "{script}" "%1"'


def protocol_registered() -> bool:
    """协议是否已经注册过。"""
    if not is_windows():
        return False
    try:
        winreg = _winreg()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY):
            return True
    except OSError:
        return False


def register_protocol() -> tuple[bool, str]:
    """注册 netboost:// 协议。返回 (成功, 说明)。"""
    if not is_windows():
        return False, "仅支持 Windows"
    winreg = _winreg()
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _KEY, 0,
                                winreg.KEY_WRITE) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:netboost Protocol")
            winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                _KEY + r"\DefaultIcon", 0, winreg.KEY_WRITE) as k:
            icon = os.path.join(_base_dir(), "app.ico")
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, icon if os.path.exists(icon) else "")
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                _KEY + r"\shell\open\command", 0,
                                winreg.KEY_WRITE) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, _launcher_command())
        return True, "已注册 netboost:// 协议（仅当前用户）"
    except OSError as e:
        return False, f"注册失败：{e}"


def unregister_protocol() -> tuple[bool, str]:
    """删除协议注册，恢复到注册之前的状态。"""
    if not is_windows():
        return False, "仅支持 Windows"
    winreg = _winreg()
    removed = False
    try:
        # 自底向上删除子键，最后删根键
        for sub in (r"\shell\open\command", r"\shell\open", r"\shell",
                    r"\DefaultIcon", ""):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY + sub, 0,
                                    winreg.KEY_ALL_ACCESS) as k:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _KEY + sub)
                removed = True
            except OSError:
                pass
        return True, ("已还原：协议注册已移除"
                      if removed else "本来就是干净的，无需还原")
    except OSError as e:
        return False, f"还原失败：{e}"


def _base_dir() -> str:
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------
# 浏览器探测（只读）
# --------------------------------------------------------------------------

def detect_browsers() -> list[dict]:
    """列出检测到的浏览器。只读注册表，不修改任何东西。"""
    if not is_windows():
        return []
    winreg = _winreg()
    found = []
    for name, key, short in _BROWSER_KEYS:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key):
                found.append({"name": name, "short": short, "installed": True})
        except OSError:
            # 再看机器级
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key):
                    found.append({"name": name, "short": short, "installed": True})
            except OSError:
                continue
    return found


def open_in_browser_help(short: str) -> str:
    """给用户一句如实的接入说明（不做虚假承诺）。"""
    chrominum = {"Chrome", "Edge", "360极速", "QQ浏览器", "Brave", "Vivaldi", "Opera"}
    if short in chrominum:
        return (f"{short} 属于 Chromium 内核，官方没有提供「修改默认下载器」的接口。"
                f"可用方式：在网页里给链接使用 netboost:// 前缀，或右键复制链接后在 netboost 中粘贴。")
    if short == "Firefox":
        return ("Firefox 可以在 设置 → 应用 中把对应类型交给外部程序，"
                "但它无法整体接管下载；建议复制链接到 netboost。")
    return "建议复制下载链接，粘贴到 netboost 中下载。"
