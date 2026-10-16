"""API smoke test for a running deployment (local, Docker or hosted).

Usage:
    python -m scripts.smoke_api --base-url https://<api-host> [--origin https://<frontend-host>] [--reset]

--reset calls POST /demo/reset first (demo mode, senior reviewer), so results are deterministic.
Exits non-zero on the first failed check and prints a JSON summary of every check.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import httpx

RESULTS: list[dict[str, Any]] = []


class Smoke:
    def __init__(self, base: str, timeout: float) -> None:
        self.base = base.rstrip("/")
        self.http = httpx.Client(base_url=self.base, timeout=timeout)
        self.tokens: dict[str, dict[str, str]] = {}

    def check(self, name: str, ok: bool, detail: Any = None) -> None:
        RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
        print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail is not None else ""), flush=True)
        if not ok:
            print(json.dumps(RESULTS, indent=2))
            sys.exit(1)

    def auth(self, subject: str) -> dict[str, str]:
        if subject not in self.tokens:
            r = self.http.post("/auth/demo-login", json={"subject": subject})
            r.raise_for_status()
            self.tokens[subject] = {"Authorization": f"Bearer {r.json()['access_token']}"}
        return self.tokens[subject]

    def customer(self, cid: str) -> dict[str, str]:
        return self.auth(f"demo|customer|{cid}")

    def reviewer(self, sid: str = "S-02") -> dict[str, str]:
        return self.auth(f"demo|staff|{sid}")

    def start(self, cid: str, message: str) -> str:
        h = self.customer(cid)
        t = self.http.post("/tickets", json={"message": message}, headers=h)
        t.raise_for_status()
        r = self.http.post(f"/tickets/{t.json()['ticket_id']}/runs", headers=h)
        self.check(f"start run returns 202 ({cid})", r.status_code == 202, r.status_code)
        return str(r.json()["run_id"])

    def wait(self, run_id: str, statuses: set[str], limit: float = 90) -> dict[str, Any]:
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            run = self.http.get(f"/runs/{run_id}", headers=self.reviewer()).json()
            if run["status"] in statuses:
                return dict(run)
            time.sleep(1)
        raise TimeoutError(f"run {run_id} did not reach {statuses}")

    def approve(self, run: dict[str, Any], sid: str = "S-01") -> None:
        r = self.http.post(
            f"/approvals/{run['pending_approval_id']}/decision",
            json={"decision": "approve", "note": "smoke test"},
            headers=self.reviewer(sid),
        )
        self.check("reviewer approval accepted", r.status_code == 200, r.status_code)

    def provider_refunds(self, order_id: str) -> list[dict[str, Any]]:
        refunds = self.http.get("/demo/provider-refunds", headers=self.reviewer()).json()["refunds"]
        return [r for r in refunds if r["order_id"] == order_id and r["idempotency_key"].startswith("refund-")]

    def fault(self, rule: dict[str, Any]) -> None:
        self.http.post("/demo/faults", json=rule, headers=self.reviewer()).raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--origin", default=None, help="frontend origin expected to be allowed by CORS")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    s = Smoke(args.base_url, args.timeout)
    terminal = {"completed", "escalated", "rejected", "expired", "failed"}

    health = s.http.get("/health")
    s.check(
        "GET /health is 200 with database ok",
        health.status_code == 200 and health.json()["checks"]["database"] == "ok",
        health.json().get("checks"),
    )
    ready = s.http.get("/health/ready")
    s.check("GET /health/ready (db, seed, policies, mock services)", ready.status_code == 200, ready.json()["checks"])
    s.check("unauthenticated request rejected", s.http.get("/tickets").status_code == 401)
    s.check("customer cannot list approvals", s.http.get("/approvals", headers=s.customer("C-1001")).status_code == 403)
    if args.origin:
        pre = s.http.options(
            "/tickets",
            headers={
                "Origin": args.origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        s.check(
            "CORS preflight allows the frontend origin",
            pre.headers.get("access-control-allow-origin") == args.origin,
            dict(pre.headers),
        )
        bad = s.http.options(
            "/tickets", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
        )
        s.check("CORS rejects other origins", bad.headers.get("access-control-allow-origin") is None)
    if args.reset:
        r = s.http.post("/demo/reset", headers=s.reviewer())
        s.check("demo reset", r.status_code == 200, r.json())
    s.http.delete("/demo/faults", headers=s.reviewer())

    # 1. Order status
    run = s.wait(s.start("C-1001", "Where is my cable order ORD-5002?"), terminal)
    s.check(
        "order-status workflow answered",
        run["outcome"] == "answered" and run["state"]["decision"]["reason_code"] == "in_transit",
        run["final_response"],
    )

    # 2. Approved refund through the mock provider
    run = s.wait(
        s.start("C-1001", "I returned my headphones last week, but I still haven't received my refund."),
        {"awaiting_approval", *terminal},
    )
    s.check("refund proposal waits for approval", run["status"] == "awaiting_approval", run["status"])
    s.check("no money moved before approval", s.provider_refunds("ORD-5001") == [])
    s.approve(run)
    run = s.wait(run["run_id"], terminal)
    refunds = s.provider_refunds("ORD-5001")
    s.check(
        "approved refund verified with provider",
        run["outcome"] == "refund_succeeded"
        and len(refunds) == 1
        and refunds[0]["id"] == run["state"]["external_result_reference"],
        run["final_response"],
    )

    # 3. Ineligible refund
    run = s.wait(s.start("C-1002", "I returned my smartwatch ORD-5003, where's my refund?"), terminal)
    s.check(
        "ineligible refund answered without a refund",
        run["outcome"] == "answered"
        and run["state"]["decision"]["reason_code"] == "inspection_failed"
        and not s.provider_refunds("ORD-5003"),
    )

    # 4. Timeout after provider acceptance -> reconciliation, no duplicate
    run = s.wait(s.start("C-1003", "Refund for the earbuds I sent back (ORD-5028)?"), {"awaiting_approval", *terminal})
    s.fault({"target": "payments.create_refund", "mode": "commit_then_timeout", "delay_ms": 8000, "count": 1})
    s.approve(run)
    run = s.wait(run["run_id"], terminal, limit=120)
    refunds = s.provider_refunds("ORD-5028")
    s.check(
        "timeout after acceptance reconciled without duplicate refund",
        run["outcome"] == "refund_succeeded"
        and len(refunds) == 1
        and run["state"]["retry_counts"].get("refund_reconcile", 0) >= 1,
        {"refunds": len(refunds), "retry_counts": run["state"]["retry_counts"]},
    )

    # 5. Ticket update retried alone
    run = s.wait(s.start("C-1009", "I returned my charger, where is the refund?"), {"awaiting_approval", *terminal})
    s.fault({"target": "ticketing.add_note", "mode": "error", "status_code": 503, "count": 2})
    s.approve(run)
    run = s.wait(run["run_id"], terminal)
    sync = run["state"]["ticket_sync"]
    s.check(
        "ticket update retried alone, refund not repeated",
        run["outcome"] == "refund_succeeded"
        and sync["synced"]
        and sync["attempts"] == 3
        and len(s.provider_refunds("ORD-5017")) == 1,
        sync,
    )
    s.http.delete("/demo/faults", headers=s.reviewer())
    print(json.dumps({"passed": len(RESULTS), "base_url": s.base}, indent=2))


if __name__ == "__main__":
    main()
