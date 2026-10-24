"""
netboost 图形界面（Tkinter，零第三方依赖）
========================================

核心流程：粘贴/拖入链接 → 自动识别文件类型 → 自动选好对应文件夹 → 用户确认。

双击 netboost下载器.exe 启动，或运行 python app.py
"""

from __future__ import annotations

import os
import queue
import shlex
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import browser as browser_mod  # noqa: E402
from config import (  # noqa: E402
    CATEGORIES, category_dir, load_config, save_config,
)
from core import DownloadError, Downloader, format_eta, format_size, format_speed  # noqa: E402
from detect import (  # noqa: E402
    category_icon, category_label, detect, detect_from_url, is_subtitle,
)
from subtitle import (  # noqa: E402
    download_subtitle, import_local_subtitle,
)

APP_TITLE = "netboost · 多连接下载器"
BG = "#0f1419"
CARD = "#171d25"
CARD2 = "#21262d"
FG = "#e6edf3"
MUTED = "#96a3b0"
ACCENT = "#2f81f7"
GREEN = "#3fb950"
RED = "#f85149"
YELLOW = "#d29922"

# ---- 字号 ----
F_BODY = ("Microsoft YaHei UI", 12)
F_SMALL = ("Microsoft YaHei UI", 11)
F_BOLD = ("Microsoft YaHei UI", 12, "bold")
F_TITLE = ("Microsoft YaHei UI", 20, "bold")
F_BIG = ("Microsoft YaHei UI", 14, "bold")
F_ENTRY = ("Consolas", 12)
F_TREE = ("Microsoft YaHei UI", 11)
F_TREE_HEAD = ("Microsoft YaHei UI", 11, "bold")
F_ROW = ("Microsoft YaHei UI", 13)

ROW_H = 38

CAT_COLORS = {
    "video": "#f778ba",
    "music": "#3fb950",
    "document": "#d29922",
    "program": "#2f81f7",
    "other": "#8b98a5",
}


class DownloadRow:
    def __init__(self, dl: Downloader, tree: ttk.Treeview, category: str):
        self.dl = dl
        self.tree = tree
        self.category = category
        self.sub_urls: list[str] = []
        self.sub_results: list[str] = []
        self.item = tree.insert("", "end", values=(
            dl.url[:60], self.cat_text, "?", "0%", "0 B/s", "等待中", "--:--",
        ))
        self.last_status = ""

    @property
    def cat_text(self) -> str:
        return f"{category_icon(self.category)} {category_label(self.category)}"

    def refresh(self) -> None:
        dl = self.dl
        total = dl.total or 0
        done = dl.downloaded()
        pct = (done / total * 100.0) if total else 0.0
        if dl.failed:
            state = "失败"
        elif dl.paused:
            state = "已暂停"
        elif total and done >= total:
            state = "完成"
        else:
            state = "下载中"
        self.last_status = state
        remain = max(0, total - done)
        eta = (remain / dl.speed) if dl.speed > 1 and remain else None
        name = dl.filename or dl.url[:60]
        if self.sub_results:
            name += f"  (+{len(self.sub_results)} 字幕)"
        self.tree.item(self.item, values=(
            name, self.cat_text,
            format_size(total) if total else "?",
            f"{pct:.1f}%",
            format_speed(dl.speed) if state == "下载中" else "0 B/s",
            f"{state}({dl.active_connections})" if state == "下载中" else state,
            format_eta(eta) if state == "下载中" else "--:--",
        ))


# --------------------------------------------------------------------------
# 确认对话框：自动识别结果 + 自动目录，用户只需确认或另选
# --------------------------------------------------------------------------

class ConfirmDialog(tk.Toplevel):
    def __init__(self, master: "App", url: str, result: dict):
        super().__init__(master)
        self.app = master
        self.result = None
        self.url = url
        self.title("确认下载")
        self.configure(bg=BG)
        self.transient(master)
        self.resizable(False, False)

        det = result["detection"]
        pad = 26

        head = ttk.Frame(self)
        head.pack(fill="x", padx=pad, pady=(pad, 4))
        ttk.Label(head, text="已自动识别", style="Title2.TLabel").pack(anchor="w")

        # 类型徽标
        badge = tk.Frame(self, bg=CARD)
        badge.pack(fill="x", padx=pad, pady=(12, 6))
        color = CAT_COLORS.get(det.category, MUTED)
        tk.Label(badge, text=f"  {det.icon}  {det.label}  ", bg=color, fg="#0f1419",
                 font=F_BIG).pack(side="left", padx=(12, 12), pady=12)
        info = tk.Frame(badge, bg=CARD)
        info.pack(side="left", fill="x", expand=True, pady=8)
        tk.Label(info, text=det.filename or "（服务器未提供文件名）", bg=CARD, fg=FG,
                 font=F_BODY, anchor="w").pack(fill="x")
        tk.Label(info, text=f"判断依据：{det.reason}", bg=CARD, fg=MUTED,
                 font=F_SMALL, anchor="w").pack(fill="x", pady=(2, 0))
        size_txt = format_size(result["total"]) if result.get("total") else "未知"
        tk.Label(info, text=f"大小：{size_txt}", bg=CARD, fg=MUTED,
                 font=F_SMALL, anchor="w").pack(fill="x", pady=(2, 0))

        # 类型可手动改
        catrow = ttk.Frame(self)
        catrow.pack(fill="x", padx=pad, pady=(12, 6))
        ttk.Label(catrow, text="类型", font=F_BOLD).pack(side="left", padx=(0, 12))
        self.cat_var = tk.StringVar(value=det.category)
        self.cat_buttons: dict[str, ttk.Button] = {}
        for key, label in CATEGORIES.items():
            btn = ttk.Button(catrow, text=f"{category_icon(key)} {label}",
                             style="Cat.TButton",
                             command=lambda k=key: self._set_cat(k))
            btn.pack(side="left", padx=(0, 6))
            self.cat_buttons[key] = btn

        # 保存目录
        dirrow = ttk.Frame(self)
        dirrow.pack(fill="x", padx=pad, pady=(6, 6))
        ttk.Label(dirrow, text="保存到", font=F_BOLD).pack(side="left", padx=(0, 12))
        self.dir_var = tk.StringVar(value=result["out_dir"])
        ttk.Entry(dirrow, textvariable=self.dir_var, font=("Consolas", 11)).pack(
            side="left", fill="x", expand=True)
        ttk.Button(dirrow, text="另选…", command=self._pick_dir).pack(
            side="left", padx=(10, 0))

        # 字幕（仅视频）—— 必须在底部按钮之前 pack，否则会显示到按钮下方
        self.sub_box = ttk.Frame(self)
        ttk.Label(self.sub_box, text="字幕", font=F_BOLD).pack(side="left", padx=(0, 12))
        self.sub_var = tk.StringVar()
        ttk.Entry(self.sub_box, textvariable=self.sub_var,
                  font=F_ENTRY).pack(side="left", fill="x", expand=True)
        ttk.Button(self.sub_box, text="本地字幕…",
                   command=self._pick_subtitle).pack(side="left", padx=(10, 0))
        self.sub_hint = ttk.Label(
            self, text="字幕会与影片同名保存（如 影片.mkv + 影片.srt），播放器一般能自动加载。"
                       "可填多个链接或本地文件，空格分隔。",
            style="Muted.TLabel", wraplength=760, justify="left")
        self.sub_box.pack(fill="x", padx=pad, pady=(6, 6))
        self.sub_hint.pack(fill="x", padx=pad, pady=(0, 4))

        # 底部
        foot = ttk.Frame(self)
        foot.pack(fill="x", padx=pad, pady=(16, pad))
        self._foot = foot
        ttk.Button(foot, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(foot, text="确认下载", style="Accent.TButton",
                   command=self._confirm).pack(side="right", padx=(0, 10))

        # 现在控件齐了，再设置初始分类（会处理字幕栏显隐）
        self._set_cat(det.category, keep_dir=True)
        self.update_idletasks()
        w = max(860, self.winfo_reqwidth())
        self.geometry(f"{w}x{self.winfo_reqheight()}"
                      f"+{master.winfo_rootx() + 70}+{master.winfo_rooty() + 60}")
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()
        self.bind("<Return>", lambda e: self._confirm())
        self.bind("<Escape>", lambda e: self._cancel())

    def _set_cat(self, key: str, keep_dir: bool = False):
        self.cat_var.set(key)
        for k, btn in self.cat_buttons.items():
            btn.configure(style="CatOn.TButton" if k == key else "Cat.TButton")
        if not keep_dir:
            self.dir_var.set(category_dir(self.app.cfg, key))
        self._apply_visibility()

    def _apply_visibility(self):
        is_video = self.cat_var.get() == "video"
        # 始终在「底部按钮」之前插入，保证字幕栏位置正确
        if is_video:
            self.sub_box.pack(fill="x", padx=26, pady=(6, 6), before=self._foot)
            self.sub_hint.pack(fill="x", padx=26, pady=(0, 4), before=self._foot)
        else:
            self.sub_box.pack_forget()
            self.sub_hint.pack_forget()
        self.update_idletasks()

    def _pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.dir_var.get() or ".",
                                    parent=self, title="选择保存文件夹")
        if d:
            self.dir_var.set(os.path.normpath(d))

    def _pick_subtitle(self):
        paths = filedialog.askopenfilenames(
            parent=self, title="选择字幕文件",
            filetypes=[("字幕文件", "*.srt *.ass *.ssa *.vtt *.sub *.idx *.smi"),
                       ("所有文件", "*.*")])
        if paths:
            joined = " ".join(f'"{p}"' for p in paths)
            cur = self.sub_var.get().strip()
            self.sub_var.set((cur + " " + joined).strip())

    def _confirm(self):
        out_dir = self.dir_var.get().strip()
        if not out_dir:
            messagebox.showerror("不能为空", "请填写保存文件夹", parent=self)
            return
        raw = self.sub_var.get().strip()
        subs = []
        if raw:
            try:
                subs = shlex.split(raw, posix=False)
            except ValueError:
                subs = raw.split()
        self.result = {
            "category": self.cat_var.get(),
            "out_dir": os.path.normpath(os.path.expanduser(out_dir)),
            "subtitles": subs,
        }
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


# --------------------------------------------------------------------------
# 设置对话框
# --------------------------------------------------------------------------

class SettingsDialog(tk.Toplevel):
    def __init__(self, master: "App"):
        super().__init__(master)
        self.app = master
        self.title("设置")
        self.configure(bg=BG)
        self.transient(master)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=18, pady=18)

        self._build_dirs_tab(nb)
        self._build_browser_tab(nb)
        self._build_about_tab(nb)

        foot = ttk.Frame(self)
        foot.pack(fill="x", padx=18, pady=(0, 18))
        ttk.Button(foot, text="恢复默认", command=self._reset_dirs).pack(side="left")
        ttk.Button(foot, text="保存", style="Accent.TButton",
                   command=self._save).pack(side="right")
        ttk.Button(foot, text="取消", command=self.destroy).pack(side="right", padx=(0, 8))

        self.update_idletasks()
        w = max(820, self.winfo_reqwidth())
        self.geometry(f"{w}x{self.winfo_reqheight()}"
                      f"+{master.winfo_rootx() + 50}+{master.winfo_rooty() + 30}")
        self.grab_set()

    # ---- 目录页
    def _build_dirs_tab(self, nb):
        tab = ttk.Frame(nb, style="Card.TFrame")
        nb.add(tab, text="  分类文件夹  ")
        pad = 20

        ttk.Label(tab, text="自动识别后，文件会存到对应类型的文件夹",
                  style="CardTitle.TLabel").pack(anchor="w", padx=pad, pady=(pad, 2))
        ttk.Label(tab, text="下载器会自己判断目标文件是视频/音乐/文档/程序，"
                            "自动选好这里的目录；你每次都可以在确认框里改。",
                  style="CardMuted.TLabel", wraplength=740,
                  justify="left").pack(anchor="w", padx=pad, pady=(0, 12))

        self.dir_vars: dict[str, tk.StringVar] = {}
        form = ttk.Frame(tab, style="Card.TFrame")
        form.pack(fill="x", padx=pad, pady=(0, 12))
        for key, label in CATEGORIES.items():
            row = ttk.Frame(form, style="Card.TFrame")
            row.pack(fill="x", pady=5)
            tk.Label(row, text=f"{category_icon(key)} {label}", bg=CARD, fg=FG,
                     font=F_BOLD, width=7, anchor="w").pack(side="left")
            var = tk.StringVar(value=category_dir(self.app.cfg, key))
            self.dir_vars[key] = var
            ttk.Button(row, text="选择…", width=8,
                       command=lambda v=var: self._pick(v)).pack(side="right")
            ttk.Entry(row, textvariable=var, font=("Consolas", 11),
                      width=50).pack(side="left", fill="x", expand=True, padx=(10, 8))

    def _pick(self, var):
        d = filedialog.askdirectory(initialdir=var.get() or ".", parent=self)
        if d:
            var.set(os.path.normpath(d))

    def _reset_dirs(self):
        from config import default_config
        d = default_config()["dirs"]
        for key, var in self.dir_vars.items():
            var.set(d[key])

    # ---- 浏览器页
    def _build_browser_tab(self, nb):
        tab = ttk.Frame(nb, style="Card.TFrame")
        nb.add(tab, text="  浏览器接入  ")
        pad = 20

        ttk.Label(tab, text="把下载交给 netboost（可随时还原）",
                  style="CardTitle.TLabel").pack(anchor="w", padx=pad, pady=(pad, 2))
        ttk.Label(tab, text="下面每一项默认都是关闭的。开启只会做「仅当前用户」的注册，"
                            "点「还原」就能完整撤回，不会影响系统其它程序。",
                  style="CardMuted.TLabel", wraplength=740,
                  justify="left").pack(anchor="w", padx=pad, pady=(0, 12))

        reg = browser_mod.protocol_registered()
        box = tk.Frame(tab, bg=CARD)
        box.pack(fill="x", padx=pad, pady=(0, 10))
        self.proto_var = tk.BooleanVar(value=reg)

        line = tk.Frame(box, bg=CARD)
        line.pack(fill="x", padx=12, pady=10)
        tk.Checkbutton(line, text="启用 netboost:// 链接协议", variable=self.proto_var,
                       bg=CARD, fg=FG, selectcolor=CARD2, activebackground=CARD,
                       activeforeground=FG, font=F_BOLD).pack(side="left")
        tk.Label(line, text=f"当前状态：{'已注册' if reg else '未注册'}",
                 bg=CARD, fg=GREEN if reg else MUTED, font=F_SMALL).pack(side="right")

        ttk.Label(tab, text="说明：netboost:// 让网页上的链接可以直接唤起本程序下载。",
                  style="CardMuted.TLabel", wraplength=740,
                  justify="left").pack(anchor="w", padx=pad, pady=(0, 10))

        # 浏览器检测
        ttk.Label(tab, text="检测到的浏览器", style="CardTitle.TLabel").pack(
            anchor="w", padx=pad, pady=(6, 4))
        found = browser_mod.detect_browsers()
        if found:
            self.browser_list = tk.Listbox(tab, height=min(7, len(found)),
                                           bg=CARD2, fg=FG, font=F_BODY,
                                           selectbackground=ACCENT, borderwidth=0,
                                           highlightthickness=0)
            for b in found:
                self.browser_list.insert("end", f"  {b['name']}")
            self.browser_list.pack(fill="x", padx=pad, pady=(0, 6))
            ttk.Button(tab, text="查看该浏览器的接入方式",
                       command=self._browser_help).pack(anchor="w", padx=pad)
        else:
            ttk.Label(tab, text="未检测到常见浏览器", style="CardMuted.TLabel").pack(
                anchor="w", padx=pad)

    def _browser_help(self):
        found = browser_mod.detect_browsers()
        sel = self.browser_list.curselection() if hasattr(self, "browser_list") else ()
        if not sel:
            messagebox.showinfo("提示", "请先在列表里选中一个浏览器", parent=self)
            return
        b = found[sel[0]]
        messagebox.showinfo(f"{b['name']} 接入方式",
                            browser_mod.open_in_browser_help(b["short"]), parent=self)

    # ---- 关于页
    def _build_about_tab(self, nb):
        tab = ttk.Frame(nb, style="Card.TFrame")
        nb.add(tab, text="  关于  ")
        pad = 20
        ttk.Label(tab, text="netboost · 多连接分片下载器",
                  style="CardTitle.TLabel").pack(anchor="w", padx=pad, pady=(pad, 6))
        text = (
            "功能：自动识别文件类型 → 自动选文件夹 → 确认即下。\n"
            "多连接分片加速、断点续传、暂停恢复、可选限速。\n\n"
            "配置文件位置（可直接编辑或备份）：\n"
            f"{__import__('config').config_path()}\n\n"
            "隐私：本程序不收集任何信息，不上传任何数据。\n"
            "所有设置都保存在上面这个本地文件里。"
        )
        ttk.Label(tab, text=text, style="CardMuted.TLabel", justify="left",
                  wraplength=740).pack(anchor="w", padx=pad, pady=(0, 12))

    # ---- 保存
    def _save(self):
        for key, var in self.dir_vars.items():
            value = var.get().strip()
            if not value:
                messagebox.showerror("不能为空", f"「{category_label(key)}」需要一个文件夹",
                                     parent=self)
                return
            self.app.cfg["dirs"][key] = os.path.normpath(
                os.path.expanduser(os.path.expandvars(value)))

        want = bool(self.proto_var.get())
        currently = browser_mod.protocol_registered()
        if want and not currently:
            ok, msg = browser_mod.register_protocol()
            if not ok:
                messagebox.showerror("注册失败", msg, parent=self)
                return
        elif not want and currently:
            ok, msg = browser_mod.unregister_protocol()
            if not ok:
                messagebox.showerror("还原失败", msg, parent=self)
                return
        self.app.cfg["browser"]["protocol"] = want
        self.app.cfg["browser"]["last_registered"] = want

        if save_config(self.app.cfg):
            self.app.status_var.set("设置已保存")
            self.destroy()
        else:
            messagebox.showerror("保存失败", "无法写入配置文件，请检查权限", parent=self)


# --------------------------------------------------------------------------
# 主窗口
# --------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)

        try:
            base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
            ico = os.path.join(base, "app.ico")
            if os.path.exists(ico):
                self.iconbitmap(default=ico)
        except (tk.TclError, OSError):
            pass

        try:
            scale = self.winfo_fpixels("1i") / 96.0
        except tk.TclError:
            scale = 1.0
        self.tk.call("tk", "scaling", scale)

        w, h = int(1120 * min(scale, 1.25)), int(880 * min(scale, 1.25))
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        h = min(h, sh - 100)
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
        self.minsize(940, 700)
        self.configure(bg=BG)

        self.rows: dict[str, DownloadRow] = {}
        self.queue: queue.Queue = queue.Queue()
        self.threads: dict[str, threading.Thread] = {}
        self.cfg = load_config()

        self._build_style()
        self._build_ui()
        self._startup_check()
        self.after(120, self._drain_queue)
        self.after(300, self._tick)
        self._handle_cli_url()

    # ---------------------------------------------------------------- 启动

    def _startup_check(self):
        """上次若注册过协议、现在用户关掉了，就顺手清掉，保证不留垃圾。"""
        b = self.cfg.get("browser", {})
        if b.get("last_registered") and not b.get("protocol", False):
            browser_mod.unregister_protocol()
            self.cfg["browser"]["last_registered"] = False
            save_config(self.cfg)

    def _handle_cli_url(self):
        """被 netboost:// 或命令行唤起来时，自动填入链接并识别。"""
        args = [a for a in sys.argv[1:] if a.startswith(("http://", "https://", "netboost://"))]
        if not args:
            return
        url = args[0]
        if url.startswith("netboost://"):
            # netboost://https://example.com/a.mp4 或 netboost:///https://...
            url = url[len("netboost://"):].lstrip("/")
        if not url.startswith(("http://", "https://")):
            return
        self.after(300, lambda u=url: self.begin_download(u, confirm=True))

    # ---------------------------------------------------------------- 样式

    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=FG, fieldbackground=CARD2)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=FG, font=F_BODY)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=F_SMALL)
        style.configure("CardTitle.TLabel", background=CARD, foreground=FG, font=F_BIG)
        style.configure("CardMuted.TLabel", background=CARD, foreground=MUTED, font=F_SMALL)
        style.configure("Title.TLabel", background=BG, foreground=FG, font=F_TITLE)
        style.configure("Title2.TLabel", background=BG, foreground=FG, font=F_BIG)
        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                        font=("Microsoft YaHei UI", 12, "bold"), borderwidth=0,
                        padding=(20, 10))
        style.map("Accent.TButton", background=[("active", "#1f6feb"), ("disabled", "#2a3441")])
        style.configure("TButton", background=CARD2, foreground=FG, borderwidth=0,
                        padding=(14, 9), font=F_BOLD)
        style.map("TButton", background=[("active", "#30363d")])
        style.configure("Cat.TButton", background=CARD2, foreground=MUTED, borderwidth=0,
                        padding=(16, 10), font=F_BOLD)
        style.map("Cat.TButton", background=[("active", "#2d3846")])
        style.configure("CatOn.TButton", background=ACCENT, foreground="#ffffff",
                        borderwidth=0, padding=(16, 10), font=F_BOLD)
        style.map("CatOn.TButton", background=[("active", "#1f6feb")])
        style.configure("TEntry", fieldbackground=CARD2, foreground=FG, insertcolor=FG,
                        borderwidth=0, padding=(10, 8), font=F_BODY)
        style.configure("TSpinbox", fieldbackground=CARD2, foreground=FG, borderwidth=0,
                        padding=8, font=F_BODY, arrowsize=16)
        style.configure("TCheckbutton", background=BG, foreground=FG, font=F_BODY)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=FG,
                        borderwidth=0, rowheight=ROW_H, font=F_TREE)
        style.configure("Treeview.Heading", background=CARD2, foreground=MUTED,
                        borderwidth=0, font=F_TREE_HEAD, padding=(6, 8))
        style.map("Treeview", background=[("selected", "#1f6feb")],
                  foreground=[("selected", "#ffffff")])
        style.configure("TProgressbar", background=ACCENT, troughcolor=CARD2,
                        borderwidth=0, thickness=6)
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=CARD2, foreground=MUTED,
                        font=F_BODY, padding=(18, 10))
        style.map("TNotebook.Tab", background=[("selected", CARD)],
                  foreground=[("selected", FG)])
        for orient in ("Vertical", "Horizontal"):
            style.configure(f"{orient}.TScrollbar", background="#2a3441", troughcolor=CARD,
                            bordercolor=CARD, arrowcolor=MUTED, darkcolor="#2a3441",
                            lightcolor="#2a3441", borderwidth=0, width=14)
            style.map(f"{orient}.TScrollbar",
                      background=[("active", "#3d4b5c"), ("pressed", "#4a5a6d")])
            style.configure(f"{orient}.Scrollbar.trough", background=CARD, bordercolor=CARD,
                            lightcolor=CARD, darkcolor=CARD)
            style.configure(f"{orient}.Scrollbar.thumb", background="#2a3441",
                            bordercolor="#2a3441", lightcolor="#2a3441", darkcolor="#2a3441")

    # ---------------------------------------------------------------- 主界面

    def _build_ui(self):
        pad = 22
        head = ttk.Frame(self)
        head.pack(fill="x", padx=pad, pady=(pad, 8))
        ttk.Label(head, text="netboost", style="Title.TLabel").pack(side="left")
        ttk.Label(head, text="粘贴链接，自动识别类型并选好文件夹",
                  style="Muted.TLabel").pack(side="left", pady=(10, 0), padx=(10, 0))
        ttk.Button(head, text="设置", command=self.on_settings).pack(side="right")

        # 链接输入
        row1 = ttk.Frame(self)
        row1.pack(fill="x", padx=pad, pady=(6, 6))
        ttk.Label(row1, text="链接", font=F_BOLD).pack(side="left", padx=(0, 12))
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(row1, textvariable=self.url_var, font=F_ENTRY)
        self.url_entry.pack(side="left", fill="x", expand=True)
        self.url_entry.bind("<Return>", lambda e: self.on_start())
        ttk.Button(row1, text="粘贴", command=self.on_paste).pack(side="left", padx=(10, 0))
        self.btn_start = ttk.Button(row1, text="识别并下载", style="Accent.TButton",
                                    command=self.on_start)
        self.btn_start.pack(side="left", padx=(8, 0))

        # 连接数 / 限速
        row3 = ttk.Frame(self)
        row3.pack(fill="x", padx=pad, pady=(0, 12))
        ttk.Label(row3, text="连接", font=F_BOLD).pack(side="left", padx=(0, 6))
        self.conn_var = tk.IntVar(value=8)
        ttk.Spinbox(row3, from_=1, to=64, width=5, textvariable=self.conn_var,
                    font=F_BODY).pack(side="left")
        ttk.Label(row3, text="上限", font=F_BOLD).pack(side="left", padx=(14, 6))
        self.maxc_var = tk.IntVar(value=32)
        ttk.Spinbox(row3, from_=1, to=128, width=5, textvariable=self.maxc_var,
                    font=F_BODY).pack(side="left")
        self.adaptive_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row3, text="自动增压", variable=self.adaptive_var).pack(
            side="left", padx=(16, 0))
        ttk.Label(row3, text="限速", font=F_BOLD).pack(side="left", padx=(16, 6))
        self.rate_var = tk.StringVar()
        ttk.Entry(row3, textvariable=self.rate_var, width=9,
                  font=F_ENTRY).pack(side="left")
        ttk.Label(row3, text="(留空=不限)", style="Muted.TLabel").pack(side="left", padx=(8, 0))

        # 底部（先 pack，避免被列表挤掉）
        bottom = ttk.Frame(self)
        bottom.pack(side="bottom", fill="x", padx=pad, pady=(8, 10))
        ttk.Button(bottom, text="暂停/继续", command=self.on_toggle_pause).pack(side="left")
        ttk.Button(bottom, text="取消任务", command=self.on_cancel).pack(side="left", padx=(8, 0))
        ttk.Button(bottom, text="打开文件夹", command=self.on_open_selected).pack(
            side="left", padx=(8, 0))
        self.status_var = tk.StringVar(value="就绪 —— 粘贴链接后会自动识别类型")
        ttk.Label(bottom, textvariable=self.status_var, style="Muted.TLabel",
                  anchor="w").pack(side="left", fill="x", expand=True, padx=(16, 0))

        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.progress.pack(side="bottom", fill="x")

        # 任务列表
        card = ttk.Frame(self, style="Card.TFrame")
        card.pack(fill="both", expand=True, padx=pad, pady=(0, 12))
        cols = ("name", "cat", "size", "pct", "speed", "state", "eta")
        self.tree = ttk.Treeview(card, columns=cols, show="headings", selectmode="browse")
        for c, text, w, anchor in (
            ("name", "文件", 380, "w"), ("cat", "类型", 110, "center"),
            ("size", "大小", 110, "e"), ("pct", "进度", 90, "e"),
            ("speed", "速度", 120, "e"), ("state", "状态", 130, "center"),
            ("eta", "剩余", 100, "center"),
        ):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, anchor=anchor, stretch=(c == "name"))
        self.tree.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        sb = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview,
                           style="Vertical.TScrollbar")
        sb.pack(side="right", fill="y", pady=8, padx=(0, 8))
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", self.on_open_selected)

    # ---------------------------------------------------------------- 交互

    def on_settings(self):
        SettingsDialog(self)

    def on_paste(self):
        try:
            text = self.clipboard_get().strip()
        except tk.TclError:
            return
        if text:
            self.url_var.set(text.splitlines()[0].strip())

    def on_start(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showinfo("提示", "请先填入下载链接")
            return
        if not url.lower().startswith(("http://", "https://")):
            messagebox.showerror("链接无效", "目前只支持 http:// 或 https:// 链接")
            return
        self.begin_download(url, confirm=True)

    def begin_download(self, url: str, confirm: bool = True):
        """探测 → 识别类型 → 自动选目录 → 弹确认框 → 下载。"""
        self.status_var.set(f"正在识别 {url[:70]}…")
        self.btn_start.configure(state="disabled")

        def worker():
            d = Downloader(url, out_dir=os.path.expanduser("~"), connections=2,
                           max_connections=2, adaptive=False, retries=3, timeout=20)
            try:
                d.probe()
                det = detect(url, d.filename or "", d.content_type or "")
                payload = {
                    "dl": d,
                    "detection": det,
                    "total": d.total,
                    "out_dir": category_dir(self.cfg, det.category),
                }
                self.queue.put(("probed", payload))
            except DownloadError as e:
                self.queue.put(("error", (None, str(e))))
            except Exception as e:  # noqa: BLE001
                self.queue.put(("error", (None, f"识别失败：{e!r}")))

        threading.Thread(target=worker, daemon=True).start()

    def _after_probe(self, payload: dict):
        self.btn_start.configure(state="normal")
        url = payload["dl"].url
        det = payload["detection"]
        self.status_var.set(f"识别为「{det.label}」——{det.reason}")

        if not self.cfg.get("ui", {}).get("confirm_before_download", True):
            self._start_download(url, det.category, payload["out_dir"], [])
            return

        dlg = ConfirmDialog(self, url, payload)
        self.wait_window(dlg)
        self.url_var.set("")
        if dlg.result:
            self._start_download(url, dlg.result["category"],
                                 dlg.result["out_dir"], dlg.result["subtitles"])

    def _parse_rate(self):
        text = self.rate_var.get().strip()
        if not text:
            return None
        mult = 1.0
        v = text.lower().strip()
        for suffix, m in (("gb/s", 1024 ** 3), ("mb/s", 1024 ** 2), ("kb/s", 1024),
                          ("gb", 1024 ** 3), ("mb", 1024 ** 2), ("kb", 1024),
                          ("g", 1024 ** 3), ("m", 1024 ** 2), ("k", 1024), ("b", 1)):
            if v.endswith(suffix):
                v = v[: -len(suffix)].strip()
                mult = m
                break
        try:
            value = float(v) * mult
        except ValueError:
            messagebox.showerror("限速格式错误", "请填类似 5M、800K 的值，或留空表示不限速")
            return False
        return value if value > 0 else None

    def _start_download(self, url: str, category: str, out_dir: str,
                        subtitle_inputs: list[str]):
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            messagebox.showerror("目录不可用", f"无法创建保存目录：{e}")
            return
        rate_limit = self._parse_rate()
        if rate_limit is False:
            return

        dl = Downloader(
            url, out_dir=out_dir,
            connections=max(1, int(self.conn_var.get())),
            max_connections=max(1, int(self.maxc_var.get())),
            adaptive=bool(self.adaptive_var.get()),
            rate_limit=rate_limit or None,
            log=lambda m: self.queue.put(("log", m)),
        )
        row = DownloadRow(dl, self.tree, category)
        row.sub_urls = [s for s in subtitle_inputs if s.strip()]
        self.rows[str(row.item)] = row
        self.status_var.set(f"开始下载：{url[:70]}")
        t = threading.Thread(target=self._run_download, args=(row,), daemon=True)
        self.threads[str(row.item)] = t
        t.start()

    def _run_download(self, row: DownloadRow):
        dl = row.dl
        try:
            dl.probe()
        except DownloadError as e:
            self.queue.put(("error", (row, str(e))))
            return
        if not dl.supports_ranges:
            self.queue.put(("log", f"{dl.filename}：服务器不支持分段，只能单连接"))
        try:
            res = dl.run()
        except DownloadError as e:
            self.queue.put(("error", (row, str(e))))
            return
        except Exception as e:  # noqa: BLE001
            self.queue.put(("error", (row, f"意外错误：{e!r}")))
            return

        if row.sub_urls and not res.partial:
            video_name = os.path.basename(res.path)
            movie_dir = os.path.dirname(res.path)
            for item in row.sub_urls:
                try:
                    cleaned = item.strip().strip('"')
                    if not cleaned or is_subtitle(video_name):
                        continue
                    if cleaned.lower().startswith(("http://", "https://")):
                        path = download_subtitle(cleaned, video_name, movie_dir,
                                                 log=self._sub_log(row))
                    elif os.path.isfile(cleaned):
                        path = import_local_subtitle(cleaned, video_name, movie_dir,
                                                     log=self._sub_log(row))
                    else:
                        self.queue.put(("log", f"字幕无效：{cleaned}"))
                        continue
                    row.sub_results.append(path)
                except (DownloadError, OSError) as e:
                    self.queue.put(("log", f"字幕处理失败：{e}"))

        self.queue.put(("done", (row, res)))

    def _sub_log(self, row):
        return lambda m: self.queue.put(("log", f"{row.dl.filename}: {m}"))

    def _selected_row(self) -> DownloadRow | None:
        sel = self.tree.selection()
        return self.rows.get(sel[0]) if sel else None

    def on_toggle_pause(self):
        row = self._selected_row()
        if not row:
            self.status_var.set("请先在列表里选中一个任务")
            return
        if row.dl.paused:
            row.dl.resume_paused()
            self.status_var.set(f"继续：{row.dl.filename}")
        else:
            row.dl.pause()
            self.status_var.set(f"暂停：{row.dl.filename}")

    def on_cancel(self):
        row = self._selected_row()
        if not row:
            self.status_var.set("请先在列表里选中一个任务")
            return
        row.dl.stop()
        self.status_var.set(f"已取消（进度保留，可续传）：{row.dl.filename}")

    def on_open_selected(self, event=None):
        row = self._selected_row()
        if not row:
            self.status_var.set("请先在列表里选中一个任务")
            return
        target = row.dl.path
        if not target or not os.path.exists(target):
            target = row.dl.out_dir
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(target)])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(target)])
        except OSError:
            pass

    # ---------------------------------------------------------------- 轮询

    def _drain_queue(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self.status_var.set(payload)
                elif kind == "probed":
                    self._after_probe(payload)
                elif kind == "error":
                    row, msg = payload
                    self.btn_start.configure(state="normal")
                    if row is None:
                        self.status_var.set(f"✗ {msg}")
                    else:
                        self.status_var.set(f"✗ {row.dl.filename or row.dl.url}: {msg}")
                elif kind == "done":
                    row, res = payload
                    name = os.path.basename(res.path)
                    if res.partial:
                        self.status_var.set(f"未完成（可续传）：{name}")
                    else:
                        extra = f" · 字幕 {len(row.sub_results)} 个" if row.sub_results else ""
                        self.status_var.set(
                            f"完成 {name} · 平均 {format_speed(res.avg_speed)} · "
                            f"峰值连接 {res.peak_connections}{extra}")
        except queue.Empty:
            pass
        self.after(120, self._drain_queue)

    def _tick(self):
        active = 0
        total_bytes = 0
        total_done = 0
        total_speed = 0.0
        for row in self.rows.values():
            row.refresh()
            dl = row.dl
            if dl.total:
                total_bytes += dl.total
                total_done += dl.downloaded()
            if not dl.paused and not dl.failed and dl.total and dl.downloaded() < dl.total:
                active += 1
                total_speed += dl.speed
        if total_bytes:
            self.progress["value"] = total_done / total_bytes * 100.0
        elif not self.rows:
            self.progress["value"] = 0
        if self.rows:
            self.title(f"{APP_TITLE}  ·  {active} 个任务下载中  ·  合计 {format_speed(total_speed)}")
        self.after(300, self._tick)


def main():
    try:
        app = App()
    except tk.TclError as e:
        print(f"无法启动图形界面：{e}", file=sys.stderr)
        return 1
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
