"""tkinter 图形界面：双击 exe 就能用。

布局：上面一排操作按钮 + 状态栏，下面一个日志框。所有耗时操作在后台线程跑，
print 输出通过队列转发到日志框；关窗口会先停掉监视线程。
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import time
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import scrolledtext

from .config import CONFIG_TEMPLATE, default_config_path, load_config
from .converter import convert_one, find_ncmdump, is_stable, output_exists


class _QueueWriter:
    """把 print 的内容转发到 GUI 日志队列。"""

    def __init__(self, q: queue.Queue, original):
        self.q = q
        self.original = original

    def write(self, text):
        if self.original:
            try:
                self.original.write(text)
            except Exception:
                pass
        if text and text.strip():
            self.q.put(text.rstrip("\n"))

    def flush(self):
        if self.original:
            try:
                self.original.flush()
            except Exception:
                pass


def run() -> int:
    root = tk.Tk()
    root.title("ncm-pipeline")
    root.geometry("820x560")
    root.minsize(680, 460)

    q: queue.Queue = queue.Queue()
    sys.stdout = _QueueWriter(q, getattr(sys, "stdout", None))
    sys.stderr = _QueueWriter(q, getattr(sys, "stderr", None))

    # 配置：没有就按模板生成一份，提示用户改路径
    cfg_path = default_config_path()
    if not Path(cfg_path).exists():
        Path(cfg_path).parent.mkdir(parents=True, exist_ok=True)
        Path(cfg_path).write_text(CONFIG_TEMPLATE, encoding="utf-8")
    cfg = load_config(Path(cfg_path))

    state = {
        "busy": False,
        "watch_stop": threading.Event(),
        "server_proc": None,
    }

    # ── 顶部：配置信息 ──
    top = tk.Frame(root)
    top.pack(fill="x", padx=10, pady=(10, 4))
    cfg_label = tk.Label(top, fg="#555", anchor="w")
    cfg_label.pack(side="left", fill="x", expand=True)

    def refresh_cfg_label():
        cfg_label.config(text="配置: %s" % cfg_path)

    refresh_cfg_label()

    def reload_config():
        nonlocal cfg
        if not Path(cfg_path).exists():
            Path(cfg_path).write_text(CONFIG_TEMPLATE, encoding="utf-8")
        cfg = load_config(Path(cfg_path))
        refresh_cfg_label()
        log("配置已载入: %s" % cfg_path)

    # ── 中部：按钮区 ──
    grid = tk.Frame(root)
    grid.pack(fill="x", padx=10, pady=4)

    buttons: dict[str, tk.Button] = {}

    def add_btn(text, col, row, fn, color="#2b6cb0"):
        b = tk.Button(grid, text=text, width=16, fg="white", bg=color,
                      activebackground=color, relief="flat",
                      command=lambda: guarded(fn))
        b.grid(row=row, column=col, sticky="nsew", padx=4, pady=4)
        buttons[text] = b
        return b

    for c in range(4):
        grid.columnconfigure(c, weight=1)

    # ── 状态栏 ──
    status = tk.Label(root, text="就绪", fg="#2f855a", anchor="w")
    status.pack(fill="x", padx=12)

    # ── 日志框 ──
    logbox = scrolledtext.ScrolledText(root, height=18, state="disabled",
                                       font=("Consolas", 9))
    logbox.pack(fill="both", expand=True, padx=10, pady=8)

    def log(text):
        logbox.config(state="normal")
        logbox.insert("end", str(text) + "\n")
        logbox.see("end")
        logbox.config(state="disabled")

    def drain_queue():
        try:
            while True:
                line = q.get_nowait()
                log(line)
        except queue.Empty:
            pass
        root.after(200, drain_queue)

    def set_busy(busy: bool):
        state["busy"] = busy
        for b in buttons.values():
            b.config(state="disabled" if busy else "normal")
        if state["watch_stop"].is_set():
            buttons["监视转码：开始"].config(state="normal")

    def guarded(fn):
        if state["busy"]:
            log("有任务正在跑，等它结束再点。")
            return
        set_busy(True)
        status.config(text="运行中…", fg="#b7791f")

        def worker():
            try:
                fn()
                status.config(text="完成", fg="#2f855a")
            except Exception as e:
                log("出错: %s" % e)
                log(traceback.format_exc())
                status.config(text="出错（看日志）", fg="#c53030")
            finally:
                root.after(0, lambda: set_busy(False))

        threading.Thread(target=worker, daemon=True).start()

    # ── 各操作 ──
    def t_doctor():
        from .cli import cmd_doctor
        import argparse
        args = argparse.Namespace(config=str(cfg_path), get_tools=False)
        cmd_doctor(args)

    def t_get_tools():
        from .converter import download_ncmdump
        download_ncmdump(cfg.tools_dir)

    def t_convert_once():
        from .converter import convert_pending
        n = convert_pending(cfg)
        print("本次转换 %d 个" % n)

    def t_dedup(dry: bool):
        from .dedup import run_dedup
        run_dedup(cfg.output_dir, cfg.duplicates_dir, cfg.reference_dirs,
                  dry_run=dry,
                  report_path=Path(cfg.data_dir) / "dedup_report.json")

    def t_login():
        from .fetch import login
        p = login(cfg)
        print("cookie 已保存: %s" % p)

    def t_fetch():
        from .fetch import run_fetch
        run_fetch(cfg)

    def t_analyze():
        from .analyze import run_analyze
        run_analyze(cfg)
        print("报告: %s" % cfg.report_path)

    def t_open_report():
        if Path(cfg.report_path).exists():
            os.startfile(str(cfg.report_path))
        else:
            print("报告还不存在，先点「生成报告」。")

    def t_open_config():
        os.startfile(str(cfg_path))

    def t_server():
        from .api import server_reachable, start_server
        if server_reachable(cfg.api_base):
            print("API 服务已在运行。")
            return
        p = start_server(cfg.api_server_dir, cfg.api_base)
        if p is None:
            print("找不到 API 服务端（%s 下没有 node_modules）。先按 README 安装：\n"
                  "  npm install @neteaseapireborn/api" % cfg.api_server_dir)
            return
        state["server_proc"] = p
        print("API 服务已启动（后台进程）。")

    # 监视转码：单独一个开关按钮
    watch_btn = tk.Button(grid, text="监视转码：开始", width=16, fg="white",
                          bg="#2f855a", activebackground="#2f855a",
                          relief="flat", command=lambda: toggle_watch())
    watch_btn.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
    buttons["监视转码：开始"] = watch_btn

    def toggle_watch():
        if watch_btn["text"].endswith("开始"):
            ncmdump = find_ncmdump(cfg.ncmdump, cfg.tools_dir)
            if not ncmdump:
                log("没找到 ncmdump，先点「下载转码器」。")
                return
            state["watch_stop"].clear()
            state["busy"] = True
            for name, b in buttons.items():
                b.config(state="disabled")
            watch_btn.config(text="监视转码：停止", bg="#c53030",
                             state="normal")
            status.config(text="监视中（关掉窗口也会停）", fg="#2f855a")
            print("[watch] %s -> %s" % (cfg.watch_dirs, cfg.output_dir))

            def watch_loop():
                failed: dict[str, int] = {}
                try:
                    while not state["watch_stop"].is_set():
                        ok = skip = wait = fail = 0
                        ncms: list[Path] = []
                        for d in cfg.watch_dirs:
                            if Path(d).is_dir():
                                ncms += list(Path(d).glob("*.ncm"))
                        for f in ncms:
                            if state["watch_stop"].is_set():
                                break
                            base = f.stem
                            if output_exists(cfg.output_dir, base) or \
                               (Path(cfg.done_dir) / (base + ".done")).exists():
                                skip += 1
                                continue
                            if not is_stable(f, cfg.stable_seconds):
                                wait += 1
                                continue
                            if convert_one(ncmdump, f, cfg.output_dir,
                                           cfg.done_dir):
                                ok += 1
                                failed.pop(str(f), None)
                            else:
                                fail += 1
                                failed[str(f)] = failed.get(str(f), 0) + 1
                        print("[%s] ncm=%d converted=%d skip=%d wait=%d fail=%d"
                              % (time.strftime("%H:%M:%S"), len(ncms), ok,
                                 skip, wait, fail), flush=True)
                        for _ in range(cfg.round_seconds * 5):
                            if state["watch_stop"].is_set():
                                break
                            time.sleep(0.2)
                finally:
                    print("[watch] 已停止" if state["watch_stop"].is_set()
                          else "[watch] 结束")
                    if failed:
                        print("仍失败的文件：")
                        for k in list(failed)[:10]:
                            print("  -", k)

            threading.Thread(target=watch_loop, daemon=True).start()
        else:
            state["watch_stop"].set()
            watch_btn.config(text="监视转码：开始", bg="#2f855a")
            state["busy"] = False
            for name, b in buttons.items():
                b.config(state="normal")
            status.config(text="就绪", fg="#2f855a")

    # 摆按钮（两行）
    add_btn("环境自检", 1, 0, t_doctor)
    add_btn("下载转码器", 2, 0, t_get_tools)
    add_btn("单次转换", 3, 0, t_convert_once)
    add_btn("去重预览", 1, 1, lambda: t_dedup(True), color="#805ad5")
    add_btn("去重执行", 2, 1, lambda: t_dedup(False), color="#805ad5")
    add_btn("扫码登录", 3, 1, t_login, color="#2f855a")
    add_btn("启动API服务", 0, 0, t_server, color="#4a5568")
    add_btn("拉取数据", 0, 1, t_fetch, color="#2f855a")
    add_btn("生成报告", 0, 2, t_analyze, color="#2b6cb0")
    add_btn("打开报告", 1, 2, t_open_report, color="#2b6cb0")
    add_btn("打开配置", 2, 2, t_open_config, color="#4a5568")
    add_btn("重新载入配置", 3, 2, reload_config, color="#4a5568")

    log("ncm-pipeline 就绪。")
    log("第一次用：先「下载转码器」→「环境自检」，然后改好配置里的路径"
        "（点「打开配置」），再按 监视转码 → 去重 → 登录 → 拉取数据 → 生成报告 的顺序来。")
    drain_queue()

    def on_close():
        state["watch_stop"].set()
        try:
            if state["server_proc"]:
                state["server_proc"].terminate()
        except Exception:
            pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return 0
