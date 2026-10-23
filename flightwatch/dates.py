"""日期组合生成。

支持两种窗口模式：
- relative（默认）：以"运行当天"为基准往后推。
  start = 今天 + offset_days，出发日共 span_days 天，
  返程最晚 = 最晚出发日 + tail_days。
  例：今天 2026-10-22，offset=10 → 窗口 11-01 ~ 12-08；明天跑就是 11-02 ~ 12-09。
- fixed：写死日期，用于临时锁定某段窗口。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


@dataclass(frozen=True)
class Combo:
    dep: date
    ret: date
    gap: int

    @property
    def dep_str(self) -> str:
        return self.dep.isoformat()

    @property
    def ret_str(self) -> str:
        return self.ret.isoformat()

    @property
    def nights(self) -> int:
        return (self.ret - self.dep).days


def resolve(window: dict, today: date | None = None) -> tuple[date, date, date, str]:
    """解析窗口，返回 (最早出发日, 最晚出发日, 最晚返程日, 计算说明)。"""
    mode = (window.get("mode") or "fixed").lower()
    gaps = list(window.get("gap_days", [3, 4, 5]))

    if mode == "relative":
        base = today or date.today()
        offset = int(window.get("offset_days", 10))
        span = int(window.get("span_days", 38))
        start = base + timedelta(days=offset)
        last_dep = start + timedelta(days=max(span - 1, 0))
        tail = window.get("tail_days")
        tail_days = int(tail) if tail is not None else (max(gaps) if gaps else 3)
        max_ret = last_dep + timedelta(days=tail_days)
        note = f"按今天 {base:%Y-%m-%d} +{offset} 天滚动"
        return start, last_dep, max_ret, note

    start = _d(window["start"])
    last_dep = _d(window["latest_departure"])
    max_ret = _d(window["max_return"])
    return start, last_dep, max_ret, "固定窗口"


def build(window: dict, today: date | None = None) -> list[Combo]:
    """按窗口和间隔展开全部 (去程, 返程) 组合。"""
    start, last_dep, max_ret, _ = resolve(window, today)
    gaps = list(window.get("gap_days", [3, 4, 5]))

    combos: list[Combo] = []
    d = start
    while d <= last_dep:
        for g in gaps:
            r = d + timedelta(days=g)
            if r <= max_ret:
                combos.append(Combo(dep=d, ret=r, gap=g))
        d += timedelta(days=1)
    return combos
