"""Unit tests for visual product recognition and /agent/respond/image endpoint."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.api.main import app
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.product_vision import (
    VisionExtraction,
    VisionRecognitionResult,
    format_vision_reply,
    match_vision_to_catalog,
    prepare_vision_jpeg,
)

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "thanh_lam_products.json"


@pytest.fixture(scope="module")
def snapshot_repo() -> SnapshotProductRepository:
    return SnapshotProductRepository(CATALOG_PATH)


def _make_test_jpeg(width: int = 1200, height: int = 900) -> bytes:
    im = Image.new("RGB", (width, height), (210, 40, 40))
    # Add high-contrast packaging rectangle so test image passes the blank-frame quality gate
    for y in range(height // 4, (3 * height) // 4):
        for x in range(width // 4, width // 4 + 40):
            im.putpixel((x, y), (245, 245, 245))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def test_prepare_vision_jpeg_resizes_and_validates() -> None:
    raw = _make_test_jpeg(1600, 1200)
    out = prepare_vision_jpeg(raw, max_side=896)
    with Image.open(io.BytesIO(out)) as resized:
        assert max(resized.size) <= 896
        assert resized.mode == "RGB"

    with pytest.raises(ValueError):
        prepare_vision_jpeg(b"not-an-image")


def test_match_vision_handles_compound_brand_spacing_and_pack_size(
    snapshot_repo: SnapshotProductRepository,
) -> None:
    ext_beef = VisionExtraction(
        is_retail_product=True,
        brand="Ace Foods",
        product_name="Ba chỉ bò Mỹ",
        pack_size="500g",
        container_type="khay",
        category_vi="thịt bò",
        confidence=0.92,
        model="deepseek-v4.1-flash:free",
    )
    res_beef = match_vision_to_catalog(ext_beef, snapshot_repo)
    assert res_beef.matched_products
    assert "acefoods" in res_beef.matched_products[0].name.lower()
    assert "500g" in res_beef.matched_products[0].name.lower()

    ext_omo = VisionExtraction(
        is_retail_product=True,
        brand="OMO",
        product_name="Nước giặt OMO Matic+ Comfort",
        variant="tinh dầu thơm",
        pack_size="2,8 kg",
        container_type="túi",
        category_vi="nước giặt",
        confidence=0.95,
        model="deepseek-v4.1-flash:free",
    )
    res_omo = match_vision_to_catalog(ext_omo, snapshot_repo)
    assert res_omo.matched_products
    assert "omo" in res_omo.matched_products[0].name.lower()
    assert "2,8kg" in res_omo.matched_products[0].name.lower()


def test_match_vision_rejects_non_retail_and_low_confidence(
    snapshot_repo: SnapshotProductRepository,
) -> None:
    non_prod = VisionExtraction(
        is_retail_product=False,
        confidence=0.0,
        visual_description_vi="ảnh tối không rõ",
    )
    res = match_vision_to_catalog(non_prod, snapshot_repo)
    assert res.matched_products == ()
    assert res.match_method == "not_retail_product"
    reply = format_vision_reply(res)
    assert "chưa nhận diện rõ" in reply.lower()


def test_respond_image_endpoint_grounds_session_and_returns_cards(
    snapshot_repo: SnapshotProductRepository,
) -> None:
    sample = snapshot_repo.search(
        "Sữa tươi tiệt trùng nguyên chất TH True Milk hộp 1 lít", limit=1
    )[0]
    mock_result = VisionRecognitionResult(
        extraction=VisionExtraction(
            is_retail_product=True,
            brand="TH true MILK",
            product_name="Sữa tươi tiệt trùng nguyên chất",
            pack_size="1 lít",
            confidence=0.96,
            model="deepseek-v4.1-flash:free",
        ),
        matched_products=(sample,),
        top_score=120.0,
        match_method="vlm_catalog_rerank",
    )

    with TestClient(app) as client:
        sess = client.post("/sessions", json={"kiosk_id": "kiosk-vision-test"})
        assert sess.status_code == 201
        sid = sess.json()["session_id"]

        with patch(
            "backend.services.product_vision.ProductVisionService.recognize",
            new=AsyncMock(return_value=mock_result),
        ):
            resp = client.post(
                "/agent/respond/image",
                data={"session_id": sid, "message": "Đây là sản phẩm gì?", "language": "vi"},
                files={"file": ("milk.jpg", _make_test_jpeg(), "image/jpeg")},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == sid
        assert sample.product_id in body["grounded_product_ids"]
        assert len(body["products"]) == 1
        assert body["products"][0]["product_id"] == sample.product_id
        assert "TH True Milk" in body["text"] or sample.name in body["text"]


def test_local_embedder_is_strictly_lazy_and_fast_path_skips_vlm(
    snapshot_repo: SnapshotProductRepository,
) -> None:
    import asyncio

    from backend.services.local_product_embedder import (
        LocalEmbedMatchResult,
        LocalProductEmbedder,
        LocalVisualCandidate,
    )
    from backend.services.product_vision import ProductVisionService

    # 1. Confirm lazy init performs zero model loading
    embedder = LocalProductEmbedder(model_dir="data/models/nonexistent-dir")
    assert embedder._loaded is False
    assert embedder.has_artifacts() is False

    # 2. High-confidence local match skips cloud VLM entirely (sim=0.95 passes sibling guard)
    sample = snapshot_repo.search("TH True Milk hộp 1 lít", limit=1)[0]

    class _StubFastEmbedder(LocalProductEmbedder):
        def has_artifacts(self) -> bool:
            return True

        def match_image_bytes(self, raw_image_bytes: bytes, **kwargs) -> LocalEmbedMatchResult:
            return LocalEmbedMatchResult(
                candidates=(
                    LocalVisualCandidate(
                        product_id=sample.product_id,
                        name=sample.name,
                        brand=sample.brand or "",
                        category=sample.category or "",
                        price_vnd=sample.price_vnd,
                        similarity=0.95,
                    ),
                ),
                top1_similarity=0.95,
                top2_similarity=0.61,
                margin=0.34,
                is_high_confidence=True,
                is_hybrid_candidate=True,
                backend_name="tflite_litert",
                model_file="thanh_lam_mobilenetv3_fp16.tflite",
                embed_ms=18.5,
                match_ms=0.4,
                total_ms=21.2,
            )

    svc = ProductVisionService(local_embedder=_StubFastEmbedder())
    with patch.object(svc, "extract_from_jpeg", new=AsyncMock()) as mock_vlm:
        res = asyncio.run(svc.recognize(_make_test_jpeg(640, 480), snapshot_repo))
        mock_vlm.assert_not_awaited()
    assert res.match_method == "tflite_fast_path"
    assert res.matched_products[0].product_id == sample.product_id
    assert res.latency_ms["vlm_ms"] == 0.0
    assert res.latency_ms["local_embed_ms"] == 18.5

    # 3. Moderate sim (0.86) on a product with pack-size siblings escalates to Hybrid VLM
    class _StubSiblingGuardEmbedder(_StubFastEmbedder):
        def match_image_bytes(self, raw_image_bytes: bytes, **kwargs) -> LocalEmbedMatchResult:
            base = super().match_image_bytes(raw_image_bytes, **kwargs)
            return LocalEmbedMatchResult(
                candidates=base.candidates,
                top1_similarity=0.86,
                top2_similarity=0.61,
                margin=0.25,
                is_high_confidence=True,
                is_hybrid_candidate=True,
                backend_name="tflite_litert",
                model_file="thanh_lam_mobilenetv3_fp16.tflite",
                embed_ms=12.0,
                match_ms=0.3,
                total_ms=14.0,
            )

    svc_guard = ProductVisionService(local_embedder=_StubSiblingGuardEmbedder())
    mock_ext = VisionExtraction(
        is_retail_product=True,
        brand="TH true MILK",
        product_name="Sữa tươi tiệt trùng nguyên chất",
        pack_size="1 lít",
        confidence=0.96,
        model="deepseek-v4.1-flash:free",
    )
    with patch.object(svc_guard, "extract_from_jpeg", new=AsyncMock(return_value=mock_ext)) as m2:
        res2 = asyncio.run(svc_guard.recognize(_make_test_jpeg(640, 480), snapshot_repo))
        m2.assert_awaited_once()
    assert res2.match_method == "tflite_vlm_hybrid"
    assert res2.matched_products[0].product_id == sample.product_id


def test_quality_gate_and_clear_ood_and_cache_skip_vlm(
    snapshot_repo: SnapshotProductRepository,
) -> None:
    import asyncio

    from backend.services.local_product_embedder import (
        LocalEmbedMatchResult,
        LocalProductEmbedder,
    )
    from backend.services.product_vision import ProductVisionService

    # 1. Covered-lens dark photo rejected instantly without calling VLM or local embedder
    dark_buf = io.BytesIO()
    Image.new("RGB", (320, 240), (5, 5, 5)).save(dark_buf, format="JPEG")
    svc = ProductVisionService(local_embedder=LocalProductEmbedder(model_dir="data/models/none"))
    with patch.object(svc, "extract_from_jpeg", new=AsyncMock()) as mock_vlm:
        res_dark = asyncio.run(svc.recognize(dark_buf.getvalue(), snapshot_repo))
        mock_vlm.assert_not_awaited()
    assert res_dark.match_method == "quality_gate_reject"
    assert res_dark.matched_products == ()

    # 2. Clear-OOD local embedder result skips cloud VLM & caches repeat identical upload
    class _StubOodEmbedder(LocalProductEmbedder):
        def __init__(self) -> None:
            super().__init__(model_dir="data/models/none")
            self.calls = 0

        def has_artifacts(self) -> bool:
            return True

        def match_image_bytes(self, raw_image_bytes: bytes, **kwargs) -> LocalEmbedMatchResult:
            self.calls += 1
            return LocalEmbedMatchResult(
                top1_similarity=0.35,
                top2_similarity=0.33,
                margin=0.02,
                is_high_confidence=False,
                is_hybrid_candidate=False,
                is_clear_ood=True,
                crops_run=2,
                backend_name="onnx_parity",
                model_file="thanh_lam_mobilenetv3_fp16.onnx",
                embed_ms=10.0,
                match_ms=0.3,
                total_ms=12.0,
            )

    ood_emb = _StubOodEmbedder()
    svc_ood = ProductVisionService(local_embedder=ood_emb)
    photo = _make_test_jpeg(400, 300)
    with patch.object(svc_ood, "extract_from_jpeg", new=AsyncMock()) as mock_vlm:
        r1 = asyncio.run(svc_ood.recognize(photo, snapshot_repo))
        r2 = asyncio.run(svc_ood.recognize(photo, snapshot_repo))
        mock_vlm.assert_not_awaited()
    assert r1.match_method == "tflite_ood_fast_reject"
    assert r2.match_method == "tflite_ood_fast_reject:cache"
    assert ood_emb.calls == 1


def _make_test_mp4() -> bytes:
    import av
    import numpy as np

    buf = io.BytesIO()
    with av.open(buf, mode="w", format="mp4") as container:
        stream = container.add_stream("mpeg4", rate=10)
        stream.width = 320
        stream.height = 240
        stream.pix_fmt = "yuv420p"
        for frame_idx in range(10):
            if frame_idx < 4:
                arr = np.full((240, 320, 3), 8, dtype=np.uint8)
            else:
                arr = np.full((240, 320, 3), 230, dtype=np.uint8)
                arr[:, ::12, :] = 35
                arr[::12, :, :] = 20
            vf = av.VideoFrame.from_ndarray(arr, format="rgb24")
            for packet in stream.encode(vf):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return buf.getvalue()


def test_video_upload_extracts_sharpest_frame_and_recognizes() -> None:
    from backend.services.product_vision import extract_sharpest_frame_from_video_bytes

    mp4_bytes = _make_test_mp4()
    jpeg_bytes, meta = extract_sharpest_frame_from_video_bytes(mp4_bytes)
    assert jpeg_bytes[:2] == b"\xff\xd8"
    assert len(jpeg_bytes) < 50_000
    assert meta["frames_sampled"] >= 2
    assert meta["best_sharpness"] > 10.0
    # Verify decoded frame is bright/sharp (not one of the pitch-black leading frames).
    decoded = Image.open(io.BytesIO(jpeg_bytes)).convert("L")
    assert float(decoded.resize((32, 32)).getextrema()[1]) > 150.0



