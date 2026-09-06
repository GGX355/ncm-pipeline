"""品味统计与趋势分析。

主视角自动选择：
- 若某一天加入的歌 > 30%（时间戳被「删歌单重加」等批量操作污染）→ 以
  「收藏批次」为横轴（批次1=最早收藏 … 10=最近，依据最大歌单的 trackIds
  数组顺序：越靠前越新收藏），日历年视图降级为附录；
- 否则以日历年为主视角。
字体跨平台：Microsoft YaHei / PingFang SC / Noto Sans CJK / SimHei；负号正常。
"""
from __future__ import annotations

import html
import json
import time
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from .regions import REGIONS, RCOLOR, region_of, save_unknown

plt.rcParams["font.sans-serif"] = [
    "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "SimHei",
    "WenQuanYi Zen Hei", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

BULK_SHARE_THRESHOLD = 0.30


def _load(data_dir: Path, name: str):
    return json.loads((Path(data_dir) / name).read_text(encoding="utf-8"))


def _style(ax):
    ax.spines[["top", "right"]].set_visible(False)


def _fmt_dur(ms) -> str:
    if not ms:
        return "-"
    total_min = int(ms / 60000)
    h, m = total_min // 60, total_min % 60
    if h >= 24:
        return "%d天%d小时%d分" % (h // 24, h % 24, m)
    return "%d小时%d分" % (h, m)


def _build_df(data_dir: Path) -> tuple[pd.DataFrame, dict, list, list]:
    trackids = _load(data_dir, "trackids.json")
    songs = _load(data_dir, "songs.json")
    playlists = _load(data_dir, "playlists.json")
    playrecord = _load(data_dir, "playrecord.json")
    rows = []
    for x in trackids:
        s = songs.get(str(x["id"])) or songs.get(x["id"])
        if not s:
            continue
        t = x.get("t") or 0
        rows.append({"pid": x["pid"], "pname": x.get("pname", ""),
                     "id": x["id"], "t": t, "title": s["name"],
                     "artists": s["artists"], "album": s["album"],
                     "duration": s.get("duration") or 0, "pop": s.get("pop")})
    df = pd.DataFrame(rows)
    return df, songs, playlists, playrecord


def _bucket_column(df: pd.DataFrame, trackids: list) -> tuple[int, str]:
    """用最大歌单的数组顺序构造收藏批次；返回 (主歌单曲目数, 主歌单名)。"""
    counts: dict[int, int] = {}
    name: dict[int, str] = {}
    for x in trackids:
        counts[x["pid"]] = counts.get(x["pid"], 0) + 1
        name[x["pid"]] = x.get("pname") or str(x["pid"])
    main_pid = max(counts, key=counts.get)
    order = [x["id"] for x in trackids if x["pid"] == main_pid]
    pos: dict[int, int] = {}
    for i, sid in enumerate(order):
        pos[sid] = max(pos.get(sid, -1), i)
    n = len(order)
    df["pos"] = df["id"].map(pos)
    df["in_order_pl"] = df["pos"].notna()
    df.loc[df["in_order_pl"], "rank_old"] = n - 1 - df.loc[df["in_order_pl"], "pos"]
    df.loc[df["in_order_pl"], "batch"] = (
        df["rank_old"] * 10 // n + 1).astype("Int64")
    return n, name.get(main_pid, "")


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    df = df[df["t"] > 10**12].copy()
    df["artist_str"] = df["artists"].apply(
        lambda lst: ",".join(a["name"] or "未知歌手" for a in lst))
    df["main_artist"] = df["artists"].apply(
        lambda lst: lst[0]["name"] if lst and lst[0].get("name") else "未知歌手")
    df["album_name"] = df["album"].apply(lambda a: (a or {}).get("name") or "")
    df["publish_ms"] = df["album"].apply(
        lambda a: (a or {}).get("publishTime") or 0)
    df["add_year"] = (df["t"] // 1000).apply(
        lambda ts: time.strftime("%Y", time.localtime(ts))).astype(int)
    df["add_date"] = (df["t"] // 1000).apply(
        lambda ts: time.strftime("%Y-%m-%d", time.localtime(ts)))
    df["pub_year"] = df["publish_ms"].apply(
        lambda ms: time.gmtime(ms // 1000).tm_year if ms else None)
    df["key"] = (df["artist_str"].str.lower().str.strip() + "||" +
                 df["title"].astype(str).str.lower().str.strip())
    return df.sort_values("t").drop_duplicates("key", keep="first").copy()


def _region(dfu: pd.DataFrame, data_dir: Path) -> pd.DataFrame:
    unsure: Counter = Counter()
    regions = []
    for _, r in dfu.iterrows():
        reg, u = region_of([a["name"] or "" for a in r["artists"]],
                           r["title"])
        regions.append(reg)
        if u:
            unsure[u] += 1
    dfu = dfu.copy()
    dfu["region"] = regions
    save_unknown(unsure, data_dir)
    return dfu, unsure


def _stacked(ax, df: pd.DataFrame, xcol: str, labels: list[str],
             title: str, xlabel: str):
    pv = df.pivot_table(index=xcol, columns="region", values="key",
                        aggfunc="count", fill_value=0)
    pv = pv.reindex(columns=[c for c in REGIONS if c in pv.columns],
                    fill_value=0)
    pct = pv.div(pv.sum(axis=1), axis=0) * 100
    ax.stackplot(labels, [pct[c] for c in pct.columns], labels=pct.columns,
                 colors=[RCOLOR[c] for c in pct.columns], alpha=.9)
    ax.set_ylim(0, 100)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("占比 %")
    ax.legend(loc="lower left", bbox_to_anchor=(1.01, 0), frameon=False)
    _style(ax)


def _release_chart(ax, ax2, df, groups, title):
    df = df.copy()
    df["lag"] = df["add_year"] - df["pub_year"]
    df["is_new"] = df["pub_year"] >= (df["add_year"] - 1)
    nr = df.groupby(groups)["is_new"].mean() * 100
    ml = df.groupby(groups)["lag"].mean()
    labels = [str(i) for i in nr.index]
    ax.bar(labels, nr.values, color="#b8cfe8", alpha=.9,
           label="新歌占比%（发行≤收藏前1年）")
    for i, v in enumerate(nr.values):
        ax.text(i, v, "%.0f" % v, ha="center", va="bottom", fontsize=8)
    ax2.plot(labels, ml.reindex(nr.index).values, color="#e05c5c", marker="o",
             label="平均发行滞后（年）")
    ax.set_title(title)
    ax.set_xlabel("收藏先后（左=最早，右=最近）")
    ax.set_ylabel("新歌占比 %")
    ax2.set_ylabel("发行→收藏 平均滞后年数")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", frameon=False, fontsize=9)
    ax.spines[["top"]].set_visible(False)


def _pop_chart(ax, df, groups, title, xlabel):
    pm = df.dropna(subset=["pop"]).groupby(groups)["pop"].mean().sort_index()
    labels = [str(i) for i in pm.index]
    ax.plot(labels, pm.values, marker="o", color="#e8a33d", lw=2)
    for x, y in zip(labels, pm.values):
        ax.annotate("%.1f" % y, (x, y), textcoords="offset points",
                    xytext=(0, 8), ha="center", fontsize=9)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("pop 均值（0–100，越低越小众）")
    ax.grid(axis="y", alpha=.3)
    _style(ax)


def _adds_chart(ax, vc: pd.Series, title, xlabel):
    ax.bar([str(i) for i in vc.index], vc.values, color="#4f8fd9", alpha=.85)
    for i, v in enumerate(vc.values):
        ax.text(i, v, str(v), ha="center", va="bottom", fontsize=9)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("歌曲数")
    _style(ax)


def run_analyze(cfg) -> dict:
    data_dir = Path(cfg.data_dir)
    charts = Path(cfg.charts_dir)
    charts.mkdir(parents=True, exist_ok=True)
    df, songs, playlists, playrecord = _build_df(data_dir)
    if df.empty:
        raise RuntimeError("没有可用数据，先运行 `ncm-pipeline fetch`")
    profile = _load(data_dir, "profile.json")
    trackids = _load(data_dir, "trackids.json")
    n_entries = len(df)
    n, main_pl = _bucket_column(df, trackids)
    dfu = _prep(df)
    dfu, unsure = _region(dfu, data_dir)

    created = [p for p in playlists if p.get("userId") == profile["uid"]]
    subscribed = [p for p in playlists if p.get("userId") != profile["uid"]]
    total_ms = int(dfu["duration"].sum())
    top_artists = dfu["main_artist"].value_counts().head(20)
    top_albums = dfu[dfu["album_name"] != ""]["album_name"].value_counts().head(15)
    region_counts = dfu["region"].value_counts()
    dec = dfu.dropna(subset=["pub_year"]).copy()
    dec["decade"] = (dec["pub_year"] // 10 * 10).astype(int)
    decade_counts = dec["decade"].value_counts().sort_index()
    years = sorted(dfu["add_year"].unique())
    span = "%d – %d" % (years[0], years[-1]) if years else "-"

    # 视角自动选择
    day_counts = dfu["add_date"].value_counts()
    bulk_date, bulk_cnt = day_counts.idxmax(), int(day_counts.max())
    use_batches = bulk_cnt / max(len(dfu), 1) > BULK_SHARE_THRESHOLD

    xcol = "batch" if use_batches else "add_year"
    bucket_lbl = ("收藏批次（1=最早 → 10=最近）" if use_batches else "加入年份")
    dfv = dfu[dfu["in_order_pl"]] if use_batches else dfu

    # ---- 主视角四图 ----
    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=150)
    _adds_chart(ax, dfv[xcol].value_counts().sort_index(),
                "每个%s加入的歌曲数量（去重后 %d 首）" %
                ("收藏批次" if use_batches else "年份", len(dfu)),
                bucket_lbl)
    fig.tight_layout(); fig.savefig(charts / "adds.png"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 5), dpi=150)
    _stacked(ax, dfv, xcol, [str(i) for i in sorted(dfv[xcol].unique())],
             "语种/地区占比随收藏先后变化" if use_batches
             else "语种/地区占比随加入年份变化", bucket_lbl)
    fig.tight_layout(); fig.savefig(charts / "region_stacked.png"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 4.8), dpi=150)
    ax2 = ax.twinx()
    _release_chart(ax, ax2, dfv.dropna(subset=["pub_year"]),
                   xcol if not use_batches else "batch",
                   "「当年听新歌」还是「挖老歌」？（按%s）" %
                   ("收藏批次" if use_batches else "加入年份"))
    ax2.spines[["top"]].set_visible(False)
    fig.tight_layout(); fig.savefig(charts / "release_trend.png"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 4.5), dpi=150)
    _pop_chart(ax, dfv, xcol if not use_batches else "batch",
               "小众度趋势：网易热度 pop 均值（按%s）" %
               ("收藏批次" if use_batches else "加入年份"), bucket_lbl)
    fig.tight_layout(); fig.savefig(charts / "pop_trend.png"); plt.close(fig)

    # ---- 总览三图 ----
    fig, ax = plt.subplots(figsize=(7.6, 5.2), dpi=150)
    rc = region_counts.reindex([c for c in REGIONS if c in region_counts.index])
    wedges, _, _ = ax.pie(
        rc.values, labels=None,
        autopct=lambda p: ("%1.1f%%" % p) if p >= 3 else "",
        colors=[RCOLOR[c] for c in rc.index], startangle=90,
        wedgeprops=dict(width=.42, edgecolor="w"))
    ax.legend(wedges, ["%s：%d 首（%.1f%%）" % (c, v, v / rc.sum() * 100)
                       for c, v in rc.items()],
              loc="center left", bbox_to_anchor=(1.02, 0.5), frameon=False)
    ax.set_title("语种/地区分布（去重后全部歌曲）")
    fig.tight_layout(); fig.savefig(charts / "region_pie.png"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 6.5), dpi=150)
    ta = top_artists[::-1]
    ax.barh(ta.index, ta.values, color="#4f8fd9", alpha=.85)
    for i, v in enumerate(ta.values):
        ax.text(v, i, " %d" % v, va="center", fontsize=9)
    ax.set_title("Top 20 歌手（按去重后歌曲数）")
    ax.set_xlabel("歌曲数")
    _style(ax)
    fig.tight_layout(); fig.savefig(charts / "top_artists.png"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 4.5), dpi=150)
    ax.bar([str(int(d)) + "后" for d in decade_counts.index],
           decade_counts.values, color="#9b6fd9", alpha=.85)
    for i, v in enumerate(decade_counts.values):
        ax.text(i, v, str(v), ha="center", va="bottom", fontsize=9)
    ax.set_title("专辑发行年代分布")
    ax.set_xlabel("发行年代")
    ax.set_ylabel("歌曲数")
    _style(ax)
    fig.tight_layout(); fig.savefig(charts / "decade_dist.png"); plt.close(fig)

    # ---- 附录：另一种视角的加入量/地区图 ----
    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=150)
    if use_batches:
        _adds_chart(ax, dfu["add_year"].value_counts().sort_index(),
                    "附录：按「加入时间戳」统计（受 %s 歌单重加 %d 首影响，仅供参考）"
                    % (bulk_date, bulk_cnt), "加入年份")
        fig.tight_layout(); fig.savefig(charts / "adds_by_year.png"); plt.close(fig)
        fig, ax = plt.subplots(figsize=(9, 4.6), dpi=150)
        _stacked(ax, dfu, "add_year", [str(y) for y in years],
                 "附录：时间戳视角的语种占比（失真，仅供参考）", "加入年份")
        fig.tight_layout(); fig.savefig(charts / "region_stacked_year.png"); plt.close(fig)
    else:
        _adds_chart(ax, dfu[dfu["in_order_pl"]]["batch"].value_counts().sort_index(),
                    "附录：按「收藏批次」视角（歌单内顺序）", "收藏批次")
        fig.tight_layout(); fig.savefig(charts / "adds_by_batch.png"); plt.close(fig)

    # ---- 批次/年度 Top3 表 ----
    grp = dfv.groupby(xcol)
    top3_rows = []
    for g, dsub in grp:
        vc = dsub["main_artist"].value_counts()
        total = int(vc.sum())
        top3_rows.append({"label": str(g), "total": total,
                          "top3": [(a, int(c)) for a, c in vc.head(3).items()],
                          "share": round(sum(c for _, c in vc.head(3).items())
                                         / max(total, 1) * 100, 1),
                          "pop": round(dsub["pop"].dropna().astype(float).mean(), 1)
                          if dsub["pop"].notna().any() else 0.0})

    # ---- 交叉洞察 ----
    played_ids = {r["id"]: r.get("playCount", 0) for r in playrecord}
    m_played = dfu["id"].map(played_ids).fillna(0)
    never_played = dfu[m_played == 0].sort_values("t", ascending=False).head(20)
    not_collected = [r for r in playrecord if r["id"] not in set(dfu["id"])]
    not_collected.sort(key=lambda r: -r.get("playCount", 0))
    not_collected = not_collected[:20]

    def song_cell(song_id) -> str:
        s = songs.get(str(song_id)) or songs.get(song_id) or {}
        ar = ",".join(a.get("name") or "" for a in s.get("artists", []))
        return html.escape("%s - %s" % (ar, s.get("name") or song_id))

    # ---- summary.json ----
    summary = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "uid": profile.get("uid"), "nickname": profile.get("nickname"),
        "playlists_created": len(created),
        "playlists_subscribed": len(subscribed),
        "unique_songs": len(dfu), "entries": n_entries,
        "total_duration_ms": total_ms, "years_span": span,
        "view_mode": "batch" if use_batches else "year",
        "timestamp_note": ("%s 单日加入 %d 首（占比 %.0f%%），疑似批量整理/重加，"
                           "趋势以收藏批次为准" % (bulk_date, bulk_cnt,
                                               bulk_cnt / len(dfu) * 100))
        if use_batches else "",
        "region_counts": {k: int(v) for k, v in region_counts.items()},
        "top_artists": {k: int(v) for k, v in top_artists.items()},
        "top_albums": {k: int(v) for k, v in top_albums.items()},
        "bucket_stats": top3_rows,
        "adds_by_year": {int(k): int(v) for k, v in
                         dfu["add_year"].value_counts().sort_index().items()},
        "collected_never_played_total": int((dfu[m_played == 0]).shape[0]),
        "main_playlist": {"name": main_pl, "tracks": n},
    }
    (data_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    _write_report(cfg, profile, summary, dfu, df, top_albums, top3_rows,
                  never_played, not_collected, song_cell, unsure,
                  use_batches, bulk_date, bulk_cnt)
    print("ANALYZE_OK view=%s unique=%d entries=%d" %
          (summary["view_mode"], len(dfu), n_entries))
    return summary


def _noop(*a, **k):
    return None


def _write_report(cfg, profile, summary, dfu, df, top_albums, top3_rows,
                  never_played, not_collected, song_cell, unsure,
                  use_batches, bulk_date, bulk_cnt) -> None:
    e = html.escape

    def table(headers, rows_):
        th = "".join("<th>%s</th>" % h for h in headers)
        trs = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in r)
                      for r in rows_)
        return ('<table class="tbl"><thead><tr>%s</tr></thead><tbody>%s</tbody>'
                "</table>" % (th, trs))

    cards = """
    <div class="cards">
      <div class="card"><b>%d</b><span>创建歌单</span></div>
      <div class="card"><b>%d</b><span>收藏歌单</span></div>
      <div class="card"><b>%d</b><span>去重后歌曲</span></div>
      <div class="card"><b>%s</b><span>总时长</span></div>
      <div class="card"><b>%s</b><span>收藏跨度</span></div>
      <div class="card"><b>%d</b><span>收藏了但没听过</span></div>
    </div>""" % (summary["playlists_created"], summary["playlists_subscribed"],
                 summary["unique_songs"], _fmt_dur(summary["total_duration_ms"]),
                 e(summary["years_span"]),
                 summary["collected_never_played_total"])

    top3_tbl = table(["分组", "歌曲数", "Top 1", "Top 2", "Top 3", "Top3 占比",
                      "pop均值"],
                     [[e(r["label"]), str(r["total"])] +
                      ["%s（%d）" % (e(a), c) for a, c in r["top3"]] +
                      ["%.1f%%" % r["share"], "%.1f" % r["pop"]]
                      for r in top3_rows])
    albums_tbl = table(["专辑", "歌曲数"],
                       [[e(a), str(c)] for a, c in top_albums.items()])
    np_tbl = table(["#", "歌曲", "加入时间", "所在歌单"],
                   [[str(i + 1), song_cell(r["id"]), r["add_date"],
                     e(str(r["pname"])[:20])]
                    for i, (_, r) in enumerate(never_played.iterrows())])
    pn_tbl = table(["#", "歌曲", "播放次数", "热度分"],
                   [[str(i + 1), song_cell(r["id"]), str(r.get("playCount", 0)),
                     str(r.get("score", 0))]
                    for i, r in enumerate(not_collected)])

    unknown_note = ""
    if unsure:
        ua = "".join("<li>%s（%d 首）</li>" % (e(a), c)
                     for a, c in unsure.most_common(15))
        unknown_note = ("<div class='note'><b>待确认歌手</b>：以下全汉字名歌手按"
                        "「华语」处理了，若是日语歌手请改 regions.py 白名单后重跑"
                        "（完整清单 data/unknown_region.json）：<ul>%s</ul></div>"
                        % ua)

    bulk_note = ""
    if use_batches:
        bulk_note = ("<div class='note'><b>为什么按「批次」而不是按「年份」？</b>"
                     "加入时间戳显示 %s 单日加入 <b>%d</b> 首（歌单删除后重加/批量整理）。"
                     "好在歌单位置顺序保留了收藏先后（越靠前越新收藏），"
                     "趋势按「收藏批次1→10」排列：批次1=最早收藏的约1/10。"
                     "若能确认最早收藏的大致年份，可把批次换算成年份。</div>"
                     % (e(bulk_date), bulk_cnt))
    axis = "收藏批次（1=最早 → 10=最近）" if use_batches else "加入年份"

    report = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>音乐品味报告 - {nick}</title><style>
body{{font-family:"Microsoft YaHei","PingFang SC",sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#222;line-height:1.6}}
h1{{font-size:26px;border-bottom:3px solid #e05c5c;padding-bottom:8px}} h2{{font-size:20px;margin-top:40px;border-left:5px solid #4f8fd9;padding-left:10px}}
h3{{font-size:16px;margin-top:24px}}
img{{max-width:100%;border:1px solid #eee;border-radius:6px;margin:8px 0}}
.cards{{display:flex;flex-wrap:wrap;gap:12px;margin:20px 0}}
.card{{flex:1 1 140px;background:#f7f8fa;border-radius:10px;padding:14px;text-align:center}}
.card b{{display:block;font-size:24px;color:#e05c5c}} .card span{{font-size:13px;color:#666}}
table.tbl{{border-collapse:collapse;width:100%;font-size:13px;margin:10px 0}}
.tbl th{{background:#f0f3f7;text-align:left;padding:6px 8px}} .tbl td{{border-bottom:1px solid #eee;padding:6px 8px}}
.note{{background:#fff8e6;border:1px solid #f0dc9a;border-radius:8px;padding:10px 14px;font-size:13px}}
.small{{color:#888;font-size:12px}}
</style></head><body>
<h1>🎵 音乐品味报告 — {nick}</h1>
<p class="small">生成时间 {gen} ｜ 数据来源：本人账号歌单（trackIds 顺序+加入时间戳）+ 听歌排行 Top100 + 歌曲元数据；cookie 仅存本地 data/cookie.json ｜ 本工具仅用于个人数据统计</p>
{bulk_note}
{cards}
<h2>一、总统计</h2>
<p>共 <b>{nu}</b> 首去重歌曲（按「歌手-歌名」，原始 {ne} 条记录）。</p>
<img src="charts/region_pie.png"><img src="charts/top_artists.png">
<h3>Top 专辑</h3>{albums_tbl}
<img src="charts/decade_dist.png">
<h2>二、分时段品味趋势（{axis}）</h2>
<img src="charts/adds.png">
<img src="charts/region_stacked.png">
<img src="charts/release_trend.png">
<img src="charts/pop_trend.png">
<h3>每组的 Top3 歌手与集中度</h3>{top3_tbl}
<h2>三、交叉洞察</h2>
<h3>收藏了但几乎没听过（未进听歌排行 Top100，取最近收藏的 20 首）</h3>{np_tbl}
<h3>听了很多但从未收藏（按播放次数 Top 20）</h3>{pn_tbl}
{appendix}
{unknown_note}
<p class="small">说明：pop 为网易 0–100 热度值，均值走低=口味变小众；「新歌占比」指收藏时发行不超过 1 年的歌；听歌排行接口只返回 Top 100 高频记录，不在表=听得很少。中间数据存 data/*.json，重跑 fetch+analyze 即可续算。</p>
</body></html>""".format(
        nick=e(profile.get("nickname") or str(profile.get("uid"))),
        gen=summary["generated_at"], bulk_note=bulk_note, cards=cards,
        nu=summary["unique_songs"], ne=summary["entries"], axis=e(axis),
        albums_tbl=albums_tbl, top3_tbl=top3_tbl, np_tbl=np_tbl,
        pn_tbl=pn_tbl, unknown_note=unknown_note,
        appendix=("<h2>附录：时间戳视角（失真，仅供参考）</h2>\n"
                  '<img src="charts/adds_by_year.png">\n'
                  '<img src="charts/region_stacked_year.png">'
                  if use_batches else ""))
    Path(cfg.report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg.report_path).write_text(report, encoding="utf-8")
