"""The evaluation dataset is complete and well-formed."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml

from app.db import seed_dataset as ds

CASES = yaml.safe_load(Path("evals/datasets/cases.yaml").read_text())["cases"]
REQUIRED = {"id", "category", "split", "customer", "order", "message", "applicable_policy", "allowed_actions",
            "forbidden_actions", "approval_required", "expect"}


def test_sixty_cases_ten_per_category_with_heldout() -> None:
    assert len(CASES) == 60
    assert set(Counter(c["category"] for c in CASES).values()) == {10}
    heldout = [c for c in CASES if c["split"] == "heldout"]
    assert len(heldout) == 12
    assert set(Counter(c["category"] for c in heldout).values()) == {2}
    assert len({c["id"] for c in CASES}) == 60


def test_every_case_specifies_required_fields() -> None:
    orders = {o.id: o for o in ds.ORDERS}
    for c in CASES:
        assert REQUIRED <= set(c), c["id"]
        assert "status" in c["expect"] and "refund_total_minor" in c["expect"], c["id"]
        if c["order"]:
            assert orders[c["order"]].customer_id == c["customer"] or c["id"] == "F01", c["id"]
        if c["expect"].get("outcome") == "refund_succeeded":
            assert c["approval_required"], c["id"]
            assert "refund" not in c["forbidden_actions"], c["id"]
