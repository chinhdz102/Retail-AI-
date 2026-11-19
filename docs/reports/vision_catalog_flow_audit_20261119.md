# Catalog and vision readiness audit — 2026-11-19

## Live catalog smoke test

The backend was exercised through the running API with the Vietnamese flows from the requested test set.

| Scenario | Result | Notes |
| --- | --- | --- |
| Sữa tươi → ít/không đường → hạn chế đường | Pass after routing fix | Follow-up turns now keep the milk scope and return only rows whose names carry the requested label. The response states that the name label does not replace the nutrition panel. |
| Nước giải khát → ít đường | Pass after routing fix | The first turn now browses the `Nước Ngọt` catalog family without an LLM call. The follow-up keeps the drink taxonomy and excludes unrelated functional drinks. |
| Ăn sáng nhanh, không cần nấu → bỏ bánh mì | Pass after routing fix | The plan selects catalog rows from Cháo, Mì and Bánh bao. A negative product-category follow-up removes a named catalog category while preserving the other choices. |
| Ăn lẩu | Pass | The plan returns meat slices, seafood, leafy vegetables, mushrooms and hotpot items. It does not return sauces, minced meat or unrelated household goods. |
| Store administration and checkout on the snapshot | Safe fail-closed | The current snapshot has product data, but no verified branch-order or transaction feed. The assistant declines to invent order, revenue, cart-promotion or replenishment facts. |

Regression checks passed with **1119 tests** under the demo catalog and **157 snapshot consultation/vision tests**. The uploaded-image endpoint was also checked with a real catalog image. It returns `503` until the Kaggle TFLite bundle is installed, which prevents an ungrounded product or shelf claim.

## Model and data decision

The notebook uses a MobileNetV3Small embedding model with a 256-dimensional normalized output. The app performs cosine retrieval against the catalog gallery and joins the result to the product and shelf manifest. This is a better fit for a catalog that receives new SKUs than a fixed 3,000-class classifier.

The current catalog contains one clean reference image for most SKUs. That supports a bootstrap export, but it cannot establish phone-photo accuracy. Production validation needs at least three real photos per important SKU, a product-disjoint test split, Recall@1 and Recall@5, false-match rate, and abstention rate. A full shelf photo still needs object detection or a crop step before retrieval.

## Research used

The one-shot retail recognition literature supports metric-learning retrieval with augmentation and hard-negative mining for large SKU sets. The shelf-monitoring literature uses product embeddings and cosine similarity for gallery matching. Google’s on-device supermarket work also uses compact on-device product recognition. Recent catalog-to-real work treats the task as embedding retrieval and trains with staged contrastive learning and hard negatives. Sources include the [JSAI one-shot retail paper](https://www.jstage.jst.go.jp/article/pjsai/JSAI2023/0/JSAI2023_2T1GS1004/_pdf), [Shelf Management](https://doi.org/10.1016/j.eswa.2024.124635), [Google on-device supermarket recognition](https://research.google/blog/on-device-supermarket-product-recognition/), and [Cat2Real](https://arxiv.org/abs/2607.09888).

## Remaining work before enabling automatic shelf directions

1. Finish the private Kaggle dataset upload and run the notebook to export `sku_embedder.tflite`, `gallery_embeddings.npy`, `gallery_metadata.json`, and `model_card.json`.
2. Copy those artifacts into `backend/assets/vision` or point `VISION_MODEL_DIR` at the export directory.
3. Collect phone photos across angle, distance, glare and occlusion. Measure the thresholds before showing a single top-1 result.
4. Add a detector or cropper for full-shelf photos. Keep the current top-k confirmation behavior when the score margin is small.
