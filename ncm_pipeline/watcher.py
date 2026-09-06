"""常驻监视：轮询 watch_dirs，新 ncm 等稳定后自动转码。

幂等策略（实测踩坑后定稿）：
- 成品存在（os.path.isfile 字面匹配，绝不用会吃掉 [ ] 的通配符判断）→ 跳过
- .done 标记存在 → 跳过
- 文件大小在 stable_seconds 内无变化才转，防止下到一半
每轮打印一行汇总，Ctrl+C 退出时打印仍失败清单。
"""
from __future__ import annotations

import time
from pathlib import Path

from .converter import (convert_one, find_ncmdump, is_stable,
                        output_exists)


def watch(cfg, once: bool = False, quiet_exit_rounds: int = 0) -> None:
    ncmdump = find_ncmdump(cfg.ncmdump, cfg.tools_dir)
    if not ncmdump:
        raise RuntimeError("未找到 ncmdump，先运行 `ncm-pipeline doctor --get-tools`")
    print("[watch] %s -> %s" % (cfg.watch_dirs, cfg.output_dir), flush=True)
    print("[watch] ncmdump: %s   Ctrl+C 退出" % ncmdump, flush=True)
    failed: dict[str, int] = {}
    quiet = 0
    try:
        while True:
            ok = skip = wait = fail = 0
            ncms: list[Path] = []
            for d in cfg.watch_dirs:
                if Path(d).is_dir():
                    ncms += list(Path(d).glob("*.ncm"))
            for f in ncms:
                base = f.stem
                if output_exists(cfg.output_dir, base):
                    skip += 1
                    continue
                if (Path(cfg.done_dir) / (base + ".done")).exists():
                    skip += 1
                    continue
                if not is_stable(f, cfg.stable_seconds):
                    wait += 1
                    continue
                if convert_one(ncmdump, f, cfg.output_dir, cfg.done_dir):
                    ok += 1
                    failed.pop(str(f), None)
                else:
                    fail += 1
                    failed[str(f)] = failed.get(str(f), 0) + 1
            print("[%s] ncm=%d converted=%d skip=%d wait=%d fail=%d still_failed=%d"
                  % (time.strftime("%H:%M:%S"), len(ncms), ok, skip, wait,
                     fail, len(failed)), flush=True)
            if once:
                break
            if (quiet_exit_rounds > 0 and ok == 0 and wait == 0 and fail == 0):
                quiet += 1
                if quiet >= quiet_exit_rounds:
                    print("[watch] 连续 %d 轮无新文件，退出" % quiet, flush=True)
                    break
            else:
                quiet = 0
            time.sleep(cfg.round_seconds)
    except KeyboardInterrupt:
        print("[watch] 手动退出", flush=True)
    if failed:
        print("[watch] 仍失败的文件（下轮会自动重试；持续失败请检查文件）：", flush=True)
        for k in failed:
            print("  -", k, flush=True)
