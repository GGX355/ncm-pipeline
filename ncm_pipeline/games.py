"""把成品库喂给游戏。

- GTA5 自电台：官方机制，把 mp3/m4a/wma 放进 文档\\Rockstar Games\\GTA V\\User Music，
  游戏里设置→声音→执行快速扫描即可。flac 不被支持，先用 ffmpeg 转成 mp3 320k。
- 地平线 6：游戏不支持自定义电台，社区做法是替换原电台的歌曲槽位
  （Nexus 的 FH6 Radio Tools / Forza Radio Mod Tool 这类 GUI 工具）。
  槽位映射和进歌点必须人工核对，所以这里只做预处理：批量转成 mp3 320k、
  规范命名、输出独立文件夹并附操作指引，最后一步交给那些工具。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from mutagen import File as MFile

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
GTA_EXTS = (".mp3", ".m4a", ".wma")       # GTA5 自电台直接认
LIB_EXTS = (".flac", ".mp3", ".m4a")      # 成品库里的格式
FH_GUIDE = """地平线 6 歌曲包 —— 使用说明
====================================

这个文件夹里的 mp3（320k）是按 mod 工具要求准备好的原料。

下一步（槽位替换，需要手动核对，10~30 分钟）：
  1. 下载 Nexus 的「FH6 Radio Tools」：
     https://www.nexusmods.com/forzahorizon6/mods/19
     （或用 GitHub 的 Forza Radio Mod Tool，目前只支持 FH4/5）
  2. 按它的流程：选本文件夹作为音乐来源 → 指向 Fmod Bank Tools →
     挑一个愿意顶替的原电台（比如 Horizon Pulse）→ 把槽位映射到这些 mp3
  3. 每首歌记得核对进歌点（cue point），不然开头会缺一段
  4. 游戏更新可能重置电台文件，重做一遍即可

为什么不能全自动：槽位数量有限、进歌点必须人耳核对、游戏更新会冲掉，
全自动替换几百首出了问题很难排查，所以最后一步留给成熟工具。
"""


def sanitize(text: str) -> str:
    """Windows 文件名禁用字符全部替换成空格，压缩空白。"""
    s = re.sub(r'[\\/:*?"<>|]+', " ", text or "")
    return re.sub(r"\s+", " ", s).strip()[:120]


def song_name(path: Path) -> tuple[str, str]:
    """从标签里拿 歌手/歌名，退化用文件名拆。"""
    artist = title = None
    try:
        m = MFile(str(path), easy=True)
        if m:
            artist = (m.get("artist") or [None])[0]
            title = (m.get("title") or [None])[0]
    except Exception:
        pass
    if not artist or not title:
        stem = path.stem
        if " - " in stem:
            a, t = stem.split(" - ", 1)
            artist, title = artist or a, title or t
        else:
            artist, title = artist or "未知歌手", title or stem
    return str(artist).strip(), str(title).strip()


def ensure_ffmpeg(tools_dir: Path) -> Path:
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)
    cand = Path(tools_dir) / "ffmpeg.exe"
    if cand.is_file():
        return cand
    raise RuntimeError(
        "没找到 ffmpeg（转 mp3 用）。装一个即可：winget install Gyan.FFmpeg，"
        "或去 https://www.gyan.dev/ffmpeg/builds/ 下载后把 ffmpeg.exe 放到 "
        + str(tools_dir))


def iter_library(dirs):
    for d in dirs:
        if Path(d).is_dir():
            for f in sorted(Path(d).iterdir()):
                if f.is_file() and f.suffix.lower() in LIB_EXTS:
                    yield f


def transcode_to_mp3(ffmpeg: Path, src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(
        [str(ffmpeg), "-y", "-i", str(src), "-codec:a", "libmp3lame",
         "-b:a", "320k", "-vn", str(dest)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW, timeout=600)
    if p.returncode != 0 or not dest.exists():
        raise RuntimeError("ffmpeg 转码失败: %s" % src.name)


def find_gta_music_dir() -> Path:
    """文档目录可能被 OneDrive 重定向，优先读注册表。"""
    cands = []
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion"
                                r"\Explorer\User Shell Folders") as k:
                val, _ = winreg.QueryValueEx(k, "Personal")
                cands.append(Path(os_expand(val)))
        except Exception:
            pass
    cands.append(Path.home() / "Documents")
    for base in cands:
        p = base / "Rockstar Games" / "GTA V" / "User Music"
        if p.parent.parent.parent.exists():
            return p
    return cands[0] / "Rockstar Games" / "GTA V" / "User Music"


def os_expand(s: str) -> str:
    import os
    return os.path.expandvars(s)


def sync_gta(cfg, limit: int | None = None, log=print) -> dict:
    """增量同步成品库到 GTA5 自电台目录。"""
    ffmpeg = ensure_ffmpeg(cfg.tools_dir)
    dest_dir = find_gta_music_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    log("GTA5 自电台目录：%s" % dest_dir)
    copied = converted = skipped = failed = 0
    for i, f in enumerate(iter_library(cfg.watch_dirs)):
        if limit and i >= limit:
            break
        artist, title = song_name(f)
        name = sanitize("%s - %s" % (artist, title))
        if f.suffix.lower() in GTA_EXTS:
            dest = dest_dir / (name + f.suffix.lower())
            if dest.exists():
                skipped += 1
                continue
            try:
                shutil.copy2(f, dest)
                copied += 1
            except OSError as e:
                failed += 1
                log("  复制失败 %s: %s" % (f.name, e))
        else:  # flac -> mp3 320k
            dest = dest_dir / (name + ".mp3")
            if dest.exists():
                skipped += 1
                continue
            try:
                transcode_to_mp3(ffmpeg, f, dest)
                converted += 1
            except Exception as e:
                failed += 1
                log("  转码失败 %s: %s" % (f.name, e))
        if (copied + converted) % 25 == 0 and copied + converted > 0:
            log("  进度：已同步 %d（复制 %d / 转码 %d）"
                % (copied + converted, copied, converted))
    log("GTA5 同步完成：新增 %d（复制 %d / flac转码 %d），已存在跳过 %d，失败 %d"
        % (copied + converted, copied, converted, skipped, failed))
    log("进游戏：设置 → 声音 → 执行快速扫描，然后电台选 Self Radio。")
    return {"copied": copied, "converted": converted, "skipped": skipped,
            "failed": failed, "dest": str(dest_dir)}


def prep_horizon(cfg, limit: int | None = None, log=print) -> dict:
    """把成品库整理成地平线 6 mod 工具能直接用的 mp3 歌曲包。"""
    ffmpeg = ensure_ffmpeg(cfg.tools_dir)
    out_dir = Path(cfg.output_dir).parent / "Horizon6歌曲包"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "使用说明.txt").write_text(FH_GUIDE, encoding="utf-8")
    log("歌曲包输出：%s" % out_dir)
    copied = converted = skipped = failed = 0
    for i, f in enumerate(iter_library(cfg.watch_dirs)):
        if limit and i >= limit:
            break
        artist, title = song_name(f)
        dest = out_dir / (sanitize("%s - %s" % (artist, title)) + ".mp3")
        if dest.exists():
            skipped += 1
            continue
        try:
            if f.suffix.lower() == ".mp3":
                shutil.copy2(f, dest)
                copied += 1
            else:
                transcode_to_mp3(ffmpeg, f, dest)
                converted += 1
        except Exception as e:
            failed += 1
            log("  失败 %s: %s" % (f.name, e))
        if (copied + converted) % 25 == 0 and copied + converted > 0:
            log("  进度：已准备 %d（复制 %d / 转码 %d）"
                % (copied + converted, copied, converted))
    log("地平线歌曲包完成：新增 %d（复制 %d / 转码 %d），跳过 %d，失败 %d"
        % (copied + converted, copied, converted, skipped, failed))
    log("看 %s/使用说明.txt 做最后一步槽位替换（要人工核对，见说明）。"
        % out_dir)
    return {"copied": copied, "converted": converted, "skipped": skipped,
            "failed": failed, "dest": str(out_dir)}
