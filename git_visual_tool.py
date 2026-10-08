#!/usr/bin/env python3
# -*- coding: utf-8-sig -*-
"""
Git 可视化工具（桌面版，适配 Windows，跨平台可用）
====================================================================
把常用 Git 功能封装为按钮，点击即执行，并且：

  1. 命令预览 —— 每次点击按钮，都显示实际执行的 git 命令；
  2. 终端区   —— 实时回显命令、执行过程与结果（带颜色区分）；
  3. 进度条   —— 网络操作（克隆/拉取/推送）显示百分比，本地操作显示动画；
  4. 日志     —— 全部过程自动写入日志文件 git_tool.log，可在"历史日志"页查看；
  5. 使用说明 —— 内置"使用说明"页，说明每个功能的作用与使用时机。

运行方式：
    python git_visual_tool.py
环境要求：
    Python 3.6+（自带 tkinter，无需安装第三方库）+ 已安装 Git
"""

import datetime
import json
import os
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_NAME = "Git 可视化工具"
APP_VERSION = "v1.3"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(APP_DIR, "git_tool.log")
CONFIG_FILE = os.path.join(APP_DIR, "git_tool_config.json")

# Windows 下隐藏子进程的黑控制台窗口
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# 从 git 进度输出中解析百分比（如 "Receiving objects:  45% (12/26)"）
PROGRESS_RE = re.compile(
    rb"(Counting|Compressing|Writing|Receiving|Resolving|Enumerating|Updating)"
    rb"[^%\r\n]{0,120}?(\d{1,3})%", re.IGNORECASE)


# ----------------------------------------------------------------------
# 工具函数（与界面无关，可独立测试）
# ----------------------------------------------------------------------
def decode_bytes(b):
    """按 utf-8 → gbk 顺序解码 git 输出字节，避免中文乱码。"""
    for enc in ("utf-8", "gbk"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


# URL 中的账号密码（如 https://user:token@host/x），显示时打码
CRED_RE = re.compile(r"(?<=//)[^/@\s]+:[^/@\s]+(?=@)")


def mask_secrets(text):
    """把文本里 URL 的账号密码部分打码。只用于显示，不影响真实执行。"""
    return CRED_RE.sub("***", text)


def display_cmd(args):
    """把参数列表转为便于展示的命令行字符串（带引号处理）。

    命令里若带有含凭据的 URL（如 https://user:token@...），
    显示时打码，避免明文进入命令预览、终端和日志。
    """
    try:
        return mask_secrets(subprocess.list2cmdline(args))
    except Exception:
        return mask_secrets(" ".join(args))


def build_command(template, values=None):
    """
    将命令模板中的 {key} 占位符替换为用户输入。
    值为空的占位参数（可选参数）会被整段去掉。
    """
    values = values or {}
    cmd = []
    for part in template:
        for key, val in values.items():
            part = part.replace("{" + key + "}", val)
        cmd.append(part)
    # 去掉值为空的可选参数（必填项已由对话框保证非空）
    return [p for p in cmd if p.strip() != ""]


def norm_path(p):
    """规整用户输入的仓库路径。

    Windows 资源管理器"复制文件地址"得到的是带英文引号的路径
    （如 "C:\\my project"），直接 isdir 会失败，这里去掉首尾引号。
    """
    return (p or "").strip().strip('"').strip("'").strip()


# ----------------------------------------------------------------------
# 命令执行器：后台线程运行 git，事件经队列回传（线程安全）
# 事件类型：
#   ("start", args, cwd)      开始执行
#   ("note", text)            功能说明
#   ("cmd",  display)         回显命令
#   ("out",  text)            标准输出
#   ("err",  text)            标准错误
#   ("progress", pct)         进度百分比
#   ("done", code, secs, killed)  执行结束
# ----------------------------------------------------------------------
class GitExecutor:

    def __init__(self):
        self.events = queue.Queue()
        self.running = False
        self.proc = None
        self.killed = False
        self._stop_requested = False

    def run(self, cwd, args, note=None):
        """启动后台线程执行命令；返回 False 表示已有命令在执行。"""
        if self.running:
            return False
        self.running = True
        self.killed = False
        self._stop_requested = False
        threading.Thread(target=self._worker, args=(cwd, args, note),
                         daemon=True).start()
        return True

    def stop(self):
        """终止当前正在执行的命令。"""
        self._stop_requested = True
        p = self.proc
        if p is not None and p.poll() is None:
            try:
                p.kill()
                self.killed = True
            except OSError:
                pass

    # ---- 内部实现 ----------------------------------------------------
    def _worker(self, cwd, args, note):
        put = self.events.put
        t0 = time.time()
        put(("start", args, cwd))
        if note:
            put(("note", note))
        put(("cmd", display_cmd(args)))
        # 禁止 git 在终端等待用户输入账号密码（避免卡死）
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        try:
            self.proc = subprocess.Popen(
                args, cwd=cwd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, env=env,
                creationflags=CREATE_NO_WINDOW)
        except FileNotFoundError:
            put(("err", "找不到 git 命令：请先安装 Git 并加入 PATH 后重试"
                        "（https://git-scm.com/download/win）"))
            put(("done", -1, time.time() - t0, False))
            self.running = False
            return
        except Exception as e:
            put(("err", "启动命令失败：%s" % e))
            put(("done", -1, time.time() - t0, False))
            self.running = False
            return
        if self._stop_requested:
            # 停止请求发生在 Popen 完成之前（start 后立刻点终止/关窗）：补杀
            self.killed = True
            try:
                self.proc.kill()
            except OSError:
                pass

        pumps = [threading.Thread(target=self._pump,
                                  args=(self.proc.stdout, "out"), daemon=True),
                 threading.Thread(target=self._pump,
                                  args=(self.proc.stderr, "err"), daemon=True)]
        for t in pumps:
            t.start()
        for t in pumps:
            t.join()
        code = self.proc.wait()
        put(("done", code, time.time() - t0, self.killed))
        self.running = False

    def _pump(self, stream, kind):
        """逐块读取输出流，按 \\n / \\r / \\r\\n 切分为行后分发。"""
        buf = b""
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            buf += chunk
            while True:
                i_n = buf.find(b"\n")
                i_r = buf.find(b"\r")
                if i_n < 0 and i_r < 0:
                    break
                if i_r < 0 or (0 <= i_n < i_r):
                    line, buf = buf[:i_n], buf[i_n + 1:]
                else:
                    if i_r == len(buf) - 1:
                        break  # \r 在末尾，等下一块判断是否为 \r\n
                    if buf[i_r + 1:i_r + 2] == b"\n":
                        line, buf = buf[:i_r], buf[i_r + 2:]
                    else:
                        line, buf = buf[:i_r], buf[i_r + 1:]
                self._emit(kind, line)
        if buf:
            self._emit(kind, buf)

    def _emit(self, kind, raw):
        """输出一行：若是 git 进度行则只更新进度，不打印到终端。"""
        if not raw.strip():
            return
        m = PROGRESS_RE.search(raw)
        if m:
            try:
                pct = max(0, min(100, int(m.group(2))))
                self.events.put(("progress", pct))
                return
            except ValueError:
                pass
        self.events.put((kind, decode_bytes(raw).rstrip("\r\n")))


# ----------------------------------------------------------------------
# 悬停提示
# ----------------------------------------------------------------------
class Tooltip:
    """鼠标悬停在按钮上时显示功能说明与对应命令。"""

    def __init__(self, widget, text, delay=450):
        self.widget = widget
        self.text = text
        self.tip = None
        self._id = None
        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._hide)
        widget.bind("<ButtonPress>", self._hide)

    def _schedule(self, _=None):
        self._hide()
        self._id = self.widget.after(450, self._show)

    def _show(self):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry("+%d+%d" % (x, y))
        tk.Label(tw, text=self.text, justify="left",
                 background="#ffffe6", relief="solid", borderwidth=1,
                 font=("Microsoft YaHei UI", 9)).pack(ipadx=6, ipady=3)

    def _hide(self, _=None):
        if self._id is not None:
            try:
                self.widget.after_cancel(self._id)
            except Exception:
                pass
            self._id = None
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


# ----------------------------------------------------------------------
# 参数输入对话框
# fields: [(显示标签, 键名, 默认值, 是否必填, 是否多行), ...]
# ----------------------------------------------------------------------
class ParamDialog(tk.Toplevel):

    def __init__(self, parent, title, fields):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.values = None

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        self._fields = {}
        for i, (label, key, default, required, multiline) in enumerate(fields):
            req_text = "（必填）" if required else "（可选，留空则忽略）"
            ttk.Label(body, text=label + req_text).grid(
                row=i, column=0, sticky="nw", pady=5, padx=(0, 10))
            if multiline:
                w = tk.Text(body, width=52, height=4, wrap="word")
                if default:
                    w.insert("1.0", default)
                self._fields[key] = ("text", w, required, label)
            else:
                var = tk.StringVar(value=default)
                w = ttk.Entry(body, textvariable=var, width=54)
                self._fields[key] = ("entry", var, required, label)
            w.grid(row=i, column=1, sticky="we", pady=5)
            if i == 0:
                w.focus_set()

        btns = ttk.Frame(body)
        btns.grid(row=len(fields), column=0, columnspan=2,
                  sticky="e", pady=(14, 0))
        ttk.Button(btns, text="取消", command=self.destroy).pack(
            side="right", padx=(8, 0))
        ttk.Button(btns, text="确定", command=self._on_ok).pack(side="right")

        self.bind("<Return>", self._on_enter)
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        self.lift()

    def _on_enter(self, _):
        # 多行输入框内回车应换行，不触发确定
        if isinstance(self.focus_get(), tk.Text):
            return
        self._on_ok()

    def _on_ok(self):
        vals = {}
        for key, (kind, obj, required, label) in self._fields.items():
            if kind == "text":
                v = obj.get("1.0", "end").strip()
            else:
                v = obj.get().strip()
            if required and not v:
                messagebox.showwarning("提示", "请填写：%s" % label, parent=self)
                return
            vals[key] = v
        self.values = vals
        self.destroy()

    @staticmethod
    def ask(parent, title, fields):
        dlg = ParamDialog(parent, title, fields)
        parent.wait_window(dlg)
        return dlg.values


# ----------------------------------------------------------------------
# 主界面
# ----------------------------------------------------------------------
class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("%s %s" % (APP_NAME, APP_VERSION))
        self.geometry("1200x760")
        self.minsize(980, 640)

        self.executor = GitExecutor()
        self._action_buttons = []   # 所有功能按钮（执行中禁用）
        self._clone_target = None   # 克隆完成后建议切换的目录
        self._cmd_queue = []        # 需要连续执行的多条命令（如配置身份）
        self._recent_err = []       # 本次命令的错误输出（用于识别失败原因）
        self._is_repo = False
        self._git_ok = False
        self._run_cwd = ""

        self._build_ui()
        self._load_config()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(40, self._poll_events)
        self.after(80, self._startup_check)

    # ============================== 界面 ==============================
    def _build_ui(self):
        # ---- 顶栏：仓库路径 ----
        top = ttk.Frame(self, padding=(10, 8, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="仓库路径:").pack(side="left")
        self.repo_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.repo_var, font=("Microsoft YaHei UI", 10))
        entry.pack(side="left", fill="x", expand=True, padx=6)
        entry.bind("<Return>", lambda e: self._refresh_status())
        entry.bind("<FocusOut>", lambda e: self._refresh_status())
        ttk.Button(top, text="浏览…", command=self._browse).pack(side="left", padx=2)
        ttk.Button(top, text="刷新状态", command=self._refresh_status).pack(side="left", padx=2)
        self.gitver_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.gitver_var, foreground="#777").pack(side="right")

        # ---- 底部状态栏 ----
        bar = ttk.Frame(self, padding=(10, 2))
        bar.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="仓库：未选择")
        ttk.Label(bar, textvariable=self.status_var, foreground="#333").pack(side="left")
        self.last_var = tk.StringVar(value="就绪")
        ttk.Label(bar, textvariable=self.last_var, foreground="#777").pack(side="right")

        # ---- 三个页签 ----
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(2, 4))
        tab_op = ttk.Frame(self.nb)
        tab_log = ttk.Frame(self.nb)
        tab_help = ttk.Frame(self.nb)
        self.nb.add(tab_op, text="  操作  ")
        self.nb.add(tab_log, text="  历史日志  ")
        self.nb.add(tab_help, text="  使用说明  ")
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._build_tab_op(tab_op)
        self._build_tab_log(tab_log)
        self._build_tab_help(tab_help)

    # ---- 操作页：左侧按钮区 + 右侧终端区 ----
    def _build_tab_op(self, parent):
        main = ttk.Frame(parent)
        main.pack(fill="both", expand=True)

        # 左侧：可滚动的功能按钮面板
        side = ttk.Frame(main, width=320)
        side.pack(side="left", fill="y", padx=(0, 8))
        side.pack_propagate(False)
        self._build_sidebar(side)

        # 右侧：命令预览 / 进度条 / 终端 / 自定义命令
        right = ttk.Frame(main)
        right.pack(side="left", fill="both", expand=True)

        row1 = ttk.Frame(right)
        row1.pack(fill="x", pady=(0, 4))
        ttk.Label(row1, text="命令预览:").pack(side="left")
        self.preview_var = tk.StringVar(value="（点击左侧按钮后，这里显示将要执行的 git 命令）")
        pe = ttk.Entry(row1, textvariable=self.preview_var, state="readonly",
                       font=("Consolas", 10))
        pe.pack(side="left", fill="x", expand=True, padx=6)
        self.btn_stop = ttk.Button(row1, text="终止", command=self._stop,
                                   state="disabled", width=6)
        self.btn_stop.pack(side="left")

        row2 = ttk.Frame(right)
        row2.pack(fill="x", pady=(0, 4))
        self.progress = ttk.Progressbar(row2, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True)
        self.prog_label_var = tk.StringVar(value="就绪")
        ttk.Label(row2, textvariable=self.prog_label_var, width=10,
                  anchor="e").pack(side="left", padx=(6, 0))

        # 终端区（深色背景、等宽字体、右键菜单）
        conf = ttk.Frame(right)
        conf.pack(fill="both", expand=True)
        self.con = tk.Text(conf, wrap="none", state="disabled",
                           background="#1e1e1e", foreground="#d4d4d4",
                           insertbackground="#d4d4d4",
                           selectbackground="#264f78",
                           font=("Consolas", 10), relief="flat",
                           padx=8, pady=6)
        ysb = ttk.Scrollbar(conf, orient="vertical", command=self.con.yview)
        xsb = ttk.Scrollbar(conf, orient="horizontal", command=self.con.xview)
        self.con.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        ysb.pack(side="right", fill="y")
        xsb.pack(side="bottom", fill="x")
        self.con.pack(side="left", fill="both", expand=True)
        for tag, color in (("cmd", "#dcdcaa"), ("note", "#569cd6"),
                           ("out", "#d4d4d4"), ("err", "#f48771"),
                           ("ok", "#73c991"), ("warn", "#d7ba7d"),
                           ("info", "#9cdcfe")):
            self.con.tag_configure(tag, foreground=color)
        menu = tk.Menu(self.con, tearoff=0)
        menu.add_command(label="复制", command=self._copy_sel)
        menu.add_command(label="全选", command=lambda: self.con.tag_add("sel", "1.0", "end"))
        menu.add_command(label="清屏", command=self._clear_console)
        self.con.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

        # 自定义命令输入行
        row3 = ttk.Frame(right)
        row3.pack(fill="x", pady=(6, 0))
        tk.Label(row3, text="git >", bg="#1e1e1e", fg="#73c991",
                 font=("Consolas", 10), padx=6).pack(side="left")
        self.custom_var = tk.StringVar()
        self.custom_entry = tk.Entry(row3, textvariable=self.custom_var,
                                     bg="#1e1e1e", fg="#d4d4d4",
                                     insertbackground="#d4d4d4",
                                     relief="flat", font=("Consolas", 10))
        self.custom_entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.custom_entry.bind("<Return>", self._run_custom)
        self.btn_run_custom = ttk.Button(row3, text="运行", width=6,
                                         command=self._run_custom)
        self.btn_run_custom.pack(side="left", padx=(6, 0))

    def _build_sidebar(self, parent):
        canvas = tk.Canvas(parent, highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # 鼠标滚轮（仅当指针位于按钮面板上时滚动）
        def _wheel(ev):
            try:
                w = self.winfo_containing(ev.x_root, ev.y_root)
                inside = False
                while w is not None:
                    if w is canvas or w is inner or w is parent:
                        inside = True
                        break
                    w = getattr(w, "master", None)
                if inside:
                    step = ev.delta // 120 if abs(ev.delta) >= 120 else (1 if ev.delta > 0 else -1)
                    canvas.yview_scroll(-step, "units")
            except Exception:
                pass

        canvas.bind_all("<MouseWheel>", _wheel)
        canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
        canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))

        for cat, acts in self._build_actions():
            lf = ttk.LabelFrame(inner, text=cat, padding=(8, 6))
            lf.pack(fill="x", padx=4, pady=5)
            lf.columnconfigure(0, weight=1)
            lf.columnconfigure(1, weight=1)
            for idx, a in enumerate(acts):
                b = ttk.Button(lf, text=a["label"],
                               command=lambda a=a: self._do_action(a))
                b.grid(row=idx // 2, column=idx % 2, sticky="we", padx=3, pady=2)
                Tooltip(b, "%s\n命令：%s" % (a["what"], a["hint"]))
                self._action_buttons.append(b)

    # ---- 历史日志页 ----
    def _build_tab_log(self, parent):
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(0, 4))
        ttk.Button(bar, text="刷新", command=self._refresh_log_tab).pack(side="left")
        ttk.Button(bar, text="打开日志文件", command=self._open_log).pack(side="left", padx=4)
        ttk.Button(bar, text="清空日志", command=self._clear_log).pack(side="left")
        self.loginfo_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.loginfo_var,
                  foreground="#777").pack(side="right")

        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(frame, wrap="none", state="disabled",
                                font=("Consolas", 10))
        ysb = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
        xsb = ttk.Scrollbar(frame, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        ysb.pack(side="right", fill="y")
        xsb.pack(side="bottom", fill="x")
        self.log_text.pack(fill="both", expand=True)

    # ---- 使用说明页 ----
    def _build_tab_help(self, parent):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, wrap="word", state="disabled",
                       font=("Microsoft YaHei UI", 10), padx=12, pady=10)
        ysb = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=ysb.set)
        ysb.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.tag_configure("h1", font=("Microsoft YaHei UI", 15, "bold"),
                           foreground="#1a1a1a", spacing1=10, spacing3=6)
        text.tag_configure("h2", font=("Microsoft YaHei UI", 12, "bold"),
                           foreground="#0b5cad", spacing1=12, spacing3=4)
        text.tag_configure("b", font=("Microsoft YaHei UI", 10, "bold"))
        text.tag_configure("cmd", font=("Consolas", 10), foreground="#8b1a1a",
                           lmargin1=28, lmargin2=28)
        text.tag_configure("p", lmargin1=28, lmargin2=28, spacing3=2)
        text.tag_configure("warn", foreground="#c03000",
                           lmargin1=28, lmargin2=28)
        text.tag_configure("plain", spacing3=2)

        items = self._build_help_items()
        text.configure(state="normal")
        for tag, line in items:
            text.insert("end", line + "\n", tag)
        text.configure(state="disabled")

    # ============================== 功能定义 ==============================
    def _build_actions(self):
        """返回 [(分类, [动作定义…]), …]。动作定义中的键：
        label 按钮文字 / hint 展示命令 / what 作用 / when 何时使用 /
        warn 危险提示(自动弹确认框) / cmd 命令模板 / params 参数对话框 /
        need_repo 是否要求当前是 git 仓库 / handler 特殊处理函数
        """
        def A(label, cmd, hint, what, when=None, warn=None,
              params=None, need_repo=True, handler=None):
            return dict(label=label, cmd=cmd, hint=hint, what=what, when=when,
                        warn=warn, params=params, need_repo=need_repo,
                        handler=handler)

        return [
            ("仓库操作", [
                A("初始化仓库", ["git", "init"], "git init",
                  "在所选文件夹创建一个空的 Git 仓库",
                  "项目第一次纳入版本控制、想让普通文件夹开始被 Git 管理时",
                  need_repo=False),
                A("克隆仓库", None, "git clone <地址> [目录]",
                  "从远程地址（GitHub / Gitee / 公司服务器）下载完整仓库到本地",
                  "想获取别人已有的项目，或把远程仓库下载到新电脑时",
                  need_repo=False,
                  params=[("仓库地址（URL）", "url", "", True, False),
                          ("克隆到子目录", "dir", "", False, False)],
                  handler=self._act_clone),
                A("添加远程", ["git", "remote", "add", "{name}", "{url}"],
                  "git remote add <名称> <地址>",
                  "为本地仓库关联一个远程仓库地址",
                  "init 新建仓库后，需要连接到 GitHub 等远程时",
                  params=[("远程名称", "name", "origin", True, False),
                          ("远程地址（URL）", "url", "", True, False)]),
                A("查看远程", ["git", "remote", "-v"], "git remote -v",
                  "查看已关联的远程仓库名称和地址",
                  "确认远程地址是否正确、推送前检查关联关系时"),
                A("配置身份", None,
                  "git config --global user.name / user.email",
                  "设置提交者姓名和邮箱。Git 要求每次提交都记录作者，"
                  "首次提交前必须配置，否则提交必定失败",
                  "第一次使用 Git 时必须先做一次；"
                  "提交时若提示 Author identity unknown 也是因为缺这步",
                  need_repo=False,
                  params=[("姓名（如 Zhang San）", "name", "", True, False),
                          ("邮箱（如 you@example.com）", "email", "", True, False)],
                  handler=self._act_identity),
            ]),
            ("查看", [
                A("查看状态", ["git", "status"], "git status",
                  "查看哪些文件被修改、已暂存、未跟踪（Git 中最常用的命令）",
                  "任何操作前后都建议先看一下，做到心中有数"),
                A("查看改动", ["git", "diff"], "git diff",
                  "查看工作区文件相对暂存区/上次提交的具体改动内容",
                  "提交前确认自己到底改了什么"),
                A("已暂存改动", ["git", "diff", "--staged"], "git diff --staged",
                  "查看已 add 进暂存区、即将随下次提交生效的改动",
                  "点击提交之前，最后确认要提交的内容"),
                A("提交历史", ["git", "log", "--graph", "--oneline",
                              "--decorate", "--all", "-n", "50"],
                  "git log --graph --oneline -n 50",
                  "以图形方式查看最近 50 次提交（含分支合并轨迹）",
                  "回顾改动历史、查找某次提交、回退前确认目标位置"),
                A("提交详情", ["git", "show", "{commit}"], "git show <提交号>",
                  "查看某次提交改了哪些文件和具体内容",
                  "想细看历史中某次提交时；HEAD 表示最近一次提交",
                  params=[("提交号", "commit", "HEAD", True, False)]),
            ]),
            ("暂存与提交", [
                A("暂存全部", ["git", "add", "-A"], "git add -A",
                  "把所有修改、新增、删除的文件放入暂存区",
                  "确认所有改动都要提交时（最常用）"),
                A("暂存文件", ["git", "add", "--", "{paths}"], "git add <路径>",
                  "只把指定文件放入暂存区",
                  "一次提交只想包含部分文件时；路径填 . 表示当前目录全部",
                  params=[("文件/目录路径", "paths", ".", True, False)]),
                A("提交", ["git", "commit", "-m", "{message}"],
                  'git commit -m "提交说明"',
                  "把暂存区内容保存为一个版本（存档点）",
                  "一批修改完成并暂存后；建议每个小功能提交一次，说明写清楚改了什么",
                  params=[("提交说明", "message", "", True, True)]),
                A("修改上次提交", ["git", "commit", "--amend", "--no-edit"],
                  "git commit --amend --no-edit",
                  "把当前已暂存的改动并入最近一次提交（不产生新提交）",
                  "刚提交完发现漏了文件，且还没有推送到远程时",
                  warn="此操作会改写最近一次提交的历史。如果该提交已经推送，"
                       "之后再推送可能需要强制推送，会影响协作者，请谨慎。"),
            ]),
            ("分支", [
                A("查看分支", ["git", "branch", "-a", "-v"], "git branch -a -v",
                  "查看本地/远程全部分支及各自最新提交",
                  "不确定有哪些分支、想确认当前所在分支时"),
                A("新建分支", ["git", "branch", "{name}"], "git branch <分支名>",
                  "创建一个新分支（不切换过去）",
                  "准备开发新功能，想保留现场时",
                  params=[("新分支名", "name", "", True, False)]),
                A("新建并切换", ["git", "checkout", "-b", "{name}"],
                  "git checkout -b <分支名>",
                  "创建新分支并立即切换到它",
                  "开发新功能、修 bug 时最常用的起点",
                  params=[("新分支名", "name", "", True, False)]),
                A("切换分支", ["git", "checkout", "{name}"], "git checkout <分支名>",
                  "切换到另一个分支",
                  "在功能分支与主线之间来回切换时；切换前建议先提交或储藏当前改动",
                  params=[("目标分支名", "name", "", True, False)]),
                A("合并分支", ["git", "merge", "{branch}"], "git merge <分支名>",
                  "把指定分支的成果合并到当前分支",
                  "功能开发完成要合回主线时；注意先切换到目标分支再执行合并",
                  params=[("要合并进来的分支", "branch", "", True, False)]),
                A("删除分支", ["git", "branch", "-d", "{name}"],
                  "git branch -d <分支名>",
                  "删除已合并的本地分支（未合并的会拒绝删除，属安全保护）",
                  "分支任务完成并合并后做清理；确认无用再删",
                  params=[("要删除的分支名", "name", "", True, False)]),
            ]),
            ("远程同步", [
                A("拉取 fetch", ["git", "fetch", "--all", "--progress"],
                  "git fetch --all",
                  "下载远程最新提交到本地，但不合并进本地分支",
                  "想先看看远程有什么更新，再决定是否合并时"),
                A("拉取合并 pull", ["git", "pull", "--progress"], "git pull",
                  "下载远程更新并自动合并到当前分支",
                  "每天开始工作、以及推送之前，先同步远程最新代码"),
                A("推送 push", ["git", "push", "--progress"], "git push",
                  "把本地提交上传到远程仓库",
                  "提交完成、想让别人（或服务器）看到你的成果时"),
                A("推送并关联", ["git", "push", "-u", "origin", "HEAD", "--progress"],
                  "git push -u origin HEAD",
                  "推送并把当前分支与远程分支建立跟踪关系",
                  "新建分支后的第一次推送；之后直接用【推送】即可",
                  need_repo=True),
                A("强制推送", ["git", "push", "--force-with-lease", "--progress"],
                  "git push --force-with-lease",
                  "用本地历史覆盖远程分支（带回滚保护：远程有他人新提交时会拒绝）",
                  "回退/改写了已推送的历史后必须使用；请先【拉取 fetch】确认远程状态",
                  warn="会用本地历史覆盖远程分支，可能丢失远程上的新提交，"
                       "并影响正在协作的同事。仅在明确知道后果时使用。"),
            ]),
            ("撤销与回退", [
                A("撤销修改", ["git", "restore", "--", "{paths}"],
                  "git restore <路径>",
                  "丢弃工作区中未暂存的修改，把文件恢复成暂存区/上次提交的样子",
                  "改砸了、想放弃当前未提交的修改时；路径填 . 表示全部",
                  warn="未提交的修改被撤销后无法恢复，请确认改动确实不需要了。",
                  params=[("文件/目录路径", "paths", ".", True, False)]),
                A("取消暂存", ["git", "restore", "--staged", "--", "{paths}"],
                  "git restore --staged <路径>",
                  "把文件移出暂存区（不影响文件内容本身）",
                  "add 错了文件、想重新挑选本次提交内容时",
                  params=[("文件/目录路径", "paths", ".", True, False)]),
                A("回退(保留修改)", ["git", "reset", "{commit}"],
                  "git reset <提交号>",
                  "回退到指定提交，文件修改保留在工作区（默认混合模式）",
                  "想撤销提交、重新组织提交内容时",
                  params=[("目标提交（如 HEAD~1 或提交号）", "commit", "HEAD", True, False)]),
                A("回退(保留暂存)", ["git", "reset", "--soft", "{commit}"],
                  "git reset --soft <提交号>",
                  "回退到指定提交，所有修改保留在暂存区",
                  "想把最近几次提交合并成一次提交时",
                  params=[("目标提交（如 HEAD~2 或提交号）", "commit", "HEAD", True, False)]),
                A("回退(彻底丢弃)", ["git", "reset", "--hard", "{commit}"],
                  "git reset --hard <提交号>",
                  "彻底回退：文件内容、暂存区、提交历史一起回到指定提交",
                  "确定某个版本之后的方向全部错误时；最彻底也最危险",
                  warn="将丢弃目标提交之后的所有提交和未提交修改，且无法恢复。"
                       "请先用【提交历史】确认要回退到的位置。",
                  params=[("目标提交（如 HEAD~1 或提交号）", "commit", "HEAD", True, False)]),
                A("反做提交", ["git", "revert", "--no-edit", "{commit}"],
                  "git revert <提交号>",
                  "生成一个新提交来抵消指定提交的改动（不改写历史，安全）",
                  "想撤销某次已推送的提交时——团队协作中的首选方式",
                  params=[("要撤销的提交号", "commit", "HEAD", True, False)]),
                A("清理未跟踪", ["git", "clean", "-fd"], "git clean -fd",
                  "删除所有未跟踪的文件和目录（不在 Git 管理中的新文件）",
                  "想让工作区完全干净时；编译产物建议改用 .gitignore 忽略",
                  warn="将删除所有未跟踪的文件和目录（新建且未 add 的文件），"
                       "删除后无法恢复。"),
            ]),
            ("储藏与标签", [
                A("储藏修改", ["git", "stash", "push", "-u", "-m", "{message}"],
                  'git stash push -u -m "说明"',
                  "把当前修改临时收起来，工作区恢复干净",
                  "要切分支但不想带着半成品、或想临时保存现场时",
                  params=[("储藏说明", "message", "暂存当前修改", True, False)]),
                A("恢复储藏", ["git", "stash", "pop"], "git stash pop",
                  "恢复最近一次储藏的修改，并从储藏列表中删除",
                  "回到现场继续干活时"),
                A("储藏列表", ["git", "stash", "list"], "git stash list",
                  "查看所有储藏记录",
                  "想确认之前存过什么时"),
                A("标签列表", ["git", "tag", "-n"], "git tag -n",
                  "查看所有标签及说明（标签常用于标记版本）",
                  "确认已有哪些版本号时"),
                A("新建标签", ["git", "tag", "-a", "{name}", "-m", "{message}"],
                  'git tag -a <名称> -m "说明"',
                  "给当前提交打上版本标签（如 v1.0.0）",
                  "发布版本、标记里程碑时",
                  params=[("标签名（如 v1.0.0）", "name", "", True, False),
                          ("标签说明", "message", "版本标记", True, False)]),
            ]),
        ]

    # ---- 特殊动作：克隆（需要记住目标目录，完成后提示切换） ----
    def _act_clone(self, values):
        url = values["url"].strip()
        target = values.get("dir", "").strip()
        cmd = ["git", "clone", "--progress", url]
        if target:
            cmd.append(target)
        # 预估克隆到的目录，用于完成后询问是否切换
        if target:
            guess = os.path.join(self._get_cwd(), target) \
                if not os.path.isabs(target) else target
        else:
            name = url.rstrip("/").split("/")[-1].split(":")[-1]
            if name.endswith(".git"):
                name = name[:-4]
            guess = os.path.join(self._get_cwd(), name) if name else None
        self._clone_target = guess
        self._run(cmd, note="从 %s 克隆仓库" % mask_secrets(url))
        return True

    # ---- 特殊动作：配置身份（需要连续写两条 config，用队列串联） ----
    def _act_identity(self, values):
        name = values["name"].strip()
        email = values["email"].strip()
        self._cmd_queue = [["git", "config", "--global", "user.email", email]]
        started = self._run(["git", "config", "--global", "user.name", name],
                            note="配置 Git 提交身份（全局，对所有仓库生效）")
        if not started:
            self._cmd_queue = []
        return started

    # ============================== 执行流程 ==============================
    def _do_action(self, a):
        """功能按钮统一入口：校验 → 参数对话框 → 危险确认 → 执行。"""
        if self.executor.running:
            self._console("有命令正在执行，请稍候或点击【终止】。", "warn")
            return
        if not self._git_ok:
            messagebox.showwarning(APP_NAME, "未检测到 Git，请先安装 Git 后重试。")
            return
        path = norm_path(self.repo_var.get())
        if not path or not os.path.isdir(path):
            messagebox.showwarning(APP_NAME, "请先在上方选择一个有效的文件夹。")
            return
        if a.get("need_repo", True) and not self._is_repo:
            messagebox.showwarning(
                APP_NAME, "所选文件夹还不是 Git 仓库。\n\n"
                "可先执行【初始化仓库】，或改选已有的仓库目录。")
            return

        values = {}
        params = a.get("params")
        if params:
            values = ParamDialog.ask(self, a["label"] + " · 输入参数", params)
            if values is None:
                return  # 用户取消

        if a.get("handler"):
            a["handler"](values)
            return

        cmd = build_command(a["cmd"], values)
        if not cmd:
            return
        if a.get("warn"):
            if not messagebox.askyesno(
                    "危险操作确认",
                    "即将执行：\n    " + display_cmd(cmd) +
                    "\n\n" + a["warn"] + "\n\n确定要继续吗？"):
                self._console("已取消：%s" % a["label"], "info")
                return
        self._run(cmd, note=a["what"])

    def _run(self, cmd, note=None):
        self._console("")  # 空行分隔
        self.preview_var.set(display_cmd(cmd))
        if not self.executor.run(self._get_cwd(), cmd, note):
            self._console("有命令正在执行中，请稍候。", "warn")
            return False
        return True

    def _run_custom(self, _=None):
        """终端下方的自定义命令行：自动补 git 前缀。"""
        if self.executor.running:
            self._console("有命令正在执行，请先等待完成或点击【终止】。", "warn")
            return
        if not self._git_ok:
            messagebox.showwarning(APP_NAME, "未检测到 Git，请先安装 Git 后重试。")
            return
        path = norm_path(self.repo_var.get())
        if not path or not os.path.isdir(path):
            messagebox.showwarning(APP_NAME, "请先在上方选择一个有效的文件夹。")
            return
        raw = self.custom_var.get().strip()
        if not raw:
            return
        try:
            tokens = shlex.split(raw, posix=(os.name != "nt"))
        except ValueError:
            # 引号不配对（如少写了后半引号）时按空格拆分，而不是抛英文报错
            tokens = raw.split()
            self._console("提示：命令里的引号似乎没有配对，已按空格拆分执行，"
                          "请核对命令是否正确。", "warn")
        if not tokens:
            return
        tokens = [t.strip('"') for t in tokens]
        if tokens[0].lower() != "git":
            tokens = ["git"] + tokens  # 友好处理：省略 git 前缀时自动补上
        self.custom_var.set("")
        self._run(tokens, note="自定义命令")

    def _stop(self):
        self.executor.stop()
        self._console("正在终止当前命令…", "warn")

    # ============================== 事件处理 ==============================
    def _poll_events(self):
        """主线程轮询执行器事件队列，更新终端/进度条/日志。"""
        try:
            while True:
                ev = self.executor.events.get_nowait()
                self._handle_event(ev)
        except queue.Empty:
            pass
        self.after(40, self._poll_events)

    def _handle_event(self, ev):
        kind = ev[0]
        if kind == "start":
            self._run_cwd = ev[2]
            self._recent_err = []
            self._set_busy(True)
        elif kind == "note":
            self._console("【功能】" + ev[1], "note")
        elif kind == "cmd":
            self.preview_var.set(ev[1])
            self._console("$ " + ev[1], "cmd")
            self._log_line("=" * 60)
            self._log_line(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                           + "  目录: " + (self._run_cwd or self._get_cwd()))
            self._log_line("$ " + ev[1])
        elif kind in ("out", "err"):
            self._console(ev[1], kind)
            if kind == "err":
                self._recent_err.append(ev[1])
            self._log_line(ev[1])
        elif kind == "progress":
            self.progress.stop()
            self.progress.configure(mode="determinate", value=ev[1])
            self.prog_label_var.set("%d%%" % ev[1])
        elif kind == "done":
            code, secs, killed = ev[1], ev[2], ev[3]
            self._set_busy(False)
            self._prog_done()
            if killed:
                self._console("已手动终止命令。", "warn")
                self._log_line("[已终止] 耗时 %.1fs" % secs)
                self.last_var.set("上次命令：已终止")
            elif code == 0:
                self._console("完成：退出码 0，耗时 %.1f 秒" % secs, "ok")
                self._log_line("[成功] 退出码 0，耗时 %.1fs" % secs)
                self.last_var.set("上次命令：成功（%.1f 秒）" % secs)
            else:
                self._console("失败：退出码 %s，耗时 %.1f 秒"
                              "（上方红色内容为失败原因）" % (code, secs), "err")
                self._log_line("[失败] 退出码 %s，耗时 %.1fs" % (code, secs))
                self.last_var.set("上次命令：失败（退出码 %s）" % code)
            self._log_line("")
            if killed:
                self._cmd_queue = []
            elif code == 0:
                if self._cmd_queue:
                    self._run(self._cmd_queue.pop(0))  # 连续命令队列
            else:
                self._fail_hint()
            self._refresh_status()
            self._after_clone()

    def _fail_hint(self):
        """根据错误输出识别常见失败原因，给出新手能看懂的中文提示。"""
        text = "\n".join(self._recent_err)
        hints = []
        need_identity = ("Author identity unknown" in text
                         or "tell me who you are" in text)
        if need_identity:
            hints.append("失败原因：还没有配置 Git 身份（姓名和邮箱），"
                         "Git 要求每次提交都注明作者。请点击左侧【配置身份】"
                         "按钮填入姓名和邮箱，然后重新操作。")
        if "not a git repository" in text:
            hints.append("失败原因：当前文件夹不是 Git 仓库。请先执行"
                         "【初始化仓库】，或在顶部改选包含 .git 的项目目录。")
        if "configured push destination" in text:
            hints.append("失败原因：这个仓库还没有关联远程仓库，所以没地方推送。"
                         "先点【添加远程】填入远程地址（如 GitHub 仓库地址），"
                         "再用【推送并关联】完成第一次推送。")
        if "no tracking information" in text or "no upstream branch" in text:
            hints.append("失败原因：当前分支还没有和远程分支建立关联。请用"
                         "【推送并关联】完成第一次推送（会自动建立关联），"
                         "之后【推送】和【拉取合并 pull】就能直接使用了。")
        if "does not appear to be a git repository" in text \
                or "Could not read from remote repository" in text:
            hints.append("失败原因：远程地址不对或连不上。请用【查看远程】核对"
                         "地址拼写；若地址填错了，可在命令输入框执行"
                         " git remote remove 名称 删除后重新【添加远程】。")
        if "CONFLICT" in text:
            hints.append("失败原因：合并时出现冲突。打开提示的文件，搜索 "
                         "<<<<<<< 标记，手动保留想要的内容后，再【暂存全部】"
                         "并【提交】即可完成合并。")
        if "non-fast-forward" in text or "rejected" in text:
            hints.append("失败原因：远程有你本地还没有的新提交。请先执行"
                         "【拉取合并 pull】，再重新推送。")
        if "could not read Username" in text or "Authentication fail" in text \
                or "403" in text:
            hints.append("失败原因：远程仓库需要登录或没有访问权限。首次使用时"
                         "请重新执行一次，弹出登录窗口后登录；若仍失败，请确认"
                         "账号对该仓库有权限，或改用 SSH 地址。")
        if "nothing to commit" in text:
            hints.append("提示：没有可提交的内容。工作区是干净的，或改动还没有"
                         "【暂存全部】。可先执行【查看状态】确认现状。")
        for h in hints:
            self._console("提示：" + h, "warn")
            self._log_line("[提示] " + h)
        if need_identity and hints:
            if messagebox.askyesno(
                    APP_NAME, "提交失败：还没有配置 Git 身份。\n\n"
                    "是否现在打开【配置身份】窗口，填入姓名和邮箱？"):
                vals = ParamDialog.ask(
                    self, "配置身份 · 输入参数",
                    [("姓名（如 Zhang San）", "name", "", True, False),
                     ("邮箱（如 you@example.com）", "email", "", True, False)])
                if vals:
                    self._act_identity(vals)

    def _after_clone(self):
        """克隆成功后，询问是否把仓库路径切换到新目录。"""
        target = self._clone_target
        self._clone_target = None
        if target and os.path.isdir(target):
            if messagebox.askyesno(APP_NAME, "克隆完成。\n\n是否将仓库路径切换到：\n%s" % target):
                self.repo_var.set(target)
                self._save_config()
                self._refresh_status()

    # ---- 忙碌状态与进度条 --------------------------------------------
    def _set_busy(self, busy):
        state = "disabled" if busy else "normal"
        for b in self._action_buttons:
            b.configure(state=state)
        self.btn_run_custom.configure(state=state)
        self.btn_stop.configure(state="normal" if busy else "disabled")
        self.prog_label_var.set("执行中…" if busy else "就绪")
        if busy:
            self.progress.configure(mode="indeterminate")
            self.progress.start(14)

    def _prog_done(self):
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.prog_label_var.set("100%")
        self.after(800, lambda: (self.progress.configure(value=0),
                                 self.prog_label_var.set("就绪")))

    # ============================== 状态与配置 ==============================
    def _get_cwd(self):
        p = norm_path(self.repo_var.get())
        if p and os.path.isdir(p):
            return p
        return os.path.expanduser("~")

    def _browse(self):
        d = filedialog.askdirectory(
            initialdir=self.repo_var.get() or os.path.expanduser("~"))
        if d:
            self.repo_var.set(os.path.normpath(d))
            self._save_config()
            self._refresh_status()

    def _refresh_status(self):
        """同步刷新仓库/分支/变更数（显示在底部状态栏）。"""
        path = norm_path(self.repo_var.get())
        self._is_repo = False
        if not path or not os.path.isdir(path):
            self.status_var.set("仓库：未选择")
            return

        def q(*args):
            try:
                r = subprocess.run(args, cwd=path, capture_output=True,
                                   timeout=8, creationflags=CREATE_NO_WINDOW)
                return decode_bytes(r.stdout).strip()
            except Exception:
                return ""

        top = q("git", "rev-parse", "--show-toplevel")
        if not top:
            self.status_var.set("仓库：所选文件夹不是 Git 仓库（可先【初始化仓库】）")
            return
        self._is_repo = True
        branch = q("git", "branch", "--show-current") or "（HEAD 分离）"
        st = q("git", "status", "--porcelain")
        n = len([l for l in st.splitlines() if l.strip()])
        self.status_var.set("仓库：%s    分支：%s    变更文件：%d" % (top, branch, n))

    def _startup_check(self):
        """启动时检测 Git，打印欢迎信息。"""
        try:
            r = subprocess.run(["git", "--version"], capture_output=True,
                               timeout=5, creationflags=CREATE_NO_WINDOW)
            ver = (r.stdout or r.stderr).decode("utf-8", "replace").strip()
            self._git_ok = True
            self.gitver_var.set(ver.replace("git version", "Git"))
        except Exception:
            self._git_ok = False
            self.gitver_var.set("未检测到 Git")
            self._console("警告：未检测到 Git。请安装 Git for Windows 后重新打开本工具。"
                          "下载地址：https://git-scm.com/download/win", "err")
            messagebox.showwarning(
                APP_NAME, "未检测到 Git。\n\n请先安装 Git for Windows"
                          "（安装时保持默认选项即可），然后重新打开本工具。")
        if self._git_ok and not self._has_identity():
            self._console("提示：尚未配置 Git 身份（姓名/邮箱）。不配置的话首次"
                          "【提交】会失败，请先点击左侧【配置身份】按钮设置。",
                          "warn")
        self._console("%s %s 已就绪" % (APP_NAME, APP_VERSION), "ok")
        self._console("提示：先在上方选择仓库文件夹，再点击左侧功能按钮。"
                      "每次执行的命令、过程与结果都会显示在终端区并写入日志。", "info")
        self._console("日志文件：" + LOG_FILE, "info")
        self._refresh_status()

    def _has_identity(self):
        """检查是否已配置全局 user.name / user.email。"""
        def q(arg):
            try:
                r = subprocess.run(["git", "config", "--global", arg],
                                   capture_output=True, timeout=5,
                                   creationflags=CREATE_NO_WINDOW)
                return decode_bytes(r.stdout).strip()
            except Exception:
                return ""
        return bool(q("user.name")) and bool(q("user.email"))

    def _load_config(self):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
            repo = norm_path(cfg.get("repo", ""))
            if repo and os.path.isdir(repo):
                self.repo_var.set(repo)
        except Exception:
            pass
        self._refresh_status()

    def _save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"repo": norm_path(self.repo_var.get())}, f,
                          ensure_ascii=False)
        except OSError:
            pass

    def _on_close(self):
        if self.executor.running:
            if not messagebox.askyesno(
                    APP_NAME,
                    "还有命令正在执行（可能是克隆/推送等网络操作）。\n\n"
                    "现在退出会终止该命令。确定要退出吗？"):
                return
            self.executor.stop()  # 不终止的话 git 子进程会变成孤儿继续后台运行
        self._save_config()
        self.destroy()

    def _on_tab_changed(self, _):
        try:
            if self.nb.index(self.nb.select()) == 1:  # 历史日志页
                self._refresh_log_tab()
        except Exception:
            pass

    # ============================== 终端与日志 ==============================
    def _console(self, text, tag="out"):
        self.con.configure(state="normal")
        at_bottom = self.con.yview()[1] >= 0.999
        self.con.insert("end", text + "\n", tag)
        if at_bottom:
            self.con.see("end")
        self.con.configure(state="disabled")

    def _copy_sel(self):
        try:
            text = self.con.get("sel.first", "sel.last")
            if text:
                self.clipboard_clear()
                self.clipboard_append(text)
        except tk.TclError:
            pass

    def _clear_console(self):
        self.con.configure(state="normal")
        self.con.delete("1.0", "end")
        self.con.configure(state="disabled")

    def _log_line(self, text):
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass

    def _refresh_log_tab(self):
        """读取日志文件并显示（过大时只显示末尾部分）。"""
        try:
            with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            lines = []
        cut = 4000
        shown = lines[-cut:] if len(lines) > cut else lines
        size = os.path.getsize(LOG_FILE) if os.path.exists(LOG_FILE) else 0
        self.loginfo_var.set("路径：%s    大小：%.1f KB%s"
                             % (LOG_FILE, size / 1024,
                                "    （仅显示末尾 %d 行）" % cut if len(lines) > cut else ""))
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("end", "\n".join(shown))
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self):
        if not os.path.exists(LOG_FILE):
            return
        if messagebox.askyesno(APP_NAME, "确定清空日志文件吗？\n%s" % LOG_FILE):
            try:
                open(LOG_FILE, "w").close()
            except OSError:
                pass
            self._refresh_log_tab()

    def _open_log(self):
        try:
            if os.name == "nt":
                os.startfile(LOG_FILE)  # noqa: 用系统默认程序打开
            elif sys.platform == "darwin":
                subprocess.Popen(["open", LOG_FILE])
            else:
                subprocess.Popen(["xdg-open", LOG_FILE])
        except Exception as e:
            messagebox.showwarning(APP_NAME, "打开日志文件失败：%s" % e)

    # ============================== 使用说明内容 ==============================
    def _build_help_items(self):
        """组装"使用说明"页内容：[(标签, 文本), …]。"""
        it = []
        add = lambda tag, text="": it.append((tag, text))

        add("h1", "Git 可视化工具 · 使用说明")

        add("h2", "一、本工具是什么")
        add("p", "这是一个 Git 图形化操作工具：把常用 Git 功能做成按钮。每次点击按钮都会：")
        add("p", "1. 在顶部【命令预览】显示即将执行的真实 git 命令，让你边用边学；")
        add("p", "2. 命令与执行过程实时打印在终端区（黄色=命令，白色=输出，红色=错误，绿色=结果）；")
        add("p", "3. 进度条显示执行进度：克隆/拉取/推送等网络操作显示百分比，本地操作显示滚动动画；")
        add("p", "4. 全部过程自动写入日志文件，可在【历史日志】页查看、清空或用记事本打开。")
        add("p", "鼠标悬停在任意按钮上，也会显示该功能的作用和对应命令。")

        add("h2", "二、快速上手：最常用的日常流程")
        add("p", "第一次使用：先点【配置身份】填入姓名和邮箱（Git 要求每次提交注明作者，"
                 "不配置的话提交必定失败），全程只需设置一次。")
        add("p", "日常流程：① 顶部选择仓库文件夹  →  ②【查看状态】看哪些文件变了  →  ③【查看改动】确认改动内容")
        add("p", "→  ④【暂存全部】  →  ⑤【提交】填写说明  →  ⑥【推送】上传到远程仓库")
        add("p", "每天开始工作时，先【拉取合并 pull】同步最新代码；鼠标悬停按钮可随时查看提示。")

        add("h2", "三、理解 Git 的四个区域（重要）")
        add("plain", "工作区（你正在编辑的文件）")
        add("plain", "    │  git add  ←【暂存全部】/【暂存文件】")
        add("plain", "暂存区（准备提交的改动清单）")
        add("plain", "    │  git commit  ←【提交】")
        add("plain", "本地仓库（历史存档点）")
        add("plain", "    │  git push  ←【推送】")
        add("plain", "远程仓库（服务器，供团队共享）")
        add("p", "撤销类操作分别作用于不同区域：【撤销修改】作用于工作区，【取消暂存】作用于暂存区，"
                 "【回退】作用于本地提交历史。")

        add("h2", "四、功能按钮详解")
        for cat, acts in self._build_actions():
            add("b", "◆ " + cat)
            for a in acts:
                add("p", "· " + a["label"])
                add("cmd", "命令：" + a["hint"])
                add("p", "作用：" + a["what"])
                if a.get("when"):
                    add("p", "何时使用：" + a["when"])
                if a.get("warn"):
                    add("warn", "注意：" + a["warn"])
            add("plain", "")
        add("b", "◆ 自定义命令")
        add("cmd", "命令：终端区下方的 git> 输入框")
        add("p", "作用：直接输入任意 git 子命令执行（如 log --oneline -5），"
                 "省略 git 前缀时会自动补上")
        add("p", "何时使用：需要本工具按钮未覆盖的 git 功能时")
        add("b", "◆ 终止按钮")
        add("p", "作用：强制结束当前正在执行的命令")
        add("p", "何时使用：命令长时间无响应（例如等待输入账号密码）时")

        add("h2", "五、危险操作与安全建议")
        add("warn", "· 回退(彻底丢弃) reset --hard：丢弃之后的提交与修改，未推送的内容会丢失；")
        add("warn", "· 撤销修改 restore：未提交的工作区改动被丢弃后无法恢复；")
        add("warn", "· 清理未跟踪 clean -fd：删除所有未跟踪文件/目录，无法恢复；")
        add("warn", "· 强制推送 push --force-with-lease：覆盖远程历史，可能影响协作者；")
        add("warn", "· 修改上次提交 amend：改写历史，已推送的提交不建议再修改。")
        add("p", "原则：不确定时，先用【查看状态】【查看改动】【提交历史】观察现状，再决定操作；"
                 "重要操作前可先推送备份。带危险提示的按钮都会弹出二次确认。")

        add("h2", "六、常见问题")
        add("b", "Q：提交时提示“Author identity unknown / Please tell me who you are”？")
        add("p", "因为还没有配置 Git 身份。点击【配置身份】按钮填入姓名和邮箱后重试即可，"
                 "工具也会在失败时自动弹出配置窗口。")
        add("b", "Q：提示“不是 git 仓库”？")
        add("p", "先执行【初始化仓库】，或确认选择的是项目根目录（含 .git 隐藏文件夹）。")
        add("b", "Q：推送时失败 / 要求登录？")
        add("p", "首次使用请在弹出的 Git 凭据窗口中登录（需安装 Git for Windows）；"
                 "私有仓库建议向管理员索取访问权限，或改用 SSH 地址。")
        add("b", "Q：合并时出现冲突（CONFLICT）？")
        add("p", "属正常现象：打开冲突文件，搜索 <<<<<<< 标记，手动决定保留内容后，"
                 "再【暂存全部】+【提交】即可完成合并。")
        add("b", "Q：命令卡住不动？")
        add("p", "部分命令需要交互输入（如登录）。点击【终止】结束它，按提示处理后重试。")
        add("b", "Q：退出码是什么？")
        add("p", "0 表示成功；非 0 表示失败，终端中的红色输出通常就是失败原因。")
        add("b", "Q：怎么把工具打包成 exe？")
        add("p", "安装 Python 后执行：pip install pyinstaller，"
                 "然后 pyinstaller -F -w git_visual_tool.py，在 dist 目录得到 exe。")

        add("h2", "七、环境要求")
        add("p", "Python 3.6 及以上（自带 tkinter，无需安装任何第三方库）+ Git。"
                 "本工具所有操作只是调用你本机的 git 命令，不修改 Git 配置。")
        return it


def main():
    # Windows 高分屏适配：避免界面在高 DPI 屏幕上显示模糊
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    try:
        app = App()
        app.mainloop()
    except Exception:
        # 兜底防闪退：启动阶段任何异常都弹窗告知并写入日志，
        # 避免"双击后窗口一闪而过、什么都看不到"的情况。
        err = traceback.format_exc()
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write("=" * 60 + "\n[启动失败] " + err + "\n")
        except OSError:
            pass
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                APP_NAME, "启动失败，错误信息如下（已写入日志 %s）：\n\n%s"
                          % (LOG_FILE, err[-1200:]))
        except Exception:
            pass
        raise SystemExit(1)


if __name__ == "__main__":
    main()
