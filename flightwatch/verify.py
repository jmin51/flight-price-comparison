"""Playwright 真浏览器核价（--verify）。

为什么需要它：携程/同程/飞猪的价格都是前端 JS 异步渲染 + 签名接口，
纯 HTTP 抓不到。这里起一个真实 Chromium 打开搜索页，等页面自己把
价格渲染出来，再把可见价格扫一遍 + 截图存证。

登录态：用持久化 user_data_dir，一次手机号+验证码登录后长期复用
（见 登录各大平台.bat）。不登录也能看游客公开价，登录后才能看到会员价。
"""
from __future__ import annotations

import base64
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .config import Config

# 页面里出现价格的写法：¥12,345 / CN¥12345 / ￥12345 / RMB 12345
PRICE_RE = re.compile(
    r"(?:CN¥|¥|￥|RMB\s*|\bCNY\s*)(\d{1,3}(?:,\d{3})+|\d{4,6})",
    re.IGNORECASE,
)

# 平台定义：url 生成 + 登录页
PLATFORMS: dict[str, dict[str, Any]] = {
    "trip": {
        "name": "携程国际",
        "login": "https://cn.trip.com/account/signin",
    },
    "ly": {
        "name": "同程旅行",
        "login": "https://passport.ly.com/",
    },
    "fliggy": {
        "name": "飞猪",
        "login": "https://www.fliggy.com/",
    },
    "skyscanner": {
        "name": "天巡",
        "login": "https://www.skyscanner.net/",
    },
}


def build_url(platform: str, cfg: Config, dep: str, ret: str) -> str:
    """按平台拼搜索页 URL（航线、日期、往返都已带好）。"""
    r = cfg.route
    o = r["origins"][0]
    d = r["destination"]
    cabin = {"economy": "y", "premium-economy": "w", "business": "c", "first": "f"}.get(
        r.get("cabin", "economy"), "y"
    )
    adults = r.get("adults", 1)

    if platform == "trip":
        # 注意：cn.trip.com 在国内网络下会 ERR_CONNECTION_CLOSED，必须用 www.trip.com
        return (
            "https://www.trip.com/flights/showfarefirst"
            f"?dcity={o.lower()}&acity={d.lower()}&ddate={dep}&rdate={ret}"
            f"&triptype=rt&class={cabin}&quantity={adults}&locale=zh-CN&curr=CNY"
        )
    if platform == "ly":
        # 同程：`/flights/itinerary/roundtrip/` 是**国内**产品，查国际航线只会返回
        # 「没有对应航班」。国际机票在 /iflight/book1.html，参数是星号分隔的 para：
        #   {出发城市}*{到达城市}*{去程}*{返程}*RT*1_0_0*Y|S|C|F
        # 城市码必须用 SHA / SAO（不是机场码 PVG / GRU）。
        # 该页未登录会跳登录页，所以核价默认不走它，改由 flightwatch/ota.py 抓日历。
        city = {"PVG": "SHA", "SHA": "SHA", "GRU": "SAO"}
        dc = city.get(o.upper(), o.upper())
        ac = city.get(d.upper(), d.upper())
        para = quote(f"{dc}*{ac}*{dep}*{ret}*RT*1_0_0*Y|S|C|F", safe="*")
        return f"https://www.ly.com/iflight/book1.html?para={para}"
    if platform == "fliggy":
        return (
            "https://h5.m.taobao.com/trip/flight/search/index.html"
            f"?depCity={o}&arrCity={d}&depDate={dep}&backDate={ret}"
            "&tripType=1&channel=h5&isInternational=true"
        )
    if platform == "skyscanner":
        d1 = dep.replace("-", "")[2:]
        d2 = ret.replace("-", "")[2:]
        return (
            "https://www.skyscanner.net/transport/flights"
            f"/{o.lower()}/{d.lower()}/{d1}/{d2}/?adults={adults}&cabinclass={r.get('cabin', 'economy')}"
        )
    raise ValueError(f"未知平台: {platform}")


def _scan_prices(text: str, lo: int, hi: int) -> list[int]:
    out: list[int] = []
    for m in PRICE_RE.finditer(text or ""):
        raw = m.group(1).replace(",", "")
        try:
            v = int(raw)
        except ValueError:
            continue
        if lo <= v <= hi:
            out.append(v)
    return out


def _gather_text(page) -> str:
    """主文档 + 所有 iframe 的可见文本，OTR 页面常把列表塞进 iframe。"""
    parts: list[str] = []
    try:
        parts.append(page.inner_text("body") or "")
    except Exception:
        pass
    for fr in page.frames:
        if fr is page.main_frame:
            continue
        try:
            parts.append(fr.inner_text("body") or "")
        except Exception:
            continue
    return "\n".join(parts)


def _qr_prices(page, keywords: list[str], lo: int, hi: int) -> list[int]:
    """只取和指定航司（卡塔尔）出现在同一容器里的价格。

    原因：OTA 默认按价格排序，最便宜那班往往不是卡塔尔航空。
    例如携程 11/17→11/20 全页最低 ¥10,387 是埃塞俄比亚航空，
    卡塔尔航空是 ¥12,088——不按航司过滤就会报错价。
    """
    js = """
    ([kws, lo, hi]) => {
      const out = [];
      const re = /(?:CN¥|¥|￥)\\s*(\\d{1,3}(?:,\\d{3})+|\\d{4,6})/g;
      const nodes = Array.from(document.querySelectorAll('div,li,tr,section,article,td'));
      for (const e of nodes) {
        const t = e.innerText || '';
        if (t.length > 700) continue;
        let hit = false;
        for (const k of kws) { if (t.indexOf(k) >= 0) { hit = true; break; } }
        if (!hit) continue;
        let m;
        re.lastIndex = 0;
        while ((m = re.exec(t)) !== null) {
          const v = parseInt(m[1].replace(/,/g, ''), 10);
          if (v >= lo && v <= hi) out.push(v);
        }
      }
      return out;
    }
    """
    try:
        return page.evaluate(js, [keywords, lo, hi])
    except Exception:
        return []


def _wait_prices(page, lo: int, hi: int, timeout: int = 35) -> tuple[list[int], str]:
    """轮询等价格渲染出来，返回 (所有命中价, 最后一次文本)。"""
    deadline = time.time() + timeout
    best: list[int] = []
    text = ""
    while time.time() < deadline:
        text = _gather_text(page)
        found = _scan_prices(text, lo, hi)
        if found:
            best = found
            # 再多等一轮，让懒加载的第二批价格也进来
            time.sleep(2.5)
            text = _gather_text(page)
            more = _scan_prices(text, lo, hi)
            if more:
                best = more
            break
        time.sleep(1.5)
    return best, text


def _state_path(cfg: Config) -> Path:
    p = Path(cfg.root) / "data" / "verify_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class VerifySkip(Exception):
    """距上次核价太近，跳过。"""


def _check_cooldown(cfg: Config, minutes: int, force: bool) -> None:
    if force or minutes <= 0:
        return
    f = _state_path(cfg)
    if not f.exists():
        return
    try:
        import json

        last = float(json.loads(f.read_text(encoding="utf-8")).get("ts", 0))
    except Exception:
        return
    gap = (time.time() - last) / 60
    if gap < minutes:
        raise VerifySkip(
            f"距上次核价 {gap:.0f} 分钟，冷却期 {minutes} 分钟未到，本次跳过"
            "（OTA 反爬按 IP 计数，跑太勤会被临时封）"
        )


def _save_state(cfg: Config) -> None:
    try:
        import json

        _state_path(cfg).write_text(json.dumps({"ts": time.time()}), encoding="utf-8")
    except Exception:
        pass


def _real_ua(p) -> str:
    """用内核真实版本号拼 UA。

    Playwright 默认 UA 里带 "HeadlessChrome"，这是最明显的机器人标记，
    携程的 whaleguard 会直接返 432。这里把版本号取出来，去掉 Headless。
    """
    try:
        b = p.chromium.launch(headless=True)
        ver = b.version  # 例如 156.0.8078.4
        b.close()
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{ver} Safari/537.36"
        )
    except Exception:
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )


def verify(
    cfg: Config,
    dep: str,
    ret: str,
    platforms: list[str] | None = None,
    headed: bool = False,
    timeout: int = 35,
    force: bool = False,
) -> list[dict[str, Any]]:
    """逐个平台核价。返回每家的结果 dict。"""
    from playwright.sync_api import sync_playwright

    opts = cfg.raw.get("verify", {}) or {}
    _check_cooldown(cfg, int(opts.get("min_interval_minutes", 30)), force)
    lo = int(opts.get("min_price", 2000))
    hi = int(opts.get("max_price", 80000))
    shot_dir = Path(cfg.root) / (cfg.raw.get("report", {}).get("dir", "reports"))
    shot_dir.mkdir(parents=True, exist_ok=True)
    profile = Path(cfg.root) / "data" / "browser_profile"
    profile.mkdir(parents=True, exist_ok=True)

    targets = platforms or list(opts.get("platforms") or ["trip", "ly", "fliggy"])
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results: list[dict[str, Any]] = []

    with sync_playwright() as p:
        ua = _real_ua(p)
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=not headed,
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            user_agent=ua,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        try:
            for key in targets:
                meta = PLATFORMS.get(key, {"name": key})
                url = build_url(key, cfg, dep, ret)
                page = ctx.new_page()
                rec: dict[str, Any] = {
                    "platform": key,
                    "name": meta["name"],
                    "url": url,
                    "price": None,
                    "candidates": [],
                    "shot": "",
                    "note": "",
                    "elapsed": 0.0,
                }
                t0 = time.time()
                status = None
                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    status = getattr(resp, "status", None) if resp else None
                    prices, text = _wait_prices(page, lo, hi, timeout)
                    rec["elapsed"] = round(time.time() - t0, 1)
                    if prices:
                        kws = list(opts.get("airline_keywords") or ["Qatar", "卡塔尔"])
                        matched = _qr_prices(page, kws, lo, hi)
                        if matched:
                            rec["price"] = min(matched)
                            rec["candidates"] = sorted(set(matched))[:8]
                            rec["matched"] = True
                        else:
                            rec["price"] = min(prices)
                            rec["candidates"] = sorted(set(prices))[:8]
                            rec["matched"] = False
                            rec["note"] = "未识别到卡塔尔航空，给的是全页最低价（可能是别家航司）"
                    else:
                        low = text.lower()
                        if status in (403, 405, 429, 432) or "whaleguard" in low or "block" in low:
                            rec["note"] = (
                                f"被反爬拦截（HTTP {status}），本机 IP 被临时封，"
                                "过几十分钟再试；链接可手动打开"
                            )
                        elif "没有对应航班" in text or "无可用航班" in text:
                            rec["note"] = "该页无此航线（同程国际票需登录，见「中国平台最低价」）"
                        elif any(k in low for k in ("验证", "captcha", "拖动", "安全校验", "人机")):
                            rec["note"] = "被拦：需要人机验证"
                        elif "登录" in text and len(text) < 400:
                            rec["note"] = "被拦：要求登录"
                        else:
                            rec["note"] = f"未扫到价格（页面文本 {len(text)} 字）"
                    shot = shot_dir / f"verify_{stamp}_{key}.png"
                    page.screenshot(path=str(shot), full_page=False)
                    rec["shot"] = str(shot)
                except Exception as e:  # noqa: BLE001
                    rec["elapsed"] = round(time.time() - t0, 1)
                    rec["note"] = f"{type(e).__name__}: {str(e)[:120]}"
                finally:
                    try:
                        page.close()
                    except Exception:
                        pass
                results.append(rec)
        finally:
            ctx.close()
    _save_state(cfg)
    return results


def shot_data_uri(path: str) -> str:
    """截图转 data URI（报告里内嵌用；邮件里改用附件 CID）。"""
    try:
        b = Path(path).read_bytes()
    except Exception:
        return ""
    return "data:image/png;base64," + base64.b64encode(b).decode()


def summarize(results: list[dict[str, Any]], google_price: int | None) -> str:
    """一句话总结：几家里最低多少，比 Google 便宜还是贵。"""
    got = [r for r in results if r.get("price")]
    if not got:
        return "五家均未取到价格（反爬拦截），请点报告里的链接手动核价。"
    cheapest = min(got, key=lambda r: r["price"])
    txt = f"{cheapest['name']} ¥{cheapest['price']:,}"
    if google_price:
        diff = cheapest["price"] - google_price
        pct = diff / google_price * 100
        if diff < 0:
            txt += f"，比 Google 低 ¥{-diff:,}（{abs(pct):.1f}%）"
        elif diff > 0:
            txt += f"，比 Google 高 ¥{diff:,}（{pct:.1f}%）"
        else:
            txt += "，与 Google 持平"
    return txt
