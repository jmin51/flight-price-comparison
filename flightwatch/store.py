"""SQLite 存储层：每次抓取落库，形成历史价格序列。"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    source       TEXT,
    total        INTEGER DEFAULT 0,
    ok           INTEGER DEFAULT 0,
    failed       INTEGER DEFAULT 0,
    mode         TEXT
);

CREATE TABLE IF NOT EXISTS offers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER,
    source        TEXT,
    dep_date      TEXT,
    ret_date      TEXT,
    gap_days      INTEGER,
    price         INTEGER,
    currency      TEXT,
    carriers      TEXT,
    stops         INTEGER,
    duration_min  INTEGER,
    itinerary     TEXT,
    deep_link     TEXT,
    fetched_at    TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);
CREATE INDEX IF NOT EXISTS idx_offers_combo ON offers(dep_date, ret_date, fetched_at);
CREATE INDEX IF NOT EXISTS idx_offers_price ON offers(price);

CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    dep_date   TEXT,
    ret_date   TEXT,
    kind       TEXT,
    price      INTEGER,
    prev_price INTEGER
);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def tx(self):
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ---------- runs ----------
    def start_run(self, source: str, total: int, mode: str) -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO runs(started_at, source, total, mode) VALUES (?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), source, total, mode),
            )
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, ok: int, failed: int) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE runs SET finished_at=?, ok=?, failed=? WHERE id=?",
                (datetime.now().isoformat(timespec="seconds"), ok, failed, run_id),
            )

    # ---------- offers ----------
    def add_offer(self, run_id: int, offer: dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT INTO offers
                   (run_id, source, dep_date, ret_date, gap_days, price, currency,
                    carriers, stops, duration_min, itinerary, deep_link, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    offer.get("source"),
                    offer.get("dep_date"),
                    offer.get("ret_date"),
                    offer.get("gap_days"),
                    offer.get("price"),
                    offer.get("currency"),
                    offer.get("carriers"),
                    offer.get("stops"),
                    offer.get("duration_min"),
                    offer.get("itinerary"),
                    offer.get("deep_link"),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

    # ---------- 查询 ----------
    def last_run_id(self, mode: str | None = None) -> int | None:
        sql = "SELECT id FROM runs WHERE finished_at IS NOT NULL"
        args: list[Any] = []
        if mode:
            sql += " AND mode=?"
            args.append(mode)
        sql += " ORDER BY id DESC LIMIT 1"
        row = self._conn.execute(sql, args).fetchone()
        return int(row["id"]) if row else None

    def prices_of_run(self, run_id: int, source: str = "google") -> dict[tuple[str, str], int]:
        rows = self._conn.execute(
            """SELECT dep_date, ret_date, MIN(price) AS p FROM offers
               WHERE run_id=? AND price IS NOT NULL AND source=?
               GROUP BY dep_date, ret_date""",
            (run_id, source),
        ).fetchall()
        return {(r["dep_date"], r["ret_date"]): int(r["p"]) for r in rows}

    def top_combos(self, limit: int, source: str = "google") -> list[tuple[str, str]]:
        run_id = self.last_run_id()
        if not run_id:
            return []
        rows = self._conn.execute(
            """SELECT dep_date, ret_date, MIN(price) AS p FROM offers
               WHERE run_id=? AND price IS NOT NULL AND source=?
               GROUP BY dep_date, ret_date ORDER BY p ASC LIMIT ?""",
            (run_id, source, limit),
        ).fetchall()
        return [(r["dep_date"], r["ret_date"]) for r in rows]

    def best_of_run(self, run_id: int, source: str = "google") -> dict[str, Any] | None:
        row = self._conn.execute(
            """SELECT dep_date, ret_date, MIN(price) AS p FROM offers
               WHERE run_id=? AND price IS NOT NULL AND source=?
               GROUP BY dep_date, ret_date ORDER BY p ASC LIMIT 1""",
            (run_id, source),
        ).fetchone()
        if not row or row["p"] is None:
            return None
        return {"price": int(row["p"]), "dep": row["dep_date"], "ret": row["ret_date"]}

    def historical_min(self, dep: str, ret: str) -> int | None:
        row = self._conn.execute(
            "SELECT MIN(price) AS p FROM offers WHERE dep_date=? AND ret_date=?",
            (dep, ret),
        ).fetchone()
        return int(row["p"]) if row and row["p"] is not None else None

    def previous_price(self, dep: str, ret: str, before_run: int | None = None) -> int | None:
        """上一次（不含当前 run）该组合的最低价。"""
        sql = """SELECT MIN(price) AS p FROM offers
                 WHERE dep_date=? AND ret_date=? AND run_id < ?"""
        row = self._conn.execute(sql, (dep, ret, before_run or 0)).fetchone()
        return int(row["p"]) if row and row["p"] is not None else None

    def overall_min_history(self) -> int | None:
        row = self._conn.execute("SELECT MIN(price) AS p FROM offers").fetchone()
        return int(row["p"]) if row and row["p"] is not None else None

    def series(self, dep: str, ret: str) -> list[tuple[str, int]]:
        rows = self._conn.execute(
            """SELECT substr(fetched_at,1,10) AS d, MIN(price) AS p FROM offers
               WHERE dep_date=? AND ret_date=? AND price IS NOT NULL
               GROUP BY d ORDER BY d""",
            (dep, ret),
        ).fetchall()
        return [(r["d"], int(r["p"])) for r in rows]

    # ---------- 告警冷却 ----------
    def recently_alerted(self, dep: str, ret: str, hours: int) -> bool:
        since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
        row = self._conn.execute(
            "SELECT 1 FROM alerts WHERE dep_date=? AND ret_date=? AND ts>=? LIMIT 1",
            (dep, ret, since),
        ).fetchone()
        return row is not None

    def log_alert(self, dep: str, ret: str, kind: str, price: int, prev: int | None) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO alerts(ts, dep_date, ret_date, kind, price, prev_price) VALUES (?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), dep, ret, kind, price, prev),
            )

    def stats(self) -> dict[str, Any]:
        total = self._conn.execute("SELECT COUNT(*) AS n FROM offers").fetchone()["n"]
        runs = self._conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
        return {"offers": total, "runs": runs}
