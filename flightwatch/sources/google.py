"""Google Flights 数据源（fast-flights 直连，走本机梯子）。

特点：
- 支持按航司过滤（QR）和中转次数过滤（max_stops=1 → 只保留经多哈一次中转）
- 单次往返查询约 0.6~0.9 秒，无配额限制
- 返回的 price 是【往返含税总价】，itinerary 里是去程航段明细
"""
from __future__ import annotations

import concurrent.futures
import random
import time
from datetime import date
from typing import Any
from urllib.parse import quote

from ..dates import Combo


class SourceError(RuntimeError):
    pass


def _import_lib():
    try:
        from fast_flights import FlightQuery, Passengers, create_query, get_flights
    except ImportError as e:  # pragma: no cover
        raise SourceError(
            "缺少 fast-flights 库，请运行：\n"
            '  "C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe" -m pip install fast-flights'
        ) from e
    return FlightQuery, Passengers, create_query, get_flights


def deep_link(dep: str, ret: str, origin: str, dest: str) -> str:
    q = f"Flights from {origin} to {dest} on {dep} through {ret}"
    return f"https://www.google.com/travel/flights?curr=CNY&hl=zh-CN&q={quote(q)}"


def _fmt_dt(sd) -> str:
    """SimpleDatetime -> '2026-11-12 23:35'"""
    try:
        y, m, d = sd.date
        hh, mm = sd.time
        return f"{y:04d}-{m:02d}-{d:02d} {hh:02d}:{mm:02d}"
    except Exception:
        return str(sd)


def _summarize(flight) -> tuple[str, int, int]:
    """把 Flights 对象压成一行行程摘要，返回 (itinerary, stops, duration_min)。"""
    segs = list(flight.flights or [])
    if not segs:
        return "", 0, 0
    parts = []
    total = 0
    for s in segs:
        total += int(getattr(s, "duration", 0) or 0)
        parts.append(
            f"{s.from_airport.code} {_fmt_dt(s.departure)} → {s.to_airport.code} {_fmt_dt(s.arrival)}"
        )
    return " | ".join(parts), max(len(segs) - 1, 0), total


def fetch_combo(combo: Combo, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """抓一个日期组合的最低价往返。失败抛 SourceError。"""
    FlightQuery, Passengers, create_query, get_flights = _import_lib()

    route = cfg["route"]
    origin = route["origins"][0]
    dest = route["destination"]

    q = create_query(
        flights=[
            FlightQuery(
                date=combo.dep_str,
                from_airport=origin,
                to_airport=dest,
                airlines=list(route.get("airlines") or []),
            ),
            FlightQuery(
                date=combo.ret_str,
                from_airport=dest,
                to_airport=origin,
                airlines=list(route.get("airlines") or []),
            ),
        ],
        trip="round-trip",
        seat=route.get("cabin", "economy"),
        passengers=Passengers(adults=int(route.get("adults", 1))),
        currency=route.get("currency", "CNY"),
        max_stops=route.get("max_stops", 1),
    )

    timeout = int(cfg.get("scan", {}).get("timeout", 40))
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(get_flights, q)
        try:
            results = fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError as e:
            raise SourceError(f"查询超时（>{timeout}s）") from e
        except Exception as e:  # FlightsNotFound 等
            raise SourceError(f"{type(e).__name__}: {e}") from e

    items = list(results or [])
    if not items:
        raise SourceError("无航班结果")

    best = min(items, key=lambda f: int(getattr(f, "price", 10**9) or 10**9))
    price = int(getattr(best, "price", 0) or 0)
    if price <= 0:
        raise SourceError("价格无效")

    itinerary, stops, duration = _summarize(best)
    carriers = ",".join(getattr(best, "airlines", []) or []) or ",".join(route.get("airlines") or [])

    return {
        "source": "google",
        "dep_date": combo.dep_str,
        "ret_date": combo.ret_str,
        "gap_days": combo.gap,
        "price": price,
        "currency": route.get("currency", "CNY"),
        "carriers": carriers,
        "stops": stops,
        "duration_min": duration,
        "itinerary": itinerary,
        "deep_link": deep_link(combo.dep_str, combo.ret_str, origin, dest),
    }


def fetch_with_retry(combo: Combo, cfg: dict[str, Any]) -> dict[str, Any] | None:
    retries = int(cfg.get("scan", {}).get("retries", 2))
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return fetch_combo(combo, cfg)
        except SourceError as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.0 + attempt * 1.5 + random.random())
    if last_err:
        raise last_err
    return None
