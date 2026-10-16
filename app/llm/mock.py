"""Deterministic mock LLM provider (default; no paid service required).

It implements the same interface as real providers and exercises the same
agent loop: it decides which tools to call from the task and the tool results
it has seen, and returns JSON that is validated like any model output. It is a
rule-based stand-in for an LLM, so evaluation results in mock mode measure the
*system* (orchestration, controls, recovery), not language-model quality.

Fault injection (``arm``) produces the failure modes a real model can exhibit:
invalid JSON, schema violations, endless tool loops, forbidden tool calls,
inflated amounts and provider errors.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import date, datetime
from typing import Any

from app.llm.base import LLMError, LLMResponse, ToolCall

_lock = threading.Lock()
_faults: dict[str, list[str]] = {}

FAULT_MODES = (
    "invalid_json",
    "schema_violation",
    "endless_tools",
    "forbidden_tool",
    "inflate_amount",
    "wrong_order",
    "provider_error",
)


def arm(agent: str, mode: str, times: int = 1) -> None:
    if mode not in FAULT_MODES:
        raise ValueError(f"unknown mock LLM fault {mode!r}")
    with _lock:
        _faults.setdefault(agent, []).extend([mode] * times)


def disarm_all() -> None:
    with _lock:
        _faults.clear()


def _take(agent: str) -> str | None:
    with _lock:
        queue = _faults.get(agent)
        if queue:
            return queue.pop(0)
    return None


def _task(messages: list[dict[str, Any]]) -> dict[str, Any]:
    first = next(m["content"] for m in messages if m["role"] == "user")
    match = re.search(r"<task_input>(.*?)</task_input>", first, re.S)
    return json.loads(match.group(1)) if match else {}


def _results(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for m in messages:
        if m["role"] == "tool":
            try:
                out[m["name"]] = json.loads(m["content"])
            except ValueError:
                out[m["name"]] = {"ok": False, "error": {"code": "UNPARSEABLE"}}
    return out


def _data(results: dict[str, dict[str, Any]], tool: str) -> dict[str, Any] | None:
    r = results.get(tool)
    return r.get("data") if r and r.get("ok") else None


# --------------------------------------------------------------------------- classifier

_PRODUCTS = {
    r"headphones?": "headphones",
    r"earbuds?|buds": "earbuds",
    r"speakers?": "speaker",
    r"(smart)?watch(es)?": "smartwatch",
    r"(smart)?phones?|mobile": "smartphone",
    r"laptops?": "laptop",
    r"tablets?": "tablet",
    r"chargers?": "charger",
    r"cables?": "cable",
    r"keyboards?": "keyboard",
    r"mouse|mice": "mouse",
    r"monitors?": "monitor",
}
_DAMAGE = re.compile(
    r"\b(damaged|broken|cracked|shattered|smashed|dent(ed)?|defective|crushed|scratch(ed|es)?|"
    r"doesn'?t (work|turn on)|not working|dead on arrival|arrived (broken|damaged))\b",
    re.I,
)
_DETAIL = re.compile(
    r"\b(cracked|shattered|smashed|dent(ed)?|crushed|scratch(ed|es)?|bent|torn|screen|hinge|"
    r"case|strap|no sound|won'?t (charge|turn on)|not charging|rattl\w+|leak\w*|missing (part|piece)s?)\b",
    re.I,
)
_REFUND = re.compile(r"\b(refund\w*|money back|returned|return(ing)?|reimburse\w*)\b", re.I)
_STATUS = re.compile(
    r"\b(where is|where's|track(ing)?|deliver(y|ed)?|arriv\w+|shipped|shipping|status|late|"
    r"delay\w*|hasn'?t (come|arrived)|not (yet )?received my (order|package))\b",
    re.I,
)
_ORDER = re.compile(r"\bORD[-\s]?(\d{4})\b|\border\s*(?:no\.?|number|#)?\s*(\d{4})\b", re.I)


def classify(text: str) -> dict[str, Any]:
    order = None
    if m := _ORDER.search(text):
        order = f"ORD-{m.group(1) or m.group(2)}"
    damaged = bool(_DAMAGE.search(text))
    if damaged:
        category = "damaged_item"
    elif _REFUND.search(text):
        category = "refund_request"
    elif _STATUS.search(text):
        category = "order_status"
    else:
        category = "other"
    lowered = text.lower()
    photos: bool | None = None
    if re.search(r"\b(no|don'?t have|can'?t (take|send)|without)( any)? (photos?|pictures?|images?)\b", lowered):
        photos = False
    elif re.search(r"\b(photos?|pictures?|pics?|images?)\b", lowered):
        photos = True
    preference = None
    if re.search(r"\b(replacement|replace|exchange)\b", lowered):
        preference = "replacement"
    elif re.search(r"\b(refund|money back)\b", lowered):
        preference = "refund"
    description = None
    if damaged and _DETAIL.search(text):
        description = next((s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if _DETAIL.search(s)), text)[:300]
    mentions = sorted({v for k, v in _PRODUCTS.items() if re.search(rf"\b(?:{k})\b", lowered)})
    return {
        "category": category,
        "order_reference": order,
        "product_mentions": mentions,
        "damage_description": description,
        "photos_available": photos if damaged else None,
        "preference": preference if damaged else None,
        "summary": text.strip()[:280],
    }


# --------------------------------------------------------------------------- shared helpers


def _evidence(order_id: str, results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    facts: list[dict[str, str]] = []
    gaps: list[str] = []
    order = _data(results, "get_order")
    if order:
        items = ", ".join(i["name"] for i in order["items"])
        facts.append(
            {
                "ref": f"order:{order_id}",
                "statement": f"Order {order_id} ({items}) is {order['status']}; paid "
                f"{order['paid_amount_minor']} paise, refunded {order['refunded_amount_minor']}, "
                f"reserved {order['reserved_refund_minor']}.",
            }
        )
    ret = _data(results, "get_return_status")
    if ret is not None:
        if ret.get("has_return") and ret.get("latest"):
            r = ret["latest"]
            facts.append(
                {
                    "ref": f"return:{r['return_id']}",
                    "statement": f"Return {r['return_id']} is {r['status']} with inspection {r['inspection_status']}.",
                }
            )
        else:
            facts.append({"ref": f"return:{order_id}", "statement": "No return has been started."})
    pay = _data(results, "get_payment_status")
    if pay is not None:
        facts.append(
            {
                "ref": f"payment:{pay['payment_reference']}",
                "statement": f"Payment {pay['status']}; provider refunded "
                f"{pay.get('provider_refunded_minor')} paise, pending {pay.get('provider_refund_pending_minor')}.",
            }
        )
        if not pay.get("provider_reachable", True):
            gaps.append("payment_provider_unreachable")
    ship = _data(results, "get_shipment_status")
    if ship is not None and ship.get("tracking_number"):
        facts.append(
            {
                "ref": f"shipment:{ship['tracking_number']}",
                "statement": f"Carrier {ship['carrier']} reports {ship['status']}; estimated delivery "
                f"{ship.get('estimated_delivery_at')}.",
            }
        )
    for tool, r in results.items():
        if not r.get("ok"):
            gaps.append(f"{tool}:{r.get('error', {}).get('code')}")
    if not facts:
        facts.append({"ref": f"order:{order_id}", "statement": "No facts could be verified."})
    return {
        "order_id": order_id,
        "facts": facts,
        "gaps": gaps,
        "summary": " ".join(f["statement"] for f in facts)[:900],
    }


_PRIMARY_TOPIC = {"refund_request": "refunds", "damaged_item": "damage", "order_status": "shipping"}
_TOPICS = {
    "refund_request": ["refunds", "returns", "exceptions"],
    "damaged_item": ["damage", "refunds", "exceptions"],
    "order_status": ["shipping", "exceptions"],
    "other": ["exceptions"],
}
_QUERY = {
    "refund_request": "refund eligibility after return received inspection passed approval",
    "damaged_item": "damaged item reporting window photos description refund replacement",
    "order_status": "shipping delay tracking estimated delivery escalation",
    "other": "human review exceptions",
}


def _policy_call(task: dict[str, Any], call_id: str) -> ToolCall:
    order = task.get("order") or {}
    when = order.get("delivered_at") or order.get("placed_at")
    effective = (datetime.fromisoformat(when).date() if when else date.today()).isoformat()
    category = task.get("category", "other")
    return ToolCall(
        call_id,
        "search_policy",
        {
            "query": f"{_QUERY.get(category, '')} {task.get('complaint', '')}"[:480],
            "policy_filters": {"topics": _TOPICS.get(category, ["exceptions"]), "effective_on": effective},
        },
    )


def _policy_finding(category: str, results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    data = _data(results, "search_policy")
    if data is None:
        return {"citations": [], "interpretation": "Policy search failed.", "missing_policy": True}
    primary = _PRIMARY_TOPIC.get(category)
    primary_docs = [d for d in data["applicable_documents"] if d["topic"] == primary]
    passages = sorted(
        data["passages"], key=lambda p: (p["document_id"] not in {d["document_id"] for d in primary_docs}, -p["score"])
    )
    citations = [
        {
            "document_id": p["document_id"],
            "version": p["version"],
            "section_ref": p["section_ref"],
            "quote": p["content"][:300],
        }
        for p in passages[:3]
    ]
    missing = primary is not None and primary in data["missing_topics"]
    conflict = bool(data["conflicts"])
    if missing:
        interpretation = f"No {primary} policy is in effect for this order's date."
    elif conflict:
        interpretation = "More than one version of a policy applies; a human must decide which governs."
    else:
        titles = ", ".join(sorted({p["title"] for p in passages[:3]}))
        interpretation = f"The applicable policy text comes from: {titles}."
    return {
        "citations": citations,
        "interpretation": interpretation,
        "applicable_version": primary_docs[0]["version"] if len(primary_docs) == 1 else None,
        "missing_policy": missing,
        "conflict": conflict,
    }


def _proposal(
    task: dict[str, Any], facts: dict[str, Any], evidence: dict[str, Any], policy: dict[str, Any], fault: str | None
) -> dict[str, Any]:
    category = task.get("category")
    order = facts.get("order") or {}
    order_id = order.get("order_id") or task.get("order_id")
    ret = (facts.get("return") or {}).get("latest") or {}
    refs = [f["ref"] for f in evidence.get("facts", [])]
    prefs = [f"{c['document_id']}{c['section_ref']}" for c in policy.get("citations", [])]
    base = {
        "order_id": order_id,
        "currency": "INR",
        "evidence_references": refs,
        "policy_references": prefs,
        "missing_information": [],
        "amount_minor": 0,
    }
    if policy.get("missing_policy") or policy.get("conflict"):
        return {**base, "action": "escalate", "reason": policy.get("interpretation", "Policy unclear.")}
    balance = int(order.get("refundable_balance_minor") or 0)
    if category == "refund_request":
        if (
            ret.get("status") == "received"
            and ret.get("inspection_status") == "passed"
            and balance > 0
            and not order.get("reserved_refund_minor")
        ):
            amount = balance * 10 if fault == "inflate_amount" else balance
            return {
                **base,
                "order_id": "ORD-5003" if fault == "wrong_order" else order_id,
                "action": "refund",
                "amount_minor": amount,
                "reason": "The return was received and passed inspection.",
            }
        return {**base, "action": "answer_only", "reason": "The refund is not currently eligible; explain the status."}
    if category == "damaged_item":
        report = task.get("damage_report") or {}
        missing = [
            k
            for k, field in (("damage_description", "description"), ("photo_confirmation", "photos_available"))
            if report.get(field) is None
        ]
        if missing:
            return {
                **base,
                "action": "request_information",
                "missing_information": missing,
                "reason": "Details of the damage are needed.",
            }
        if report.get("preference") == "replacement" or report.get("photos_available") is False:
            return {**base, "action": "escalate", "reason": "Needs the fulfilment team or a specialist."}
        return {
            **base,
            "action": "refund",
            "amount_minor": balance * 10 if fault == "inflate_amount" else balance,
            "reason": "Damage reported with a description and photos.",
        }
    if category == "order_status":
        return {**base, "action": "answer_only", "reason": "Share the latest verified tracking status."}
    return {
        **base,
        "action": "request_information",
        "missing_information": ["issue_details"],
        "reason": "The request is unclear.",
    }


# --------------------------------------------------------------------------- provider


class MockLLM:
    name = "mock"
    model = "mock-deterministic-v1"

    def chat(
        self, *, agent: str, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse:
        fault = _take(agent)
        prompt_chars = len(system) + sum(len(str(m.get("content", ""))) for m in messages)

        def reply(text: str = "", calls: list[ToolCall] | None = None) -> LLMResponse:
            out_chars = len(text) + sum(len(json.dumps(c.arguments)) for c in calls or [])
            return LLMResponse(
                text=text,
                tool_calls=calls or [],
                input_tokens=prompt_chars // 4 + 1,
                output_tokens=out_chars // 4 + 1,
                model=self.model,
                stop_reason="tool_use" if calls else "end_turn",
            )

        if fault == "provider_error":
            raise LLMError("LLM_PROVIDER_ERROR", "Injected provider failure", retryable=False)
        if fault == "invalid_json":
            return reply('Here is the result: {"category": "refund_request", "summary": ')
        if fault == "schema_violation":
            return reply('{"unexpected": true}')
        if fault == "endless_tools" and tools:
            task = _task(messages)
            return reply(
                calls=[
                    ToolCall(f"loop{len(messages)}", tools[0]["name"], {"order_id": task.get("order_id", "ORD-0000")})
                ]
            )
        if fault == "forbidden_tool":
            return reply(calls=[ToolCall("forbidden", "execute_approved_refund", {"proposal_id": "prp_any"})])

        task = _task(messages)
        results = _results(messages)
        called = any(m["role"] == "tool" for m in messages)
        last_is_repair = messages[-1]["role"] == "user" and "did not validate" in messages[-1]["content"]

        if agent == "classifier":
            return reply(json.dumps(classify("\n".join(task.get("messages", [])))))
        if agent == "investigation":
            if not called:
                oid = task["order_id"]
                names = ["get_order", "get_return_status", "get_payment_status"]
                if task.get("category") == "order_status":
                    names = ["get_order", "get_shipment_status"]
                return reply(calls=[ToolCall(f"c{i}", n, {"order_id": oid}) for i, n in enumerate(names)])
            return reply(json.dumps(_evidence(task["order_id"], results)))
        if agent == "policy":
            if not called:
                return reply(calls=[_policy_call(task, "p0")])
            return reply(json.dumps(_policy_finding(task.get("category", "other"), results)))
        if agent == "resolution":
            return reply(
                json.dumps(
                    _proposal(task, task.get("facts", {}), task.get("evidence", {}), task.get("policy", {}), fault)
                )
            )
        if agent == "single":
            if not called:
                oid = task["order_id"]
                names = ["get_order", "get_return_status", "get_payment_status"]
                if task.get("category") == "order_status":
                    names = ["get_order", "get_shipment_status"]
                calls = [ToolCall(f"c{i}", n, {"order_id": oid}) for i, n in enumerate(names)]
                return reply(calls=[*calls, _policy_call(task, "p0")])
            evidence = _evidence(task["order_id"], results)
            policy = _policy_finding(task.get("category", "other"), results)
            facts = {"order": _data(results, "get_order"), "return": _data(results, "get_return_status")}
            return reply(
                json.dumps(
                    {
                        "evidence": evidence,
                        "policy": policy,
                        "proposal": _proposal(task, facts, evidence, policy, fault),
                    }
                )
            )
        if agent == "responder":
            return reply(json.dumps({"message": task["template_message"]}))
        if last_is_repair:
            return reply("{}")
        raise LLMError("MOCK_UNKNOWN_AGENT", f"mock has no behaviour for agent {agent!r}")
