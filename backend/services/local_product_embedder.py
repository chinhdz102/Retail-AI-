"""On-demand lightweight mobile product embedder (TFLite / ONNX parity).

Design guarantees:
- Strict lazy loading: ZERO model load, RAM, or CPU usage at server startup or idle.
  Weights and the 1.58 MB FP16 SKU gallery are loaded ONLY on the first user image
  upload request (POST /agent/respond/image).
- Aspect-preserving 2-view TTA (full letterbox + 84% center crop) matching the
  MobileNetV3Large-224 TFLite training pipeline, eliminating portrait/landscape distortion.
- Native TFLite runtime preferred (ai_edge_litert -> tflite_runtime -> tensorflow.lite),
  with automatic bit-aligned ONNX Runtime fallback for Python 3.13 Windows hosts.
"""

from __future__ import annotations

import io
import json
import logging
import os
import pathlib
import threading
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

import numpy as np
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

DEFAULT_MODEL_DIR = pathlib.Path("data/models/product-vision")
IMG_SIZE = 224


@dataclass(frozen=True)
class LocalVisualCandidate:
    product_id: str
    name: str
    brand: str
    category: str
    price_vnd: int
    similarity: float


@dataclass(frozen=True)
class LocalEmbedMatchResult:
    candidates: tuple[LocalVisualCandidate, ...] = field(default_factory=tuple)
    top1_similarity: float = 0.0
    top2_similarity: float = 0.0
    margin: float = 0.0
    is_high_confidence: bool = False
    is_hybrid_candidate: bool = False
    is_clear_ood: bool = False
    crops_run: int = 0
    backend_name: str = "unavailable"
    model_file: str = ""
    load_ms: float = 0.0
    preprocess_ms: float = 0.0
    embed_ms: float = 0.0
    match_ms: float = 0.0
    total_ms: float = 0.0


def _pad_to_square_pil(
    img: Image.Image, fill: tuple[int, int, int] = (128, 128, 128)
) -> Image.Image:
    w, h = img.size
    if w == h:
        return img
    side = max(w, h)
    canvas = Image.new("RGB", (side, side), fill)
    canvas.paste(img, ((side - w) // 2, (side - h) // 2))
    return canvas


def decode_fast_rgb_pil(raw_image_bytes: bytes, *, draft_size: int = 640) -> Image.Image:
    """Fast EXIF-safe RGB decode using libjpeg DCT draft scaling for large phone JPEGs."""
    with Image.open(io.BytesIO(raw_image_bytes)) as im:
        if getattr(im, "format", None) == "JPEG":
            try:
                im.draft("RGB", (draft_size, draft_size))
            except Exception:
                pass
        return ImageOps.exif_transpose(im).convert("RGB")


def make_full_view_rgb255(rgb: Image.Image, img_size: int = IMG_SIZE) -> np.ndarray:
    """Aspect-preserving full letterbox square -> float32 RGB [1, H, W, 3] in [0, 255]."""
    v_full = _pad_to_square_pil(rgb, fill=(128, 128, 128)).resize(
        (img_size, img_size), Image.Resampling.BILINEAR
    )
    return np.asarray(v_full, dtype=np.float32)[None, ...]


def make_center_view_rgb255(rgb: Image.Image, img_size: int = IMG_SIZE) -> np.ndarray:
    """Tight 84% center square crop -> float32 RGB [1, H, W, 3] in [0, 255]."""
    w, h = rgb.size
    side = max(1, int(min(w, h) * 0.84))
    x0 = max(0, (w - side) // 2)
    y0 = max(0, (h - side) // 2)
    v_center = rgb.crop((x0, y0, x0 + side, y0 + side)).resize(
        (img_size, img_size), Image.Resampling.BILINEAR
    )
    return np.asarray(v_center, dtype=np.float32)[None, ...]


def preprocess_2view_rgb255(
    raw_image_bytes: bytes, img_size: int = IMG_SIZE
) -> tuple[np.ndarray, np.ndarray]:
    """EXIF-safe 2-view preprocessing -> float32 RGB arrays in [0.0, 255.0]."""
    rgb = decode_fast_rgb_pil(raw_image_bytes, draft_size=max(448, img_size * 2))
    return make_full_view_rgb255(rgb, img_size), make_center_view_rgb255(rgb, img_size)


class LocalProductEmbedder:
    """Lazy-loaded mobile MobileNetV3Large-224 SKU embedder + vector gallery matcher."""

    def __init__(
        self,
        model_dir: pathlib.Path | str | None = None,
        *,
        fast_path_sim: float | None = None,
        fast_path_margin: float | None = None,
        hybrid_min_sim: float | None = None,
        num_threads: int = 4,
    ) -> None:
        env_dir = os.getenv("PRODUCT_VISION_MODEL_DIR", "").strip()
        if model_dir is not None:
            self.model_dir = pathlib.Path(model_dir)
        elif env_dir:
            self.model_dir = pathlib.Path(env_dir)
        else:
            self.model_dir = DEFAULT_MODEL_DIR

        self._override_fast_sim = fast_path_sim
        self._override_fast_margin = fast_path_margin
        self._override_hybrid_sim = hybrid_min_sim
        self._num_threads = max(1, int(num_threads))

        # Strictly lazy state — untouched until first image query!
        self._lock = threading.Lock()
        self._loaded = False
        self._available = False
        self._backend_name = "unavailable"
        self._model_file = ""
        self._runner: Any = None
        self._gallery_f32: np.ndarray | None = None
        self._skus: list[dict[str, Any]] = []
        self._img_size = IMG_SIZE
        self._fast_path_sim = 0.82
        self._fast_path_margin = 0.08
        self._hybrid_min_sim = 0.72
        self._clear_ood_max_sim = 0.43

    def has_artifacts(self) -> bool:
        """Fast filesystem existence check without loading weights."""
        meta = self.model_dir / "thanh_lam_gallery_meta.json"
        gal = self.model_dir / "thanh_lam_gallery_f16.npy"
        has_model = any(
            (self.model_dir / fname).exists()
            for fname in (
                "thanh_lam_mobilenetv3_fp16.tflite",
                "thanh_lam_mobilenetv3_int8.tflite",
                "thanh_lam_mobilenetv3_fp16.onnx",
            )
        )
        return meta.exists() and gal.exists() and has_model

    def _try_load_tflite(self, tflite_path: pathlib.Path) -> tuple[str, Any] | None:
        loaders = (
            ("tflite_litert", "ai_edge_litert.interpreter", "Interpreter"),
            ("tflite_runtime", "tflite_runtime.interpreter", "Interpreter"),
            ("tflite_tf", "tensorflow.lite", "Interpreter"),
        )
        for backend_tag, mod_name, attr in loaders:
            try:
                mod = __import__(mod_name, fromlist=[attr])
                interp_cls = getattr(mod, attr)
                interp = interp_cls(model_path=str(tflite_path), num_threads=self._num_threads)
                interp.allocate_tensors()
                in_idx = interp.get_input_details()[0]["index"]
                out_idx = interp.get_output_details()[0]["index"]
                return backend_tag, ("tflite", interp, in_idx, out_idx)
            except Exception:
                continue
        return None

    def _try_load_onnx(self, onnx_path: pathlib.Path) -> tuple[str, Any] | None:
        try:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.intra_op_num_threads = self._num_threads
            opts.inter_op_num_threads = 1
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            sess = ort.InferenceSession(
                str(onnx_path),
                sess_options=opts,
                providers=["CPUExecutionProvider"],
            )
            in_name = sess.get_inputs()[0].name
            return "onnx_parity", ("onnx", sess, in_name)
        except Exception as exc:
            logger.debug("ONNX local embedder load failed: %s", exc)
            return None

    def _ensure_loaded(self) -> float:
        """Lazy-load model + gallery once on first image request. Returns load_ms."""
        if self._loaded:
            return 0.0
        with self._lock:
            if self._loaded:
                return 0.0
            t0 = perf_counter()
            self._loaded = True
            if not self.has_artifacts():
                self._available = False
                return 0.0

            try:
                meta = json.loads(
                    (self.model_dir / "thanh_lam_gallery_meta.json").read_text(encoding="utf-8")
                )
                gal_f16 = np.load(self.model_dir / "thanh_lam_gallery_f16.npy")
                gal_f32 = np.ascontiguousarray(gal_f16.astype(np.float32))
                # Ensure exact unit L2 norm
                norms = np.linalg.norm(gal_f32, axis=1, keepdims=True)
                gal_f32 = gal_f32 / np.maximum(norms, 1e-8)

                # Prefer native TFLite first (mobile / Linux / Py3.11), then ONNX parity mirror
                loaded_pair: tuple[str, Any] | None = None
                chosen_file = ""
                for tf_name in (
                    "thanh_lam_mobilenetv3_fp16.tflite",
                    "thanh_lam_mobilenetv3_int8.tflite",
                ):
                    p = self.model_dir / tf_name
                    if p.exists():
                        loaded_pair = self._try_load_tflite(p)
                        if loaded_pair is not None:
                            chosen_file = tf_name
                            break

                if loaded_pair is None:
                    onnx_p = self.model_dir / "thanh_lam_mobilenetv3_fp16.onnx"
                    if onnx_p.exists():
                        loaded_pair = self._try_load_onnx(onnx_p)
                        if loaded_pair is not None:
                            chosen_file = onnx_p.name

                if loaded_pair is None:
                    logger.warning(
                        "Local product vision artifacts found in %s, but no compatible "
                        "TFLite or ONNX runtime is available.",
                        self.model_dir,
                    )
                    self._available = False
                    return round((perf_counter() - t0) * 1000.0, 2)

                self._backend_name, self._runner = loaded_pair
                self._model_file = chosen_file
                self._gallery_f32 = gal_f32
                self._skus = list(meta.get("skus", []))
                self._img_size = int(meta.get("input_size", IMG_SIZE))

                rec = meta.get("recommended_thresholds") or {}
                self._fast_path_sim = (
                    self._override_fast_sim
                    if self._override_fast_sim is not None
                    else float(rec.get("fast_path_sim", 0.72))
                )
                self._fast_path_margin = (
                    self._override_fast_margin
                    if self._override_fast_margin is not None
                    else float(rec.get("fast_path_margin", 0.06))
                )
                self._hybrid_min_sim = (
                    self._override_hybrid_sim
                    if self._override_hybrid_sim is not None
                    else float(rec.get("hybrid_vlm_min_sim", 0.50))
                )
                self._available = len(self._skus) == gal_f32.shape[0] and gal_f32.shape[0] > 0

                load_ms = round((perf_counter() - t0) * 1000.0, 2)
                logger.info(
                    "Lazy-loaded local product embedder backend=%s file=%s skus=%d load_ms=%.1f "
                    "thresholds=(fast=%.3f, margin=%.3f, hybrid=%.3f)",
                    self._backend_name,
                    self._model_file,
                    len(self._skus),
                    load_ms,
                    self._fast_path_sim,
                    self._fast_path_margin,
                    self._hybrid_min_sim,
                )
                return load_ms
            except Exception as exc:
                logger.warning("Failed to lazy-load local product embedder: %s", exc)
                self._available = False
                return round((perf_counter() - t0) * 1000.0, 2)

    def _embed_single(self, arr_f32: np.ndarray) -> np.ndarray:
        kind = self._runner[0]
        if kind == "tflite":
            _, interp, in_idx, out_idx = self._runner
            # TFLite interpreter invoke is not thread-safe on a single instance
            with self._lock:
                interp.set_tensor(in_idx, arr_f32)
                interp.invoke()
                vec = interp.get_tensor(out_idx)[0].copy()
        else:
            _, sess, in_name = self._runner
            vec = sess.run(None, {in_name: arr_f32})[0][0]
        vec = vec.astype(np.float32, copy=False)
        norm = float(np.linalg.norm(vec))
        return vec / max(norm, 1e-8)

    def _score_embedding(
        self, emb: np.ndarray, top_k: int
    ) -> tuple[list[LocalVisualCandidate], float, float, float]:
        assert self._gallery_f32 is not None
        sims = self._gallery_f32 @ emb
        k = min(max(2, top_k), sims.shape[0])
        top_idx = np.argpartition(-sims, k - 1)[:k]
        top_idx = top_idx[np.argsort(-sims[top_idx])]

        candidates: list[LocalVisualCandidate] = []
        for idx in top_idx:
            sku = self._skus[int(idx)]
            candidates.append(
                LocalVisualCandidate(
                    product_id=str(sku.get("product_id", "")),
                    name=str(sku.get("name", "")),
                    brand=str(sku.get("brand", "")),
                    category=str(sku.get("category", "")),
                    price_vnd=int(sku.get("price_vnd", 0) or 0),
                    similarity=round(float(sims[int(idx)]), 4),
                )
            )
        sim1 = candidates[0].similarity if candidates else 0.0
        sim2 = candidates[1].similarity if len(candidates) > 1 else 0.0
        margin = round(sim1 - sim2, 4)
        return candidates, sim1, sim2, margin

    def match_image_bytes(
        self,
        raw_image_bytes: bytes,
        *,
        top_k: int = 5,
        use_2crop_tta: bool = True,
        adaptive_1crop: bool = True,
        pil_image: Image.Image | None = None,
    ) -> LocalEmbedMatchResult:
        """Run on-demand local embedding + 3,093-SKU cosine retrieval.

        When ``adaptive_1crop=True`` (default), View 1 (full letterbox) runs first (~5 ms);
        if View 1 already achieves a strong high-confidence hit (sim >= fast_path_sim + 0.03
        and margin >= fast_path_margin + 0.02), View 2 is skipped to halve CPU time.
        """
        t_all = perf_counter()
        load_ms = self._ensure_loaded()
        if not self._available or self._gallery_f32 is None:
            return LocalEmbedMatchResult(
                backend_name=self._backend_name,
                load_ms=load_ms,
                total_ms=round((perf_counter() - t_all) * 1000.0, 2),
            )

        t_pre = perf_counter()
        rgb = (
            pil_image
            if pil_image is not None
            else decode_fast_rgb_pil(raw_image_bytes, draft_size=max(448, self._img_size * 2))
        )
        v_full = make_full_view_rgb255(rgb, img_size=self._img_size)
        preprocess_ms = (perf_counter() - t_pre) * 1000.0

        t_emb = perf_counter()
        e_full = self._embed_single(v_full)
        embed_ms = (perf_counter() - t_emb) * 1000.0

        t_match = perf_counter()
        candidates, sim1, sim2, margin = self._score_embedding(e_full, top_k)
        match_ms = (perf_counter() - t_match) * 1000.0
        crops_run = 1

        # Adaptive 1-crop short-circuit: skip 2nd crop if View 1 is already decisively confident!
        skip_second = (
            not use_2crop_tta
            or (
                adaptive_1crop
                and sim1 >= max(self._fast_path_sim + 0.03, 0.85)
                and margin >= max(self._fast_path_margin + 0.02, 0.10)
            )
        )

        if not skip_second:
            t_pre2 = perf_counter()
            v_center = make_center_view_rgb255(rgb, img_size=self._img_size)
            preprocess_ms += (perf_counter() - t_pre2) * 1000.0

            t_emb2 = perf_counter()
            e_center = self._embed_single(v_center)
            emb = 0.55 * e_full + 0.45 * e_center
            emb = emb / max(float(np.linalg.norm(emb)), 1e-8)
            embed_ms += (perf_counter() - t_emb2) * 1000.0

            t_match2 = perf_counter()
            candidates, sim1, sim2, margin = self._score_embedding(emb, top_k)
            match_ms += (perf_counter() - t_match2) * 1000.0
            crops_run = 2

        is_high = sim1 >= self._fast_path_sim and margin >= self._fast_path_margin
        is_hybrid = sim1 >= self._hybrid_min_sim
        is_clear_ood = crops_run >= 2 and sim1 < self._clear_ood_max_sim
        total_ms = (perf_counter() - t_all) * 1000.0

        return LocalEmbedMatchResult(
            candidates=tuple(candidates),
            top1_similarity=sim1,
            top2_similarity=sim2,
            margin=margin,
            is_high_confidence=is_high,
            is_hybrid_candidate=is_hybrid,
            is_clear_ood=is_clear_ood,
            crops_run=crops_run,
            backend_name=self._backend_name,
            model_file=self._model_file,
            load_ms=round(load_ms, 2),
            preprocess_ms=round(preprocess_ms, 2),
            embed_ms=round(embed_ms, 2),
            match_ms=round(match_ms, 3),
            total_ms=round(total_ms, 2),
        )
