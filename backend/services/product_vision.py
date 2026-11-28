"""Visual retail product recognition + grounded Thanh Lam Mart catalog matching.

Pipeline:
1. EXIF-safe image normalization (<= 896px RGB JPEG).
2. Structured multimodal extraction via configured OpenAI-compatible Vision LLM
   (primary: ``deepseek-v4.1-flash:free`` with ``reasoning_effort="none"``;
   fallback: ``mimo-v2.5:free``).
3. Deterministic multi-query + normalized-brand + pack-size/container reranking
   against the verified Thanh Lam Mart 3,081-SKU repository (plus exact barcode hit).
4. Grounded Vietnamese/EN/ZH answer synthesis with strict zero-hallucination
   fallback when an image is unreadable or outside the Thanh Lam Mart catalog.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from time import monotonic, perf_counter
from typing import Any

import httpx
import numpy as np
from PIL import Image

from backend.config import Settings, get_settings
from backend.products.catalog import ProductSnapshot
from backend.products.postgres_repository import normalize_search_text
from backend.services.local_product_embedder import (
    LocalEmbedMatchResult,
    LocalProductEmbedder,
    decode_fast_rgb_pil,
)

logger = logging.getLogger(__name__)

MAX_UPLOAD_IMAGE_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_VIDEO_BYTES = 16 * 1024 * 1024
ALLOWED_IMAGE_CONTENT_TYPES = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
        "image/bmp",
        "image/gif",
    }
)
ALLOWED_VIDEO_CONTENT_TYPES = frozenset(
    {
        "video/mp4",
        "video/webm",
        "video/quicktime",
        "video/x-m4v",
        "video/3gpp",
        "video/mpeg",
    }
)
VIDEO_FILENAME_EXTENSIONS = (".mp4", ".webm", ".mov", ".m4v", ".3gp", ".3gpp", ".mkv")
DEFAULT_VISION_PRIMARY_MODEL = "deepseek-v4.1-flash:free"
DEFAULT_VISION_FALLBACK_MODEL = "mimo-v2.5:free"

_VISION_SYSTEM_PROMPT = (
    "Thanh Lam Mart Vietnam product recognizer. Read packaging brand, product name, "
    "flavor/variant, exact pack size (g/kg/ml/L/lít/lốc/thùng), container "
    "(chai/lon/hộp giấy/hộp/túi/gói/khay/thùng/lốc/tuýp), and barcode digits if visible. "
    "If NOT a supermarket product or completely dark/blurry/unreadable, set "
    'is_retail_product=false, confidence=0.0, and visual_description_vi="<=8 words in Vietnamese". '
    'When is_retail_product=true, set visual_description_vi="". Never guess unseen brands. '
    'Return ONLY minified JSON: {"is_retail_product":bool,"brand":str,"product_name":str,'
    '"variant":str,"pack_size":str,"container_type":str,"category_vi":str,"barcode":str,'
    '"confidence":float,"visual_description_vi":str}'
)

_IMAGE_CACHE_MAX_ITEMS = 64
_IMAGE_CACHE_TTL_SECONDS = 300.0

_FILLER_TOKENS = frozenset(
    {
        "huong",
        "vi",
        "chinh",
        "hang",
        "cao",
        "cap",
        "thuong",
        "hang",
        "loai",
        "san",
        "pham",
        "moi",
        "tu",
        "nhien",
        "nguyen",
        "ban",
        "the",
        "for",
        "with",
        "and",
        "flavor",
        "taste",
        "pack",
        "bottle",
        "can",
        "box",
        "bag",
    }
)

_CONTAINER_TERMS = (
    "thung",
    "loc",
    "hop giay",
    "khay",
    "tui",
    "chai",
    "lon",
    "hop",
    "goi",
    "tuyp",
    "binh",
    "can",
    "hu",
    "vi",
)

_MEASURE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(kg|g|gr|gam|ml|lít|lit|l)\b",
    re.IGNORECASE,
)

_PACK_COUNT_RE = re.compile(
    r"(\d+)\s*(?:goi|lon|chai|hop|mieng|cuon|tui|chiec|c)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class VisionExtraction:
    is_retail_product: bool
    brand: str = ""
    product_name: str = ""
    variant: str = ""
    pack_size: str = ""
    container_type: str = ""
    category_vi: str = ""
    visible_text: str = ""
    barcode: str = ""
    confidence: float = 0.0
    visual_description_vi: str = ""
    model: str = ""


@dataclass(frozen=True)
class VisionRecognitionResult:
    extraction: VisionExtraction
    matched_products: tuple[ProductSnapshot, ...] = field(default_factory=tuple)
    related_products: tuple[ProductSnapshot, ...] = field(default_factory=tuple)
    top_score: float = 0.0
    match_method: str = "none"
    local_match: LocalEmbedMatchResult | None = None
    latency_ms: dict[str, float] = field(default_factory=dict)


def _score_pil_sharpness(rgb: Image.Image) -> float:
    """Fast gradient-sharpness + exposure score on a 96x96 grayscale thumbnail."""
    thumb = rgb.convert("L").resize((96, 96), Image.Resampling.BILINEAR)
    gray = np.asarray(thumb, dtype=np.float32)
    mean = float(gray.mean())
    std = float(gray.std())
    if mean < 18.0 or mean > 246.0 or std < 7.0:
        return 0.01 * std
    gx = np.abs(gray[:, 1:] - gray[:, :-1]).mean()
    gy = np.abs(gray[1:, :] - gray[:-1, :]).mean()
    exposure = mean / 45.0 if mean < 45.0 else ((255.0 - mean) / 35.0 if mean > 220.0 else 1.0)
    return float((gx + gy) * max(0.25, exposure))


def extract_sharpest_frame_from_video_bytes(
    raw_video_bytes: bytes,
    *,
    max_side: int = 640,
    max_samples: int = 8,
) -> tuple[bytes, dict[str, Any]]:
    """In-memory PyAV video keyframe extractor (zero disk writes, serverless-safe).

    Samples up to ``max_samples`` strided frames, scores sharpness & lighting,
    and returns a compact 640px JPEG of the clearest frame for instant recognition.
    """
    started = perf_counter()
    if not raw_video_bytes:
        raise ValueError("Video tải lên bị trống.")
    if len(raw_video_bytes) > MAX_UPLOAD_VIDEO_BYTES:
        raise ValueError("Video vượt quá giới hạn 16 MB. Bạn quay clip ngắn hơn nhé.")

    try:
        import av  # type: ignore[import-untyped]
    except Exception as exc:
        raise ValueError(
            "Máy chủ chưa hỗ trợ giải mã video trực tiếp; bạn thử chụp ảnh sản phẩm nhé."
        ) from exc

    best_img: Image.Image | None = None
    best_score = -1.0
    sampled = 0
    try:
        with av.open(io.BytesIO(raw_video_bytes)) as container:
            v_streams = [s for s in container.streams if s.type == "video"]
            if not v_streams:
                raise ValueError("Tệp video không có luồng hình ảnh hợp lệ.")
            stream = v_streams[0]
            stream.thread_type = "AUTO"
            total_frames = int(stream.frames or 0)
            # Sample evenly across the first ~150 frames (~5s at 30fps) so decoding stays <80ms.
            stride = max(1, min(total_frames, 180) // max(1, max_samples)) if total_frames else 8

            for idx, frame in enumerate(container.decode(stream)):
                if idx % stride != 0 and sampled > 0:
                    if idx > 240:
                        break
                    continue
                rgb = frame.to_image().convert("RGB")
                score = _score_pil_sharpness(rgb)
                sampled += 1
                if score > best_score:
                    best_score = score
                    best_img = rgb
                if sampled >= max_samples:
                    break
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(
            "Không đọc được video đã tải lên. Bạn thử gửi định dạng MP4/WebM hoặc chụp ảnh nhé."
        ) from exc

    if best_img is None:
        raise ValueError("Không tìm thấy khung hình hợp lệ trong video.")

    jpeg_bytes = encode_pil_to_jpeg(best_img, max_side=max_side, quality=78)
    elapsed_ms = round((perf_counter() - started) * 1000.0, 1)
    return jpeg_bytes, {
        "frames_sampled": sampled,
        "best_sharpness": round(best_score, 2),
        "extract_ms": elapsed_ms,
        "jpeg_bytes": len(jpeg_bytes),
    }


def decode_vision_image(raw_bytes: bytes, *, max_side: int = 640) -> Image.Image:
    """Validate and single-decode an uploaded image using libjpeg DCT draft scaling."""
    if not raw_bytes:
        raise ValueError("Ảnh tải lên trống.")
    if len(raw_bytes) > MAX_UPLOAD_IMAGE_BYTES:
        raise ValueError("Ảnh vượt quá giới hạn 8 MB. Bạn chụp lại hoặc chọn ảnh nhẹ hơn nhé.")
    try:
        rgb = decode_fast_rgb_pil(raw_bytes, draft_size=max_side)
        if max(rgb.size) > max_side:
            rgb.thumbnail((max_side, max_side), Image.Resampling.BILINEAR)
        return rgb
    except Exception as exc:
        raise ValueError(
            "Không đọc được định dạng ảnh. Bạn gửi ảnh JPG, PNG hoặc WebP nhé."
        ) from exc


def encode_pil_to_jpeg(rgb: Image.Image, *, max_side: int = 640, quality: int = 76) -> bytes:
    """Fast JPEG encode for VLM payload (~20-40 KB, skips slow Huffman optimize pass)."""
    img = rgb if max(rgb.size) <= max_side else rgb.copy()
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.Resampling.BILINEAR)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality)
    return out.getvalue()


def check_image_usability(rgb: Image.Image) -> str | None:
    """Sub-millisecond check rejecting covered-lens dark shots or blank flat frames."""
    tiny = rgb.convert("L").resize((48, 48), Image.Resampling.BILINEAR)
    arr = np.asarray(tiny, dtype=np.float32)
    mean_lum = float(arr.mean())
    std_lum = float(arr.std())
    if mean_lum < 15.0 and std_lum < 12.0:
        return "ảnh quá tối hoặc bị che ống kính"
    if std_lum < 5.5:
        return "ảnh quá mờ hoặc không có chi tiết sản phẩm"
    return None


def prepare_vision_jpeg(raw_bytes: bytes, *, max_side: int = 640, quality: int = 76) -> bytes:
    """Validate, EXIF-orient, and resize an uploaded image to compact RGB JPEG."""
    rgb = decode_vision_image(raw_bytes, max_side=max_side)
    return encode_pil_to_jpeg(rgb, max_side=max_side, quality=quality)


def _compact_alnum(text: str) -> str:
    norm = normalize_search_text(text)
    return re.sub(r"[^a-z0-9]+", "", norm)


def _clean_tokens(text: str) -> list[str]:
    norm = normalize_search_text(text)
    norm = re.sub(r"[^a-z0-9]+", " ", norm)
    return [tok for tok in norm.split() if tok and tok not in _FILLER_TOKENS]


def _extract_measure_base(text: str) -> tuple[float, str] | None:
    """Normalize weight/volume to canonical (grams, 'g') or (milliliters, 'ml')."""
    matches = list(_MEASURE_RE.finditer(text or ""))
    if not matches:
        return None
    # Collect all measures in the label and return the final item-unit token.
    parsed: list[tuple[float, str]] = []
    for m in matches:
        raw_num = m.group(1)
        unit = m.group(2).lower()
        # Handle Vietnamese thousands dot like "2.250 kg" vs "1.5 lít"
        if (
            "." in raw_num
            and len(raw_num.split(".")[-1]) == 3
            and unit in {"g", "gr", "gam", "ml"}
        ):
            val = float(raw_num.replace(".", ""))
        else:
            val = float(raw_num.replace(",", "."))
        if unit == "kg":
            parsed.append((val * 1000.0, "g"))
        elif unit in {"g", "gr", "gam"}:
            parsed.append((val, "g"))
        elif unit in {"l", "lit", "lít"}:
            parsed.append((val * 1000.0, "ml"))
        elif unit == "ml":
            parsed.append((val, "ml"))
    return parsed[-1] if parsed else None


def _all_measures_base(text: str) -> list[tuple[float, str]]:
    out: list[tuple[float, str]] = []
    for m in _MEASURE_RE.finditer(text or ""):
        raw_num = m.group(1)
        unit = m.group(2).lower()
        if "." in raw_num and len(raw_num.split(".")[-1]) == 3 and unit in {"g", "gr", "gam", "ml"}:
            val = float(raw_num.replace(".", ""))
        else:
            val = float(raw_num.replace(",", "."))
        if unit == "kg":
            out.append((val * 1000.0, "g"))
        elif unit in {"g", "gr", "gam"}:
            out.append((val, "g"))
        elif unit in {"l", "lit", "lít"}:
            out.append((val * 1000.0, "ml"))
        elif unit == "ml":
            out.append((val, "ml"))
    return out


def _detect_container(text: str) -> str:
    norm = normalize_search_text(text)
    for term in _CONTAINER_TERMS:
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", norm):
            return term
    return ""


def _parse_vision_json(raw_text: str, model_id: str) -> VisionExtraction:
    text = (raw_text or "").strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
        if m:
            text = m.group(1)
    m2 = re.search(r"\{.*\}", text, re.S)
    if not m2:
        return VisionExtraction(is_retail_product=False, confidence=0.0, model=model_id)
    try:
        data = json.loads(m2.group(0))
    except json.JSONDecodeError:
        return VisionExtraction(is_retail_product=False, confidence=0.0, model=model_id)
    if not isinstance(data, dict):
        return VisionExtraction(is_retail_product=False, confidence=0.0, model=model_id)

    def _s(key: str) -> str:
        val = data.get(key)
        return str(val).strip() if isinstance(val, (str, int, float)) and val is not None else ""

    raw_conf = data.get("confidence", 0.0)
    try:
        conf = max(0.0, min(1.0, float(raw_conf)))
    except (TypeError, ValueError):
        conf = 0.0

    barcode = re.sub(r"\D+", "", _s("barcode"))
    if len(barcode) < 8:
        barcode = ""

    return VisionExtraction(
        is_retail_product=bool(data.get("is_retail_product")),
        brand=_s("brand"),
        product_name=_s("product_name"),
        variant=_s("variant"),
        pack_size=_s("pack_size"),
        container_type=_s("container_type"),
        category_vi=_s("category_vi"),
        visible_text=_s("visible_text"),
        barcode=barcode,
        confidence=conf,
        visual_description_vi=_s("visual_description_vi"),
        model=model_id,
    )


def _iter_all_snapshots(repository: Any) -> list[ProductSnapshot]:
    """Read cached ProductSnapshot items when using SnapshotProductRepository."""
    raw = getattr(repository, "_products", None)
    if isinstance(raw, dict):
        return list(raw.values())
    if isinstance(raw, (list, tuple)):
        return list(raw)
    candidates = getattr(repository, "_candidates", None)
    if isinstance(candidates, (list, tuple)):
        items = [getattr(c, "product", None) for c in candidates]
        return [p for p in items if isinstance(p, ProductSnapshot)]
    return []


def score_vision_candidate(extraction: VisionExtraction, product: ProductSnapshot) -> float:
    """Score how closely a Thanh Lam Mart catalog SKU matches the visual extraction."""
    prod_name_norm = normalize_search_text(product.name)
    prod_brand_norm = normalize_search_text(product.brand or "")
    prod_cat_norm = normalize_search_text(product.category or "")
    prod_full_norm = f"{prod_name_norm} {prod_brand_norm} {prod_cat_norm}"
    prod_compact = _compact_alnum(f"{product.name} {product.brand or ''}")
    prod_tokens = set(_clean_tokens(f"{product.name} {product.brand or ''}"))

    score = 0.0

    # 1. Brand match (highest signal — handles space variations like "Ace Foods" == "ACEFOODS")
    brand_compact = _compact_alnum(extraction.brand)
    brand_tokens = _clean_tokens(extraction.brand)
    brand_matched = False
    if brand_compact and len(brand_compact) >= 2:
        if brand_compact in prod_compact:
            score += 46.0
            brand_matched = True
        elif brand_tokens:
            hit_bt = sum(
                1
                for t in brand_tokens
                if len(t) >= 2 and (t in prod_tokens or t in prod_compact)
            )
            if hit_bt == len(brand_tokens):
                score += 42.0
                brand_matched = True
            elif hit_bt > 0:
                score += 20.0 * (hit_bt / len(brand_tokens))

    # 2. Core product name + variant token overlap
    name_tokens = _clean_tokens(f"{extraction.product_name} {extraction.variant}")
    # Drop pure numeric/unit tokens here so weight is scored cleanly in step 4
    lexical_tokens = [
        t for t in name_tokens
        if not re.fullmatch(r"\d+(?:g|kg|ml|l|lit)?", t) and t not in {"matic"}
    ]
    if lexical_tokens:
        matched_lex = 0.0
        for tok in lexical_tokens:
            if tok in prod_tokens:
                matched_lex += 1.0
            elif len(tok) >= 4 and tok in prod_compact:
                matched_lex += 0.85
        recall = matched_lex / len(lexical_tokens)
        score += 36.0 * recall

    # Domain equivalence: TH True Milk unsweetened is named "nguyên chất" in Thanh Lam Mart
    ext_all_norm = normalize_search_text(
        f"{extraction.brand} {extraction.product_name} "
        f"{extraction.variant} {extraction.visible_text}"
    )
    if "th true milk" in ext_all_norm and "th true milk" in prod_full_norm:
        if ("khong duong" in ext_all_norm or "nguyen chat" in ext_all_norm) and (
            "nguyen chat" in prod_name_norm or "khong duong" in prod_name_norm
        ):
            score += 10.0

    # 3. Category match
    cat_tokens = _clean_tokens(extraction.category_vi)
    if cat_tokens:
        cat_hits = sum(1 for t in cat_tokens if t in prod_tokens or t in prod_cat_norm)
        score += 10.0 * (cat_hits / len(cat_tokens))

    # 4. Pack size / weight / volume alignment (e.g. 630ml ~ 630g, 2,8 kg == 2,8kg)
    ext_measures = _all_measures_base(f"{extraction.pack_size} {extraction.product_name}")
    prod_measures = _all_measures_base(product.name)
    if ext_measures and prod_measures:
        best_ratio = 0.0
        for ev, eu in ext_measures:
            for pv, pu in prod_measures:
                if ev <= 0 or pv <= 0:
                    continue
                ratio = min(ev, pv) / max(ev, pv)
                # Shampoo/liquid detergents often swap ml vs g for identical numeric size
                unit_factor = 1.0 if eu == pu else 0.92
                best_ratio = max(best_ratio, ratio * unit_factor)
        if best_ratio >= 0.94:
            score += 22.0
        elif best_ratio >= 0.78:
            score += 11.0
        elif best_ratio < 0.45:
            score -= 8.0

    # 5. Container / packaging form alignment (thùng vs gói, lon vs hộp giấy, chai vs túi)
    ext_container = _detect_container(
        f"{extraction.container_type} {extraction.pack_size} {extraction.product_name}"
    )
    prod_container = _detect_container(f"{product.name} {product.uom or ''}")
    if ext_container and prod_container:
        if ext_container == prod_container:
            score += 12.0
        elif ext_container == "thung" and prod_container != "thung":
            score -= 10.0
        elif prod_container == "thung" and ext_container not in {"thung", ""}:
            score -= 8.0

    # 6. Penalize weak brand-less fuzzy hits when a distinct brand was clearly read
    if brand_compact and len(brand_compact) >= 3 and not brand_matched:
        score -= 18.0

    return round(score, 2)


def match_vision_to_catalog(
    extraction: VisionExtraction,
    repository: Any,
    *,
    limit: int = 4,
    local_match: LocalEmbedMatchResult | None = None,
) -> VisionRecognitionResult:
    """Resolve visual extraction strictly against verified Thanh Lam Mart SKUs."""
    if not extraction.is_retail_product or extraction.confidence < 0.30:
        return VisionRecognitionResult(
            extraction=extraction,
            match_method="not_retail_product",
            local_match=local_match,
        )

    all_snapshots = _iter_all_snapshots(repository)
    snap_by_id = {snap.product_id: snap for snap in all_snapshots}

    # 1. Exact barcode match if visible digits match Thanh Lam Mart catalog
    if extraction.barcode and all_snapshots:
        for snap in all_snapshots:
            snap_bc = re.sub(r"\D+", "", str(getattr(snap, "barcode", "") or ""))
            if snap_bc and snap_bc == extraction.barcode:
                return VisionRecognitionResult(
                    extraction=extraction,
                    matched_products=(snap,),
                    top_score=100.0,
                    match_method="barcode_exact",
                    local_match=local_match,
                )

    # 2. Gather candidate pool via progressive queries + compact-brand scan + local visual top-K
    pool: dict[str, ProductSnapshot] = {}
    local_sim_by_pid: dict[str, float] = {}
    if local_match and local_match.is_hybrid_candidate:
        for cand in local_match.candidates:
            local_sim_by_pid[cand.product_id] = cand.similarity
            snap = snap_by_id.get(cand.product_id)
            if snap is None and hasattr(repository, "get_by_id"):
                try:
                    snap = repository.get_by_id(cand.product_id)
                except Exception:
                    snap = None
            if snap is not None:
                pool[snap.product_id] = snap

    def _add_search(query: str, q_limit: int = 12) -> None:
        cleaned = re.sub(r"[+/\\()\[\]]+", " ", query).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if not cleaned or len(cleaned) < 2:
            return
        try:
            for item in repository.search(cleaned, limit=q_limit):
                pool[item.product_id] = item
        except Exception:
            pass

    brand_compact = _compact_alnum(extraction.brand)
    clean_name = " ".join(_clean_tokens(extraction.product_name))
    clean_variant = " ".join(_clean_tokens(extraction.variant))
    clean_brand = " ".join(_clean_tokens(extraction.brand))
    clean_cat = " ".join(_clean_tokens(extraction.category_vi))

    candidate_queries = [
        f"{extraction.brand} {clean_name} {extraction.pack_size}",
        f"{brand_compact} {clean_name}",
        f"{clean_brand} {clean_name} {clean_variant}",
        f"{clean_brand} {clean_cat}",
        f"{clean_name} {clean_variant}",
        clean_name,
        extraction.brand,
    ]
    seen_q: set[str] = set()
    for q in candidate_queries:
        qk = normalize_search_text(q).strip()
        if qk and qk not in seen_q:
            seen_q.add(qk)
            _add_search(q, q_limit=10)

    # Direct compact-brand sweep catches spacing mismatches ("Ace Foods" -> "ACEFOODS")
    if brand_compact and len(brand_compact) >= 3 and all_snapshots:
        for snap in all_snapshots:
            if brand_compact in _compact_alnum(f"{snap.name} {snap.brand or ''}"):
                pool[snap.product_id] = snap

    # Also sweep strong lexical tokens if pool is still empty
    if not pool and all_snapshots:
        key_toks = [
            t
            for t in _clean_tokens(f"{extraction.product_name} {extraction.category_vi}")
            if len(t) >= 3
        ]
        if key_toks:
            for snap in all_snapshots:
                stoks = set(_clean_tokens(snap.name))
                if len(stoks.intersection(key_toks)) >= min(2, len(key_toks)):
                    pool[snap.product_id] = snap

    if not pool:
        return VisionRecognitionResult(extraction=extraction, match_method="catalog_miss")

    def _hybrid_score(item: ProductSnapshot) -> float:
        base = score_vision_candidate(extraction, item)
        vis_sim = local_sim_by_pid.get(item.product_id, 0.0)
        # Modest visual-agreement tiebreaker (never overrides an explicit brand/size mismatch)
        if vis_sim > 0.0 and base >= 18.0:
            base += round(14.0 * vis_sim, 2)
        return round(base, 2)

    scored = sorted(
        ((_hybrid_score(item), item) for item in pool.values()),
        key=lambda pair: (pair[0], -(pair[1].price_vnd or 0)),
        reverse=True,
    )
    top_score, _ = scored[0]
    if top_score < 28.0:
        return VisionRecognitionResult(
            extraction=extraction,
            top_score=top_score,
            match_method="below_threshold",
            local_match=local_match,
        )

    # Top matched SKU + up to (limit - 1) closely related variants/sizes
    primary_matches = [scored[0][1]]
    related: list[ProductSnapshot] = []
    for sc, item in scored[1:]:
        if len(primary_matches) + len(related) >= limit:
            break
        if sc >= max(28.0, top_score - 12.0) and len(primary_matches) < 2:
            primary_matches.append(item)
        elif sc >= max(24.0, top_score - 22.0):
            related.append(item)

    method = "tflite_vlm_hybrid" if local_sim_by_pid else "vlm_catalog_rerank"
    return VisionRecognitionResult(
        extraction=extraction,
        matched_products=tuple(primary_matches),
        related_products=tuple(related),
        top_score=top_score,
        match_method=method,
        local_match=local_match,
    )


def _has_ambiguous_pack_size_sibling(
    local_match: LocalEmbedMatchResult,
    repository: Any,
) -> bool:
    """Return True when top1 has a same-brand sibling SKU with a different pack size."""
    if not local_match.candidates or local_match.top1_similarity >= 0.92:
        return False
    top = local_match.candidates[0]
    top_brand = _compact_alnum(top.brand)
    if len(top_brand) < 2:
        return False
    top_lex = {
        t
        for t in _clean_tokens(top.name)
        if not re.fullmatch(r"\d+(?:g|kg|ml|l|lit)?", t)
    }
    top_meas = _extract_measure_base(top.name)
    if not top_lex or top_meas is None:
        return False

    for snap in _iter_all_snapshots(repository):
        if snap.product_id == top.product_id:
            continue
        if _compact_alnum(snap.brand or "") != top_brand:
            continue
        snap_meas = _extract_measure_base(snap.name)
        if snap_meas is None or snap_meas == top_meas:
            continue
        snap_lex = {
            t
            for t in _clean_tokens(snap.name)
            if not re.fullmatch(r"\d+(?:g|kg|ml|l|lit)?", t)
        }
        overlap = len(top_lex.intersection(snap_lex))
        if overlap >= max(2, min(len(top_lex), len(snap_lex)) - 1):
            return True
    return False


def _resolve_snapshot_by_id(repository: Any, product_id: str) -> ProductSnapshot | None:
    raw = getattr(repository, "_products", None)
    if isinstance(raw, dict) and product_id in raw:
        return raw[product_id]
    if hasattr(repository, "get_by_id"):
        try:
            snap = repository.get_by_id(product_id)
            if isinstance(snap, ProductSnapshot):
                return snap
        except Exception:
            pass
    for snap in _iter_all_snapshots(repository):
        if snap.product_id == product_id:
            return snap
    return None


class ProductVisionService:
    """Async on-demand hybrid product recognizer (local mobile TFLite/ONNX + Vision LLM)."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        local_embedder: LocalProductEmbedder | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._client: httpx.AsyncClient | None = None
        # Strictly lazy: LocalProductEmbedder performs ZERO I/O until first image upload!
        self.local_embedder = (
            local_embedder if local_embedder is not None else LocalProductEmbedder()
        )
        # Compact LRU result cache keyed by SHA-256 of raw upload bytes (instant repeat queries)
        self._result_cache: OrderedDict[str, tuple[float, VisionRecognitionResult]] = OrderedDict()

    def _cache_get(self, key: str) -> VisionRecognitionResult | None:
        entry = self._result_cache.get(key)
        if entry is None:
            return None
        ts, res = entry
        if (monotonic() - ts) > _IMAGE_CACHE_TTL_SECONDS:
            self._result_cache.pop(key, None)
            return None
        self._result_cache.move_to_end(key)
        return res

    def _cache_put(self, key: str, result: VisionRecognitionResult) -> None:
        self._result_cache[key] = (monotonic(), result)
        self._result_cache.move_to_end(key)
        while len(self._result_cache) > _IMAGE_CACHE_MAX_ITEMS:
            self._result_cache.popitem(last=False)

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    def _vision_credentials(self) -> tuple[str, str, list[str]]:
        base_url = self.settings.effective_chat_base_url.rstrip("/")
        creds = self.settings.chat_api_credentials
        api_key = creds[0][1] if creds else ""
        models: list[str] = []
        primary = self.settings.effective_chat_model.strip()
        if primary and "v4-flash:free" not in primary:
            models.append(primary)
        for fallback in (DEFAULT_VISION_PRIMARY_MODEL, DEFAULT_VISION_FALLBACK_MODEL):
            if fallback not in models:
                models.append(fallback)
        return base_url, api_key, models

    async def extract_from_jpeg(
        self,
        jpeg_bytes: bytes,
        *,
        local_hints: LocalEmbedMatchResult | None = None,
    ) -> VisionExtraction:
        base_url, api_key, models = self._vision_credentials()
        if not base_url or not api_key:
            raise RuntimeError("Dịch vụ nhận diện hình ảnh chưa được cấu hình API key.")

        b64 = base64.b64encode(jpeg_bytes).decode("ascii")
        last_error: str = "unknown"

        user_prompt = "Trả JSON."
        if local_hints and local_hints.is_hybrid_candidate and local_hints.candidates:
            hint_lines = "; ".join(
                f"{c.name} ({c.brand}, {c.similarity:.2f})"
                for c in local_hints.candidates[:3]
            )
            user_prompt = (
                "Trả JSON. Ứng viên gợi ý (chỉ chọn nếu đúng bao bì; không phải hàng siêu thị -> "
                f"is_retail_product=false): {hint_lines}."
            )

        client = self._http_client()
        for model_id in models:
            payload: dict[str, Any] = {
                "model": model_id,
                "max_tokens": 160,
                "temperature": 0,
                "reasoning_effort": "none",
                "messages": [
                    {"role": "system", "content": _VISION_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                            },
                        ],
                    },
                ],
            }
            try:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                )
                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code} ({model_id})"
                    logger.warning("Vision call failed %s: %s", last_error, resp.text[:160])
                    continue
                body = resp.json()
                content = (
                    body.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                if not content or not str(content).strip():
                    last_error = f"Empty response ({model_id})"
                    continue
                return _parse_vision_json(str(content), model_id)
            except Exception as exc:
                last_error = f"{type(exc).__name__} ({model_id})"
                logger.warning("Vision call exception on %s: %s", model_id, exc)
                continue

        raise RuntimeError(f"Không kết nối được model nhận diện ảnh ({last_error}).")

    def _build_local_fast_path_result(
        self,
        local_match: LocalEmbedMatchResult,
        repository: Any,
        *,
        limit: int,
        t_start: float,
    ) -> VisionRecognitionResult | None:
        resolved: list[ProductSnapshot] = []
        for cand in local_match.candidates:
            snap = _resolve_snapshot_by_id(repository, cand.product_id)
            if snap is not None:
                resolved.append(snap)
            if len(resolved) >= limit:
                break
        if not resolved:
            return None

        top = resolved[0]
        related = tuple(resolved[1:limit])
        extraction = VisionExtraction(
            is_retail_product=True,
            brand=top.brand or "",
            product_name=top.name,
            category_vi=top.category or "",
            confidence=min(0.99, max(0.75, local_match.top1_similarity)),
            visual_description_vi=top.name,
            model=f"local:{local_match.model_file or local_match.backend_name}",
        )
        total_ms = round((perf_counter() - t_start) * 1000.0, 2)
        return VisionRecognitionResult(
            extraction=extraction,
            matched_products=(top,),
            related_products=related,
            top_score=round(local_match.top1_similarity * 100.0, 2),
            match_method="tflite_fast_path",
            local_match=local_match,
            latency_ms={
                "local_load_ms": local_match.load_ms,
                "local_preprocess_ms": local_match.preprocess_ms,
                "local_embed_ms": local_match.embed_ms,
                "local_match_ms": local_match.match_ms,
                "vlm_ms": 0.0,
                "total_ms": total_ms,
            },
        )

    async def recognize(
        self,
        raw_image_bytes: bytes,
        repository: Any,
        *,
        limit: int = 4,
        prefer_local_fast_path: bool = True,
    ) -> VisionRecognitionResult:
        t_start = perf_counter()
        if not raw_image_bytes:
            raise ValueError("Ảnh tải lên trống.")
        if len(raw_image_bytes) > MAX_UPLOAD_IMAGE_BYTES:
            raise ValueError("Ảnh vượt quá giới hạn 8 MB. Bạn chụp lại hoặc chọn ảnh nhẹ hơn nhé.")

        # 0. Instant SHA-256 LRU cache hit for repeated uploads / follow-ups (<1 ms!)
        digest = hashlib.sha256(raw_image_bytes).hexdigest()
        cache_key = f"{digest}:{limit}:{int(prefer_local_fast_path)}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return VisionRecognitionResult(
                extraction=cached.extraction,
                matched_products=cached.matched_products,
                related_products=cached.related_products,
                top_score=cached.top_score,
                match_method=f"{cached.match_method}:cache",
                local_match=cached.local_match,
                latency_ms={
                    "total_ms": round((perf_counter() - t_start) * 1000.0, 2),
                    "vlm_ms": 0.0,
                },
            )

        # 1. Single-decode with libjpeg hardware draft scaling (<= 640px, zero double decode!)
        decoded_rgb = await asyncio.to_thread(decode_vision_image, raw_image_bytes, max_side=640)

        # 2. Sub-millisecond image quality gate (catches covered lens / blank walls in <3 ms!)
        unusable_reason = check_image_usability(decoded_rgb)
        if unusable_reason is not None:
            res_unusable = VisionRecognitionResult(
                extraction=VisionExtraction(
                    is_retail_product=False,
                    confidence=0.0,
                    visual_description_vi=unusable_reason,
                    model="local:quality_gate",
                ),
                match_method="quality_gate_reject",
                latency_ms={
                    "vlm_ms": 0.0,
                    "total_ms": round((perf_counter() - t_start) * 1000.0, 2),
                },
            )
            self._cache_put(cache_key, res_unusable)
            return res_unusable

        # 3. On-demand local MobileNetV3 TFLite/ONNX (adaptive 1-crop ~5ms / 2-crop ~11ms!)
        local_match: LocalEmbedMatchResult | None = None
        if self.local_embedder is not None and self.local_embedder.has_artifacts():
            try:
                local_match = await asyncio.to_thread(
                    self.local_embedder.match_image_bytes,
                    raw_image_bytes,
                    top_k=max(5, limit + 1),
                    use_2crop_tta=True,
                    adaptive_1crop=True,
                    pil_image=decoded_rgb,
                )
            except Exception as exc:
                logger.warning("Local embedder skipped due to error: %s", exc)
                local_match = None

        # 4A. Fast-Path: high-confidence unique match -> return in ~12-20 ms (zero cloud!)
        if (
            prefer_local_fast_path
            and local_match is not None
            and local_match.is_high_confidence
            and not _has_ambiguous_pack_size_sibling(local_match, repository)
        ):
            fast_res = self._build_local_fast_path_result(
                local_match, repository, limit=limit, t_start=t_start
            )
            if fast_res is not None:
                self._cache_put(cache_key, fast_res)
                return fast_res

        # 4B. Clear-OOD Local Reject: obviously non-retail scene (<0.43 after 2-crop) -> ~15 ms!
        if prefer_local_fast_path and local_match is not None and local_match.is_clear_ood:
            ood_res = VisionRecognitionResult(
                extraction=VisionExtraction(
                    is_retail_product=False,
                    confidence=0.0,
                    visual_description_vi="không khớp bao bì sản phẩm siêu thị",
                    model=f"local:{local_match.model_file or local_match.backend_name}",
                ),
                match_method="tflite_ood_fast_reject",
                local_match=local_match,
                latency_ms={
                    "local_load_ms": local_match.load_ms,
                    "local_preprocess_ms": local_match.preprocess_ms,
                    "local_embed_ms": local_match.embed_ms,
                    "local_match_ms": local_match.match_ms,
                    "vlm_ms": 0.0,
                    "total_ms": round((perf_counter() - t_start) * 1000.0, 2),
                },
            )
            self._cache_put(cache_key, ood_res)
            return ood_res

        # 5. Escalate ambiguous / sibling-size / borderline-OOD images to compact 640px VLM
        jpeg_bytes = encode_pil_to_jpeg(decoded_rgb, max_side=640, quality=76)
        t_vlm = perf_counter()
        try:
            extraction = await self.extract_from_jpeg(jpeg_bytes, local_hints=local_match)
        except Exception:
            if local_match is not None and local_match.is_hybrid_candidate:
                fallback = self._build_local_fast_path_result(
                    local_match, repository, limit=limit, t_start=t_start
                )
                if fallback is not None:
                    return VisionRecognitionResult(
                        extraction=fallback.extraction,
                        matched_products=fallback.matched_products,
                        related_products=fallback.related_products,
                        top_score=fallback.top_score,
                        match_method="tflite_offline_fallback",
                        local_match=local_match,
                        latency_ms=fallback.latency_ms,
                    )
            raise

        vlm_ms = round((perf_counter() - t_vlm) * 1000.0, 2)
        res = match_vision_to_catalog(
            extraction, repository, limit=limit, local_match=local_match
        )
        total_ms = round((perf_counter() - t_start) * 1000.0, 2)
        final_res = VisionRecognitionResult(
            extraction=res.extraction,
            matched_products=res.matched_products,
            related_products=res.related_products,
            top_score=res.top_score,
            match_method=res.match_method,
            local_match=local_match,
            latency_ms={
                "local_load_ms": local_match.load_ms if local_match else 0.0,
                "local_preprocess_ms": local_match.preprocess_ms if local_match else 0.0,
                "local_embed_ms": local_match.embed_ms if local_match else 0.0,
                "local_match_ms": local_match.match_ms if local_match else 0.0,
                "vlm_ms": vlm_ms,
                "total_ms": total_ms,
            },
        )
        self._cache_put(cache_key, final_res)
        return final_res


def _format_vnd(amount: int) -> str:
    return f"{amount:,}đ".replace(",", ".")


def format_vision_reply(
    result: VisionRecognitionResult,
    *,
    user_question: str = "",
    language: str = "vi",
) -> str:
    """Build a crisp, grounded Vietnamese/EN/ZH response for an image turn."""
    ext = result.extraction
    if not ext.is_retail_product or ext.confidence < 0.30:
        if language == "en":
            return (
                "I couldn't clearly identify a supermarket product in this photo. "
                "Please hold the packaging label closer with better light and try again."
            )
        desc = f" ({ext.visual_description_vi})" if ext.visual_description_vi else ""
        return (
            f"Dạ, mình chưa nhận diện rõ sản phẩm siêu thị nào trong ảnh này{desc}. "
            "Bạn đưa mặt trước bao bì có tên sản phẩm hoặc mã vạch lại gần "
            "và đủ sáng hơn rồi chụp lại giúp mình nhé."
        )

    if not result.matched_products:
        seen_label = " ".join(
            x for x in (ext.product_name, ext.brand, ext.pack_size) if x
        ).strip() or ext.visual_description_vi
        if language == "en":
            return (
                f"From the photo, this looks like {seen_label}, "
                "but I could not verify an exact matching SKU in the "
                "current Thanh Lam Mart catalog."
            )
        return (
            f"Dạ, theo bao bì trong ảnh thì đây là {seen_label}. "
            "Tuy nhiên mình chưa tìm thấy mã sản phẩm khớp chính xác trong danh mục "
            "Thanh Lam Mart hiện tại nên chưa báo giá cụ thể được. "
            "Bạn có muốn mình gợi ý sản phẩm tương tự đang có trong siêu thị không ạ?"
        )

    top = result.matched_products[0]
    price_str = _format_vnd(top.price_vnd)
    promo_note = ""
    if top.list_price_vnd and top.list_price_vnd > top.price_vnd:
        promo_note = f" (đang giảm từ {_format_vnd(top.list_price_vnd)})"
    elif top.promotion:
        promo_note = f" ({top.promotion})"

    if language == "en":
        reply = (
            f"This looks like {top.name} with a reference price of {price_str}. "
            "You can check the card below or ask me to compare sizes or add it to your cart."
        )
        return reply

    reply = f"Dạ, trong ảnh là {top.name}, giá {price_str}{promo_note}."
    if len(result.matched_products) > 1:
        alt = result.matched_products[1]
        reply += f" Ngoài ra trong danh mục còn có {alt.name} ({_format_vnd(alt.price_vnd)})."
    elif result.related_products:
        reply += " Bên dưới mình có để thêm các sản phẩm cùng dòng để bạn tiện tham khảo."

    q_norm = normalize_search_text(user_question)
    if any(w in q_norm for w in ("gia", "bao nhieu", "nhieu tien")):
        reply += " Bạn cần mình so sánh đơn giá hay tìm loại tiết kiệm hơn không ạ?"
    elif any(w in q_norm for w in ("khuyen mai", "giam gia", "uu dai")):
        if not promo_note:
            reply += " Hiện sản phẩm này chưa ghi nhận khuyến mãi riêng."
    else:
        reply += (
            " Bạn muốn mình xem thêm sản phẩm khác, so sánh "
            "hay gợi ý món dùng kèm không ạ?"
        )
    return reply


def is_default_identify_question(message: str) -> bool:
    """Return True when the shopper just wants product identification/price/promo."""
    cleaned = normalize_search_text(message).strip()
    if not cleaned:
        return True
    custom_cues = (
        "nau",
        "che bien",
        "an kem",
        "dung kem",
        "ngon",
        "tot",
        "calo",
        "dinh duong",
        "thanh phan",
        "di ung",
        "tre em",
        "ba bau",
        "so sanh",
        "cach dung",
        "bao quan",
        "nuong",
        "lau",
    )
    if any(cue in cleaned for cue in custom_cues):
        return False
    identify_re = re.compile(
        r"^(?:day la (?:san pham|mon|cai|loai|hang|sp) (?:gi|nao)|"
        r"day la gi|san pham gi|san pham nay (?:la gi|ten gi|gia bao nhieu)|"
        r"cai nay (?:la gi|loai nao|ten gi|bao nhieu|gia bao nhieu|gia sao)|"
        r"loai nay (?:la gi|bao nhieu|gia bao nhieu)|"
        r"gia bao nhieu|gia sao|xem gia|check gia|quet anh|nhan dien|"
        r"co khuyen mai khong|co giam gia khong|"
        r"what is this|what product is this|how much is this)\b"
    )
    if identify_re.search(cleaned) and len(cleaned.split()) <= 10:
        return True
    return len(cleaned.split()) <= 5 and any(
        w in cleaned for w in ("loai nao", "ten gi", "gia", "bao nhieu", "xem", "kiem tra")
    )
