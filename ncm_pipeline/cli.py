"""命令行入口：init / doctor / serve / login / fetch / analyze / watch / convert / verify / dedup"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from pathlib import Path

from . import __version__
from .config import CONFIG_TEMPLATE, default_config_path, load_config


def _cfg(args):
    return load_config(getattr(args, "config", None))


def cmd_init(args) -> int:
    p = Path(args.config) if args.config else Path.cwd() / "config.toml"
    if p.exists() and not args.force:
        print("已存在 %s（--force 覆盖）" % p)
        return 1
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    print("已生成配置 %s，请修改路径后运行 `ncm-pipeline doctor`" % p)
    return 0


def cmd_doctor(args) -> int:
    ok = True
    v = sys.version_info
    print("python: %d.%d.%d %s" % (v[0], v[1], v[2],
                                   "OK" if v >= (3, 11) else "需 >=3.11"))
    ok &= v >= (3, 11)
    for mod in ("requests", "mutagen", "pandas", "matplotlib"):
        spec = importlib.util.find_spec(mod)
        print("依赖 %-11s %s" % (mod, "OK" if spec else "缺失"))
        ok &= spec is not None
    cfg = _cfg(args)
    from .converter import find_ncmdump
    exe = find_ncmdump(cfg.ncmdump, cfg.tools_dir)
    print("ncmdump:", exe or "未找到（--get-tools 自动下载）")
    ok &= exe is not None
    import shutil
    node = shutil.which("node")
    print("node:", node or "未安装（API 数据功能需要）")
    from .api import server_reachable
    alive = server_reachable(cfg.api_base)
    print("API 服务 %s: %s" % (cfg.api_base, "可达" if alive else "未启动（`ncm-pipeline serve`）"))
    if args.get_tools and not exe:
        from .converter import download_ncmdump
        download_ncmdump(cfg.tools_dir)
    data_files = ["playlists.json", "trackids.json", "songs.json",
                  "playrecord.json", "profile.json"]
    have = [f for f in data_files if (Path(cfg.data_dir) / f).exists()]
    print("数据文件: %d/%d（%s）" % (len(have), len(data_files),
                                    cfg.data_dir if have else "尚未 fetch"))
    try:
        import matplotlib.font_manager as fm
        names = {f.name for f in fm.fontManager.ttflist}
        cjk = [n for n in ("Microsoft YaHei", "PingFang SC",
                           "Noto Sans CJK SC", "SimHei") if n in names]
        print("中文字体:", ", ".join(cjk) if cjk else "未找到（图表中文会变方框）")
        ok &= bool(cjk)
    except Exception:
        pass
    print("DOCTOR", "OK" if ok else "HAS_ISSUES")
    return 0 if ok else 1


def cmd_serve(args) -> int:
    from .api import server_reachable, start_server
    cfg = _cfg(args)
    if server_reachable(cfg.api_base):
        print("API 服务已在运行: %s" % cfg.api_base)
        return 0
    p = start_server(cfg.api_server_dir, cfg.api_base)
    if p is None:
        print("未找到服务端（%s）。安装：\n"
              "  mkdir -p %s && cd %s && npm init -y && "
              "npm install @neteaseapireborn/api"
              % (cfg.api_server_dir, cfg.api_server_dir, cfg.api_server_dir))
        return 1
    print("API 服务启动中: %s（日志 %s/server.log，Ctrl+C 停止）"
          % (cfg.api_base, cfg.api_server_dir))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        p.terminate()
    return 0


def cmd_login(args) -> int:
    from .fetch import login
    cfg = _cfg(args)
    p = login(cfg, open_image=not args.no_open)
    print("cookie 已保存: %s" % p)
    return 0


def cmd_fetch(args) -> int:
    from .fetch import run_fetch
    run_fetch(_cfg(args))
    return 0


def cmd_analyze(args) -> int:
    from .analyze import run_analyze
    run_analyze(_cfg(args))
    print("报告: %s" % _cfg(args).report_path)
    return 0


def cmd_watch(args) -> int:
    from .watcher import watch
    watch(_cfg(args), once=args.once,
          quiet_exit_rounds=args.quiet_exit_rounds)
    return 0


def cmd_convert(args) -> int:
    from .converter import convert_pending
    n = convert_pending(_cfg(args))
    print("本次转换 %d 个" % n)
    return 0


def cmd_verify(args) -> int:
    from .verify import run_verify
    ok = run_verify(_cfg(args).output_dir, n=args.n)
    return 0 if ok else 1


def cmd_gui(args) -> int:
    from .gui import run as run_gui
    return run_gui()


def cmd_dedup(args) -> int:
    from .dedup import run_dedup
    cfg = _cfg(args)
    run_dedup(cfg.output_dir, cfg.duplicates_dir, cfg.reference_dirs,
              dry_run=args.dry_run,
              report_path=Path(cfg.data_dir) / "dedup_report.json")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:  # 双击 exe / 无参数直接开图形界面
        from .gui import run as run_gui
        return run_gui()
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", help="config.toml 路径")
    # 子命令里的 -c 用 SUPPRESS 默认值：避免子命名空间回拷覆盖主解析器读到的值
    common_sub = argparse.ArgumentParser(add_help=False)
    common_sub.add_argument("-c", "--config", default=argparse.SUPPRESS,
                            help=argparse.SUPPRESS)
    ap = argparse.ArgumentParser(
        prog="ncm-pipeline", parents=[common],
        description="网易云个人音乐库小工具：ncm 转码 / 去重 / 歌单数据分析")
    ap.add_argument("-V", "--version", action="version",
                    version="ncm-pipeline %s" % __version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="生成 config.toml", parents=[common_sub])
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("doctor", help="环境自检", parents=[common_sub])
    p.add_argument("--get-tools", action="store_true",
                   help="ncmdump 缺失时自动从 GitHub Releases 下载")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("serve", help="启动本机 API 服务（需已 npm install）", parents=[common_sub])
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("login", help="扫码登录，cookie 存本地", parents=[common_sub])
    p.add_argument("--no-open", action="store_true",
                   help="不自动打开二维码图片")
    p.set_defaults(fn=cmd_login)

    p = sub.add_parser("fetch", help="拉取歌单/听歌数据存 JSON", parents=[common_sub])
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("analyze", help="生成图表与 report.html", parents=[common_sub])
    p.set_defaults(fn=cmd_analyze)

    p = sub.add_parser("watch", help="常驻监视 ncm 并自动转码", parents=[common_sub])
    p.add_argument("--once", action="store_true", help="扫一遍就退出")
    p.add_argument("--quiet-exit", type=int, default=0, dest="quiet_exit_rounds",
                   help="连续 N 轮无新文件后自动退出（0=常驻）")
    p.set_defaults(fn=cmd_watch)

    p = sub.add_parser("convert", help="单次扫描转换所有未处理的 ncm", parents=[common_sub])
    p.set_defaults(fn=cmd_convert)

    p = sub.add_parser("verify", help="mutagen 抽检成品", parents=[common_sub])
    p.add_argument("-n", type=int, default=5)
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("gui", help="打开图形界面")
    p.set_defaults(fn=cmd_gui)

    p = sub.add_parser("dedup", help="按歌手-歌名去重", parents=[common_sub])
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_dedup)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
