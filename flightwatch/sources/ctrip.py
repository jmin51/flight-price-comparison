"""携程国内站核价（flights.ctrip.com，免登录）。

与旧 verify.py 里的「携程国际 www.trip.com」不是一回事：
    www.trip.com  → Akamai whaleguard 按 IP 拦（HTTP 432），成功率约一半
    flights.ctrip.com/online/list/round-sha-sao → 国内站，headless 稳定可用

两步流程（这是关键，往返列表页给的是"去程 + 最低返程"的起价，返程不一定是卡塔尔）：
    1) 打开 round-{出发城市}-{到达城市}?depdate=去_回
    2) 在去程列表里挑卡塔尔航空最便宜的那条 → 点「选为去程」
    3) 进入返程列表，**只取返程航司是卡塔尔航空**的卡片
    4) 那条卡片上的价格就是「去程 QR + 返程 QR」的往返含税总价

城市码用三字城市码小写：上海 SHA、圣保罗 SAO。
"""
from __future__ import annotations

import random
import re
import time
from pathlib import Path
from typing import Any

CITY = {"PVG": "sha", "SHA": "sha", "GRU": "sao", "SAO": "sao"}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")

BLOCK_KEYWORDS = ("拖动滑块", "请完成验证", "安全验证", "滑动验证", "访问验证", "异常流量", "验证码")

_CARDS_JS = """
() => [...document.querySelectorAll('.flight-item')].map(c => {
  const t = (c.innerText || '').replace(/\\s+/g, ' ').trim();
  const m = t.match(/[¥￥]\\s?([\\d,]+)/);
  const air = (t.match(/^([\\u4e00-\\u9fa5]{2,10}航空)/) || [])[1] || '';
  return {air, price: m ? +m[1].replace(/,/g, '') : null, text: t.slice(0, 240), multi: /个航司/.test(t)};
})
"""


class Blocked(RuntimeError):
    """被风控拦截，整批停下，不做破解。"""


class NotRendered(RuntimeError):
    """页面没出列表/价格，或该组合没有卡塔尔舱位。"""


def search_url(dep: str, ret: str, origin: str = "PVG", dest: str = "GRU") -> str:
    o = CITY.get(origin.upper(), origin.lower())
    d = CITY.get(dest.upper(), dest.lower())
    return (f"https://flights.ctrip.com/online/list/round-{o}-{d}"
            f"?depdate={dep}_{ret}&cabin=y_s&adult=1&child=0&infant=0")


class Probe:
    def __init__(self, cfg: Any, headless: bool = True, timeout: int = 45,
                 profile: str | Path | None = None) -> None:
        self.cfg = cfg
        self.headless = headless
        self.timeout = max(15, int(timeout))
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
            viewport={"width": 1600, "height": 1000},
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

    def _check_block(self) -> None:
        body = self.page.inner_text("body")
        for kw in BLOCK_KEYWORDS:
            if kw in body:
                raise Blocked(f"风控验证：{kw}")

    def fetch(self, dep: str, ret: str, retry: int = 1) -> dict[str, Any]:
        assert self.page is not None
        route = getattr(self.cfg, "route", {}) or {}
        origin = str((route.get("origins") or ["PVG"])[0])
        dest = str(route.get("destination") or "GRU")

        last = ""
        for attempt in range(retry + 1):
            try:
                return self._fetch_once(dep, ret, origin, dest)
            except Blocked:
                raise
            except NotRendered as e:
                last = str(e)
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {str(e)[:80]}"
            if attempt < retry:
                time.sleep(3.0)
        raise NotRendered(last)

    def _fetch_once(self, dep: str, ret: str, origin: str, dest: str) -> dict[str, Any]:
        self.page.goto(search_url(dep, ret, origin, dest),
                       wait_until="domcontentloaded", timeout=self.timeout * 1000)
        try:
            self.page.wait_for_selector(".flight-item", timeout=self.timeout * 1000)
        except Exception:  # noqa: BLE001
            self._check_block()
            raise NotRendered("去程列表没出来")
        self._check_block()

        go_cards = self.page.evaluate(_CARDS_JS)
        qr_go = [c for c in go_cards if c["air"] == "卡塔尔航空" and c["price"]]
        if not qr_go:
            raise NotRendered("去程列表里没有卡塔尔航空")
        qr_go.sort(key=lambda c: c["price"])

        # 点「选为去程」进入返程列表：从最便宜的 QR 去程开始试，售罄的跳过
        clicked = False
        pick = qr_go[0]
        for cand in qr_go[:3]:
            clicked = self.page.evaluate("""(price) => {
              const cards = [...document.querySelectorAll('.flight-item')]
                .filter(c => /卡塔尔航空/.test(c.innerText||'')
                          && (c.innerText||'').includes(String(price)));
              for (const c of cards) {
                const btns = [...c.querySelectorAll('*')]
                  .filter(e => (e.innerText||'').trim() === '选为去程');
                if (btns.length) { btns[btns.length - 1].click(); return true; }
              }
              return false;
            }""", cand["price"])
            if clicked:
                pick = cand
                break
        if not clicked:
            raise NotRendered("前 3 条卡塔尔去程都没有可点的「选为去程」（可能售罄）")

        try:
            self.page.wait_for_function(
                "() => /已选去程/.test(document.body.innerText) && /选择返程/.test(document.body.innerText)",
                timeout=self.timeout * 1000,
            )
            self.page.wait_for_timeout(2500)
        except Exception:  # noqa: BLE001
            self._check_block()
            raise NotRendered("返程列表没出来")
        self._check_block()

        ret_cards = [c for c in self.page.evaluate(_CARDS_JS) if c["price"]]
        if not ret_cards:
            raise NotRendered("返程列表为空")

        qr_ret = [c for c in ret_cards if c["air"] == "卡塔尔航空" and not c["multi"]]
        all_min = min(c["price"] for c in ret_cards)
        qr_total = min((c["price"] for c in qr_ret), default=None)

        if qr_total is None:
            return {
                "source": "ctrip",
                "dep_date": dep,
                "ret_date": ret,
                "price": all_min,
                "qr_total": None,
                "all_min": all_min,
                "currency": "CNY",
                "carriers": "卡塔尔去程 + 非 QR 返程",
                "note": "携程：去程 QR，返程无 QR 舱位（已取全航司最低）",
            }

        return {
            "source": "ctrip",
            "dep_date": dep,
            "ret_date": ret,
            "price": qr_total,
            "qr_total": qr_total,
            "go_from": pick["price"],
            "all_min": all_min,
            "currency": "CNY",
            "carriers": "卡塔尔航空（去+返）",
            "note": "携程往返含税，去程与返程均为卡塔尔航空",
        }


def fetch_many(cfg: Any, combos: list[tuple[str, str]], headless: bool = True,
               timeout: int = 45, delay: tuple[float, float] = (2.0, 3.5),
               on_progress=None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    total = len(combos)
    with Probe(cfg, headless=headless, timeout=timeout) as probe:
        for i, (dep, ret) in enumerate(combos, 1):
            try:
                r = probe.fetch(dep, ret)
            except Blocked as e:
                if on_progress:
                    on_progress(i, total, dep, ret, f"被拦：{e}")
                break
            except Exception as e:  # noqa: BLE001
                if on_progress:
                    on_progress(i, total, dep, ret, f"跳过：{str(e)[:70]}")
                continue
            out.append(r)
            if on_progress:
                on_progress(i, total, dep, ret, "")
            if i < total:
                time.sleep(random.uniform(*delay))
    return out
