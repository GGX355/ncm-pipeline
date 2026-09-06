"""拉取本人账号数据（合规：仅自己的登录态、自己的数据），全部存本地 JSON。

输出到 data_dir：
  profile.json / playlists.json / trackids.json / songs.json / playrecord.json
  cookie.json（扫码后写入，仅本地）
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from .api import (CookieExpired, NetEaseAPI, load_cookie, save_cookie,
                  server_reachable, start_server)


def login(cfg, open_image: bool = True) -> Path:
    if not server_reachable(cfg.api_base):
        p = start_server(cfg.api_server_dir, cfg.api_base)
        if p is None:
            print("API 服务不可达且无法自动启动。请先在本机部署：\n"
                  "  mkdir -p %s && cd %s\n"
                  "  npm init -y && npm install @neteaseapireborn/api\n"
                  "  PORT=3000 node node_modules/@neteaseapireborn/api/app.js"
                  % (cfg.api_server_dir, cfg.api_server_dir))
            sys.exit(1)
        time.sleep(4)
    api = NetEaseAPI(cfg.api_base)
    cookie = api.qr_login(Path(cfg.data_dir) / "login_qr.png",
                          open_image=open_image)
    return save_cookie(cfg.data_dir, cookie)


def slim_playlist(p: dict) -> dict:
    return {"id": p["id"], "name": p.get("name"),
            "trackCount": p.get("trackCount"),
            "createTime": p.get("createTime"),
            "subscribed": p.get("subscribed"), "userId": p.get("userId"),
            "playCount": p.get("playCount")}


def _save(data_dir: Path, name: str, obj) -> None:
    (Path(data_dir) / name).write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    print("saved", name, flush=True)


def fetch_all(cfg) -> None:
    api = NetEaseAPI(cfg.api_base, load_cookie(cfg.data_dir))
    uid = api.uid()
    st = api.login_status()
    prof = st["data"]["profile"]
    _save(cfg.data_dir, "profile.json",
          {"uid": uid, "nickname": prof.get("nickname"),
           "signature": prof.get("signature")})
    print("uid =", uid, flush=True)

    pls = [slim_playlist(p) for p in api.user_playlists(uid)]
    _save(cfg.data_dir, "playlists.json", pls)
    print("playlists:", len(pls), flush=True)

    # trackIds 全量 + 数组顺序保留（前=新后=旧），t 取 at 兜底
    trackids = []
    for p in pls:
        try:
            tids = api.playlist_track_ids(p["id"])
        except Exception as e:
            print("SKIP playlist", p["id"], p["name"], e, flush=True)
            continue
        for t in tids:
            trackids.append({"pid": p["id"], "pname": p["name"],
                             "id": t["id"], "t": t["t"]})
        print("  [%s] %s : %d" % (p["id"], p["name"], len(tids)), flush=True)
        time.sleep(0.4)
    _save(cfg.data_dir, "trackids.json", trackids)

    # 歌曲元数据走 /playlist/track/all（/song/detail 缺 publishTime/pop）
    songs: dict[int, dict] = {}
    for p in pls:
        try:
            for s in api.playlist_tracks_paged(p["id"]):
                songs[s["id"]] = {
                    "name": s.get("name"),
                    "artists": [{"id": a.get("id"), "name": a.get("name")}
                                for a in s.get("ar", [])],
                    "album": {"id": (s.get("al") or {}).get("id"),
                              "name": (s.get("al") or {}).get("name"),
                              "publishTime": s.get("publishTime") or
                              (s.get("al") or {}).get("publishTime") or 0},
                    "duration": s.get("dt"), "pop": s.get("pop"),
                    "fee": s.get("fee")}
        except Exception as e:
            print("SKIP tracks", p["id"], e, flush=True)
    _save(cfg.data_dir, "songs.json", songs)
    print("unique songs:", len(songs), flush=True)

    try:
        records = api.user_record(uid, 0)
    except Exception as e:
        print("playrecord 获取失败（不影响其他数据）:", e, flush=True)
        records = []
    _save(cfg.data_dir, "playrecord.json", records)
    print("FETCH_DONE", flush=True)


def run_fetch(cfg) -> None:
    try:
        fetch_all(cfg)
    except CookieExpired as e:
        print(e)
        print("重新扫码登录中…")
        login(cfg)
        fetch_all(cfg)
