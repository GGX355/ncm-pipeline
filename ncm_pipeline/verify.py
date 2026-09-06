"""mutagen 抽检成品：格式可识别 + 标题/歌手在 + 封面已嵌入。"""
from __future__ import annotations

import random
from pathlib import Path

import mutagen
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.id3 import ID3


def check_file(path: Path) -> tuple[bool, str]:
    m = mutagen.File(str(path), easy=True)
    if m is None:
        return False, "格式无法识别"
    title = (m.get("title") or [""])[0]
    artist = (m.get("artist") or [""])[0]
    album = (m.get("album") or [""])[0]
    cover = False
    if isinstance(m, FLAC):
        cover = len(m.pictures) > 0
    elif isinstance(m, MP3):
        try:
            cover = any(k.startswith("APIC") for k in ID3(str(path)).keys())
        except Exception:
            pass
    ok = bool(title and artist and cover)
    return ok, ("fmt=%s title=%r artist=%r album=%r cover=%s"
                % (type(m).__name__, title[:30], artist[:30], album[:25], cover))


def run_verify(output_dir: Path, n: int = 5, seed: int | None = None) -> bool:
    files = sorted(Path(output_dir).glob("*.flac")) + \
        sorted(Path(output_dir).glob("*.mp3"))
    if not files:
        print("成品目录为空: %s" % output_dir)
        return False
    rng = random.Random(seed)
    picks = rng.sample(files, min(n, len(files)))
    all_ok, passed = True, 0
    for p in picks:
        ok, msg = check_file(p)
        all_ok &= ok
        passed += ok
        print("%s | %s | %s" % ("PASS" if ok else "FAIL", msg, p.name))
    print("SPOTCHECK %s（%d/%d）" % ("OK" if all_ok else "HAS_FAILURES",
                                    passed, len(picks)))
    return all_ok
