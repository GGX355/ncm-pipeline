"""本机网易云 API 服务（@neteaseapireborn/api）的 HTTP 客户端。

已修坑（来自实测）：
- /login/status 的 code 嵌在 data 里，不在顶层
- 听歌排行路由是 /user/record（不是 /user/playrecord），只返回 Top 100
- /song/detail 在部分版本缺 publishTime/pop → 用 /playlist/track/all 取歌曲元数据
- /playlist/track/all 分页要「取到空为止」（单页可能少于 limit）
- /playlist/detail 只带前 ~1000 首 tracks，但 trackIds 全量；
  新版接口加入时间在 at 字段（t 恒为 0）
- privileges 在响应的独立数组里，不在 song 对象上

本模块只访问 127.0.0.1 的本机服务，cookie 仅存本地。
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests


class APIError(RuntimeError):
    pass


class CookieExpired(APIError):
    pass


class NetEaseAPI:
    def __init__(self, base: str, cookie: str | None = None,
                 timeout: int = 40, retries: int = 3):
        self.base = base.rstrip("/")
        self.cookie = cookie
        self.timeout = timeout
        self.retries = retries

    # ---- 基础请求：重试 + code 判定（顶层或 data 内层）----
    def get(self, path: str, **params) -> dict:
        if self.cookie:
            params["cookie"] = self.cookie
        params.setdefault("timestamp", int(time.time() * 1000))
        last = None
        for _ in range(self.retries):
            try:
                j = requests.get(self.base + path, params=params,
                                 timeout=self.timeout).json()
            except Exception as e:  # 网络/JSON 解析失败可重试
                last = e
                time.sleep(2)
                continue
            top = j.get("code")
            inner = (j["data"].get("code")
                     if isinstance(j.get("data"), dict) else None)
            if 200 in (top, inner):
                return j
            if 301 in (top, inner) or 302 in (top, inner):
                raise CookieExpired("cookie 失效，请重新扫码登录")
            last = j
            time.sleep(2)
        raise APIError("%s 请求失败: %s" % (path, last))

    # ---- 登录 ----
    def login_status(self) -> dict:
        return self.get("/login/status")

    def qr_login(self, qr_png_path: Path, total_budget: int = 30 * 60,
                 poll: int = 2, open_image: bool = True) -> str:
        """生成二维码并轮询，成功返回 cookie 原始串。过期自动重生成。"""
        deadline = time.time() + total_budget
        opened = False
        while time.time() < deadline:
            key = self.get("/login/qr/key")["data"]["unikey"]
            info = self.get("/login/qr/create", key=key, qrimg="true")["data"]
            qrimg = info.get("qrimg", "")
            if qrimg.startswith("data:image"):
                qr_png_path.parent.mkdir(parents=True, exist_ok=True)
                qr_png_path.write_bytes(base64.b64decode(qrimg.split(",", 1)[1]))
            if not opened and open_image:
                try:
                    if sys.platform == "win32":
                        os.startfile(str(qr_png_path))  # noqa: S606
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", str(qr_png_path)])
                    else:
                        subprocess.Popen(["xdg-open", str(qr_png_path)])
                except Exception:
                    pass
                opened = True
            print("二维码已刷新，等待扫码… 图片路径: %s" % qr_png_path, flush=True)
            t0 = time.time()
            while time.time() - t0 < 110:
                time.sleep(poll)
                st = self.get("/login/qr/check", key=key)
                code = st.get("code") or (st.get("data") or {}).get("code")
                if code == 802:
                    print("已扫码，请在手机上确认…", flush=True)
                elif code == 803:
                    cookie = st.get("cookie") or (st.get("data") or {}).get("cookie")
                    if not cookie:
                        raise APIError("登录成功但未返回 cookie: %s" % st)
                    print("登录成功，cookie 已保存（仅本地）", flush=True)
                    return cookie
                elif code == 800:
                    print("二维码过期，重新生成…", flush=True)
                    break
        raise APIError("等待超时，未完成登录")

    # ---- 数据接口 ----
    def uid(self) -> int:
        st = self.login_status()
        prof = ((st.get("data") or {}).get("profile")) or {}
        if not prof.get("userId"):
            raise CookieExpired("未登录或 cookie 失效")
        return prof["userId"]

    def user_playlists(self, uid: int) -> list[dict]:
        pls, offset = [], 0
        while True:
            j = self.get("/user/playlist", uid=uid, limit=100, offset=offset)
            batch = j.get("playlist") or []
            pls += batch
            if not (j.get("more") and len(batch) >= 100):
                break
            offset += 100
            time.sleep(0.4)
        return pls

    def playlist_track_ids(self, pid: int) -> list[dict]:
        """全部 trackIds（顺序=歌单内顺序，前=新后=旧）；t 取 at 兜底。"""
        j = self.get("/playlist/detail", id=pid, n=0)
        out = []
        for t in (j.get("playlist") or {}).get("trackIds") or []:
            out.append({"id": t["id"],
                        "t": t.get("t") or t.get("at") or 0})
        return out

    def playlist_tracks_paged(self, pid: int, page: int = 500,
                              sleep: float = 0.4):
        """逐页产出完整曲目对象（含 publishTime/pop）。取到空为止。"""
        offset = 0
        while True:
            j = self.get("/playlist/track/all", id=pid,
                         limit=page, offset=offset)
            trs = j.get("songs") or []
            if not trs:
                return
            yield from trs
            offset += page
            time.sleep(sleep)

    def song_privileges(self, pid: int, page: int = 500) -> dict[int, dict]:
        """privileges 在独立数组里：{song_id: privilege}"""
        out: dict[int, dict] = {}
        offset = 0
        while True:
            j = self.get("/playlist/track/all", id=pid,
                         limit=page, offset=offset)
            for p in j.get("privileges") or []:
                sid = p.get("id") or p.get("songId")
                if sid:
                    out[sid] = p
            if not (j.get("songs") or []):
                return out
            offset += page
            time.sleep(0.4)

    def user_record(self, uid: int, rtype: int = 0) -> list[dict]:
        """听歌排行（type=0 所有时间）。接口只返回 Top 100。"""
        j = self.get("/user/record", uid=uid, type=rtype)
        rows = j.get("allData") or j.get("weekData") or []
        return [{"id": x["song"]["id"], "playCount": x.get("playCount", 0),
                 "score": x.get("score", 0)} for x in rows]


def load_cookie(data_dir: Path) -> str:
    p = Path(data_dir) / "cookie.json"
    if not p.exists():
        raise CookieExpired("未找到 cookie（%s），请先 `ncm-pipeline login` 扫码" % p)
    return json.loads(p.read_text(encoding="utf-8"))["raw"]


def save_cookie(data_dir: Path, cookie: str) -> Path:
    p = Path(data_dir) / "cookie.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"raw": cookie,
                             "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def server_reachable(base: str, timeout: int = 5) -> bool:
    try:
        r = requests.get(base.rstrip("/") + "/login/status", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def start_server(server_dir: Path, base: str) -> subprocess.Popen | None:
    """尝试在后台启动本机 API 服务（需已 npm install @neteaseapireborn/api）。"""
    app = Path(server_dir) / "node_modules" / "@neteaseapireborn" / "api" / "app.js"
    if not app.exists():
        return None
    port = base.rsplit(":", 1)[-1]
    env = dict(os.environ, PORT=port)
    log = open(Path(server_dir) / "server.log", "ab")
    return subprocess.Popen(["node", str(app)], cwd=str(server_dir),
                            env=env, stdout=log, stderr=log)
