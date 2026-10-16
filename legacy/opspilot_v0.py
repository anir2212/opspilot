"""Local incident triage with evidence-linked recommendations and approval queue."""
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

RUNBOOKS = {
    "RB-PAY": ("payments", "payment_errors", "Check payment service error logs and recent deploys; roll back only after approval."),
    "RB-LAT": ("api", "latency", "Check p95 latency, downstream timeouts and saturation."),
    "RB-BACK": ("backup", "backup_failure", "Check job error code, storage capacity and retry policy."),
}


def connect(path="opspilot.sqlite"):
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, service TEXT NOT NULL, kind TEXT NOT NULL, value REAL NOT NULL, ts TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS actions(id INTEGER PRIMARY KEY, incident_id TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')));
    CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY, service TEXT NOT NULL, summary TEXT NOT NULL);
    """)
    return db


def add_event(db, service, kind, value, ts=None):
    if kind not in {"payment_errors", "latency", "backup_failure", "deploy", "cpu", "memory"}:
        raise ValueError("unknown metric or event")
    ts = ts or datetime.now(timezone.utc).isoformat()
    cur = db.execute("INSERT INTO events(service,kind,value,ts) VALUES(?,?,?,?)", (service, kind, value, ts))
    db.commit()
    return cur.lastrowid


def search_runbooks(query, k=3):
    words = set(query.lower().replace("_", " ").split())
    scored = []
    for rid, (service, kind, body) in RUNBOOKS.items():
        blob = f"{service} {kind.replace('_',' ')} {body}".lower().split()
        score = len(words.intersection(blob))
        if score:
            scored.append((score, rid, body))
    return [{"id": rid, "text": body} for score, rid, body in sorted(scored, reverse=True)[:k]]


def triage(db, service):
    rows = db.execute("SELECT id,kind,value,ts FROM events WHERE service=? ORDER BY ts DESC LIMIT 100", (service,)).fetchall()
    if not rows:
        return {"service": service, "status": "no evidence", "claims": []}
    by_kind = {}
    for row in rows:
        by_kind.setdefault(row[1], []).append(row)
    candidates = []
    for rid, (rb_service, kind, body) in RUNBOOKS.items():
        if rb_service != service or kind not in by_kind:
            continue
        newest = by_kind[kind][0]
        if newest[2] > 0:
            candidates.append({"cause": kind, "evidence": f"event:{newest[0]}", "runbook": rid, "step": body})
    candidates.sort(key=lambda x: x["evidence"], reverse=True)
    return {"service": service, "status": "investigate" if candidates else "no matching signal", "claims": candidates[:3]}


def draft_action(db, incident_id, text):
    if not db.execute("SELECT 1 FROM incidents WHERE id=?", (incident_id,)).fetchone():
        raise ValueError("unknown incident")
    cur = db.execute("INSERT INTO actions(incident_id,text,status) VALUES(?,?,'pending')", (incident_id, text))
    db.commit()
    return cur.lastrowid


def decide_action(db, action_id, approve):
    cur = db.execute("UPDATE actions SET status=? WHERE id=? AND status='pending'", ("approved" if approve else "rejected", action_id))
    db.commit()
    if cur.rowcount != 1:
        raise ValueError("action is absent or already decided")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["seed", "triage", "pending", "draft", "decide"])
    p.add_argument("--service", default="payments")
    p.add_argument("--incident", default="INC-1")
    p.add_argument("--text", default="")
    p.add_argument("--action-id", type=int)
    p.add_argument("--decision", choices=["approve", "reject"])
    args = p.parse_args()
    db = connect()
    if args.command == "seed":
        db.execute("INSERT OR IGNORE INTO incidents VALUES('INC-1','payments','Elevated payment errors')")
        add_event(db, "payments", "payment_errors", 23)
        add_event(db, "payments", "deploy", 1)
        print("seeded INC-1")
    elif args.command == "triage":
        print(json.dumps(triage(db, args.service), indent=2))
    elif args.command == "pending":
        print(json.dumps(db.execute("SELECT id,incident_id,text FROM actions WHERE status='pending'").fetchall()))
    elif args.command == "draft":
        if not args.text.strip():
            p.error("--text is required for draft")
        print(json.dumps({"pending_action_id": draft_action(db, args.incident, args.text)}))
    else:
        if args.action_id is None or args.decision is None:
            p.error("--action-id and --decision are required for decide")
        decide_action(db, args.action_id, args.decision == "approve")
        print(json.dumps({"action_id": args.action_id, "status": "approved" if args.decision == "approve" else "rejected"}))


if __name__ == "__main__":
    main()
