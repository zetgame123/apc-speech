"""Local ONNX text embedding engine (intfloat/multilingual-e5-small INT8, 384 dims)
for semantic and hybrid call search.

Runs strictly offline via onnxruntime + tokenizers without PyTorch/transformers.
Builds call embedding text exclusively from masked data (ADR-004).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Sequence
from typing import Any

import numpy as np

from app.core.config import get_settings
from app.core.logging import get_logger
from app.pii.masker import get_pii_masker

logger = get_logger("embeddings")

EMBEDDING_DIM = 384
MAX_SEQ_LEN = 512
MAX_EMBEDDING_TEXT_CHARS = 2400

_INQUIRY_LABELS_RU: dict[str, str] = {
    "pricing": "стоимость и прайс",
    "availability": "занятость и свободные периоды",
    "inventory_search": "подбор мест и инвентаря",
    "specs": "технические требования",
    "order_status": "статус заказа и брони",
    "production": "производство и монтаж",
    "documents_billing": "документы и бухгалтерия",
    "complaint": "претензии и проблемы",
    "partnership_supply": "сотрудничество и инвентарь партнёров",
    "programmatic_digital": "диджитал и программатик",
    "spam_irrelevant": "спам и нецелевой звонок",
    "other": "прочее",
}


def _get_attr(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def build_call_embedding_text(analysis: Any | None, segments: Sequence[Any]) -> str:
    """Assembles semantic search text for a call strictly from masked data.

    Never reads `text_original` from transcript segments and applies PII masking
    as defense-in-depth to guarantee ADR-004 compliance.
    """
    parts: list[str] = []

    if analysis is not None:
        inquiry_type = _get_attr(analysis, "inquiry_type")
        if inquiry_type:
            label = _INQUIRY_LABELS_RU.get(str(inquiry_type), str(inquiry_type))
            parts.append(f"Тип обращения: {label}.")

        summary = _get_attr(analysis, "summary")
        if summary and str(summary).strip():
            parts.append(f"Краткое содержание: {str(summary).strip()}")

        entities = _get_attr(analysis, "entities")
        if isinstance(entities, dict):
            ent_parts: list[str] = []
            cities = entities.get("cities")
            if isinstance(cities, list) and cities:
                ent_parts.append("города: " + ", ".join(str(c) for c in cities if c))
            formats = entities.get("formats")
            if isinstance(formats, list) and formats:
                ent_parts.append("форматы: " + ", ".join(str(f) for f in formats if f))
            product_category = entities.get("product_category")
            if product_category and str(product_category) != "other":
                ent_parts.append(f"категория: {product_category}")
            periods = entities.get("periods") or (
                [entities["target_period"]] if entities.get("target_period") else []
            )
            if isinstance(periods, list) and periods:
                ent_parts.append("период: " + ", ".join(str(p) for p in periods if p))
            budget = entities.get("budget_rub")
            if budget is not None:
                ent_parts.append(f"бюджет: {budget} руб.")
            quantity = entities.get("quantity")
            if quantity is not None:
                ent_parts.append(f"количество: {quantity}")
            if ent_parts:
                parts.append("Сущности: " + "; ".join(ent_parts) + ".")

        agreements = _get_attr(analysis, "agreements")
        if isinstance(agreements, list) and agreements:
            agr_items: list[str] = []
            for item in agreements:
                if isinstance(item, dict):
                    action = item.get("action") or item.get("text") or item.get("description")
                    owner = item.get("owner")
                    deadline = item.get("deadline")
                    pieces = [str(p).strip() for p in (owner, action, deadline) if p]
                    if pieces:
                        agr_items.append(" — ".join(pieces))
                elif isinstance(item, str) and item.strip():
                    agr_items.append(item.strip())
            if agr_items:
                parts.append("Договорённости: " + "; ".join(agr_items) + ".")

    seg_texts: list[str] = []
    for seg in segments or []:
        # Strictly read `text_masked` only — never `text_original`.
        masked = _get_attr(seg, "text_masked")
        if masked and str(masked).strip():
            role = _get_attr(seg, "speaker_role")
            if role:
                seg_texts.append(f"[{role}] {str(masked).strip()}")
            else:
                seg_texts.append(str(masked).strip())

    if seg_texts:
        parts.append("Диалог: " + " ".join(seg_texts))

    combined = "\n".join(parts).strip()
    if not combined:
        return ""

    if len(combined) > MAX_EMBEDDING_TEXT_CHARS:
        combined = combined[:MAX_EMBEDDING_TEXT_CHARS]

    # Defense-in-depth: ensure no PII leaked into summary/agreements/entities
    return get_pii_masker().mask_text(combined)


def cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """Computes cosine similarity between two numeric vectors (SQLite fallback)."""
    a = np.asarray(vec_a, dtype=np.float32)
    b = np.asarray(vec_b, dtype=np.float32)
    if a.shape != b.shape or a.size == 0:
        return 0.0
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= 1e-12:
        return 0.0
    return float(np.dot(a, b) / denom)


class TextEmbeddingEngine:
    """Lazy-loaded singleton wrapper for multilingual-e5-small INT8 ONNX."""

    def __init__(
        self,
        model_dir: str | None = None,
        model_name: str | None = None,
        threads: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        settings = get_settings()
        self.enabled = settings.embedding_enabled if enabled is None else enabled
        self.model_name = model_name or settings.embedding_model
        self.model_dir = model_dir or settings.embedding_model_dir
        self.threads = max(1, threads if threads is not None else settings.embedding_threads)

        self._lock = threading.Lock()
        self._session: Any = None
        self._tokenizer: Any = None
        self._input_names: set[str] = set()

    @property
    def model_path(self) -> str:
        return os.path.join(self.model_dir, "model.onnx")

    @property
    def tokenizer_path(self) -> str:
        return os.path.join(self.model_dir, "tokenizer.json")

    def is_available(self) -> bool:
        return (
            bool(self.enabled)
            and os.path.isfile(self.model_path)
            and os.path.isfile(self.tokenizer_path)
        )

    def _ensure_loaded(self) -> None:
        if self._session is not None and self._tokenizer is not None:
            return
        with self._lock:
            if self._session is not None and self._tokenizer is not None:
                return
            if not self.enabled:
                raise RuntimeError("Embedding engine is disabled via EMBEDDING_ENABLED=false")
            if not os.path.isfile(self.model_path) or not os.path.isfile(self.tokenizer_path):
                raise FileNotFoundError(
                    f"Embedding model files not found in {self.model_dir} "
                    "(expected model.onnx and tokenizer.json)"
                )

            import onnxruntime as ort
            from tokenizers import Tokenizer

            tokenizer = Tokenizer.from_file(self.tokenizer_path)
            tokenizer.enable_truncation(max_length=MAX_SEQ_LEN)

            sess_options = ort.SessionOptions()
            sess_options.intra_op_num_threads = self.threads
            sess_options.inter_op_num_threads = 1
            sess_options.enable_cpu_mem_arena = False
            sess_options.enable_mem_pattern = False
            sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

            session = ort.InferenceSession(
                self.model_path,
                sess_options=sess_options,
                providers=["CPUExecutionProvider"],
            )
            self._tokenizer = tokenizer
            self._session = session
            self._input_names = {inp.name for inp in session.get_inputs()}
            logger.info(
                "embedding_engine_loaded",
                model_name=self.model_name,
                threads=self.threads,
            )

    def _encode(self, prefixed_text: str) -> list[float]:
        self._ensure_loaded()
        encoded = self._tokenizer.encode(prefixed_text)
        input_ids = np.asarray([encoded.ids], dtype=np.int64)
        attention_mask = np.asarray([encoded.attention_mask], dtype=np.int64)

        ort_inputs: dict[str, np.ndarray] = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        if "token_type_ids" in self._input_names:
            ort_inputs["token_type_ids"] = np.zeros_like(input_ids, dtype=np.int64)

        outputs = self._session.run(None, ort_inputs)
        last_hidden_state = np.asarray(outputs[0], dtype=np.float32)

        # Mean pooling over non-padding tokens
        mask = attention_mask[..., None].astype(np.float32)
        summed = (last_hidden_state * mask).sum(axis=1)
        counts = np.clip(mask.sum(axis=1), a_min=1e-9, a_max=None)
        pooled = summed / counts

        # L2 normalization for cosine similarity
        norm = np.linalg.norm(pooled, axis=1, keepdims=True)
        normalized = pooled / np.clip(norm, a_min=1e-12, a_max=None)
        return normalized[0].astype(np.float32).tolist()

    def embed_passage(self, text: str) -> list[float]:
        cleaned = (text or "").strip()
        if not cleaned:
            return [0.0] * EMBEDDING_DIM
        return self._encode(f"passage: {cleaned}")

    def embed_query(self, text: str) -> list[float]:
        cleaned = (text or "").strip()
        if not cleaned:
            return [0.0] * EMBEDDING_DIM
        return self._encode(f"query: {cleaned}")

    def embed_passage_safe(self, text: str) -> list[float] | None:
        if not self.is_available():
            return None
        try:
            return self.embed_passage(text)
        except Exception as exc:
            logger.warning("embedding_passage_failed", error_type=type(exc).__name__)
            return None

    def embed_query_safe(self, text: str) -> list[float] | None:
        if not self.is_available():
            return None
        try:
            return self.embed_query(text)
        except Exception as exc:
            logger.warning("embedding_query_failed", error_type=type(exc).__name__)
            return None


_engine_lock = threading.Lock()
_engine_instance: TextEmbeddingEngine | None = None


def get_embedding_engine() -> TextEmbeddingEngine:
    global _engine_instance
    if _engine_instance is None:
        with _engine_lock:
            if _engine_instance is None:
                _engine_instance = TextEmbeddingEngine()
    return _engine_instance


def reset_embedding_engine() -> None:
    global _engine_instance
    with _engine_lock:
        _engine_instance = None
