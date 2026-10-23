"""机票监控 · 本地控制面板。

把「一键扫描 / 快速扫描 / 打开最新报告」收进一个网页界面。

用法：
    双击 启动面板.bat
    或 python panel.py [--port 5001] [--no-browser]

关闭那个黑窗口即停止服务。旧 bat 全部保留，可继续单独用。
"""
from __future__ import annotations

import io
import json
import os
import queue
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    from flask import Flask, Response, jsonify, request, send_file
except ImportError:  # pragma: no cover
    print("缺少 flask，请先执行：")
    print(f'  "{sys.executable}" -m pip install flask')
    raise SystemExit(1)

from flightwatch.config import _detect_system_proxy, load  # noqa: E402
from flightwatch.dates import build as build_combos  # noqa: E402
from flightwatch.dates import resolve as resolve_window  # noqa: E402

PY = sys.executable
CFG = load()
DB = CFG.db_path()
OTA_CACHE = ROOT / "data" / "ota_cache.json"
REPORTS = CFG.report_dir()

app = Flask(__name__, static_folder=None)

STATE: dict = {
    "proc": None,
    "mode": None,
    "started_at": None,
    "done": 0,
    "total": 0,
    "last_price": "",
    "lines": [],
    "code": None,
    "error": None,
}
LOCK = threading.Lock()
SUBSCRIBERS: set[queue.Queue] = set()
_REUSED = False

RE_PROGRESS = re.compile(r"(\d+)/(\d+)\s+(\d{4}-\d{2}-\d{2})→(\d{4}-\d{2}-\d{2})\s+(\S+)")
RE_COMBO_TOTAL = re.compile(r"组合\s+(\d+)")
RE_OTA = re.compile(r"同程旅行\s+¥([\d,]+)\s+(\d{4}-\d{2}-\d{2})\s+→\s+(\d{4}-\d{2}-\d{2})")
RE_OTA_DIFF = re.compile(r"（(低于|高于) Google ¥([\d,]+)）")
RE_REPORT = re.compile(r"报告：(.+\.html)")


# ---------------------------------------------------------------- 事件广播
def _broadcast(evt: dict) -> None:
    for q in list(SUBSCRIBERS):
        try:
            q.put_nowait(evt)
        except Exception:  # noqa: BLE001
            pass


def _emit(text: str) -> None:
    STATE["lines"].append(text)
    if len(STATE["lines"]) > 500:
        del STATE["lines"][:100]
    _broadcast({"type": "log", "text": text})


def _is_running() -> bool:
    p = STATE["proc"]
    return bool(p and p.poll() is None)


# ---------------------------------------------------------------- 扫描
def _handle_line(text: str) -> None:
    t = text.strip()
    if not t:
        return

    m = RE_PROGRESS.search(t)
    if m:
        STATE["done"] = int(m.group(1))
        STATE["total"] = int(m.group(2))
        STATE["last_price"] = m.group(5)
        _broadcast({
            "type": "progress",
            "done": STATE["done"],
            "total": STATE["total"],
            "combo": f"{m.group(3)} → {m.group(4)}",
            "price": m.group(5),
        })
        return

    m = RE_COMBO_TOTAL.search(t)
    if m:
        STATE["total"] = int(m.group(1))
        _broadcast({"type": "progress", "done": 0, "total": STATE["total"],
                    "combo": "", "price": ""})

    m = RE_OTA.search(t)
    if m:
        price = int(m.group(1).replace(",", ""))
        diff = RE_OTA_DIFF.search(t)
        cache = {
            "price": price,
            "dep": m.group(2),
            "ret": m.group(3),
            "compare": f"{diff.group(1)} Google ¥{diff.group(2)}" if diff else "",
            "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        try:
            OTA_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        _broadcast({"type": "ota", "ota": cache})

    m = RE_REPORT.search(t)
    if m:
        _broadcast({"type": "report", "path": m.group(1).strip()})


def _run_scan(mode: str) -> None:
    cmd = [PY, str(ROOT / "run.py"), "--mode", mode, "--no-open"]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )
    except Exception as e:  # noqa: BLE001
        with LOCK:
            STATE["proc"] = None
            STATE["error"] = f"启动失败：{e}"
        _emit(f"[面板] 启动失败：{e}")
        _broadcast({"type": "done", "code": -1})
        return

    with LOCK:
        STATE["proc"] = proc
        STATE["mode"] = mode
        STATE["started_at"] = time.time()
        STATE["done"] = 0
        STATE["last_price"] = ""
        STATE["code"] = None
        STATE["error"] = None

    _broadcast({"type": "started", "mode": mode})

    stream = io.TextIOWrapper(proc.stdout, encoding="utf-8", errors="replace", newline="")
    buf = ""
    while True:
        ch = stream.read(1)
        if not ch:
            break
        if ch in ("\r", "\n"):
            if buf.strip():
                _handle_line(buf)
                _emit(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        _handle_line(buf)
        _emit(buf.strip())

    code = proc.wait()
    with LOCK:
        STATE["proc"] = None
        STATE["code"] = code
        if code != 0 and not STATE["error"]:
            STATE["error"] = f"退出码 {code}"

    _emit(f"[面板] 扫描结束，退出码 {code}" if code == 0 else f"[面板] 扫描结束，退出码 {code}（有问题）")
    _broadcast({"type": "done", "code": code})


# ---------------------------------------------------------------- 数据查询
def _db_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=3)
    conn.row_factory = sqlite3.Row
    return conn


def _metrics() -> dict:
    out = {
        "current": None,
        "domestic": None,
        "hist": None,
        "offers": 0,
        "runs": 0,
        "last_run": None,
    }
    if not DB.exists():
        return out
    try:
        conn = _db_ro()
    except sqlite3.Error:
        return out
    try:
        row = conn.execute("SELECT COUNT(*) n FROM offers").fetchone()
        out["offers"] = int(row["n"]) if row else 0
        row = conn.execute("SELECT COUNT(*) n FROM runs").fetchone()
        out["runs"] = int(row["n"]) if row else 0

        # 本地实价库覆盖：携程已核过多少个日期组合（越搜越多）
        row = conn.execute(
            "SELECT COUNT(DISTINCT dep_date || '|' || ret_date) n FROM offers "
            "WHERE source='ctrip' AND price IS NOT NULL"
        ).fetchone()
        out["ctrip_covered"] = int(row["n"]) if row else 0

        row = conn.execute(
            "SELECT dep_date, ret_date, price FROM offers WHERE price IS NOT NULL "
            "AND source='google' ORDER BY price ASC LIMIT 1"
        ).fetchone()
        if row:
            out["hist"] = {"price": int(row["price"]), "dep": row["dep_date"], "ret": row["ret_date"]}

        run = conn.execute(
            "SELECT id, started_at, finished_at, mode, total, ok, failed FROM runs "
            "WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if run:
            out["last_run"] = {
                "id": run["id"],
                "started_at": run["started_at"],
                "finished_at": run["finished_at"],
                "mode": run["mode"],
                "total": run["total"],
                "ok": run["ok"],
                "failed": run["failed"],
            }
            best = conn.execute(
                "SELECT dep_date, ret_date, MIN(price) p FROM offers WHERE run_id=? "
                "AND price IS NOT NULL AND source='google' "
                "GROUP BY dep_date, ret_date ORDER BY p ASC LIMIT 1",
                (run["id"],),
            ).fetchone()
            if best:
                out["current"] = {"price": int(best["p"]), "dep": best["dep_date"], "ret": best["ret_date"]}
            dom = conn.execute(
                "SELECT dep_date, ret_date, MIN(price) p, source FROM offers WHERE run_id=? "
                "AND price IS NOT NULL AND source IN ('ctrip','fliggy') "
                "GROUP BY dep_date, ret_date ORDER BY p ASC LIMIT 1",
                (run["id"],),
            ).fetchone()
            if dom:
                out["domestic"] = {"price": int(dom["p"]), "dep": dom["dep_date"],
                                   "ret": dom["ret_date"], "source": dom["source"]}
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return out


def _trend(limit: int = 12) -> list[dict]:
    if not DB.exists():
        return []
    try:
        conn = _db_ro()
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            "SELECT run_id, MIN(price) p, substr(MAX(fetched_at),1,16) t FROM offers "
            "WHERE price IS NOT NULL GROUP BY run_id ORDER BY run_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    data = [{"run": r["run_id"], "price": int(r["p"]), "at": r["t"]} for r in rows]
    data.reverse()
    return data


def _ota_cache() -> dict | None:
    try:
        return json.loads(OTA_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _proxy_info() -> dict:
    raw = (CFG.network.get("proxy") or "").strip()
    auto = "" if raw else _detect_system_proxy()
    proxy = raw or auto
    if not proxy:
        return {"proxy": "", "ok": False, "note": "未配置代理（梯子没开？）"}
    u = urlparse(proxy if "://" in proxy else f"http://{proxy}")
    host, port = u.hostname or "127.0.0.1", u.port or 80
    s = socket.socket()
    s.settimeout(1.5)
    try:
        ok = s.connect_ex((host, port)) == 0
    except OSError:
        ok = False
    finally:
        s.close()
    return {
        "proxy": proxy,
        "auto": bool(auto),
        "ok": ok,
        "note": "" if ok else f"{host}:{port} 连不上",
    }


def _window_info() -> dict:
    start, last_dep, max_ret, note = resolve_window(CFG.window)
    return {
        "start": start.isoformat(),
        "last_dep": last_dep.isoformat(),
        "max_return": max_ret.isoformat(),
        "note": note,
        "combos": len(build_combos(CFG.window)),
        "gaps": CFG.window.get("gap_days", []),
    }


def _report_info() -> dict:
    latest = REPORTS / "latest.html"
    if not latest.exists():
        return {"exists": False}
    st = latest.stat()
    return {
        "exists": True,
        "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
        "size_kb": round(st.st_size / 1024, 1),
    }


# ---------------------------------------------------------------- 路由
@app.get("/")
def index():
    return send_file(ROOT / "panel.html")


@app.get("/api/status")
def api_status():
    running = _is_running()
    elapsed = int(time.time() - STATE["started_at"]) if (running and STATE["started_at"]) else 0
    return jsonify({
        "running": running,
        "mode": STATE["mode"] if running else None,
        "elapsed": elapsed,
        "progress": {
            "done": STATE["done"] if running else 0,
            "total": STATE["total"] if running else 0,
            "price": STATE["last_price"] if running else "",
        },
        "last_code": STATE["code"],
        "error": STATE["error"],
        "proxy": _proxy_info(),
        "window": _window_info(),
        "metrics": _metrics(),
        "ota": _ota_cache(),
        "report": _report_info(),
        "trend": _trend(),
        "lines": STATE["lines"][-60:] if running else STATE["lines"][-20:],
    })


@app.post("/api/scan")
def api_scan():
    mode = (request.json or {}).get("mode", "full")
    if mode not in ("full", "quick"):
        return jsonify({"ok": False, "error": "mode 只能是 full 或 quick"}), 400
    if _is_running():
        return jsonify({"ok": False, "error": "已有扫描在进行中"}), 409
    threading.Thread(target=_run_scan, args=(mode,), daemon=True).start()
    return jsonify({"ok": True, "mode": mode})


@app.post("/api/stop")
def api_stop():
    p = STATE["proc"]
    if p and p.poll() is None:
        p.terminate()
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "当前没有扫描"}), 409


@app.get("/api/stream")
def api_stream():
    def gen():
        q: queue.Queue = queue.Queue(maxsize=500)
        SUBSCRIBERS.add(q)
        try:
            yield "data: " + json.dumps({"type": "hello"}) + "\n\n"
            while True:
                try:
                    evt = q.get(timeout=15)
                    yield "data: " + json.dumps(evt, ensure_ascii=False) + "\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        finally:
            SUBSCRIBERS.discard(q)

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/report")
def report_page():
    latest = REPORTS / "latest.html"
    if not latest.exists():
        return "还没有报告，先跑一次扫描。", 404
    return send_file(latest)


@app.post("/api/shutdown")
def api_shutdown():
    def bye():
        time.sleep(0.4)
        os._exit(0)

    threading.Thread(target=bye, daemon=True).start()
    return jsonify({"ok": True})


def _port_busy(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def _is_our_panel(port: int) -> bool:
    """判断该端口上跑的是不是我们自己的面板（能返回 /api/status 的 JSON）。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
        return isinstance(data, dict) and "metrics" in data and "proxy" in data
    except Exception:  # noqa: BLE001
        return False


def main() -> None:
    global _REUSED
    port = 5001
    no_browser = False
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1])
        if a == "--no-browser":
            no_browser = True

    want = port

    # 1) 已经有面板在跑 → 直接复用，不再起第二个实例
    if _is_our_panel(want):
        url = f"http://127.0.0.1:{want}/"
        _REUSED = True
        print("=" * 56)
        print("  面板已经在运行，直接打开页面")
        print(f"  地址：{url}")
        print("  要重启：先关掉原来那个黑窗口（或在页面上点「停止服务」）")
        print("=" * 56)
        if not no_browser:
            webbrowser.open(url)
        return

    # 2) 端口被别的程序占着 → 往后找空的
    for p in range(want, want + 10):
        if not _port_busy(p):
            port = p
            break
    else:
        print(f"  {want}~{want + 9} 全被占用，无法启动面板。")
        return

    url = f"http://127.0.0.1:{port}/"
    print("=" * 56)
    print("  机票监控 · 本地控制面板")
    print(f"  地址：{url}" + ("" if port == want else f"（{want} 被其他程序占用）"))
    print("  关掉这个窗口即停止服务（扫描会一起中断）")
    print("=" * 56)

    if not no_browser:
        threading.Timer(1.0, lambda u=url: webbrowser.open(u)).start()

    app.run(host="127.0.0.1", port=port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
    if _REUSED:
        raise SystemExit(3)
