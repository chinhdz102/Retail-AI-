# Vietnamese product relevance review guide

This workflow prepares independent human judgments for Vietnamese retail search and consultation. The current generated V2 datasets are development material only; exporting a review file does not make its labels independent, validated, or blind.

## Label one query-product pair

Use the product snapshot fields shown in the review sheet as the available evidence. Do not infer stock freshness, popularity, rating, taste, suitability, ingredients, health effects, or use cases from a category or brand name. Mark unsupported attributes `UNKNOWN` and recommend clarification or abstention when they determine the answer.

| Grade | Meaning |
| --- | --- |
| `EXACT` | The item is the requested identity or a fully matching variant, including explicitly requested brand, size, and other identity constraints. |
| `SUBSTITUTE` | It is a reasonable same-purpose alternative and violates no hard constraint. The reason must be stated in the rationale. |
| `COMPLEMENT` | It is useful alongside the requested item but does not satisfy the request by itself. |
| `IRRELEVANT` | It does not satisfy the request, or violates a hard constraint. |
| `UNJUDGED` | Evidence is missing or ambiguous enough that a reviewer cannot assign a relevance grade. Escalate; do not guess. |

## Constraint judgments

For each pair, mark category, exact identity, brand, price, negation, and availability as `PASS`, `FAIL`, `UNKNOWN`, or `NOT_APPLICABLE`. A hard-constraint failure makes the pair `IRRELEVANT`, even if its name looks similar. Availability in a dated snapshot is not proof of live store stock. If the query asks for “best-selling,” “tastiest,” health benefit, or another absent field, annotate `clarification_expected` or `abstention_expected` as true as appropriate.

## Independent review and adjudication

1. Freeze the query set, candidate set, catalog SHA-256, and split manifest before annotation. Do not tune on validation or blind-test judgments.
2. Give two reviewers separate files. Do not show `expected` labels, system rank, model answer, or the other reviewer's judgments. Randomize candidate order within each query using the exporter seed.
3. Each reviewer records a grade, each applicable constraint, whether clarification/abstention is expected, confidence from 1–5, evidence fields, and a short rationale.
4. Compare reviewer agreement by grade and by hard-constraint field. Adjudicate disagreements without overwriting either original judgment; preserve both and record the adjudication.
5. Keep reviewer identifiers pseudonymous. Never include customer data, credentials, or personal information.

## Split policy

`development` can contain synthetic or manually authored material used during iteration. `validation` should be independently authored and reviewed, then frozen before model selection. A `blind_test` requires a separate author/reviewer and access boundary; do not call templated SKU variants or generated prompts blind. The current V2 query templates are synthetic, highly repeated, and have no relevance qrels. Until people annotate the exported sheets and a split manifest is frozen, human metrics and a blind-test score are unavailable.

## Export

Run a benchmark first, then export its returned candidate IDs for review. The exporter creates separate annotator A/B CSVs and omits the benchmark's expected labels and ranking positions:

```powershell
uv run python -m evaluation.export_vietnamese_qrels_review `
  --dataset evaluation/cases/retail_ai_semantic_intelligence_v2_dev_rev1_20261028.json `
  --results docs/reports/artifacts/retail_ai_semantic_intelligence_v2_dev_rev1.json `
  --output-prefix evaluation/review/retail_ai_v2_dev_rev1
```

The CSVs are a queue, not completed judgments. After review, normalize rows to JSONL matching `schemas/vietnamese_product_qrel_v1.schema.json` and retain dataset/catalog hashes and each reviewer's independent labels.
