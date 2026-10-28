# Human review guide: Vietnamese retail conversation answers

Use this guide to review customer-visible API answers and multi-turn conversations. The review packet contains only the customer turns, prior dialogue context, the assistant's answer, and product/plan details returned for display. The two reviewer CSVs are independently shuffled. Do not request or use the evaluator-only manifest while scoring.

## What the scores mean

Rate each answer turn from 1 (poor) to 5 (excellent). Use the complete preceding conversation when it is shown. Score the response a customer received, not the model you think produced it. If a dimension does not apply to a greeting or other non-product answer, leave that score blank and explain why in the rationale.

| Dimension | 1 — Poor | 3 — Mixed | 5 — Strong |
| --- | --- | --- | --- |
| Understands the need | Misreads the request or ignores its key constraint. | Gets the main request but misses context, follow-up, budget, or a stated preference. | Accurately reflects the current request and relevant prior turns. |
| Product choice | Unrelated product or a clear hard-constraint violation. | Partly relevant, but misses a useful alternative or leaves a material fit question unresolved. | Relevant catalog choices satisfy the explicit constraints; uncertainty is handled instead of guessed. |
| Explanation | Missing, confusing, or gives an unsupported reason. | Gives a usable reason, but it is incomplete or loosely connected to the request. | Briefly explains the match using relevant product details without overstating them. |
| Evidence and grounding | Makes a material factual claim unsupported or contradicted by the displayed details. | Some key details are traceable, but uncertainty or evidence is unclear. | Claims are supported by the displayed information; unknown facts are clearly identified. |
| Naturalness | Hard to understand, unnatural, repetitive, or inappropriate for the customer's language/register. | Understandable but stiff, overlong, or somewhat repetitive. | Clear, concise, conversational Vietnamese appropriate to the customer's tone. |
| Helpfulness | Does not answer or offer a useful next step. | Partly useful but leaves the customer to repeat or resolve avoidable ambiguity. | Directly useful and gives a proportionate next step or clarification when needed. |

## Critical error flags

Mark each flag `true` only when the answer contains a serious, material issue. Otherwise mark `false`. Do not flag a minor wording preference as a critical error. If evidence is insufficient to decide, leave the flag blank and lower confidence rather than guessing.

- `critical_wrong_product_type`: recommends or identifies an item from the wrong requested product type.
- `critical_hard_constraint_violation`: violates an explicit must-have, exclusion, budget, or other hard constraint.
- `critical_price_or_unit_error`: states a materially wrong price, package size, quantity, or unit.
- `critical_invented_use_or_benefit`: invents a product use, ingredient, efficacy, health effect, taste, or other benefit.
- `critical_false_stock_claim`: claims current availability or quantity without support, or contradicts the answer's displayed evidence.
- `critical_unintended_purchase_action`: adds, reserves, orders, or otherwise acts without the customer's clear authorization.
- `critical_session_data_leakage`: exposes another customer's/session's information.
- `critical_other`: another serious customer-impacting issue; describe it in `critical_other_note`.

## Independent review, agreement, and adjudication

1. A coordinator freezes the exact input result files and review packet. Share each annotator CSV separately; never share the evaluator-only manifest or the other annotator's file before both reviews are complete.
2. Each reviewer uses a pseudonymous `reviewer_id`, fills ratings, flags, confidence (1–5), and a short evidence-based rationale. Keep status `INDEPENDENT_A` or `INDEPENDENT_B` only after submitting an independent review. Preserve the original files unchanged.
3. Join annotations by `review_item_id`. Report raw agreement for critical flags and exact/within-one-point agreement plus a weighted ordinal agreement statistic for each 1–5 dimension. Report the denominator and missing/N/A ratings; do not hide prevalence effects or collapse dimensions into one unsupported score.
4. A third reviewer adjudicates disagreements after seeing both rationales. Add a separate adjudication record with reviewer, timestamp, decision, and rationale. Never overwrite either independent judgment. Keep unresolved items as `UNRESOLVED`.
5. Keep reviewer identities pseudonymous and do not add customer personal data, credentials, provider traces, or model names to the annotation CSV.

## What this packet can and cannot establish

The exporter copies user-visible answer text and a small allowlist of returned product/plan fields. It deliberately removes case IDs, expected labels, automated grades, source labels, model/provider names, tool traces, product IDs, and retrieval ranks from reviewer files. The evaluator-only manifest records input artifact hashes and hidden source mapping; keep it separate.

Human ratings are not produced automatically. A completed two-reviewer sample is still not a blind held-out evaluation if its prompts or results were used to tune the system. For factual claims that cannot be checked from the displayed answer and product details, annotate uncertainty and rationale; this packet does not supply an independent product-truth oracle. Do not claim hallucination accuracy, release readiness, or user acceptance from a blank queue or a single reviewer's ratings.

## Export review queues

From the repository root, pass one result file per system/configuration and one evaluator-only label for each. Labels are written only to the manifest. Do not send that manifest to reviewers.

```powershell
uv run python -m evaluation.export_vietnamese_answer_quality_review `
  --results docs/reports/artifacts/live_snapshot_provider_probe_after_preference_fix_20261028.json `
  --source-label probe_20261028 `
  --output-prefix evaluation/review/answer_quality_probe_20261028
```

To compare variants, repeat `--results` and `--source-label` in matching order. Use files from the same query set and comparable catalog snapshot. The exporter creates `*_annotator_a.csv`, `*_annotator_b.csv`, and `*_evaluator_only_manifest.json`. The static header-only starter is `evaluation/review/vietnamese_answer_quality_template_v1.csv`; no human labels are included.
