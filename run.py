"""机票价格监控 —— 主入口。

用法：
    python run.py                 全量扫描 114 组合 + 生成报告 + 按需发邮件
    python run.py --mode quick    只扫上次 Top 组合及其邻近日期（约 40 个）
    python run.py --no-mail       不发邮件
    python run.py --no-open       不自动打开报告
    python run.py --limit 10      只跑前 10 个组合（联调用）
    python run.py --workers 2     并发数
"""
from __future__ import annotations

import argparse
import concurrent.futures
import os
import random
import sys
import time
import webbrowser
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent

# 被任务计划程序用 pythonw.exe 拉起时没有控制台，stdout 为 None，重定向到日志
if sys.stdout is None or sys.stderr is None:
    (ROOT_DIR / "logs").mkdir(exist_ok=True)
    _log = open(ROOT_DIR / "logs" / "scan.log", "a", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = _log
    if sys.stderr is None:
        sys.stderr = _log

sys.path.insert(0, str(ROOT_DIR))

from flightwatch import notify, report  # noqa: E402
from flightwatch import ota as ota_mod  # noqa: E402
from flightwatch.config import load, prepare_network  # noqa: E402
from flightwatch.dates import Combo, build as build_combos, resolve as resolve_window  # noqa: E402
from flightwatch.sources import google  # noqa: E402
from flightwatch.store import Store  # noqa: E402


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="机票价格监控")
    ap.add_argument("--mode", choices=["full", "quick"], default="full")
    ap.add_argument("--no-mail", action="store_true")
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--verify", action="store_true",
                    help="扫描结束后开真浏览器去携程/同程/飞猪核价（强制）")
    ap.add_argument("--verify-headed", action="store_true",
                    help="核价时显示浏览器窗口，方便看它卡在哪")
    ap.add_argument("--no-ota", action="store_true",
                    help="跳过中国平台（同程）最低价抓取")
    return ap.parse_args()


def _d(s: str) -> date:
    return date.fromisoformat(s)


def expand_quick(all_combos: list[Combo], seed: list[tuple[str, str]], radius: int = 2) -> list[Combo]:
    """以已有 Top 组合为中心，向四周扩展 ±radius 天，避免只盯一个点。"""
    if not seed:
        return all_combos
    want: set[tuple[date, date]] = set()
    for ds, rs in seed:
        d0, r0 = _d(ds), _d(rs)
        for i in range(-radius, radius + 1):
            for j in range(-radius, radius + 1):
                want.add((d0 + timedelta(days=i), r0 + timedelta(days=j)))
    picked = [c for c in all_combos if (c.dep, c.ret) in want]
    return picked or all_combos


def scan(combos: list[Combo], cfg: dict, workers: int) -> tuple[list[dict], list[tuple[str, str, str]]]:
    lo, hi = cfg["scan"].get("delay_seconds", [0.8, 1.6])
    offers: list[dict] = []
    failures: list[tuple[str, str, str]] = []
    done = 0
    total = len(combos)
    t0 = time.time()

    def work(c: Combo):
        time.sleep(random.uniform(lo, hi) * (0.4 if workers > 1 else 1.0))
        return c, google.fetch_with_retry(c, cfg)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futures = {ex.submit(work, c): c for c in combos}
        for fut in concurrent.futures.as_completed(futures):
            done += 1
            c = futures[fut]
            try:
                _, offer = fut.result()
                offers.append(offer)
                price = f"¥{offer['price']:,}"
            except Exception as e:
                failures.append((c.dep_str, c.ret_str, str(e)[:120]))
                price = "失败"
            bar = "#" * int(done / total * 28)
            print(
                f"\r[{bar:<28}] {done}/{total}  {c.dep_str}→{c.ret_str}  {price:<10}",
                end="",
                flush=True,
            )
    print(f"\n抓取完成，用时 {time.time() - t0:.0f}s")
    return offers, failures


def main() -> int:
    args = parse_args()
    cfg = load()
    prepare_network(cfg)
    raw = cfg.raw

    all_combos = build_combos(cfg.window)
    w_start, w_last, w_maxret, w_note = resolve_window(cfg.window)
    window_info = {
        "start": w_start.isoformat(),
        "last_dep": w_last.isoformat(),
        "max_return": w_maxret.isoformat(),
        "note": w_note,
    }
    store = Store(cfg.db_path())

    combos = all_combos
    if args.mode == "quick":
        combos = expand_quick(all_combos, store.top_combos(cfg.scan.get("quick_top_n", 20)))
    if args.limit:
        combos = combos[: args.limit]

    print(f"窗口 {w_start} ~ {w_last}（返程最晚 {w_maxret}，{w_note}）　"
          f"间隔 {cfg.window['gap_days']}　模式 {args.mode}　组合 {len(combos)}")
    print(f"航线 {raw['route']['origins'][0]} → {raw['route']['destination']}　"
          f"航司 {'/'.join(raw['route']['airlines'])}　最多 {raw['route']['max_stops']} 次中转\n")

    run_id = store.start_run("google", len(combos), args.mode)
    t0 = time.time()
    offers, failures = scan(combos, raw, args.workers)
    elapsed = time.time() - t0
    store.finish_run(run_id, len(offers), len(failures))

    degraded = not offers
    if degraded:
        print("⚠️  Google Flights 一个价格都没取到。检查梯子是否开启、能否访问 google.com。")
        print("   最近的错误：")
        for d, r, m in failures[:5]:
            print(f"   {d}→{r}: {m}")
        print("   改用中国平台数据生成降级报告…\n")

    # ---- 落库 ----
    for o in offers:
        store.add_offer(run_id, o)

    prev_prices: dict[tuple[str, str], int] = {}
    prev_run = store._conn.execute(
        "SELECT id FROM runs WHERE finished_at IS NOT NULL AND id<? ORDER BY id DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    if prev_run:
        prev_prices = store.prices_of_run(int(prev_run["id"]))

    hist_min: dict[tuple[str, str], int] = {}
    for o in offers:
        m = store._conn.execute(
            "SELECT MIN(price) AS p FROM offers WHERE dep_date=? AND ret_date=? "
            "AND run_id<? AND source='google'",
            (o["dep_date"], o["ret_date"], run_id),
        ).fetchone()
        if m and m["p"] is not None:
            hist_min[(o["dep_date"], o["ret_date"])] = int(m["p"])

    ranked = sorted(offers, key=lambda o: o["price"])
    best = ranked[0] if ranked else None
    if best:
        print(f"\n最低价：¥{best['price']:,}　{best['dep_date']} → {best['ret_date']}")

    # ---- 真浏览器核价（--verify）----
    verify_results: list[dict] = []
    v_cfg = raw.get("verify", {}) or {}
    want_verify = bool(args.verify)
    if not want_verify and best is not None:
        trigger = int(v_cfg.get("trigger_below", 0) or 0)
        want_verify = bool(v_cfg.get("auto", False)) and (trigger <= 0 or best["price"] < trigger)
    if best is None:
        want_verify = False
    if want_verify:
        try:
            from flightwatch import verify as verify_mod
        except Exception as e:  # noqa: BLE001
            print(f"核价模块不可用：{e}")
        else:
            plats = list(v_cfg.get("platforms") or ["trip", "ly", "fliggy"])
            print(f"\n开始真浏览器核价：{'、'.join(plats)}")
            try:
                verify_results = verify_mod.verify(
                    cfg,
                    best["dep_date"],
                    best["ret_date"],
                    platforms=plats,
                    headed=bool(args.verify_headed) or not v_cfg.get("headless", True),
                    timeout=int(v_cfg.get("timeout", 35)),
                    force=bool(args.verify),
                )
            except verify_mod.VerifySkip as e:
                print(f"⏳ {e}")
                verify_results = []
            except Exception as e:  # noqa: BLE001
                print(f"⚠️  核价失败（不影响扫描结果）：{type(e).__name__}: {str(e)[:160]}")
                verify_results = []
            for v in verify_results:
                v["dep_date"] = best["dep_date"]
                v["ret_date"] = best["ret_date"]
                if v.get("price"):
                    flag = "" if v.get("matched") is not False else "（全页最低，非卡塔尔）"
                    print(f"  {v['name']:<6} ¥{v['price']:,}{flag}")
                else:
                    print(f"  {v['name']:<6} {v.get('note') or '未取到'}")

    # ---- 中国平台最低价（同程国际站）----
    ota_rows: list[dict] = []
    if not args.no_ota:
        print("\n抓取中国平台最低价…")
        try:
            ota_res = ota_mod.fetch(cfg, window_info)
            ota_rows = ota_mod.summary(ota_res, top_n=5)

            # 同程报价落库（source='ly'），供后续历史对比与面板使用
            ly_offers = (ota_res.get("ly") or {}).get("offers") or []
            saved = 0
            for o in ly_offers:
                try:
                    o["currency"] = o.get("currency") or "CNY"
                    o["carriers"] = o.get("carriers") or "全航司最低"
                    store.add_offer(run_id, o)
                    saved += 1
                except Exception:  # noqa: BLE001
                    continue
            if saved:
                print(f"  同程 {saved} 个组合已入库")

            for r in ota_rows:
                if r.get("price"):
                    if best is None:
                        print(f"  {r['name']:<6} ¥{r['price']:,}  {r['dep']} → {r['ret']}  "
                              f"（Google 无数据，无法对比）")
                        continue
                    d = r["price"] - best["price"]
                    tag = "低于 Google" if d < 0 else "高于 Google"
                    print(f"  {r['name']:<6} ¥{r['price']:,}  {r['dep']} → {r['ret']}  "
                          f"（{tag} ¥{abs(d):,}）")
                else:
                    print(f"  {r['name']:<6} {r.get('status')}：{r.get('note')}")
        except Exception as e:  # noqa: BLE001
            print(f"⚠️  中国平台抓取失败（不影响 Google 结果）：{type(e).__name__}: {str(e)[:160]}")

    # ---- 飞猪核价（PC 站免登录，逐组核实际可订价）----
    fliggy_rows: list[dict] = []
    f_cfg = raw.get("fliggy", {}) or {}
    if f_cfg.get("enabled", False):
        top_n = max(1, int(f_cfg.get("probe_top_n", 5)))
        ly_run_offers: list[dict] = []
        for r in ota_rows:
            if r.get("key") == "ly":
                ly_run_offers = r.get("offers") or []
                break

        cand: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for o in ranked[:top_n]:
            key = (o["dep_date"], o["ret_date"])
            if key not in seen:
                seen.add(key)
                cand.append(key)
        for o in ly_run_offers[:top_n]:
            key = (o["dep"], o["ret"])
            if key not in seen:
                seen.add(key)
                cand.append(key)

        if cand:
            print(f"\n飞猪核价 {len(cand)} 个组合…")
            try:
                from flightwatch.sources import fliggy as fliggy_src
            except Exception as e:  # noqa: BLE001
                print(f"⚠️ 飞猪模块不可用：{type(e).__name__}: {e}")
            else:
                delay = f_cfg.get("delay_seconds", [1.0, 2.0])
                try:
                    with fliggy_src.Probe(
                        cfg,
                        headless=bool(f_cfg.get("headless", True)),
                        timeout=int(f_cfg.get("timeout", 25)),
                    ) as probe:
                        fails = 0
                        for i, (dep, ret) in enumerate(cand, 1):
                            try:
                                r = probe.fetch(dep, ret)
                            except fliggy_src.Blocked as e:
                                print(f"  [{i}/{len(cand)}] {dep} → {ret}  被拦：{e}")
                                print("  ⏸ 已停止飞猪核价（继续硬闯只会升级风控）。"
                                      "把 config.yaml 里 fliggy.headless 改成 false，"
                                      "手动过一次验证码再跑。")
                                break
                            except fliggy_src.NotRendered as e:
                                fails += 1
                                print(f"  [{i}/{len(cand)}] {dep} → {ret}  跳过：{e}")
                                if fails >= 3:
                                    print("  ⏸ 连续 3 组取不到价，疑似被短暂限流，本次放弃飞猪。"
                                          "过十几分钟或换个网络再试。")
                                    break
                                continue
                            except Exception as e:  # noqa: BLE001
                                fails += 1
                                print(f"  [{i}/{len(cand)}] {dep} → {ret}  失败："
                                      f"{type(e).__name__}: {str(e)[:70]}")
                                continue
                            fails = 0
                            fliggy_rows.append(r)
                            store.add_offer(run_id, r)
                            qr = (f"卡塔尔 ¥{r['qr_price']:,}" if r.get("qr_price")
                                  else "卡塔尔未列出")
                            print(f"  [{i}/{len(cand)}] {dep} → {ret}  "
                                  f"全航司最低 ¥{r['all_min']:,}　{qr}")
                            if i < len(cand):
                                time.sleep(random.uniform(*delay))
                except Exception as e:  # noqa: BLE001
                    print(f"⚠️ 飞猪核价不可用（不影响其他结果）：{type(e).__name__}: {str(e)[:120]}")

    # ---- 报告 ----
    matrix = {(o["dep_date"], o["gap_days"]): o["price"] for o in offers}
    path = report.build(cfg, run_id, offers, matrix, prev_prices, hist_min, elapsed, failures,
                        len(all_combos), window_info, verify_results, ota_rows, fliggy_rows)
    print(f"报告：{path}")
    print(f"库内累计 {store.stats()['offers']} 条报价 / {store.stats()['runs']} 次运行")

    # ---- 告警 ----
    if not args.no_mail and best is not None:
        reasons: list[str] = []
        best = ranked[0]
        key = (best["dep_date"], best["ret_date"])
        hmin = hist_min.get(key)
        prev = prev_prices.get(key)

        if not prev_prices:
            reasons.append("首次基线")
        else:
            if hmin is not None and best["price"] < hmin:
                reasons.append("历史新低")
            if prev and best["price"] < prev:
                pct = (prev - best["price"]) / prev * 100
                if pct >= raw["alerts"].get("drop_pct_combo", 8):
                    reasons.append(f"单组合跌 {pct:.1f}%")
            prev_best = min(prev_prices.values()) if prev_prices else None
            if prev_best and best["price"] < prev_best:
                pct = (prev_best - best["price"]) / prev_best * 100
                if pct >= raw["alerts"].get("drop_pct_overall", 5):
                    reasons.append(f"全局最低跌 {pct:.1f}%")

        if reasons and store.recently_alerted(best["dep_date"], best["ret_date"],
                                              raw["alerts"].get("cooldown_hours", 12)):
            print("告警已触发，但处于冷却期内，跳过发送。")
        elif reasons:
            subject, body = notify.build_alert_html(
                raw["route"]["origins"][0], raw["route"]["destination"],
                best, ranked[: raw["alerts"].get("top_n", 5)], reasons, path.name,
                verify_results,
            )
            ok, msg = notify.send(cfg, subject, body)
            print(f"邮件：{msg}")
            if ok:
                store.log_alert(best["dep_date"], best["ret_date"], "|".join(reasons), best["price"], prev)
        else:
            print("价格无显著变化，不发邮件。")

    if not args.no_open and cfg.report_cfg.get("auto_open", True):
        webbrowser.open(f"file:///{path.as_posix()}")

    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
