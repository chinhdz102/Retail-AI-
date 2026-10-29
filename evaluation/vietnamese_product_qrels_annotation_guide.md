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

For each pair, mark category, exact identity, brand, price, negation, availability, use-case/suitability, audience, variant, quantity/unit, and budget as `PASS`, `FAIL`, `UNKNOWN`, or `NOT_APPLICABLE`. A hard-constraint failure makes the pair `IRRELEVANT`, even if its name looks similar. A matching title/category alone does not prove suitability or audience fit. Availability in a dated snapshot is not proof of live store stock. If the query asks for “best-selling,” “tastiest,” health benefit, or another absent field, annotate `clarification_expected` or `abstention_expected` as true as appropriate.

## Independent review and adjudication

1. Freeze the query set, candidate set, catalog SHA-256, and split manifest before annotation. Do not tune on validation or blind-test judgments.
2. Give two reviewers separate files. Do not show `expected` labels, retrieval system/source, rank, model answer, evaluator-only manifest, or the other reviewer's judgments. Candidate and query order are independently randomized per reviewer with the exporter seed.
3. Each reviewer records a grade, each applicable constraint, whether clarification/abstention is expected, confidence from 1–5, evidence fields, and a short rationale.
4. Compare reviewer agreement by grade and by hard-constraint field. Adjudicate disagreements without overwriting either original judgment; preserve both and record the adjudication.
5. Keep reviewer identifiers pseudonymous. Never include customer data, credentials, or personal information.

## Split policy

`development` can contain synthetic or manually authored material used during iteration. `validation` should be independently authored, independently reviewed by at least two annotators, adjudicated, and frozen before model selection. A `blind_test` has the same requirements plus no tuning exposure, evaluator-held labels, and a data/label access boundary outside the repository. Do not call templated SKU variants or generated prompts blind. `evaluation/dataset_split_registry.json` records known repository-visible datasets as exposed development and currently registers no validation/blind dataset. The exporter rejects attempts to pass a case file under `evaluation/cases/` as `validation` or `blind_test`, and rejects unknown external hashes for those splits. Register a non-development dataset only after verifying its provenance and access boundary; a filename or CLI split flag is not evidence of blindness. Until people annotate the exported sheets and an eligible split is registered, human metrics and a blind-test score are unavailable.

## Export

Run the retrieval systems first, then export their candidate union for review. Pass one results artifact per retrieval system; the exporter interleaves candidates by rank, deduplicates IDs, and caps the pool per query. It creates separately randomized annotator A/B CSVs and omits expected labels, retrieval source, and ranking positions. It also writes an evaluator-only manifest with dataset/catalog and candidate-artifact hashes; do not distribute that manifest to annotators.

```powershell
uv run python -m evaluation.export_vietnamese_qrels_review `
  --dataset evaluation/cases/retail_ai_semantic_intelligence_v2_dev_rev1_20261028.json `
  --results docs/reports/artifacts/retail_ai_semantic_intelligence_v2_dev_rev1.json `
  --max-candidates-per-query 50 `
  --output-prefix evaluation/review/retail_ai_v2_dev_rev1
```

Repeat `--results` for each retrieval variant only when it was run against the same dataset and carries matching `case_id` values. The exporter includes only dataset cases present in at least one supplied result artifact; when a system ran a case but returned no candidates, that case remains reviewable as an explicit no-candidate row. Candidate IDs absent from the validated runtime catalog stop the export. Do not pool artifacts from unrelated query sets.

The CSVs are a queue, not completed judgments. After review, normalize rows to JSONL matching `schemas/vietnamese_product_qrel_v1_1.schema.json` and retain dataset/catalog hashes and each reviewer's independent labels.
