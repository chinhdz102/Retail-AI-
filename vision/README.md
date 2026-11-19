# SKU visual retrieval with TFLite

This project has one catalog image for most of its 3,099 SKUs. A closed-set
classifier would memorize those packshots and fail on a phone photo, a rotated
pack, glare, partial occlusion, or a newly added SKU. The deployment target is
therefore a two-stage gallery retriever.

1. A small TFLite embedder maps a query crop and each catalog image to the same
   256-dimensional vector.
2. Cosine similarity returns the top catalog rows. The result joins `product_id`
   to price, stock, category, and the human-readable planogram locator.

The Kaggle notebook trains on augmented catalog images, exports a dynamic-range
quantized TFLite model, and writes `gallery_embeddings.npy` plus
`gallery_metadata.json`. The notebook reports synthetic augmentation metrics
separately from real-phone metrics. Synthetic validation is not evidence that a
phone photo will work.

## Data requirement before production

Collect at least three real photos per important SKU with different angles,
distance, lighting, and partial occlusion. Keep a product-disjoint test set.
For a full shelf photo, add an object detector or a crop step before embedding.
The first release should return top-k candidates and ask the shopper to confirm
when the margin between the first and second candidate is small.

Generate the manifest with:

```powershell
python vision/build_catalog_manifest.py
```

The manifest includes `product_id`, image URL, taxonomy, raw shelf code, and a
human-readable locator. Do not upload `.env` or any API key to Kaggle.

## App integration

The backend exposes `POST /vision/identify` for one uploaded product crop. It
returns top catalog matches, cosine scores, and the readable shelf locator from
the same manifest. Copy the Kaggle export files into `backend/assets/vision`
before enabling the endpoint in a deployed environment.

The endpoint returns `503` when the bundle is missing and `422` for an unreadable
image. This keeps the assistant from inventing a product or shelf when the model
has not been installed.
