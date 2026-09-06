"""ncmdump 定位/下载 + ncm→flac/mp3 转码。

修坑：
- 输出是否已存在一律用 os.path.isfile（字面匹配）；PowerShell Test-Path 会把
  文件名里的 [ ] 当通配符，导致重复转换/漏判
- 转码成功写 .done 标记，双保险幂等
- 源文件要「写完再转」：等待期内大小不变才算稳定
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

import requests

NCMDUMP_VERSION = "1.5.1"
NCMDUMP_URL = ("https://github.com/taurusxin/ncmdump/releases/download/"
               "%s/ncmdump-1.5.1-windows-amd64.zip" % NCMDUMP_VERSION)


def find_ncmdump(configured: str, tools_dir: Path) -> Path | None:
    if configured:
        p = Path(configured)
        if p.is_file():
            return p
    exe = "ncmdump.exe"
    cand = [Path(tools_dir) / exe, Path.cwd() / exe]
    for p in cand:
        if p.is_file():
            return p
    which = shutil.which("ncmdump")
    return Path(which) if which else None


def download_ncmdump(tools_dir: Path) -> Path:
    """从 GitHub Releases 下载官方 ncmdump（Windows amd64）到 tools_dir。"""
    tools_dir.mkdir(parents=True, exist_ok=True)
    dest = Path(tools_dir) / "ncmdump.exe"
    zip_path = Path(tools_dir) / "ncmdump.zip"
    print("下载 ncmdump %s …" % NCMDUMP_VERSION, flush=True)
    with requests.get(NCMDUMP_URL, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(zip_path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(tools_dir)
    zip_path.unlink(missing_ok=True)
    if not dest.exists():
        cand = list(Path(tools_dir).glob("**/ncmdump.exe"))
        if not cand:
            raise RuntimeError("解压后未找到 ncmdump.exe")
        shutil.move(str(cand[0]), dest)
    print("ncmdump 就绪: %s" % dest, flush=True)
    return dest


def output_exists(out_dir: Path, base: str,
                  exts: tuple[str, ...] = (".flac", ".mp3", ".m4a")) -> bool:
    return any((Path(out_dir) / (base + e)).exists() for e in exts)


def is_stable(path: Path, stable_seconds: int) -> bool:
    """文件在 stable_seconds 秒内大小不变才返回 True。"""
    try:
        s1 = path.stat().st_size
    except OSError:
        return False
    if time.time() - path.stat().st_mtime < stable_seconds:
        time.sleep(stable_seconds)
        try:
            return path.stat().st_size == s1
        except OSError:
            return False
    return True


OK_EXTS = (".flac", ".mp3", ".m4a")


def convert_one(ncmdump: Path, ncm_path: Path, out_dir: Path,
                done_dir: Path | None = None) -> bool:
    """先转进临时目录，成功后再移动到成品目录——中途被杀也不会留下半截成品。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / ("_tmp_%d" % os.getpid())
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        try:
            proc = subprocess.run(
                [str(ncmdump), str(ncm_path), "-o", str(tmp)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=900)
        except subprocess.TimeoutExpired:
            return False
        if proc.returncode != 0:
            return False
        moved = False
        for f in list(tmp.iterdir()):
            if f.suffix.lower() not in OK_EXTS:
                continue
            dest = out_dir / f.name
            if dest.exists():
                try:
                    dest.unlink()
                except OSError:
                    continue
            try:
                f.replace(dest)
                moved = True
            except OSError:
                continue
        return moved
    finally:
        for f in tmp.iterdir():
            try:
                f.unlink()
            except OSError:
                pass
        try:
            tmp.rmdir()
        except OSError:
            pass
        if done_dir and moved_check(out_dir, Path(ncm_path).stem):
            Path(done_dir).mkdir(parents=True, exist_ok=True)
            (Path(done_dir) / (Path(ncm_path).stem + ".done")).write_text(
                time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")


def moved_check(out_dir: Path, base: str) -> bool:
    return any((out_dir / (base + e)).exists() for e in OK_EXTS)


def convert_pending(cfg) -> int:
    """扫一遍所有 watch_dirs，转换未处理的 ncm。返回本次成功数。"""
    ncmdump = find_ncmdump(cfg.ncmdump, cfg.tools_dir)
    if not ncmdump:
        raise RuntimeError("未找到 ncmdump，先运行 `ncm-pipeline doctor --get-tools`")
    ok = 0
    seen: set[Path] = set()
    for d in cfg.watch_dirs:
        if not Path(d).is_dir():
            continue
        for f in sorted(Path(d).glob("*.ncm")):
            if f in seen:
                continue
            seen.add(f)
            base = f.stem
            if output_exists(cfg.output_dir, base):
                continue
            if (Path(cfg.done_dir) / (base + ".done")).exists():
                continue
            if not is_stable(f, cfg.stable_seconds):
                continue
            if convert_one(ncmdump, f, cfg.output_dir, cfg.done_dir):
                ok += 1
            else:
                print("[失败] %s" % f.name, flush=True)
    return ok
