"""生成单文件 HTML 报告（无外部依赖，双击即看）。"""
from __future__ import annotations

import html
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .config import Config
from .sources.ctrip import search_url as _ctrip_search_url


def _money(p: int | None) -> str:
    return "—" if p is None else f"¥{p:,}"


def _link_ctrip(dep: str, ret: str) -> str:
    """携程国内站往返搜索页（免登录，点开即看价）。

    不再用携程国际 www.trip.com —— 那个被 Akamai whaleguard 按 IP 拦（HTTP 432）。
    """
    return _ctrip_search_url(dep, ret)


def _link_sky(dep: str, ret: str, o: str, d: str) -> str:
    a = dep[2:].replace("-", "")
    b = ret[2:].replace("-", "")
    return (
        f"https://www.skyscanner.net/transport/flights/{o.lower()}/{d.lower()}/{a}/{b}/"
        "?adults=1&cabinclass=economy"
    )


def _link_fliggy(dep: str, ret: str) -> str:
    return f"https://www.fliggy.com/iflight/?fromDate={dep}&backDate={ret}"


def _link_tc(dep: str, ret: str, o: str, d: str) -> str:
    """同程国际机票往返深链。

    注意：`/flights/itinerary/roundtrip/` 是**国内**产品，查 PVG→GRU 永远返回
    「没有对应航班」。国际机票在 `/iflight/`，搜索参数是星号分隔的 para：
        {出发城市}*{到达城市}*{去程}*{返程}*RT*1_0_0*Y|S|C|F
    城市码要用 SHA / SAO，不是机场码 PVG / GRU。
    这个页面未登录会跳登录页，登录后即直达结果。
    """
    city = {"PVG": "SHA", "SHA": "SHA", "GRU": "SAO", "SAO": "SAO"}
    dc = city.get(o.upper(), o.upper())
    ac = city.get(d.upper(), d.upper())
    para = f"{dc}*{ac}*{dep}*{ret}*RT*1_0_0*Y|S|C|F"
    return "https://www.ly.com/iflight/book1.html?para=" + quote(para, safe="*")


def _color_scale(prices: list[int]) -> dict[int, str]:
    """便宜=深绿，贵=浅灰。按分位分 6 档。"""
    if not prices:
        return {}
    uniq = sorted(set(prices))
    n = len(uniq)
    steps = [
        ("#0b6b3a", "#ffffff"),  # 最便宜：深绿底白字
        ("#17864a", "#ffffff"),
        ("#3fa86a", "#ffffff"),
        ("#8fcea3", "#1a1a1a"),
        ("#cfe8d8", "#1a1a1a"),
        ("#eef3f0", "#444444"),  # 最贵：浅灰
    ]
    out = {}
    for i, p in enumerate(uniq):
        idx = min(int(i / max(n, 1) * len(steps)), len(steps) - 1)
        out[p] = steps[idx]
    return out


CSS = """
* { box-sizing: border-box; }
body { margin:0; padding:28px 32px 60px; font-family: -apple-system, "Segoe UI",
       "Microsoft YaHei", sans-serif; background:#f7f8fa; color:#1a1a1a; }
h1 { font-size:22px; margin:0 0 4px; letter-spacing:-.3px; }
.sub { color:#6b7280; font-size:13px; margin-bottom:22px; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
         gap:14px; margin-bottom:28px; }
.card { background:#fff; border:1px solid #e5e7eb; border-radius:12px; padding:16px 18px;
        position:relative; }
.card.best { border-color:#0b6b3a; box-shadow:0 0 0 2px rgba(11,107,58,.12); }
.card .rank { position:absolute; top:12px; right:14px; font-size:11px; color:#9ca3af; }
.card .price { font-size:27px; font-weight:700; letter-spacing:-1px; color:#0b6b3a; }
.card .price small { font-size:12px; color:#6b7280; font-weight:400; letter-spacing:0; }
.card .dates { font-size:15px; font-weight:600; margin:8px 0 2px; }
.card .route { font-size:12px; color:#4b5563; line-height:1.55; }
.card .tags { margin-top:10px; display:flex; gap:6px; flex-wrap:wrap; }
.tag { font-size:11px; padding:2px 7px; border-radius:999px; background:#f1f5f3; color:#374151; }
.tag.newlow { background:#0b6b3a; color:#fff; }
.tag.drop { background:#dcfce7; color:#166534; }
.tag.muted { background:#f3f4f6; color:#6b7280; }
.card.domestic .price { color:#374151; }
.card.domestic { background:#fcfcfd; }
.h2note { font-size:12px; font-weight:400; color:#9ca3af; margin-left:8px; }
.links { margin-top:12px; display:flex; gap:10px; font-size:12px; flex-wrap:wrap; }
.links a { color:#2563eb; text-decoration:none; }
.links a:hover { text-decoration:underline; }
h2 { font-size:15px; margin:32px 0 12px; letter-spacing:.2px; }
table { border-collapse:separate; border-spacing:2px; font-size:12px; }
.matrix th { font-weight:600; color:#6b7280; padding:4px 8px; font-size:11px; }
.matrix td { padding:5px 9px; border-radius:4px; text-align:right;
             font-variant-numeric:tabular-nums; min-width:64px; }
.matrix td.day { background:transparent; color:#374151; text-align:left; font-weight:600;
                 padding-right:10px; }
.list { width:100%; background:#fff; border:1px solid #e5e7eb; border-radius:12px;
        border-spacing:0; overflow:hidden; }
.list th { background:#fafafa; text-align:left; padding:9px 12px; font-size:11px;
           color:#6b7280; border-bottom:1px solid #e5e7eb; }
.list td { padding:9px 12px; font-size:12px; border-bottom:1px solid #f3f4f6; }
.list tr:last-child td { border-bottom:none; }
.list tr:hover td { background:#fafefb; }
.num { text-align:right; font-variant-numeric:tabular-nums; }
.fail { color:#b91c1c; }
footer { margin-top:36px; color:#9ca3af; font-size:11px; line-height:1.7; }
"""

JS = """
function sortTable(tid, col){
  const t = document.getElementById(tid);
  const tb = t.tBodies[0];
  const rows = Array.from(tb.rows);
  const dir = t.dataset['s'+col] === 'asc' ? 'desc' : 'asc';
  t.dataset['s'+col] = dir;
  rows.sort((a,b)=>{
    let x=a.cells[col].dataset.v ?? a.cells[col].innerText;
    let y=b.cells[col].dataset.v ?? b.cells[col].innerText;
    const nx=parseFloat(x), ny=parseFloat(y);
    if(!isNaN(nx)&&!isNaN(ny)) return dir==='asc'?nx-ny:ny-nx;
    return dir==='asc'? String(x).localeCompare(y) : String(y).localeCompare(x);
  });
  rows.forEach(r=>tb.appendChild(r));
}
"""


def build(
    cfg: Config,
    run_id: int,
    best_offers: list[dict[str, Any]],
    matrix: dict[tuple[str, int], int],
    prev_prices: dict[tuple[str, str], int],
    hist_min: dict[tuple[str, str], int],
    elapsed: float,
    failures: list[tuple[str, str, str]],
    total_combos: int,
    window_info: dict[str, Any] | None = None,
    verify_results: list[dict[str, Any]] | None = None,
    ota_rows: list[dict[str, Any]] | None = None,
    fliggy_rows: list[dict[str, Any]] | None = None,
    fliggy_note: str = "",
    ctrip_rows: list[dict[str, Any]] | None = None,
    ctrip_note: str = "",
) -> Path:
    route = cfg.route
    origin = route["origins"][0]
    dest = route["destination"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    ranked = sorted(best_offers, key=lambda o: o["price"])
    top = ranked[: cfg.alerts.get("top_n", 5)]
    best_price = ranked[0]["price"] if ranked else None
    all_prices = [o["price"] for o in ranked]
    scale = _color_scale(all_prices)

    # ---- Top 卡片 ----
    cards = []
    for i, o in enumerate(top, 1):
        price = o["price"]
        prev = prev_prices.get((o["dep_date"], o["ret_date"]))
        hmin = hist_min.get((o["dep_date"], o["ret_date"]))
        is_new_low = hmin is not None and price < hmin
        delta_html = ""
        if prev and price != prev:
            pct = (price - prev) / prev * 100
            if pct <= -0.5:
                delta_html = f'<span class="tag drop">↓ {prev - price:,} ({pct:+.1f}%)</span>'

        tags = [f'<span class="tag">{html.escape(o.get("carriers") or "QR")}</span>']
        stops = o.get("stops")
        if stops is not None:
            stops_txt = "直飞" if stops == 0 else f"{stops} 次中转"
            tags.append(f'<span class="tag">{stops_txt}</span>')
        tags.append(f'<span class="tag">{o["gap_days"]} 天</span>')
        if is_new_low:
            tags.append('<span class="tag newlow">历史新低</span>')
        if delta_html:
            tags.append(delta_html)

        cards.append(
            f"""
        <div class="card{' best' if i == 1 else ''}">
          <div class="rank">#{i}</div>
          <div class="price">{_money(price)} <small>往返含税</small></div>
          <div class="dates">{o['dep_date']} → {o['ret_date']}</div>
          <div class="route">{html.escape(o.get('itinerary') or '')}</div>
          <div class="tags">{''.join(tags)}</div>
          <div class="links">
            <a href="{o['deep_link']}" target="_blank">Google Flights</a>
            <a href="{_link_ctrip(o['dep_date'], o['ret_date'])}" target="_blank">携程</a>
            <a href="{_link_sky(o['dep_date'], o['ret_date'], origin, dest)}" target="_blank">天巡</a>
            <a href="{_link_tc(o['dep_date'], o['ret_date'], origin, dest)}" target="_blank">同程旅行</a>
            <a href="{_link_fliggy(o['dep_date'], o['ret_date'])}" target="_blank">飞猪</a>
          </div>
        </div>"""
        )

    # ---- 国内平台 Top5（只算「去返都是卡塔尔」的真实可订价）----
    ly_row = next((r for r in (ota_rows or []) if r.get("key") == "ly"), None)
    ly_offers = (ly_row or {}).get("offers") or []
    ly_best = ly_offers[0]["price"] if ly_offers else None

    dom_pick: dict[tuple[str, str], dict[str, Any]] = {}
    for r in (ctrip_rows or []):
        if r.get("qr_total"):
            dom_pick[(r["dep_date"], r["ret_date"])] = {
                "price": r["qr_total"], "platform": "携程", "all_min": r.get("all_min")}
    for r in (fliggy_rows or []):
        qp = r.get("qr_price")
        if not qp:
            continue
        key = (r["dep_date"], r["ret_date"])
        cur = dom_pick.get(key)
        if cur is None or qp < cur["price"]:
            dom_pick[key] = {"price": qp, "platform": "飞猪", "all_min": r.get("all_min")}

    dom_sorted = sorted(dom_pick.items(), key=lambda kv: kv[1]["price"])[: cfg.alerts.get("top_n", 5)]
    dom_best = dom_sorted[0][1]["price"] if dom_sorted else None

    dom_cards = []
    for i, ((dep, ret), info) in enumerate(dom_sorted, 1):
        try:
            gap = (date.fromisoformat(ret) - date.fromisoformat(dep)).days
        except ValueError:
            gap = None
        diff = ""
        if best_price:
            d = info["price"] - best_price
            if d > 0:
                diff = f'<span class="tag muted">比 Google 高 ¥{d:,}</span>'
            elif d < 0:
                diff = f'<span class="tag drop">比 Google 低 ¥{-d:,}</span>'
            else:
                diff = '<span class="tag">与 Google 持平</span>'
        tags = [
            f'<span class="tag">{info["platform"]}</span>',
            f'<span class="tag">{gap} 天</span>' if gap else "",
            '<span class="tag">去返都是 QR</span>',
            diff,
        ]
        dom_cards.append(
            f"""
        <div class="card domestic">
          <div class="rank">#{i}</div>
          <div class="price">{_money(info['price'])} <small>往返含税</small></div>
          <div class="dates">{dep} → {ret}</div>
          <div class="route">{info["platform"]} 实取价，去程与返程均为卡塔尔航空</div>
          <div class="tags">{''.join(t for t in tags if t)}</div>
          <div class="links">
            <a href="{_link_tc(dep, ret, origin, dest)}" target="_blank">同程旅行</a>
            <a href="{_link_ctrip(dep, ret)}" target="_blank">携程</a>
            <a href="{_link_fliggy(dep, ret)}" target="_blank">飞猪</a>
          </div>
        </div>"""
        )

    ly_hint = ""
    if ly_best:
        ly_hint = (
            '<div class="sub" style="margin-top:-4px;margin-bottom:10px;">'
            f'参考：同程全航司最低 {_money(ly_best)}（去程最低＋返程最低合成，'
            '<strong>去返可能不是同一家航司</strong>，口径不同，不参与本榜）。</div>'
        )

    domestic_html = (
        '<div class="cards">' + "".join(dom_cards) + "</div>"
        if dom_cards
        else ('<p style="color:#9ca3af;font-size:13px;">携程/飞猪这次没核到「双程都是卡塔尔」的价，'
              '所以国内榜为空 —— 不是没有票，是没核到。</p>')
    )

    # ---- 携程核价（国内站，去返双程 QR）----
    ctrip_html = ""
    if ctrip_rows:
        g_map_c = {(o["dep_date"], o["ret_date"]): o["price"] for o in ranked}
        crs = []
        for r in ctrip_rows:
            tot = r.get("qr_total")
            g = g_map_c.get((r["dep_date"], r["ret_date"]))
            if tot:
                cell = f'<b style="color:#0b6b3a;font-size:14px;">{_money(tot)}</b>'
                if g:
                    d = tot - g
                    if d > 0:
                        diff = f'<span class="tag muted">比 Google 高 ¥{d:,}</span>'
                    elif d < 0:
                        diff = f'<span class="tag drop">比 Google 低 ¥{-d:,}</span>'
                    else:
                        diff = '<span class="tag">与 Google 持平</span>'
                else:
                    diff = "—"
                mark = '<span class="tag">去返都是 QR</span>'
            else:
                cell = '<span style="color:#9ca3af;">无 QR 返程</span>'
                diff = "—"
                mark = '<span class="tag muted">返程非卡塔尔</span>'
            crs.append(
                f"<tr><td style='padding:9px 12px;white-space:nowrap'>"
                f"{r['dep_date']} → {r['ret_date']}</td>"
                f"<td style='padding:9px 12px;white-space:nowrap'>{cell}</td>"
                f"<td style='padding:9px 12px;color:#374151;'>{_money(r.get('all_min'))}</td>"
                f"<td style='padding:9px 12px;'>{diff}</td>"
                f"<td style='padding:9px 12px;'>{mark}</td></tr>"
            )
        ctrip_html = f"""
<h2>携程核价 · 往返双程均为卡塔尔 <span class="h2note">国内站 flights.ctrip.com，免登录</span></h2>
<div class="sub" style="margin-top:-6px;margin-bottom:10px;">
先选中卡塔尔去程，再在返程列表里只取<strong>返程航司也是卡塔尔</strong>的舱位，
所以这一列是真正的「QR 去 + QR 返」往返含税总价。
</div>
<table class="list"><thead><tr>
  <th>组合</th><th>双程 QR 往返价</th><th>全航司最低</th><th>对比 Google</th><th>口径</th>
</tr></thead><tbody>{''.join(crs)}</tbody></table>
"""
    elif ctrip_note:
        ctrip_html = (
            '<h2>携程核价 <span class="h2note">本次未取到</span></h2>'
            '<p style="color:#9ca3af;font-size:13px;margin:0 0 4px;">'
            f"{html.escape(ctrip_note)}</p>"
        )

    # ---- 飞猪核价（实际可订价）----
    fliggy_html = ""
    if fliggy_rows:
        g_map = {(o["dep_date"], o["ret_date"]): o["price"] for o in ranked}
        frs = []
        for r in fliggy_rows:
            qr = r.get("qr_price")
            g = g_map.get((r["dep_date"], r["ret_date"]))
            price_cell = (f'<b style="color:#0b6b3a;font-size:14px;">{_money(qr)}</b>'
                          if qr else '<span style="color:#9ca3af;">未列出</span>')
            if qr and g:
                d = qr - g
                if d > 0:
                    diff = f'<span class="tag muted">比 Google 高 ¥{d:,}</span>'
                elif d < 0:
                    diff = f'<span class="tag drop">比 Google 低 ¥{-d:,}</span>'
                else:
                    diff = '<span class="tag">与 Google 持平</span>'
            elif g:
                diff = '<span class="tag muted">飞猪无卡塔尔舱位</span>'
            else:
                diff = "—"
            stops = r.get("stops")
            stops_txt = "—" if stops is None else ("直飞" if stops == 0 else f"{stops} 次中转")
            frs.append(
                f"<tr><td style='padding:9px 12px;white-space:nowrap'>"
                f"{r['dep_date']} → {r['ret_date']}</td>"
                f"<td style='padding:9px 12px;white-space:nowrap'>{price_cell}</td>"
                f"<td style='padding:9px 12px;color:#374151;'>{_money(r.get('all_min'))}</td>"
                f"<td style='padding:9px 12px;'>{diff}</td>"
                f"<td style='padding:9px 12px;color:#6b7280;'>{stops_txt}</td></tr>"
            )
        fliggy_html = f"""
<h2>飞猪核价 · 实际可订价 <span class="h2note">PC 站免登录，逐组打开搜索页读取</span></h2>
<div class="sub" style="margin-top:-6px;margin-bottom:10px;">
飞猪给的是<strong>能下单的往返含税价</strong>；Google 那一栏多为展示价，两者差异通常来自舱位、
税费与缓存，下单前以飞猪/航司官网为准。
</div>
<table class="list"><thead><tr>
  <th>组合</th><th>卡塔尔航空</th><th>全航司最低</th><th>对比 Google</th><th>中转</th>
</tr></thead><tbody>{''.join(frs)}</tbody></table>
"""
    elif fliggy_note:
        fliggy_html = (
            '<h2>飞猪核价 <span class="h2note">本次未取到</span></h2>'
            '<p style="color:#9ca3af;font-size:13px;margin:0 0 4px;">'
            f"{html.escape(fliggy_note)}</p>"
        )

    # ---- 其他国内平台（抓不到价，只给状态和跳转）----
    ref = ranked[0] if ranked else ({"dep": ly_offers[0]["dep"], "ret": ly_offers[0]["ret"]}
                                    if ly_offers else None)
    other_trs = []
    if ly_best:
        b = ly_offers[0]
        other_trs.append(
            f"<tr><td style='padding:9px 12px;'><b>同程旅行</b></td>"
            f"<td style='padding:9px 12px;color:#6b7280;'>¥{ly_best:,}</td>"
            f"<td style='padding:9px 12px;color:#6b7280;font-size:12px;'>"
            f"全航司最低，去程+返程单程合成，去返可能不同航司（仅参考）</td>"
            f"<td style='padding:9px 12px;'>"
            f"<a href='{_link_tc(b['dep'], b['ret'], origin, dest)}' target='_blank'>打开同程</a>"
            f" <span style='color:#9ca3af;'>（{b['dep']} → {b['ret']}）</span></td></tr>"
        )
    for r in (ota_rows or []):
        key = r.get("key")
        if key != "fliggy":      # 携程已单独成块，这里只留飞猪
            continue
        if ref:
            dep = ref.get("dep_date") or ref.get("dep") or ""
            ret = ref.get("ret_date") or ref.get("ret") or ""
            if key == "trip":
                link = f'<a href="{_link_ctrip(dep, ret)}" target="_blank">打开携程</a>'
            else:
                link = f'<a href="{_link_fliggy(dep, ret)}" target="_blank">打开飞猪</a>'
            link += f' <span style="color:#9ca3af;">（按 {dep} → {ret}）</span>'
        else:
            link = "—"
        other_trs.append(
            f"<tr><td style='padding:9px 12px;'><b>{html.escape(r.get('name') or '')}</b></td>"
            f"<td style='padding:9px 12px;color:#9ca3af;'>未取价</td>"
            f"<td style='padding:9px 12px;color:#6b7280;font-size:12px;'>"
            f"{html.escape(r.get('note') or '')}</td>"
            f"<td style='padding:9px 12px;'>{link}</td></tr>"
        )

    other_html = ""
    if other_trs:
        other_html = f"""
<h2>其他国内平台 <span class="h2note">同程为全航司参考价；天巡等只给跳转</span></h2>
<table class="list"><thead><tr>
  <th>平台</th><th>价格</th><th>说明</th><th>手动查看</th>
</tr></thead><tbody>{''.join(other_trs)}</tbody></table>
"""

    # ---- 价格矩阵 ----
    dep_dates = sorted({d for (d, _g) in matrix.keys()})
    gaps = sorted({g for (_d, g) in matrix.keys()})
    head = "".join(f"<th>间隔 {g} 天</th>" for g in gaps)
    rows_html = []
    for d in dep_dates:
        tds = [f'<td class="day">{d[5:]}</td>']
        for g in gaps:
            p = matrix.get((d, g))
            if p is None:
                tds.append('<td style="background:#fafafa;color:#d1d5db">—</td>')
            else:
                bg, fg = scale.get(p, ("#eef3f0", "#444"))
                tds.append(f'<td style="background:{bg};color:{fg}">{p:,}</td>')
        rows_html.append("<tr>" + "".join(tds) + "</tr>")

    # ---- 全量列表 ----
    list_rows = []
    for i, o in enumerate(ranked, 1):
        hmin = hist_min.get((o["dep_date"], o["ret_date"]))
        mark = " 🟢" if (hmin is not None and o["price"] < hmin) else ""
        list_rows.append(
            f"""<tr>
              <td class="num">{i}</td>
              <td>{o['dep_date']}</td>
              <td>{o['ret_date']}</td>
              <td class="num">{o['gap_days']}</td>
              <td class="num" data-v="{o['price']}"><b>{o['price']:,}</b>{mark}</td>
              <td>{html.escape(o.get('carriers') or '')}</td>
              <td class="num">{o.get('stops', '')}</td>
              <td>{html.escape((o.get('itinerary') or '')[:90])}</td>
            </tr>"""
        )

    fail_html = ""
    if failures:
        items = "".join(
            f"<li>{d} → {r}：{html.escape(str(m))}</li>" for d, r, m in failures[:15]
        )
        fail_html = f'<h2>未取到价格（{len(failures)} 个）</h2><ul class="fail">{items}</ul>'

    win = window_info or {}
    win_start = win.get("start") or cfg.window.get("start", "")
    win_last = win.get("last_dep") or cfg.window.get("latest_departure", "")
    win_note = win.get("note", "")
    note_html = f"（{win_note}）" if win_note else ""

    # ---- 真浏览器核价区 ----
    verify_html = ""
    if verify_results:
        from .verify import shot_data_uri, summarize

        vrows = []
        for v in verify_results:
            if v.get("price"):
                cell = f'<b style="color:#0b6b3a;font-size:15px;">¥{v["price"]:,}</b>'
                if v.get("matched") is False:
                    cell += ' <span class="tag">非卡塔尔</span>'
                diff = ""
                if best_price:
                    d = v["price"] - best_price
                    if d < 0:
                        diff = f'<span class="tag drop">比 Google 低 ¥{-d:,}</span>'
                    elif d > 0:
                        diff = f'<span class="tag">比 Google 高 ¥{d:,}</span>'
                    else:
                        diff = '<span class="tag">与 Google 持平</span>'
                cell += f' {diff}'
            else:
                cell = f'<span style="color:#9ca3af;">{html.escape(v.get("note") or "未取到")}</span>'
            shot = v.get("shot") or ""
            img = ""
            if shot:
                uri = shot_data_uri(shot)
                if uri:
                    img = (f'<br><img src="{uri}" style="max-width:100%;margin-top:8px;'
                           f'border:1px solid #e5e7eb;border-radius:6px;">')
            vrows.append(
                f'<tr><td style="padding:8px;border-bottom:1px solid #f3f4f6;white-space:nowrap">'
                f'<a href="{v["url"]}" target="_blank">{html.escape(v["name"])}</a></td>'
                f'<td style="padding:8px;border-bottom:1px solid #f3f4f6">{cell}'
                f'<div style="color:#9ca3af;font-size:11px;">耗时 {v.get("elapsed", 0)}s</div>{img}</td></tr>'
            )
        summary = summarize(verify_results, best_price)
        verify_html = f"""
<h2>真浏览器核价（{verify_results[0].get("dep_date", "")} → {verify_results[0].get("ret_date", "")}）</h2>
<div class="sub" style="margin-bottom:10px;">
{html.escape(summary)}
</div>
<table style="width:100%;border-collapse:collapse;font-size:13px;background:#fff;
border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;">
<tbody>{''.join(vrows)}</tbody></table>
"""

    # ---- 降级提示（Google 无数据时）----
    notice_html = ""
    if not ranked:
        notice_html = (
            '<div style="background:#fff7ed;border:1px solid #fed7aa;color:#9a3412;'
            'padding:10px 14px;border-radius:8px;margin:14px 0;font-size:13px;line-height:1.7;">'
            '<b>⚠️ 本次没有取到 Google Flights 任何价格</b>（通常是梯子未开 / 访问不了 google.com）。'
            '下面「国外平台 Top 5」与价格矩阵为空，「国内平台 Top 5」仍可用。'
            '开启梯子后重新扫描即可恢复正常。</div>'
        )

    body = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>机票监控报告 {now}</title><style>{CSS}</style></head><body>
<h1>{origin} → {dest}｜卡塔尔航空 · 多哈中转</h1>
<div class="sub">扫描时间 {now}　·　窗口 {win_start} ~ {win_last}{note_html}　·　
间隔 {"/".join(map(str, cfg.window['gap_days']))} 天　·　
有效组合 {len(ranked)}/{total_combos}　·　耗时 {elapsed:.0f}s　·　
国外最低 <b>{_money(best_price)}</b>　·　国内最低（双程 QR）<b>{_money(dom_best)}</b></div>

{notice_html}

<h2>国外平台 · Top 5 <span class="h2note">Google Flights · 卡塔尔航空 · 多哈中转 · 最多 1 次中转</span></h2>
<div class="cards">{''.join(cards) if cards else '<p style="color:#9ca3af;font-size:13px;">本次没有取到 Google 价格。</p>'}</div>

<h2>国内平台 · Top 5 <span class="h2note">携程 / 飞猪实取价 · 去返双程均为卡塔尔</span></h2>
{ly_hint}
{domestic_html}

{ctrip_html}

{fliggy_html}

{other_html}

{verify_html}

<h2>价格矩阵（行 = 出发日，列 = 间隔天数）</h2>
<table class="matrix"><thead><tr><th></th>{head}</tr></thead>
<tbody>{''.join(rows_html)}</tbody></table>

<h2>全部组合（点击表头排序）</h2>
<table class="list" id="allList">
<thead><tr>
  <th onclick="sortTable('allList',0)">#</th>
  <th onclick="sortTable('allList',1)">去程</th>
  <th onclick="sortTable('allList',2)">返程</th>
  <th onclick="sortTable('allList',3)">间隔</th>
  <th onclick="sortTable('allList',4)">往返价</th>
  <th onclick="sortTable('allList',5)">航司</th>
  <th onclick="sortTable('allList',6)">中转</th>
  <th>去程行程</th>
</tr></thead>
<tbody>{''.join(list_rows)}</tbody></table>

{fail_html}

<footer>
数据源：Google Flights（fast-flights 直连，按卡塔尔航空 + 最多 1 次中转过滤）· 价格为往返含税总价。<br>
国内平台：同程旅行国际站价格日历（全航司，不限卡塔尔）· 往返价 = 去程最低 + 返程最低，
<strong>去返程不保证是同一家航司</strong>，与 Google 的「同一航司往返」不是同一口径，跨区差价仅供参考。<br>
携程被 Akamai 拦截、飞猪需设备指纹、天巡有 PerimeterX 验证，均只提供跳转链接。<br>
🟢 = 该组合创历史新低。
</footer>
<script>{JS}</script>
</body></html>"""

    out = cfg.report_dir() / f"report_{datetime.now():%Y%m%d_%H%M}.html"
    out.write_text(body, encoding="utf-8")
    latest = cfg.report_dir() / "latest.html"
    latest.write_text(body, encoding="utf-8")
    return out
