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
    "stable_seconds": 30,
    "round_seconds": 20, "duplicates_dir": "NetEase/_duplicates",
    "reference_dirs": [],
}


def default_config_path() -> Path:
    env = os.environ.get("NCM_PIPELINE_CONFIG")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):  # 双击 exe：配置固定放 exe 旁边
        return Path(sys.executable).parent / "config.toml"
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
        api_server_dir=_p(merged.get("server_dir")
                          or merged.get("api_server_dir") or "api-server"),
        stable_seconds=int(merged.get("stable_seconds", 30)),
        round_seconds=int(merged.get("round_seconds", 20)),
        duplicates_dir=_p(merged["duplicates_dir"]),
        reference_dirs=[_p(x) for x in merged.get("reference_dirs") or []],
    )
    for d in [cfg.output_dir, cfg.done_dir, cfg.data_dir, cfg.charts_dir,
              cfg.duplicates_dir]:
        d.mkdir(parents=True, exist_ok=True)
    return cfg


def render_config(*, base_dir, output_dir=None, analysis_dir=None,
                  api_server_dir=None, watch_dirs=None) -> str:
    """由向导的几个选择渲染出完整 config.toml（含注释）。"""
    base = Path(str(base_dir))
    output_dir = Path(str(output_dir)) if output_dir else base / "NetEase"
    analysis_dir = Path(str(analysis_dir)) if analysis_dir else base / "analysis"
    api_server_dir = Path(str(api_server_dir)) if api_server_dir else base / "tools" / "api-server"
    watch_dirs = watch_dirs or [base]
    tools_dir = base / "tools"
    done_dir = tools_dir / "done"
    duplicates_dir = output_dir / "_duplicates"
    return f"""# ncm-pipeline 配置（图形界面会自动维护，一般不用手改）

[paths]
# 网易云的下载目录（客户端里会在这里出现 .ncm 文件）
watch_dirs = {[str(p).replace(chr(92), '/') for p in watch_dirs]}
# 转好的歌（成品库）放这里，网盘备份也备份它
output_dir = "{str(output_dir).replace(chr(92), '/')}"
done_dir = "{str(done_dir).replace(chr(92), '/')}"
# 数据、图表和报告
data_dir = "{str(analysis_dir).replace(chr(92), '/') + '/data'}"
charts_dir = "{str(analysis_dir).replace(chr(92), '/') + '/charts'}"
report_path = "{str(analysis_dir).replace(chr(92), '/') + '/report.html'}"

[convert]
stable_seconds = 30
round_seconds = 20

[tools]
ncmdump = ""
tools_dir = "{str(tools_dir).replace(chr(92), '/')}"

[api]
base = "http://127.0.0.1:3000"
server_dir = "{str(api_server_dir).replace(chr(92), '/')}"

[dedup]
duplicates_dir = "{str(duplicates_dir).replace(chr(92), '/')}"
reference_dirs = {[str(base).replace(chr(92), '/')]}
"""
