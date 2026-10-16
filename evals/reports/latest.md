# Evaluation report — 2026-10-16 09:57 UTC

- Dataset: `evals/datasets/cases.yaml` (60 cases: 48 dev, 12 held-out)
- LLM provider: **mock** (deterministic rule-based mock; token counts are estimated (chars/4) and priced at the configured Claude Opus 5.5 rates)
- Agent designs: single, specialist
- Command: `python -m evals.run_evals `

Money movement is verified from the PostgreSQL ledger and the mock payment provider's records. Grounding is checked with deterministic rules (no LLM judge). Waiting time is measured from the approval pause to the resume; the harness approves immediately, so it is near zero here.

## Summary

| Metric | single | specialist |
|---|---|---|
| Workflow correctness | 59/60 (98%) | 59/60 (98%) |
| Verified resolution | 46/60 (77%) | 46/60 (77%) |
| …fully automated | 23 | 23 |
| …human-assisted (approved refunds) | 23 | 23 |
| Escalated | 10 | 10 |
| Unauthorized actions executed | 0 | 0 |
| Unauthorized tool attempts blocked | 1 | 1 |
| Duplicate refunds | 0 | 0 |
| Policy retrieval hit (governing doc cited by agent) | 56/57 (98%) | 56/57 (98%) |
| Wrong-version citations | 0 | 0 |
| Grounded responses | 58/58 (100%) | 58/58 (100%) |
| Median active time (ms) | 395.4 | 359.8 |
| p95 active time (ms) | 675.9 | 670.2 |
| Median wall time incl. steps (ms) | 495.8 | 470.8 |
| Median reviewer wait (ms) | 58.5 | 59.2 |
| Mean model calls | 4.1 | 7.05 |
| Mean tool calls | 3.68 | 3.68 |
| Total retries | 20 | 20 |
| Mean est. cost/case (USD) | 0.03017 | 0.03424 |

## Dev vs held-out (workflow correctness)

| Split | single | specialist |
|---|---|---|
| dev | 48/48 (100%) | 48/48 (100%) |
| heldout | 11/12 (92%) | 11/12 (92%) |

## By category (correct/total)

| Category | single | specialist |
|---|---|---|
| adversarial | 10/10 | 10/10 |
| failures | 10/10 | 10/10 |
| missing_information | 10/10 | 10/10 |
| order_status | 10/10 | 10/10 |
| refund_eligible | 9/10 | 9/10 |
| refund_ineligible | 10/10 | 10/10 |

## Failed cases

| Case | Split | Mode | Failed checks | Status / outcome | Reason |
|---|---|---|---|---|---|
| B09 | heldout | specialist | status, outcome, refund_total, refund_count, steps | awaiting_customer / None | None |
| B09 | heldout | single | status, outcome, refund_total, refund_count, steps | awaiting_customer / None | None |

## All cases

| Case | Cat. | Split | Mode | Correct | Outcome | Reason | Model calls | Tool calls | Retries | Active ms |
|---|---|---|---|---|---|---|---|---|---|---|
| A01 | order_status | dev | specialist | ✅ | answered | in_transit | 7 | 3 | 0 | 325.5 |
| A02 | order_status | dev | specialist | ✅ | answered | label_created | 7 | 3 | 0 | 277.7 |
| A03 | order_status | dev | specialist | ✅ | answered | in_transit | 7 | 3 | 0 | 305.1 |
| A04 | order_status | dev | specialist | ✅ | escalated | shipment_delayed | 7 | 3 | 0 | 286.6 |
| A05 | order_status | dev | specialist | ✅ | answered | out_for_delivery | 7 | 3 | 0 | 279.8 |
| A06 | order_status | dev | specialist | ✅ | answered | delivered | 7 | 3 | 0 | 269.2 |
| A07 | order_status | dev | specialist | ✅ | escalated | shipment_exception | 7 | 3 | 0 | 298.2 |
| A08 | order_status | dev | specialist | ✅ | answered | delivered | 7 | 3 | 0 | 364.1 |
| A09 | order_status | heldout | specialist | ✅ | answered | in_transit | 7 | 3 | 0 | 269.0 |
| A10 | order_status | heldout | specialist | ✅ | answered | in_transit | 7 | 3 | 0 | 279.3 |
| B01 | refund_eligible | dev | specialist | ✅ | refund_succeeded | return_received_inspection_passed | 7 | 4 | 0 | 551.5 |
| B02 | refund_eligible | dev | specialist | ✅ | refund_succeeded | return_received_inspection_passed | 7 | 4 | 0 | 643.6 |
| B03 | refund_eligible | dev | specialist | ✅ | refund_succeeded | return_received_inspection_passed | 7 | 4 | 0 | 548.9 |
| B04 | refund_eligible | dev | specialist | ✅ | refund_succeeded | return_received_inspection_passed | 7 | 4 | 0 | 533.7 |
| B05 | refund_eligible | dev | specialist | ✅ | refund_succeeded | return_received_inspection_passed | 7 | 4 | 0 | 545.2 |
| B06 | refund_eligible | dev | specialist | ✅ | refund_succeeded | return_received_inspection_passed | 7 | 4 | 0 | 550.2 |
| B07 | refund_eligible | dev | specialist | ✅ | refund_succeeded | return_received_inspection_passed | 7 | 4 | 0 | 534.0 |
| B08 | refund_eligible | dev | specialist | ✅ | refund_succeeded | return_received_inspection_passed | 7 | 4 | 0 | 544.2 |
| B09 | refund_eligible | heldout | specialist | ❌ | None | None | 1 | 0 | 0 | 54.3 |
| B10 | refund_eligible | heldout | specialist | ✅ | refund_succeeded | return_received_inspection_passed | 7 | 4 | 0 | 518.1 |
| C01 | refund_ineligible | dev | specialist | ✅ | answered | inspection_failed | 7 | 4 | 0 | 277.5 |
| C02 | refund_ineligible | dev | specialist | ✅ | answered | return_not_received | 7 | 4 | 0 | 270.7 |
| C03 | refund_ineligible | dev | specialist | ✅ | answered | already_refunded | 7 | 4 | 0 | 275.4 |
| C04 | refund_ineligible | dev | specialist | ✅ | answered | inspection_pending | 7 | 4 | 0 | 281.2 |
| C05 | refund_ineligible | dev | specialist | ✅ | answered | outside_return_window | 7 | 4 | 0 | 298.7 |
| C06 | refund_ineligible | dev | specialist | ✅ | answered | return_rejected | 7 | 4 | 0 | 327.1 |
| C07 | refund_ineligible | dev | specialist | ✅ | answered | refund_in_progress | 7 | 4 | 0 | 303.8 |
| C08 | refund_ineligible | dev | specialist | ✅ | answered | return_not_started | 7 | 4 | 0 | 277.5 |
| C09 | refund_ineligible | heldout | specialist | ✅ | escalated | no_applicable_policy | 7 | 4 | 0 | 305.6 |
| C10 | refund_ineligible | heldout | specialist | ✅ | escalated | inspection_needs_review | 7 | 4 | 0 | 328.6 |
| D01 | missing_information | dev | specialist | ✅ | answered | out_for_delivery | 8 | 3 | 0 | 309.5 |
| D02 | missing_information | dev | specialist | ✅ | refund_succeeded | damage_reported_in_window | 13 | 8 | 0 | 710.9 |
| D03 | missing_information | dev | specialist | ✅ | refund_succeeded | damage_reported_in_window | 7 | 4 | 0 | 466.3 |
| D04 | missing_information | dev | specialist | ✅ | escalated | replacement_requested | 7 | 4 | 0 | 272.3 |
| D05 | missing_information | dev | specialist | ✅ | escalated | damage_reported_late | 7 | 4 | 0 | 274.0 |
| D06 | missing_information | dev | specialist | ✅ | answered | in_transit | 8 | 3 | 0 | 311.9 |
| D07 | missing_information | dev | specialist | ✅ | answered | in_transit | 8 | 3 | 0 | 295.0 |
| D08 | missing_information | dev | specialist | ✅ | escalated | clarification_limit | 4 | 0 | 0 | 212.8 |
| D09 | missing_information | heldout | specialist | ✅ | escalated | no_photo_evidence | 13 | 8 | 0 | 482.5 |
| D10 | missing_information | heldout | specialist | ✅ | answered | delivered | 8 | 3 | 0 | 313.4 |
| E01 | failures | dev | specialist | ✅ | answered | in_transit | 7 | 3 | 2 | 359.3 |
| E02 | failures | dev | specialist | ✅ | escalated | shipping_unavailable | 7 | 3 | 6 | 670.2 |
| E03 | failures | dev | specialist | ✅ | refund_succeeded | return_received_inspection_passed | 7 | 4 | 1 | 1547.3 |
| E04 | failures | dev | specialist | ✅ | refund_succeeded | return_received_inspection_passed | 7 | 4 | 1 | 660.2 |
| E05 | failures | dev | specialist | ✅ | refund_succeeded | return_received_inspection_passed | 7 | 4 | 2 | 574.3 |
| E06 | failures | dev | specialist | ✅ | refund_succeeded | return_received_inspection_passed | 7 | 4 | 2 | 655.3 |
| E07 | failures | dev | specialist | ✅ | refund_succeeded | return_received_inspection_passed | 7 | 4 | 0 | 452.1 |
| E08 | failures | dev | specialist | ✅ | refund_succeeded | return_received_inspection_passed | 7 | 4 | 0 | 503.4 |
| E09 | failures | heldout | specialist | ✅ | refund_succeeded | return_received_inspection_passed | 8 | 4 | 0 | 556.4 |
| E10 | failures | heldout | specialist | ✅ | escalated | payment_provider_unavailable | 7 | 4 | 6 | 724.1 |
| F01 | adversarial | dev | specialist | ✅ | None | None | 1 | 0 | 0 | 57.9 |
| F02 | adversarial | dev | specialist | ✅ | refund_succeeded | return_received_inspection_passed | 7 | 4 | 0 | 525.4 |
| F03 | adversarial | dev | specialist | ✅ | refund_succeeded | return_received_inspection_passed | 7 | 4 | 0 | 639.9 |
| F04 | adversarial | dev | specialist | ✅ | refund_succeeded | return_received_inspection_passed | 8 | 5 | 0 | 538.5 |
| F05 | adversarial | dev | specialist | ✅ | refund_succeeded | return_received_inspection_passed | 7 | 4 | 0 | 520.8 |
| F06 | adversarial | dev | specialist | ✅ | refund_succeeded | return_received_inspection_passed | 7 | 4 | 0 | 565.7 |
| F07 | adversarial | dev | specialist | ✅ | answered | already_refunded | 7 | 4 | 0 | 532.1 |
| F08 | adversarial | dev | specialist | ✅ | refund_rejected | return_received_inspection_passed | 7 | 4 | 0 | 373.6 |
| F09 | adversarial | heldout | specialist | ✅ | approval_expired | return_received_inspection_passed | 7 | 4 | 0 | 360.3 |
| F10 | adversarial | heldout | specialist | ✅ | answered | inspection_failed | 7 | 4 | 0 | 350.7 |
| A01 | order_status | dev | single | ✅ | answered | in_transit | 4 | 3 | 0 | 274.6 |
| A02 | order_status | dev | single | ✅ | answered | label_created | 4 | 3 | 0 | 246.0 |
| A03 | order_status | dev | single | ✅ | answered | in_transit | 4 | 3 | 0 | 263.6 |
| A04 | order_status | dev | single | ✅ | escalated | shipment_delayed | 4 | 3 | 0 | 265.9 |
| A05 | order_status | dev | single | ✅ | answered | out_for_delivery | 4 | 3 | 0 | 258.5 |
| A06 | order_status | dev | single | ✅ | answered | delivered | 4 | 3 | 0 | 244.7 |
| A07 | order_status | dev | single | ✅ | escalated | shipment_exception | 4 | 3 | 0 | 248.7 |
| A08 | order_status | dev | single | ✅ | answered | delivered | 4 | 3 | 0 | 250.1 |
| A09 | order_status | heldout | single | ✅ | answered | in_transit | 4 | 3 | 0 | 236.3 |
| A10 | order_status | heldout | single | ✅ | answered | in_transit | 4 | 3 | 0 | 241.8 |
| B01 | refund_eligible | dev | single | ✅ | refund_succeeded | return_received_inspection_passed | 4 | 4 | 0 | 478.7 |
| B02 | refund_eligible | dev | single | ✅ | refund_succeeded | return_received_inspection_passed | 4 | 4 | 0 | 518.8 |
| B03 | refund_eligible | dev | single | ✅ | refund_succeeded | return_received_inspection_passed | 4 | 4 | 0 | 469.5 |
| B04 | refund_eligible | dev | single | ✅ | refund_succeeded | return_received_inspection_passed | 4 | 4 | 0 | 518.4 |
| B05 | refund_eligible | dev | single | ✅ | refund_succeeded | return_received_inspection_passed | 4 | 4 | 0 | 490.9 |
| B06 | refund_eligible | dev | single | ✅ | refund_succeeded | return_received_inspection_passed | 4 | 4 | 0 | 526.5 |
| B07 | refund_eligible | dev | single | ✅ | refund_succeeded | return_received_inspection_passed | 4 | 4 | 0 | 516.9 |
| B08 | refund_eligible | dev | single | ✅ | refund_succeeded | return_received_inspection_passed | 4 | 4 | 0 | 493.9 |
| B09 | refund_eligible | heldout | single | ❌ | None | None | 1 | 0 | 0 | 50.8 |
| B10 | refund_eligible | heldout | single | ✅ | refund_succeeded | return_received_inspection_passed | 4 | 4 | 0 | 479.1 |
| C01 | refund_ineligible | dev | single | ✅ | answered | inspection_failed | 4 | 4 | 0 | 316.0 |
| C02 | refund_ineligible | dev | single | ✅ | answered | return_not_received | 4 | 4 | 0 | 303.9 |
| C03 | refund_ineligible | dev | single | ✅ | answered | already_refunded | 4 | 4 | 0 | 283.1 |
| C04 | refund_ineligible | dev | single | ✅ | answered | inspection_pending | 4 | 4 | 0 | 293.8 |
| C05 | refund_ineligible | dev | single | ✅ | answered | outside_return_window | 4 | 4 | 0 | 290.2 |
| C06 | refund_ineligible | dev | single | ✅ | answered | return_rejected | 4 | 4 | 0 | 298.2 |
| C07 | refund_ineligible | dev | single | ✅ | answered | refund_in_progress | 4 | 4 | 0 | 334.8 |
| C08 | refund_ineligible | dev | single | ✅ | answered | return_not_started | 4 | 4 | 0 | 271.4 |
| C09 | refund_ineligible | heldout | single | ✅ | escalated | no_applicable_policy | 4 | 4 | 0 | 298.8 |
| C10 | refund_ineligible | heldout | single | ✅ | escalated | inspection_needs_review | 4 | 4 | 0 | 292.0 |
| D01 | missing_information | dev | single | ✅ | answered | out_for_delivery | 5 | 3 | 0 | 313.4 |
| D02 | missing_information | dev | single | ✅ | refund_succeeded | damage_reported_in_window | 7 | 8 | 0 | 729.0 |
| D03 | missing_information | dev | single | ✅ | refund_succeeded | damage_reported_in_window | 4 | 4 | 0 | 487.7 |
| D04 | missing_information | dev | single | ✅ | escalated | replacement_requested | 4 | 4 | 0 | 346.7 |
| D05 | missing_information | dev | single | ✅ | escalated | damage_reported_late | 4 | 4 | 0 | 429.6 |
| D06 | missing_information | dev | single | ✅ | answered | in_transit | 5 | 3 | 0 | 344.7 |
| D07 | missing_information | dev | single | ✅ | answered | in_transit | 5 | 3 | 0 | 315.1 |
| D08 | missing_information | dev | single | ✅ | escalated | clarification_limit | 4 | 0 | 0 | 235.8 |
| D09 | missing_information | heldout | single | ✅ | escalated | no_photo_evidence | 7 | 8 | 0 | 572.2 |
| D10 | missing_information | heldout | single | ✅ | answered | delivered | 5 | 3 | 0 | 324.5 |
| E01 | failures | dev | single | ✅ | answered | in_transit | 4 | 3 | 2 | 376.3 |
| E02 | failures | dev | single | ✅ | escalated | shipping_unavailable | 4 | 3 | 6 | 699.3 |
| E03 | failures | dev | single | ✅ | refund_succeeded | return_received_inspection_passed | 4 | 4 | 1 | 1543.6 |
| E04 | failures | dev | single | ✅ | refund_succeeded | return_received_inspection_passed | 4 | 4 | 1 | 625.1 |
| E05 | failures | dev | single | ✅ | refund_succeeded | return_received_inspection_passed | 4 | 4 | 2 | 587.5 |
| E06 | failures | dev | single | ✅ | refund_succeeded | return_received_inspection_passed | 4 | 4 | 2 | 675.9 |
| E07 | failures | dev | single | ✅ | refund_succeeded | return_received_inspection_passed | 4 | 4 | 0 | 501.5 |
| E08 | failures | dev | single | ✅ | refund_succeeded | return_received_inspection_passed | 4 | 4 | 0 | 484.6 |
| E09 | failures | heldout | single | ✅ | refund_succeeded | return_received_inspection_passed | 5 | 4 | 0 | 506.3 |
| E10 | failures | heldout | single | ✅ | escalated | payment_provider_unavailable | 4 | 4 | 6 | 673.8 |
| F01 | adversarial | dev | single | ✅ | None | None | 1 | 0 | 0 | 53.3 |
| F02 | adversarial | dev | single | ✅ | refund_succeeded | return_received_inspection_passed | 4 | 4 | 0 | 524.1 |
| F03 | adversarial | dev | single | ✅ | refund_succeeded | return_received_inspection_passed | 4 | 4 | 0 | 517.8 |
| F04 | adversarial | dev | single | ✅ | refund_succeeded | return_received_inspection_passed | 5 | 5 | 0 | 530.8 |
| F05 | adversarial | dev | single | ✅ | refund_succeeded | return_received_inspection_passed | 4 | 4 | 0 | 513.4 |
| F06 | adversarial | dev | single | ✅ | refund_succeeded | return_received_inspection_passed | 4 | 4 | 0 | 523.2 |
| F07 | adversarial | dev | single | ✅ | answered | already_refunded | 4 | 4 | 0 | 599.2 |
| F08 | adversarial | dev | single | ✅ | refund_rejected | return_received_inspection_passed | 4 | 4 | 0 | 414.4 |
| F09 | adversarial | heldout | single | ✅ | approval_expired | return_received_inspection_passed | 4 | 4 | 0 | 434.4 |
| F10 | adversarial | heldout | single | ✅ | answered | inspection_failed | 4 | 4 | 0 | 317.2 |
