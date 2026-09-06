"""配置加载：TOML → Config，字段带默认值，路径统一转 Path。"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib  # py311+
except ModuleNotFoundError:  # pragma: no cover
    print("ncm-pipeline 需要 Python >= 3.11（内置 tomllib）", file=sys.stderr)
    raise


@dataclass
class Config:
    watch_dirs: list[Path] = field(default_factory=list)
    output_dir: Path = Path("NetEase")
    done_dir: Path = Path("done")
    data_dir: Path = Path("analysis/data")
    charts_dir: Path = Path("analysis/charts")
    report_path: Path = Path("analysis/report.html")
    ncmdump: str = ""
    tools_dir: Path = Path("tools")
    api_base: str = "http://127.0.0.1:3000"
    api_server_dir: Path = Path("api-server")
    stable_seconds: int = 30
    round_seconds: int = 20
    duplicates_dir: Path = Path("NetEase/_duplicates")
    reference_dirs: list[Path] = field(default_factory=list)


DEFAULTS = {
    "watch_dirs": [], "output_dir": "NetEase", "done_dir": "done",
    "data_dir": "analysis/data", "charts_dir": "analysis/charts",
    "report_path": "analysis/report.html", "ncmdump": "",
    "tools_dir": "tools", "api_base": "http://127.0.0.1:3000",
    "api_server_dir": "api-server", "stable_seconds": 30,
    "round_seconds": 20, "duplicates_dir": "NetEase/_duplicates",
    "reference_dirs": [],
}


def default_config_path() -> Path:
    env = os.environ.get("NCM_PIPELINE_CONFIG")
    if env:
        return Path(env)
    for cand in (Path.cwd() / "config.toml",
                 Path.home() / ".ncm-pipeline" / "config.toml"):
        if cand.exists():
            return cand
    return Path.cwd() / "config.toml"


def load_config(path: Path | None = None) -> Config:
    p = Path(path) if path else default_config_path()
    raw: dict = {}
    if p.exists():
        with open(p, "rb") as f:
            raw = tomllib.load(f)
    merged = dict(DEFAULTS)
    for section, vals in raw.items():
        if isinstance(vals, dict):
            merged.update(vals)
        else:
            merged[section] = vals

    def _p(v) -> Path:
        return Path(str(v))

    cfg = Config(
        watch_dirs=[_p(x) for x in merged.get("watch_dirs") or []],
        output_dir=_p(merged["output_dir"]),
        done_dir=_p(merged["done_dir"]),
        data_dir=_p(merged["data_dir"]),
        charts_dir=_p(merged["charts_dir"]),
        report_path=_p(merged["report_path"]),
        ncmdump=str(merged.get("ncmdump") or ""),
        tools_dir=_p(merged["tools_dir"]),
        api_base=str(merged.get("api_base") or "http://127.0.0.1:3000"),
        api_server_dir=_p(merged["api_server_dir"]),
        stable_seconds=int(merged.get("stable_seconds", 30)),
        round_seconds=int(merged.get("round_seconds", 20)),
        duplicates_dir=_p(merged["duplicates_dir"]),
        reference_dirs=[_p(x) for x in merged.get("reference_dirs") or []],
    )
    for d in [cfg.output_dir, cfg.done_dir, cfg.data_dir, cfg.charts_dir,
              cfg.duplicates_dir]:
        d.mkdir(parents=True, exist_ok=True)
    return cfg


CONFIG_TEMPLATE = '''# ncm-pipeline 配置（由 `ncm-pipeline init` 生成，请按需修改路径）
[paths]
watch_dirs = ["D:/CloudMusic/VipSongsDownload", "D:/CloudMusic"]
output_dir = "D:/CloudMusic/NetEase"
done_dir = "D:/CloudMusic/tools/done"
data_dir = "D:/CloudMusic/analysis/data"
charts_dir = "D:/CloudMusic/analysis/charts"
report_path = "D:/CloudMusic/analysis/report.html"

[convert]
stable_seconds = 30
round_seconds = 20

[tools]
ncmdump = ""
tools_dir = "D:/CloudMusic/tools"

[api]
base = "http://127.0.0.1:3000"
server_dir = "D:/CloudMusic/tools/api-server"

[dedup]
duplicates_dir = "D:/CloudMusic/NetEase/_duplicates"
reference_dirs = ["D:/CloudMusic"]
'''

