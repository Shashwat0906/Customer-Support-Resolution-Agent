# Evaluation

`python -m evals.run_evals` runs all 60 cases in [`evals/datasets/cases.yaml`](../evals/datasets/cases.yaml) end to
end — API → worker → LangGraph → PostgreSQL → mock services — once per agent design, and writes
[`evals/reports/latest.md`](../evals/reports/latest.md) and `latest.json`. CI runs it on every push and fails if any
unauthorized action or duplicate refund occurs.

## Dataset

| Category | Cases | Covers |
|---|---|---|
| order_status | 10 | in transit, label created, out for delivery, delivered, delayed (escalate), carrier exception |
| refund_eligible | 10 | standard, high value (senior review), multi-item, inferred order, policy v1 order |
| refund_ineligible | 10 | failed inspection, return in transit, already refunded, inspection pending, outside window, rejected return, refund in progress, no return, no applicable policy, needs review |
| missing_information | 10 | missing order, damage without details, replacement request, late damage report, vague request, wrong order number, clarification limit |
| failures | 10 | flaky/down shipping API, timeout after provider acceptance, 5xx before acceptance, slow settlement, ticketing failures, worker crashes, invalid model output, payment API down |
| adversarial | 10 | cross-customer order, prompt injection (complaint and policy text), inflated model amount, forbidden tool call, customer/junior approval attempts, concurrent approvals, concurrent tickets, rejection, expiry |

Every case specifies customer, order, applicable policy, allowed and forbidden actions, whether approval is
required, the expected final state, and expected clarification/escalation (`tests/unit/test_eval_dataset.py`
enforces this). **12 cases (2 per category) are held out**: they were written before the first run, use different
phrasing, and were never used to tune the classifier or prompts.

## How outcomes are judged

- **Money**: provider refunds for the case's order (mock provider records) and every non-historic ledger operation
  must have a valid human approval (approved, reviewer present, decided before expiry). No LLM judge.
- **Correctness**: run status, outcome, decision/escalation reason, refund total and count, clarification/escalation
  events, retries, ticket sync, proposal correction, blocked tool attempts and no-leak checks as specified per case.
- **Unauthorized actions**: refunds without a valid approval + forbidden actions taken. Blocked tool attempts are
  reported separately (they were refused, not executed).
- **Duplicate refunds**: more than one new provider refund for one order.
- **Policy retrieval quality**: the governing document is among the agent's own citations (system-attached citations
  excluded); wrong-version citations counted.
- **Grounded responses**: deterministic rules — no unverified success claims, only verified amounts, provider reference
  present when a refund succeeded.
- **Latency**: active execution time (sum of worker job durations) separately from reviewer/customer wait time.
- **Cost**: model calls × estimated tokens × configured price. In mock mode token counts are estimated (chars / 4),
  so cost figures are indicative only.

## Results (latest run, mock LLM)

See the generated report for full tables. Summary:

| | Single | Specialists |
|---|---|---|
| Correct | 59/60 | 59/60 |
| Held-out | 11/12 | 11/12 |
| Unauthorized actions / duplicate refunds | 0 / 0 | 0 / 0 |
| Mean model calls | 4.1 | 7.05 |
| Mean est. cost / case | $0.030 | $0.034 |
| Median active time | ~0.35–0.40 s | ~0.35–0.40 s |

### Interpretation and caveats

- With the deterministic mock, both designs use the same rules, so equal correctness is expected; the measurable
  difference is coordination overhead (+72% model calls, +13% estimated cost for specialists). The comparison of
  *reasoning quality* requires a real model (`LLM_PROVIDER=anthropic`), which was not run here.
- The safety results (0 unauthorized, 0 duplicates) do not depend on the model: they come from the deterministic
  controls, which is the point of the architecture.
- Median reviewer wait is near zero because the harness approves immediately; in the UI the same metric records real
  waiting time.

### Iteration history (honest record)

1. First dev-only run: 45/48. Fixed: the classifier did not know "ship"; **a real bug** where a customer correcting
   their order number kept the first reference; an over-strict expectation in F07 (either concurrent run may win).
2. First full run: specialists 59/60, single 57/60. Single-mode F03/F04 failures were mock-harness artefacts (faults
   consumed on the wrong turn; mock agent not retrying tools after a refused call) — fixed in the mock. Also found
   **a real bug**: the grounding guard flagged "No refund has been issued" as a success claim (negation) — fixed.
3. Held-out B09 has failed in every run and was deliberately left untuned.
