from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

import numpy as np

from backend.products.planogram import friendly_location


class VisionModelUnavailable(RuntimeError):
    """Raised when the Kaggle-exported TFLite bundle is not installed."""


class VisionInputError(ValueError):
    """Raised when an uploaded image cannot be decoded."""


@dataclass(frozen=True)
class VisionMatch:
    product_id: str
    name: str
    category: str | None
    brand: str | None
    score: float
    shelf_code: str | None
    shelf_friendly: str | None


class TFLiteVisionRetriever:
    """On-device-style gallery retrieval backed by a Kaggle TFLite embedder."""

    def __init__(self, model_dir: str | Path) -> None:
        self.model_dir = Path(model_dir)
        self.model_path = self.model_dir / "sku_embedder.tflite"
        self.gallery_path = self.model_dir / "gallery_embeddings.npy"
        self.metadata_path = self.model_dir / "gallery_metadata.json"
        self._lock = Lock()
        self._interpreter: Any | None = None
        self._input_details: list[dict[str, Any]] = []
        self._output_details: list[dict[str, Any]] = []
        self._gallery: np.ndarray | None = None
        self._metadata: list[dict[str, Any]] | None = None

    @property
    def available(self) -> bool:
        return (
            self.model_path.is_file()
            and self.gallery_path.is_file()
            and self.metadata_path.is_file()
        )

    def _load(self) -> None:
        if not self.available:
            raise VisionModelUnavailable(
                "TFLite model bundle is not installed. "
                "Export Kaggle artifacts to backend/assets/vision."
            )
        if self._interpreter is not None:
            return
        try:
            from tflite_runtime.interpreter import Interpreter
        except ImportError:
            try:
                from tensorflow.lite import Interpreter
            except ImportError as exc:
                raise VisionModelUnavailable(
                    "Install tflite-runtime or TensorFlow to run the vision retriever."
                ) from exc

        with self._lock:
            if self._interpreter is not None:
                return
            gallery = np.asarray(np.load(self.gallery_path), dtype=np.float32)
            if gallery.ndim != 2 or gallery.shape[0] == 0:
                raise VisionModelUnavailable("Gallery embeddings are empty or malformed.")
            try:
                metadata = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise VisionModelUnavailable("Gallery metadata cannot be read.") from exc
            if not isinstance(metadata, list) or len(metadata) != gallery.shape[0]:
                raise VisionModelUnavailable(
                    "Gallery metadata and embeddings have different lengths."
                )
            interpreter = Interpreter(model_path=str(self.model_path), num_threads=2)
            interpreter.allocate_tensors()
            self._interpreter = interpreter
            self._input_details = interpreter.get_input_details()
            self._output_details = interpreter.get_output_details()
            self._gallery = gallery / np.maximum(
                np.linalg.norm(gallery, axis=1, keepdims=True), 1e-8
            )
            self._metadata = [item for item in metadata if isinstance(item, dict)]

    @staticmethod
    def _decode_image(image_bytes: bytes, input_shape: tuple[int, int]) -> np.ndarray:
        try:
            from io import BytesIO

            from PIL import Image, UnidentifiedImageError

            with Image.open(BytesIO(image_bytes)) as image:
                image = image.convert("RGB").resize(input_shape)
                pixels = np.asarray(image, dtype=np.float32)
        except (ImportError, OSError, UnidentifiedImageError, ValueError) as exc:
            raise VisionInputError("Uploaded file is not a readable image.") from exc
        return pixels[None, ...] / 127.5 - 1.0

    def identify(
        self,
        image_bytes: bytes,
        top_k: int = 5,
    ) -> tuple[list[VisionMatch], bool, list[int], int]:
        self._load()
        assert self._interpreter is not None
        assert self._gallery is not None
        assert self._metadata is not None
        if not image_bytes:
            raise VisionInputError("Uploaded image is empty.")
        top_k = max(1, min(int(top_k), 10))
        input_shape = tuple(int(value) for value in self._input_details[0]["shape"][1:3])
        query = self._decode_image(image_bytes, (input_shape[1], input_shape[0]))
        input_detail = self._input_details[0]
        if input_detail["dtype"] != np.float32:
            scale, zero_point = input_detail["quantization"]
            if not scale:
                raise VisionModelUnavailable("TFLite input quantization is invalid.")
            query = np.round(query / scale + zero_point).astype(input_detail["dtype"])
        self._interpreter.set_tensor(input_detail["index"], query)
        self._interpreter.invoke()
        embedding = np.asarray(
            self._interpreter.get_tensor(self._output_details[0]["index"]),
            dtype=np.float32,
        )
        embedding = embedding.reshape(1, -1)
        embedding /= np.maximum(np.linalg.norm(embedding, axis=1, keepdims=True), 1e-8)
        scores = (embedding @ self._gallery.T)[0]
        ranking = np.argsort(-scores)[:top_k]
        matches: list[VisionMatch] = []
        for index in ranking:
            row = self._metadata[int(index)]
            shelf_code = _optional_text(row.get("shelf_code"))
            shelf_friendly = _optional_text(row.get("shelf_friendly")) or (
                friendly_location(shelf_code) if shelf_code else None
            )
            matches.append(
                VisionMatch(
                    product_id=str(row.get("product_id", "")),
                    name=str(row.get("name", "")),
                    category=_optional_text(row.get("category")),
                    brand=_optional_text(row.get("brand")),
                    score=float(scores[int(index)]),
                    shelf_code=shelf_code,
                    shelf_friendly=shelf_friendly,
                )
            )
        confident = bool(matches and matches[0].score >= 0.35)
        if len(matches) > 1 and matches[0].score - matches[1].score < 0.03:
            confident = False
        return matches, confident, [input_shape[1], input_shape[0]], int(self._gallery.shape[0])


def _optional_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
