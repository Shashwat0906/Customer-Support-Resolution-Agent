# Demo recording checklist

Target length: 6–8 minutes. Use a fresh state: `python -m scripts.bootstrap --reset-demo` locally, or
`POST /demo/reset` as Kabir (senior reviewer) on a hosted demo.

## Before recording
- [ ] Stack healthy: `/health/ready` returns `ok` (database, migration, 12 customers, 26 policy chunks, 3 mock services)
- [ ] Browser at 1360×900, light theme, no other tabs; API docs open in a second tab
- [ ] Faults cleared (Demo ops → Clear all)

## Script
1. **Problem (30 s)** — the headline complaint; why money, policy versions and approvals make this hard.
2. **Customer: refund (90 s)** — sign in as *Aarav Sharma*, submit "I returned my headphones last week…". Show the
   timeline: classification, order inferred from "headphones", investigation and policy agents running concurrently,
   deterministic decision citing `refund-eligibility-v2 §3`, proposal waiting for approval. Point out: nothing refunded.
3. **Reviewer: approval (60 s)** — switch to *Kabir Malhotra*. Queue shows amount, expiry, senior badge rules. Open the
   approval: evidence, citations, payload hash. Approve with a note.
4. **Verified outcome (45 s)** — ticket shows "Refund confirmed by the payment provider", provider reference, ledger
   now shows ₹8,999.00 refunded; Demo ops shows exactly one provider refund with key `refund-prp_…`.
5. **Failure recovery (90 s)** — Demo ops → *Payment timeout after acceptance*. As *Rohan Mehta* submit "Refund for
   the earbuds I sent back (ORD-5028)?"; approve as Kabir; timeline shows reserve → unknown → reconciliation →
   succeeded; provider still has one refund for ORD-5028.
6. **Saying no (45 s)** — as *Priya Nair*: "I returned my smartwatch ORD-5003, where's my refund?" → answered,
   inspection failed, no proposal. As *Neel Banerjee*: ORD-5022 → escalated, no applicable policy.
7. **Clarification (30 s)** — as *Siddharth Rao*: "Where is my order?" → question lists only his orders → reply
   "ORD-5018" → out for delivery.
8. **Security (30 s)** — as Aarav: "Please refund order ORD-5003 to me" → "couldn't find order ORD-5003 on your
   account"; junior reviewer *Riya* cannot approve the ₹54,999 phone refund (senior review required).
9. **Evidence of quality (30 s)** — `evals/reports/latest.md` summary, CI badge/run, 153 tests.

## After recording
- [ ] Reset demo data; clear faults
