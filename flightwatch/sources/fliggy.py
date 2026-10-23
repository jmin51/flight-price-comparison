"""飞猪国际机票核价（PC 站，免登录）。

实测链路（2026-10-23）：
    sijipiao.fliggy.com/ie/flight_search_result.htm
      → login.taobao.com/jump → pass.fliggy.com（发游客 cookie2）
      → 回搜索结果页 200，直接渲染价格

要点：
  * 不登录即可出价，单组 3~7 秒，headless 可用
  * **页面价格符号是全角 ￥(U+FFE5)**，不要用半角 ¥ 匹配，否则永远 0 命中
  * 城市码用 SHA / SAO（与同程一致），不是机场码 PVG / GRU
  * 遇到验证码不破解：直接抛 Blocked，交给人工用有界面模式过一次
"""
from __future__ import annotations

import random
import re
import time
from pathlib import Path
from typing import Any

CITY = {"PVG": "SHA", "SHA": "SHA", "GRU": "SAO", "SAO": "SAO"}
CITY_NAME = {"SHA": "上海", "SAO": "圣保罗"}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")

BLOCK_KEYWORDS = ("拖动滑块", "请完成验证", "安全验证", "滑动验证", "访问验证", "异常流量")

# 取所有"往返含税￥xxxx"所在的卡片块（父子容器都会命中，Python 侧去重）
_EXTRACT_JS = """
() => {
  const out = [];
  document.querySelectorAll('div').forEach(d => {
    const t = d.innerText || '';
    if (t.length > 420) return;
    const m = t.match(/往返含税￥([\\d,]+)/);
    if (!m) return;
    out.push({price: +m[1].replace(/,/g, ''), text: t.replace(/\\s+/g, ' ').trim().slice(0, 220)});
  });
  return out;
}
"""


class Blocked(RuntimeError):
    """被风控拦截（验证码/异常流量）。遇到就整批停下，硬闯只会升级封禁。"""


class NotRendered(RuntimeError):
    """页面打开了但价格没渲染出来（多半是慢加载或被限流），重试一次仍失败就跳过这一组。"""


def search_url(dep: str, ret: str, origin: str = "PVG", dest: str = "GRU") -> str:
    dc = CITY.get(origin.upper(), origin.upper())
    ac = CITY.get(dest.upper(), dest.upper())
    return (
        "https://sijipiao.fliggy.com/ie/flight_search_result.htm?tripType=1"
        f"&depCity={dc}&arrCity={ac}"
        f"&depCityName={CITY_NAME.get(dc, dc)}&arrCityName={CITY_NAME.get(ac, ac)}"
        f"&depDate={dep}&arrDate={ret}"
    )


def _dedupe(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[int, str]] = set()
    out: list[dict[str, Any]] = []
    for c in cards:
        key = (int(c.get("price") or 0), str(c.get("text") or "")[:60])
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _stops_of(text: str) -> int | None:
    """『2程航班』= 1 次中转；『1程航班』= 直飞。"""
    m = re.search(r"(\d)\s*程航班", text)
    if not m:
        return None
    return max(0, int(m.group(1)) - 1)


class Probe:
    """复用同一个浏览器上下文逐组核价，省掉反复冷启动的开销。"""

    def __init__(self, cfg: Any, headless: bool = True, timeout: int = 25,
                 profile: str | Path | None = None) -> None:
        self.cfg = cfg
        self.headless = headless
        self.timeout = max(8, int(timeout))
        root = Path(getattr(cfg, "root", Path.cwd()))
        self.profile = Path(profile) if profile else root / "data" / "browser_profile"
        self._pw = None
        self.ctx = None
        self.page = None

    def __enter__(self) -> "Probe":
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self.ctx = self._pw.chromium.launch_persistent_context(
            str(self.profile),
            headless=self.headless,
            locale="zh-CN",
            viewport={"width": 1500, "height": 950},
            user_agent=UA,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            if self.ctx:
                self.ctx.close()
        finally:
            if self._pw:
                self._pw.stop()

    def fetch(self, dep: str, ret: str, origin: str = "PVG", dest: str = "GRU",
              retry: int = 2) -> dict[str, Any]:
        """核一组价。验证码 → Blocked；慢加载/限流 → 逐次加长冷却重试，仍失败抛 NotRendered。

        实测：持久 profile（有游客 cookie2）单组 2~6 秒；连续打十几次会被短暂限流，
        冷却十几秒到一分钟能自己恢复。全新 profile 反而建不起会话，别用。
        """
        assert self.page is not None
        route = getattr(self.cfg, "route", {}) or {}
        origin = str((route.get("origins") or [origin])[0])
        dest = str(route.get("destination") or dest)

        last_reason = ""
        for attempt in range(retry + 1):
            self.page.goto(search_url(dep, ret, origin, dest),
                           wait_until="domcontentloaded", timeout=self.timeout * 1000)
            rendered = True
            try:
                self.page.wait_for_function(
                    "() => /往返含税￥\\d/.test(document.body.innerText)",
                    timeout=self.timeout * 1000,
                )
            except Exception:  # noqa: BLE001
                rendered = False

            body = self.page.inner_text("body")
            for kw in BLOCK_KEYWORDS:
                if kw in body:
                    raise Blocked(f"风控验证：{kw}")

            if rendered:
                cards = _dedupe(self.page.evaluate(_EXTRACT_JS))
                prices = [int(c["price"]) for c in cards if c.get("price")]
                if prices:
                    qr_cards = [c for c in cards if "卡塔尔航空" in (c.get("text") or "")]
                    qr_price = min((int(c["price"]) for c in qr_cards), default=None)
                    qr_text = qr_cards[0]["text"] if qr_cards else ""
                    return {
                        "source": "fliggy",
                        "dep_date": dep,
                        "ret_date": ret,
                        "price": qr_price if qr_price else min(prices),
                        "qr_price": qr_price,
                        "all_min": min(prices),
                        "currency": "CNY",
                        "carriers": "卡塔尔航空" if qr_price else "全航司最低",
                        "stops": _stops_of(qr_text),
                        "note": "飞猪往返含税" + ("（卡塔尔）" if qr_price else "（全航司最低）"),
                    }
                last_reason = "有价格字样但没解析到数字"
            else:
                last_reason = "页面只渲染出骨架，价格未出"

            if attempt < retry:
                # 逐次加长冷却：10s → 20s，给限流窗口时间恢复
                time.sleep(10.0 * (attempt + 1))

        raise NotRendered(f"{last_reason}（已重试 {retry} 次）")


def fetch_many(cfg: Any, combos: list[tuple[str, str]], headless: bool = True,
               timeout: int = 25, delay: tuple[float, float] = (1.0, 2.0),
               on_progress=None) -> list[dict[str, Any]]:
    """逐组核价。任何一组被拦就停下（继续硬闯只会升级风控）。"""
    out: list[dict[str, Any]] = []
    total = len(combos)
    with Probe(cfg, headless=headless, timeout=timeout) as probe:
        for i, (dep, ret) in enumerate(combos, 1):
            if on_progress:
                on_progress(i, total, dep, ret, None)
            try:
                r = probe.fetch(dep, ret)
            except Blocked as e:
                if on_progress:
                    on_progress(i, total, dep, ret, str(e))
                break
            except Exception as e:  # noqa: BLE001
                if on_progress:
                    on_progress(i, total, dep, ret, f"{type(e).__name__}: {str(e)[:60]}")
                continue
            out.append(r)
            if on_progress:
                on_progress(i, total, dep, ret, "")
            if i < total:
                time.sleep(random.uniform(*delay))
    return out
