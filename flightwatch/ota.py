"""中国 OTA（同程 / 携程 / 飞猪）最低价抓取。

同程国际站真实链路（已实测）：
  1) 打开 https://www.ly.com/iflight/ ，页面风控 SDK 会自动给 /miflightapi/*
     请求加上 `user-dun` 头（约 650 字符的会话令牌）
  2) 抓到这个令牌后，**纯 HTTP 就能调价格日历接口**，不再需要浏览器
  3) GET /miflightapi/ts/calendar?D=SHA&A=GRU&DD=..&BD=..&ED=..
     返回 {"code":200,"data":{"LP":..,"TP":..,"RD":[{"DD":"2026-11-16","P":"5271","TP":"5571"}]}}
     P 是当天单程最低价（全航司），TP 是含税费总价
  4) 往返列表页 /iflight/book1.html 需要登录（跳 passport + 拼图验证），
     所以往返价由「去程日历 + 返程日历」合成

携程 / 飞猪：见 PLATFORM_STATUS 里的实测结论，代码保留但默认不启用。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# 同程国际站用城市码/机场码，PVG 要换成城市 SHA
LY_CITY: dict[str, str] = {"PVG": "SHA", "SHA": "SHA", "GRU": "SAO"}
LY_AIRPORT: dict[str, str] = {"PVG": "SHA", "SHA": "SHA", "GRU": "GRU"}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")

PLATFORM_STATUS = {
    "ly": {"name": "同程旅行", "ok": True,
           "note": "单程价格日历（免登录，全航司最低价）"},
    "trip": {"name": "携程国际", "ok": False,
             "note": "Akamai whaleguard 按 IP 拦截（HTTP 432），时灵时不灵"},
    "fliggy": {"name": "飞猪", "ok": False,
               "note": "mtop 需 UMID 设备指纹，且搜索页要求登录"},
}


class TokenError(RuntimeError):
    pass


# --------------------------------------------------------------------------- 令牌

class LyToken:
    """同程 user-dun 会话令牌：抓一次缓存复用，失效自动重抓。"""

    def __init__(self, path: Path, ttl_hours: float = 6.0) -> None:
        self.path = path
        self.ttl = max(0.5, float(ttl_hours)) * 3600

    def get(self, force: bool = False) -> str:
        if not force:
            cached = self._load()
            if cached:
                return cached
        tok = self._grab()
        self._save(tok)
        return tok

    def _load(self) -> str | None:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if time.time() - float(d.get("ts", 0)) < self.ttl and d.get("token"):
                return str(d["token"])
        except Exception:
            pass
        return None

    def _save(self, token: str) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"ts": time.time(), "token": token}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _grab(self) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise TokenError("未安装 playwright，无法获取同程令牌") from e

        got: dict[str, str] = {}
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            try:
                pg = b.new_page(viewport={"width": 1440, "height": 900}, locale="zh-CN")

                def on_req(r):
                    if "ts/calendar" in r.url:
                        v = r.headers.get("user-dun")
                        if v:
                            got["token"] = v

                pg.on("request", on_req)
                pg.goto("https://www.ly.com/iflight/", wait_until="domcontentloaded",
                        timeout=60000)
                for _ in range(20):
                    if got.get("token"):
                        break
                    pg.wait_for_timeout(500)
            finally:
                b.close()
        if not got.get("token"):
            raise TokenError("未能从同程页面捕获 user-dun 令牌")
        return got["token"]


# --------------------------------------------------------------------------- 接口

def _ly_get(token: str, path_qs: str, timeout: int = 25) -> dict[str, Any]:
    url = "https://www.ly.com" + path_qs
    req = urllib.request.Request(url, headers={
        "user-dun": token,
        "t-token": "1",
        "pc-token": "1",
        "Referer": "https://www.ly.com/iflight/",
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def ly_calendar(token: str, dep: str, arr: str, start: str, end: str) -> dict[str, int]:
    """返回 {日期: 单程含税总价}。

    同程日历每个日期给两个数：
      P  = 基础票价（税前，看起来很美但不可信）
      TP = 含税总价（真正要付的钱）
    必须用 TP，否则会把价格低估三到四成。
    """
    qs = ("/miflightapi/ts/calendar?" + urllib.parse.urlencode({
        "D": LY_CITY.get(dep.upper(), dep.upper()),
        "A": LY_AIRPORT.get(arr.upper(), arr.upper()),
        "DD": start,
        "BD": start,
        "ED": end,
        "_t": str(int(time.time() * 1000)),
    }))
    j = _ly_get(token, qs)
    if j.get("code") != 200:
        raise TokenError(f"同程日历返回 {j.get('code')}: {j.get('message')}")
    out: dict[str, int] = {}
    for x in ((j.get("data") or {}).get("RD") or []):
        d = x.get("DD")
        raw = x.get("TP") or x.get("P")   # 优先含税总价
        if d and raw:
            try:
                out[str(d)] = int(raw)
            except (TypeError, ValueError):
                pass
    return out


def ly_roundtrips(
    token: str,
    origin: str,
    dest: str,
    start: str,
    end: str,
    gaps: list[int],
) -> list[dict[str, Any]]:
    """用去程/返程两张单程日历合成往返组合。"""
    out_cal = ly_calendar(token, origin, dest, start, end)
    back_cal = ly_calendar(token, dest, origin, start, end)
    rows: list[dict[str, Any]] = []
    for ds, p_out in out_cal.items():
        try:
            d0 = date.fromisoformat(ds)
        except ValueError:
            continue
        for gap in gaps:
            rs = (d0 + timedelta(days=int(gap))).isoformat()
            p_back = back_cal.get(rs)
            if not p_back:
                continue
            rows.append({
                "source": "ly",
                "dep_date": ds,
                "ret_date": rs,
                "gap_days": int(gap),
                "price": p_out + p_back,
                "out_price": p_out,
                "back_price": p_back,
                "note": "去程+返程（单程日历合成，全航司最低）",
            })
    rows.sort(key=lambda x: x["price"])
    return rows


# --------------------------------------------------------------------------- 汇总

def fetch(cfg: Any, window: dict[str, Any] | None = None) -> dict[str, Any]:
    """抓取各中国平台。返回 {platform: {status, offers, note}}。"""
    raw = getattr(cfg, "raw", cfg) or {}
    opts = raw.get("cn_ota", {}) or {}
    if not opts.get("enabled", True):
        return {}

    w = window or {}
    start = str(w.get("start") or raw.get("window", {}).get("start") or "")
    end = str(w.get("max_return") or w.get("last_dep") or "")
    if not start or not end:
        today = date.today()
        start = (today + timedelta(days=10)).isoformat()
        end = (today + timedelta(days=51)).isoformat()

    gaps = list((raw.get("window", {}) or {}).get("gap_days") or [3, 4, 5])
    origin = str((raw.get("route", {}) or {}).get("origins", ["PVG"])[0])
    dest = str((raw.get("route", {}) or {}).get("destination", "GRU"))

    result: dict[str, Any] = {}

    ly_opts = opts.get("ly", {}) or {}
    if ly_opts.get("enabled", True):
        rec: dict[str, Any] = {"name": PLATFORM_STATUS["ly"]["name"], "offers": [], "note": ""}
        token = LyToken(
            Path(str(ly_opts.get("token_cache") or "data/ly_token.json")),
            ttl_hours=float(ly_opts.get("token_ttl_hours", 6)),
        )
        try:
            try:
                tok = token.get()
                offers = ly_roundtrips(tok, origin, dest, start, end, gaps)
            except TokenError:
                # 令牌过期/失效，重新抓一次
                tok = token.get(force=True)
                offers = ly_roundtrips(tok, origin, dest, start, end, gaps)
            rec["offers"] = offers
            rec["note"] = PLATFORM_STATUS["ly"]["note"]
            if not offers:
                rec["note"] = "接口正常但窗口内无报价"
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"{type(e).__name__}: {str(e)[:140]}"
            rec["note"] = rec["error"]
        result["ly"] = rec

    for key in ("trip", "fliggy"):
        p_opt = opts.get(key, {}) or {}
        if p_opt.get("enabled", False):
            result[key] = {
                "name": PLATFORM_STATUS[key]["name"],
                "offers": [],
                "note": PLATFORM_STATUS[key]["note"],
                "error": "需真浏览器，见 `--verify`",
            }
    return result


def summary(result: dict[str, Any]) -> list[dict[str, Any]]:
    """把 fetch() 的结果压成报告要用的表行。"""
    rows: list[dict[str, Any]] = []
    for key in ("ly", "trip", "fliggy"):
        rec = result.get(key)
        meta = PLATFORM_STATUS[key]
        if rec is None:
            rows.append({
                "key": key, "name": meta["name"], "price": None,
                "status": "未启用" if key != "ly" else "未尝试",
                "note": meta["note"], "dep": "", "ret": "",
            })
            continue
        offers = rec.get("offers") or []
        if offers:
            b = offers[0]
            rows.append({
                "key": key, "name": rec.get("name", meta["name"]),
                "price": b["price"], "dep": b["dep_date"], "ret": b["ret_date"],
                "status": "正常", "note": rec.get("note", ""),
            })
        else:
            rows.append({
                "key": key, "name": rec.get("name", meta["name"]), "price": None,
                "dep": "", "ret": "",
                "status": "受限", "note": rec.get("note") or meta["note"],
            })
    return rows
