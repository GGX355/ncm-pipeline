"""图形界面：第一次打开弹设置向导（每栏都有说明、全是浏览文件夹），之后是主界面。

所有耗时操作在后台线程跑，print 经队列转发到日志框；关窗口先停监视线程。
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, scrolledtext

from .config import default_config_path, load_config, render_config


class _QueueWriter:
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


CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def run() -> int:
    root = tk.Tk()
    root.title("ncm-pipeline")
    root.geometry("860x600")
    root.minsize(720, 500)

    q: queue.Queue = queue.Queue()
    sys.stdout = _QueueWriter(q, getattr(sys, "stdout", None))
    sys.stderr = _QueueWriter(q, getattr(sys, "stderr", None))

    cfg_path = default_config_path()
    state = {"cfg": None, "busy": False,
             "watch_stop": threading.Event(), "server_proc": None}

    logbox = scrolledtext.ScrolledText(root, height=16, state="disabled",
                                       font=("Consolas", 9))

    def log(text):
        logbox.config(state="normal")
        logbox.insert("end", str(text) + "\n")
        logbox.see("end")
        logbox.config(state="disabled")

    def drain_queue():
        try:
            while True:
                log(q.get_nowait())
        except queue.Empty:
            pass
        root.after(200, drain_queue)

    def load_cfg() -> bool:
        """配置存在则载入返回 True；不存在返回 False（该走向导）。"""
        if not Path(cfg_path).exists():
            return False
        state["cfg"] = load_config(Path(cfg_path))
        return True

    header = tk.Frame(root)
    header.pack(fill="x", padx=10, pady=(8, 2))
    summary = tk.Label(header, fg="#444", anchor="w", justify="left")
    summary.pack(fill="x")

    def refresh_summary():
        c = state["cfg"]
        if not c:
            summary.config(text="还没有配置，先完成设置向导（半分钟）。")
            return
        summary.config(text="音乐目录: %s\n成品库: %s ｜ 报告: %s"
                       % (c.watch_dirs[0] if c.watch_dirs else "-",
                          c.output_dir, c.report_path))

    status = tk.Label(root, text="就绪", fg="#2f855a", anchor="w")
    status.pack(fill="x", padx=12)
    logbox.pack(fill="both", expand=True, padx=10, pady=(4, 8))

    buttons: dict[str, tk.Button] = {}

    def set_busy(busy):
        state["busy"] = busy
        for b in buttons.values():
            b.config(state="disabled" if busy else "normal")

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
                status.config(text="出错（看下面日志）", fg="#c53030")
            finally:
                root.after(0, lambda: (set_busy(False),
                                       status.config(text="就绪", fg="#2f855a")))

        threading.Thread(target=worker, daemon=True).start()

    # ---------------- 各操作 ----------------
    def t_doctor():
        from .cli import cmd_doctor
        import argparse
        cmd_doctor(argparse.Namespace(config=str(cfg_path), get_tools=False))

    def t_get_tools():
        from .converter import download_ncmdump
        download_ncmdump(cfg.tools_dir)

    def t_convert_once():
        from .converter import convert_pending
        n = convert_pending(state["cfg"])
        print("本次转换 %d 个" % n)
        if n == 0:
            print("（没有新 ncm。如果不对，检查「音乐目录」：网易云 设置→下载与缓存 "
                  "里写的那个才是下载目录）")

    def t_dedup(dry: bool):
        from .dedup import run_dedup
        c = state["cfg"]
        run_dedup(c.output_dir, c.duplicates_dir, c.reference_dirs,
                  dry_run=dry,
                  report_path=Path(c.data_dir) / "dedup_report.json")

    def t_install_api():
        """一键安装本机 API 服务（拉歌单数据用，装一次就行）。"""
        c = state["cfg"]
        if not shutil.which("node"):
            print("没检测到 Node.js。先去 https://nodejs.org/zh-cn 下载安装"
                  "（一路下一步），装完再点这个按钮。已帮你打开下载页。")
            webbrowser.open("https://nodejs.org/zh-cn")
            return
        server_dir = Path(c.api_server_dir)
        server_dir.mkdir(parents=True, exist_ok=True)
        app = server_dir / "node_modules" / "@neteaseapireborn" / "api" / "app.js"
        if app.exists():
            print("API 服务已经装过了（%s）" % server_dir)
        else:
            print("开始安装 API 服务到 %s ，一般 1~3 分钟，等日志停下…" % server_dir)
            for cmd in ("npm init -y",
                        "npm install @neteaseapireborn/api --no-fund --no-audit"):
                p = subprocess.run(cmd, cwd=str(server_dir), shell=True,
                                   capture_output=True, text=True,
                                   creationflags=CREATE_NO_WINDOW)
                tail = ((p.stdout or "") + (p.stderr or ""))[-500:].strip()
                if tail:
                    print(tail)
                if p.returncode != 0:
                    raise RuntimeError("npm 命令失败（%s），看上面报错" % cmd)
            print("安装完成。")
        from .api import server_reachable, start_server
        if not server_reachable(c.api_base):
            state["server_proc"] = start_server(c.api_server_dir, c.api_base)
            time.sleep(3)
            print("API 服务已启动（%s）。它是独立的小后台进程，关掉本窗口也留着。"
                  % c.api_base)
        else:
            print("API 服务已在运行。")

    def t_login():
        from .fetch import login
        p = login(state["cfg"])
        print("cookie 已保存: %s（只在这台电脑上）" % p)

    def t_fetch():
        from .fetch import run_fetch
        run_fetch(state["cfg"])

    def t_analyze():
        from .analyze import run_analyze
        run_analyze(state["cfg"])
        print("报告: %s" % state["cfg"].report_path)

    def t_open(path: Path, what: str):
        if Path(path).exists():
            os.startfile(str(path))
        else:
            print("%s还不存在：%s" % (what, path))

    def t_game_gta():
        from .games import sync_gta
        sync_gta(state["cfg"])

    def t_game_horizon():
        from .games import prep_horizon
        prep_horizon(state["cfg"])

    def t_open_config():
        os.startfile(str(cfg_path))

    # ---------------- 主界面按钮 ----------------
    grid = tk.Frame(root)
    grid.pack(fill="x", padx=10, pady=4)
    for c in range(4):
        grid.columnconfigure(c, weight=1)

    def add_btn(text, col, row, fn, color="#2b6cb0"):
        b = tk.Button(grid, text=text, width=17, fg="white", bg=color,
                      activebackground=color, relief="flat",
                      command=lambda: guarded(fn))
        b.grid(row=row, column=col, sticky="nsew", padx=4, pady=4)
        buttons[text] = b
        return b

    watch_btn = tk.Button(grid, text="①监视转码：开始", width=17, fg="white",
                          bg="#2f855a", activebackground="#2f855a",
                          relief="flat")
    watch_btn.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
    buttons["①监视转码：开始"] = watch_btn

    add_btn("下载转码器", 1, 0, t_get_tools, color="#2f855a")
    add_btn("去重预览", 2, 0, lambda: t_dedup(True), color="#805ad5")
    add_btn("去重执行", 3, 0, lambda: t_dedup(False), color="#805ad5")
    add_btn("②安装/启动API", 0, 1, t_install_api, color="#2f855a")
    add_btn("③扫码登录", 1, 1, t_login, color="#2f855a")
    add_btn("④拉取数据", 2, 1, t_fetch, color="#2f855a")
    add_btn("⑤生成报告", 3, 1, t_analyze, color="#2b6cb0")
    add_btn("环境自检", 0, 2, t_doctor, color="#4a5568")
    add_btn("单次转换（不监视）", 1, 2, t_convert_once, color="#4a5568")
    add_btn("打开成品库", 2, 2,
            lambda: t_open(state["cfg"].output_dir, "成品库"), color="#4a5568")
    add_btn("打开报告", 3, 2,
            lambda: t_open(state["cfg"].report_path, "报告"), color="#2b6cb0")
    add_btn("重新走一遍向导", 0, 3, lambda: open_wizard(edit=True),
            color="#6b7280")
    add_btn("编辑配置文件", 1, 3, t_open_config, color="#6b7280")
    add_btn("同步到GTA5", 2, 3, t_game_gta, color="#3d5a80")
    add_btn("地平线6歌曲包", 3, 3, t_game_horizon, color="#3d5a80")

    hint = tk.Label(root, fg="#888", anchor="w", justify="left",
                    text="推荐顺序：②安装API → ③扫码登录 → ④拉取数据 → ⑤生成报告；"
                         "客户端下载歌的时候点 ①监视转码 挂着就行；"
                         "想开车听歌就用最右下两个游戏按钮。")
    hint.pack(fill="x", padx=12)

    # ---------------- 监视转码 ----------------
    def toggle_watch():
        if watch_btn["text"].endswith("开始"):
            c = state["cfg"]
            from .converter import find_ncmdump
            ncmdump = find_ncmdump(c.ncmdump, c.tools_dir)
            if not ncmdump:
                log("没找到转码器，先点「下载转码器」。")
                return
            state["watch_stop"].clear()
            set_busy(True)
            watch_btn.config(text="①监视转码：停止", bg="#c53030", state="normal")
            status.config(text="监视中（最小化没关系，关掉窗口才停）", fg="#2f855a")
            print("[watch] 盯着 %s ，成品放 %s" % (c.watch_dirs, c.output_dir))

            def loop():
                from .converter import is_stable, convert_one
                failed: dict[str, int] = {}
                try:
                    while not state["watch_stop"].is_set():
                        ok = skip = wait = fail = 0
                        ncms: list[Path] = []
                        for d in c.watch_dirs:
                            if Path(d).is_dir():
                                ncms += list(Path(d).glob("*.ncm"))
                        for f in ncms:
                            if state["watch_stop"].is_set():
                                break
                            base = f.stem
                            done = any((Path(c.output_dir) / (base + e)).exists()
                                       for e in (".flac", ".mp3", ".m4a"))
                            done = done or (Path(c.done_dir) /
                                            (base + ".done")).exists()
                            if done:
                                skip += 1
                                continue
                            if not is_stable(f, c.stable_seconds):
                                wait += 1
                                continue
                            if convert_one(ncmdump, f, c.output_dir, c.done_dir):
                                ok += 1
                                failed.pop(str(f), None)
                            else:
                                fail += 1
                                failed[str(f)] = failed.get(str(f), 0) + 1
                        print("[%s] 发现 %d 个 ncm｜新转 %d｜已完成 %d｜等写完 %d｜失败 %d"
                              % (time.strftime("%H:%M:%S"), len(ncms), ok,
                                 skip, wait, fail), flush=True)
                        if ncms and ok == 0 and skip == 0:
                            print("一个都没转出来？检查「音乐目录」是不是网易云的"
                                  "下载文件夹（客户端 设置→下载与缓存 里那个）。")
                        for _ in range(c.round_seconds * 5):
                            if state["watch_stop"].is_set():
                                break
                            time.sleep(0.2)
                finally:
                    print("[watch] 已停止")
                    if failed:
                        print("反复失败的文件（转不出来的看一眼）：")
                        for k in list(failed)[:10]:
                            print("  -", k)

            threading.Thread(target=loop, daemon=True).start()
        else:
            state["watch_stop"].set()
            watch_btn.config(text="①监视转码：开始", bg="#2f855a")
            set_busy(False)
            status.config(text="就绪", fg="#2f855a")

    watch_btn.config(command=toggle_watch)

    # ---------------- 向导 ----------------
    STEPS = [
        ("① 网易云把歌下载到哪个文件夹？",
         "打开网易云客户端 → 设置 → 下载与缓存，里面写的\"下载目录\"就是它。\n"
         "判断标准：这个文件夹里能看到 .ncm 文件，或者有 VipSongsDownload 子文件夹\n"
         "（有的话会自动一起监视，不用单独填）。"),
        ("② 转出来的歌（成品）放哪？",
         "转好的 flac/mp3 都进这个文件夹，它就是你的成品库，网盘自动备份也备份它。\n"
         "默认放在音乐目录旁边的 NetEase 文件夹，不确定就保持默认。"),
        ("③ 数据和报告放哪？",
         "品味报告 report.html、图表、中间数据都在这。默认放在 analysis 文件夹。"),
        ("④ API 服务装哪？（拉歌单数据用）",
         "分析要用的\"加入时间、听歌排行\"只存在网易的服务器上，需要一个本地小服务去取，\n"
         "装一次就行。保持默认，回主界面点「②安装/启动API」就能一键装好。"),
    ]

    def open_wizard(edit: bool = False):
        c = state["cfg"]
        base0 = str(c.watch_dirs[0]) if (edit and c and c.watch_dirs) else ""
        out0 = str(c.output_dir) if (edit and c) else ""
        ana0 = (str(Path(c.data_dir).parent) if (edit and c) else "")
        api0 = str(c.api_server_dir) if (edit and c) else ""

        wiz = tk.Toplevel(root)
        wiz.title("ncm-pipeline 设置向导")
        wiz.geometry("760x600")
        wiz.grab_set()
        tk.Label(wiz, fg="#555", anchor="w", justify="left",
                 text=("改一改现有设置：" if edit else
                       "第一次用，回答 4 个问题就能用了，不确定的保持默认。")).pack(
            fill="x", padx=12, pady=(10, 2))
        frame = tk.Frame(wiz)
        frame.pack(fill="both", expand=True, padx=12)
        entries: list[tk.Entry] = []

        for i, (title, hint) in enumerate(STEPS):
            tk.Label(frame, text=title, anchor="w",
                     font=("Microsoft YaHei", 10, "bold")).grid(
                row=i * 3, column=0, columnspan=2, sticky="w", pady=(10, 0))
            tk.Label(frame, text=hint, fg="#666", anchor="w",
                     justify="left", wraplength=680).grid(
                row=i * 3 + 1, column=0, columnspan=2, sticky="w")
            e = tk.Entry(frame)
            e.insert(0, (base0, out0, ana0, api0)[i])
            e.grid(row=i * 3 + 2, column=0, sticky="we", pady=2)

            def browse(e=e):
                d = filedialog.askdirectory(parent=wiz, title="选文件夹",
                                            initialdir=e.get() or ".")
                if d:
                    e.delete(0, "end")
                    e.insert(0, d.replace("/", "\\"))
            tk.Button(frame, text="浏览…", width=8,
                      command=browse).grid(row=i * 3 + 2, column=1, padx=4)
            entries.append(e)
        frame.columnconfigure(0, weight=1)

        def finish():
            base, out, ana, api = (e.get().strip() for e in entries)
            if not base:
                log("第①栏不能空：告诉我网易云把歌下到哪了。")
                return
            if not Path(base).exists():
                log("这个文件夹不存在：%s（先去建一个，或重新选）" % base)
                return
            Path(cfg_path).parent.mkdir(parents=True, exist_ok=True)
            base_p = Path(base)
            watch = [base_p]
            if (base_p / "VipSongsDownload").is_dir():
                watch.insert(0, base_p / "VipSongsDownload")
            Path(cfg_path).write_text(
                render_config(base_dir=base, output_dir=out or None,
                              analysis_dir=ana or None,
                              api_server_dir=api or None,
                              watch_dirs=watch),
                encoding="utf-8")
            state["cfg"] = load_config(Path(cfg_path))
            refresh_summary()
            log("配置已保存：%s" % cfg_path)
            log("成品会放在 %s；转码器、数据都归在 %s 下的 tools、analysis 里。"
                % (state["cfg"].output_dir, base))
            wiz.grab_release()
            wiz.destroy()

        btns = tk.Frame(wiz)
        btns.pack(fill="x", padx=12, pady=8)
        tk.Button(btns, text="完成", width=12, fg="white", bg="#2f855a",
                  relief="flat", command=finish).pack(side="right")
        tk.Button(btns, text="取消", width=8,
                  command=lambda: (wiz.grab_release(), wiz.destroy())
                  ).pack(side="right", padx=6)

    # ---------------- 启动 ----------------
    if load_cfg():
        refresh_summary()
    else:
        refresh_summary()
        log("第一次用？马上弹出向导，回答 4 个问题就能用了。")
        root.after(300, lambda: open_wizard(edit=False))

    def on_close():
        state["watch_stop"].set()
        try:
            if state["server_proc"]:
                state["server_proc"].terminate()
        except Exception:
            pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(200, drain_queue)
    root.mainloop()
    return 0
