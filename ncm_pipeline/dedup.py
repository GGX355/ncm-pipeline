"""成品库去重：按「歌手 - 歌名」归一化匹配，同 key 保留质量最高的一份。

修坑（实测）：
- 客户端命名文件时会把 Windows 禁用字符（: " / 等）删掉，且歌手数可能与元数据
  不一致、歌名里可能自带 " - " → 解析文件名时对每个 " - " 切分位置都注册候选键，
  匹配时用「去标点小写」归一化
- 参考目录（客户端直下）里的文件只参与判断、绝不移动；库内文件质量不高于参考
  文件时移入库内 _duplicates
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from mutagen import File as MFile

QUALITY = {".flac": 2, ".wav": 2, ".m4a": 1, ".mp3": 0}
AUD = tuple(QUALITY)


def norm(s: str) -> str:
    if not s:
        return ""
    return "".join(re.findall(
        r"[\w\u4e00-\u9fff\u3040-\u30ff\u31f0-\u31ff\uac00-\ud7af]+", s.lower()))


def tags(path: Path) -> tuple[str, str] | None:
    artist = title = None
    try:
        m = MFile(str(path), easy=True)
        if m:
            artist = (m.get("artist") or [None])[0]
            title = (m.get("title") or [None])[0]
    except Exception:
        pass
    if not artist or not title:
        base = path.stem
        if " - " not in base:
            return None
        a, t = base.split(" - ", 1)
        artist, title = artist or a, title or t
    return (str(artist).strip(), str(title).strip())


def add_key(indexes: dict, artist: str, title: str, path: Path) -> None:
    na, nt = norm(artist), norm(title)
    if not na or not nt:
        return
    indexes["full"].setdefault((na, nt), []).append(path)
    indexes["first"].setdefault((na.split(",")[0], nt), []).append(path)
    indexes["title"].setdefault(nt, []).append(path)


def scan_dir(dirpath: Path, indexes: dict) -> None:
    if not Path(dirpath).is_dir():
        return
    for f in sorted(Path(dirpath).iterdir()):
        if not f.is_file() or f.suffix.lower() not in QUALITY:
            continue
        t = tags(f)
        if not t:
            continue
        # 歌名里可能自带 " - "：每个切分位置都注册
        parts = (t[0] + " - " + t[1]).split(" - ")
        for k in range(1, len(parts)):
            add_key(indexes, " - ".join(parts[:k]), " - ".join(parts[k:]), f)


def best(files: list[Path]) -> Path:
    return sorted(files, key=lambda p: (QUALITY.get(p.suffix.lower(), 0),
                                        p.stat().st_size), reverse=True)[0]


def run_dedup(library: Path, duplicates_dir: Path,
              reference_dirs: list[Path], dry_run: bool = False,
              report_path: Path | None = None) -> dict:
    lib_idx: dict = {"full": {}, "first": {}, "title": {}}
    scan_dir(library, lib_idx)
    moved, notes = [], []
    # 1) 库内重复
    inlib_groups = 0
    for files in lib_idx["full"].values():
        if len(files) > 1:
            inlib_groups += 1
            keep = best(files)
            for p in files:
                if p != keep:
                    moved.append({"file": p.name, "from": "library",
                                  "reason": "库内重复(歌手-歌名)"})
                    if not dry_run:
                        _move(p, duplicates_dir)
    # 2) 与参考目录重复：只在「完整歌手串」和「首歌手」两级键上移动，
    #    纯标题级键可能撞名（不同歌手的同名曲），不做移动依据
    ref_idx: dict = {"full": {}, "first": {}, "title": {}}
    for d in reference_dirs:
        scan_dir(d, ref_idx)
    cross_groups = 0
    for key_group in ("full", "first"):
        for key, ref_files in ref_idx[key_group].items():
            lib_files = lib_idx[key_group].get(key) or []
            lib_files = [p for p in lib_files if p.exists()]
            if not ref_files or not lib_files:
                continue
            cross_groups += 1
            rb, nb = best(ref_files), best(lib_files)
            rq = QUALITY.get(rb.suffix.lower(), 0)
            nq = QUALITY.get(nb.suffix.lower(), 0)
            if nq <= rq:
                moved.append({"file": nb.name, "from": "library",
                              "reason": "与参考目录重复，且质量不更高"})
                if not dry_run:
                    _move(nb, duplicates_dir)
            else:
                notes.append("保留库内高质量文件 %s（参考目录同名文件质量较低，未动）"
                             % nb.name)
    # 标题级匹配有合并误伤可能：只做报告，不据此移动文件
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dry_run": dry_run,
        "inlib_dup_groups": inlib_groups,
        "cross_dup_groups": cross_groups,
        "moved": moved,
        "notes": notes,
    }
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    print("dedup %s：库内重复组 %d，跨目录重复组 %d，移动 %d 个文件"
          % ("(dry-run)" if dry_run else "", inlib_groups, cross_groups,
             len(moved)))
    for n in notes[:10]:
        print("  note:", n)
    return report


def _move(path: Path, duplicates_dir: Path) -> None:
    duplicates_dir.mkdir(parents=True, exist_ok=True)
    dest = duplicates_dir / path.name
    i = 1
    while dest.exists():
        dest = duplicates_dir / ("%s_(%d)%s" % (path.stem, i, path.suffix))
        i += 1
    shutil.move(str(path), str(dest))
