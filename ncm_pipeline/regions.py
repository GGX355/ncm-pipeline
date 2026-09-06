"""语种/地区推断：歌手名+歌名的文字系统启发式 + 全汉字日语歌手白名单。

规则顺序：谚文→韩语；假名→日语；汉字歌手→查白名单否则华语；标题汉字→华语；
泰/梵/西里尔/阿拉伯→其他；其余→欧美。
无法判定的全汉字名歌手会收集到 unknown_region.json，请使用者人工确认。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

REGIONS = ["华语", "欧美", "日语", "韩语", "其他"]
RCOLOR = {"华语": "#e05c5c", "欧美": "#4f8fd9", "日语": "#e8a33d",
          "韩语": "#9b6fd9", "其他": "#9aa5ab"}

RE_KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff\u31f0-\u31ff]")
RE_HANGUL = re.compile(r"[\uac00-\ud7af\u1100-\u11ff]")
RE_HAN = re.compile(r"[\u4e00-\u9fff]")
RE_OTHER = re.compile(r"[\u0e00-\u0e7f\u0900-\u097f\u0400-\u04ff\u0600-\u06ff]")

# 全汉字名、脚本无法判定的日语歌手（欢迎补充；拿不准的会进 unknown_region.json）
JP_KANJI_ARTISTS = {
    "米津玄師", "中島美嘉", "尾崎豊", "福山雅治", "桑田佳祐", "平井堅", "嵐",
    "浜田省吾", "沢田研二", "矢沢永吉", "美空ひばり", "円広志", "中島美雪",
    "竹内まりや", "松任谷由実", "杏里", "北島三郎", "五木ひろし", "坂本九",
    "ちあきなおみ", "由紀さおり", "薬師丸ひろ子", "原由子", "今井美樹", "ゆず",
    "平沢進", "戸川純", "灰野敬二", "中山美穂", "小泉今日子", "工藤静香",
    "鈴木慶一", "大滝詠一", "山下達郎", "吉田拓郎", "井上陽水", "長渕剛",
    "玉置浩二", "徳永英明", "稲垣潤一", "小田和正", "尾崎亜美", "来生たかお",
    "安全地帯", "チェッカーズ", "澤野弘之", "瑞葵",
}


def region_of(artists: list[str], title: str) -> tuple[str, str | None]:
    joined = "".join(artists)
    if RE_HANGUL.search(joined) or RE_HANGUL.search(title or ""):
        return "韩语", None
    if RE_KANA.search(joined) or RE_KANA.search(title or ""):
        return "日语", None
    han_names = [a for a in artists if RE_HAN.search(a)]
    if han_names:
        # 括号后缀先去掉再比对（瑞葵(mizuki) -> 瑞葵）
        stripped = [re.sub(r"[（(].*", "", a).strip() for a in han_names]
        if any(a in JP_KANJI_ARTISTS for a in stripped):
            return "日语", None
        return "华语", han_names[0]
    if RE_HAN.search(title or ""):
        return "华语", None
    if RE_OTHER.search(joined + (title or "")):
        return "其他", None
    return "欧美", None


def save_unknown(unsure: Counter, data_dir: Path) -> None:
    (Path(data_dir) / "unknown_region.json").write_text(
        json.dumps([{"artist": a, "songs": c} for a, c in unsure.most_common()],
                   ensure_ascii=False, indent=1),
        encoding="utf-8")
