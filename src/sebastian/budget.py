"""Persistent integer accounting. Uncertain requests retain their full reservation."""
import json
import sqlite3
import time
import uuid
from datetime import datetime
from contextlib import contextmanager
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from zoneinfo import ZoneInfo

from .common import Rejected, canonical


def micros(amount):
    return int((Decimal(str(amount)) * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


def cost(input_tokens, output_tokens, price):
    # Rates are USD / million, so token * rate is already microdollars.
    upper_input = max(Decimal(str(price["input"])), Decimal(str(price["cache_write"])))
    return int((input_tokens * upper_input + output_tokens * Decimal(str(price["output"])))
               .to_integral_value(rounding=ROUND_CEILING))


class Ledger:
    def __init__(self, path, *, now=time.time):
        self.path, self.now = str(path), now
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, created REAL, state TEXT, restricted INTEGER DEFAULT 0,
                    confidential INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS calls (
                    id TEXT PRIMARY KEY, task TEXT, model TEXT, created REAL,
                    day TEXT, month TEXT, reserved INTEGER, charged INTEGER,
                    state TEXT, provider_id TEXT, usage TEXT);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, created REAL, kind TEXT, detail TEXT);
                CREATE TABLE IF NOT EXISTS flags (name TEXT PRIMARY KEY, value TEXT);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def event(self, kind, detail):
        with self.connect() as db:
            db.execute("INSERT INTO events(created,kind,detail) VALUES(?,?,?)",
                       (self.now(), kind, canonical(detail)))

    def task(self):
        identifier = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO tasks(id,created,state) VALUES(?,?,?)",
                       (identifier, self.now(), "active"))
        return identifier

    def task_info(self, identifier):
        with self.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (identifier,)).fetchone()
        if row is None or row["state"] != "active":
            raise Rejected("Task is absent or closed; start a new chat.")
        return dict(row)

    def mark(self, task, restricted=False, confidential=False):
        self.task_info(task)
        with self.connect() as db:
            db.execute("UPDATE tasks SET restricted=MAX(restricted,?), confidential=MAX(confidential,?) WHERE id=?",
                       (int(restricted), int(confidential), task))

    def close_task(self, task):
        with self.connect() as db:
            db.execute("UPDATE tasks SET state='closed' WHERE id=?", (task,))

    def reserve(self, task, model, amount, settings, identifier=None):
        identifier = identifier or uuid.uuid4().hex
        when = datetime.fromtimestamp(self.now(), ZoneInfo("America/Halifax"))
        day, month = when.strftime("%Y-%m-%d"), when.strftime("%Y-%m")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM flags WHERE name='billing_frozen'").fetchone():
                raise Rejected("Billing is frozen after a bound violation; reconcile before continuing.")
            task_row = db.execute("SELECT state FROM tasks WHERE id=?", (task,)).fetchone()
            if task_row is None or task_row[0] != "active":
                raise Rejected("Task is no longer active.")
            if db.execute("SELECT 1 FROM calls WHERE id=?", (identifier,)).fetchone():
                raise Rejected("This request has already been dispatched; it cannot be replayed.")
            if amount > micros(settings["budget.request_usd"]):
                raise Rejected("Request exceeds the per-request allowance.")
            # Old unresolved reservations count against current windows too.
            scopes = [("task", task), ("day", day), ("month", month)]
            for scope, value in scopes:
                if scope == "task":
                    predicate, params = "task=?", (value,)
                else:
                    predicate, params = f"({scope}=? OR state='reserved')", (value,)
                used = db.execute(f"SELECT COALESCE(SUM(COALESCE(charged,reserved)),0) FROM calls WHERE {predicate}", params).fetchone()[0]
                if used + amount > micros(settings[f"budget.{scope}_usd"]):
                    raise Rejected(f"Request exceeds the {scope} budget, including outstanding reservations.")
            db.execute("INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (identifier, task, model, self.now(), day, month, amount, None, "reserved", None, None))
        return identifier

    def settle(self, identifier, amount, usage=None, provider_id=None):
        if type(amount) is not int or amount < 0:
            raise Rejected("Invalid accounting amount.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT reserved,state FROM calls WHERE id=?", (identifier,)).fetchone()
            if row is None or row["state"] != "reserved":
                raise Rejected("No unresolved reservation with that ID.")
            violation = amount > row["reserved"]
            db.execute("UPDATE calls SET charged=?,state='settled',usage=?,provider_id=? WHERE id=?",
                       (amount, canonical(usage or {}), provider_id, identifier))
            if violation:
                db.execute("INSERT OR REPLACE INTO flags VALUES('billing_frozen',?)", (identifier,))
        if violation:
            raise Rejected("Provider usage exceeded the reservation. Further external calls are frozen.")

    def report(self):
        with self.connect() as db:
            calls = [dict(row) for row in db.execute("SELECT * FROM calls ORDER BY created DESC LIMIT 100")]
            totals = db.execute("SELECT COALESCE(SUM(charged),0), COALESCE(SUM(CASE WHEN state='reserved' THEN reserved ELSE 0 END),0) FROM calls").fetchone()
            frozen = bool(db.execute("SELECT 1 FROM flags WHERE name='billing_frozen'").fetchone())
        return {"accounted_usd": totals[0] / 1e6, "unresolved_usd": totals[1] / 1e6,
                "frozen": frozen, "calls": calls, "timezone": "America/Halifax"}

    def audit(self, limit=20):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]
