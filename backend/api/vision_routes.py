"""Multipart image-recognition chat endpoint (POST /agent/respond/image).

Allows desktop and mobile kiosk shoppers to snap a live camera photo or upload
a product image, recognize the exact Thanh Lam Mart SKU via ProductVisionService,
update session grounding memory, and answer follow-up questions in one turn.
"""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile

from backend.services.product_vision import (
    ALLOWED_IMAGE_CONTENT_TYPES,
    ALLOWED_VIDEO_CONTENT_TYPES,
    MAX_UPLOAD_IMAGE_BYTES,
    MAX_UPLOAD_VIDEO_BYTES,
    VIDEO_FILENAME_EXTENSIONS,
    ProductVisionService,
    extract_sharpest_frame_from_video_bytes,
    format_vision_reply,
    is_default_identify_question,
)
from backend.shared.contracts import IntentType, SessionState

logger = logging.getLogger(__name__)

router = APIRouter(tags=["vision"])

_vision_service: ProductVisionService | None = None


def get_product_vision_service(settings=None) -> ProductVisionService:
    global _vision_service
    if _vision_service is None:
        _vision_service = ProductVisionService(settings)
    elif settings is not None and _vision_service.settings is not settings:
        _vision_service.settings = settings
    return _vision_service


@router.post("/agent/respond/image")
async def respond_to_product_image(
    response: Response,
    session_id: str = Form(...),
    file: UploadFile = File(...),
    message: str = Form(""),
    language: Literal["vi", "en", "zh"] = Form("vi"),
):
    """Identify a photographed retail product and answer the shopper's question."""
    import backend.api.main as api_main

    started = perf_counter()
    try:
        parsed_session_id = UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    api_main._session_or_http_error(parsed_session_id)

    content_type = (file.content_type or "").split(";")[0].strip().lower()
    filename_lower = (file.filename or "").strip().lower()
    is_video = (
        content_type in ALLOWED_VIDEO_CONTENT_TYPES
        or content_type.startswith("video/")
        or filename_lower.endswith(VIDEO_FILENAME_EXTENSIONS)
    )
    if (
        not is_video
        and content_type
        and content_type not in ALLOWED_IMAGE_CONTENT_TYPES
    ):
        raise HTTPException(
            status_code=415,
            detail="Chỉ hỗ trợ ảnh (JPG, PNG, WebP) hoặc video ngắn (MP4, MOV, WebM).",
        )

    max_bytes = MAX_UPLOAD_VIDEO_BYTES if is_video else MAX_UPLOAD_IMAGE_BYTES
    raw_bytes = await file.read(max_bytes + 1)
    if not raw_bytes:
        raise HTTPException(
            status_code=422,
            detail="Video tải lên bị trống." if is_video else "Ảnh tải lên bị trống.",
        )
    if len(raw_bytes) > max_bytes:
        limit_mb = max_bytes // (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=(
                f"Tệp vượt quá giới hạn {limit_mb} MB. "
                "Bạn chụp lại hoặc chọn ảnh/video ngắn hơn nhé."
            ),
        )

    video_meta: dict | None = None
    if is_video:
        try:
            raw_bytes, video_meta = await asyncio.to_thread(
                extract_sharpest_frame_from_video_bytes,
                raw_bytes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    settings = api_main.get_settings()
    repository = api_main.get_product_repository()
    vision_svc = get_product_vision_service(settings)

    try:
        recognition = await vision_svc.recognize(raw_bytes, repository, limit=4)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Product vision recognition failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Chưa kết nối được dịch vụ nhận diện hình ảnh. Bạn thử lại sau giây lát nhé.",
        ) from exc

    user_q = (message or "").strip()
    display_user_msg = user_q or (
        "What product is in this photo?"
        if language == "en"
        else "Đây là sản phẩm gì?"
    )

    matched = list(recognition.matched_products)
    related = list(recognition.related_products)
    grounded_ids = [p.product_id for p in matched]
    all_shown_ids = [p.product_id for p in (*matched, *related)]

    # Build grounded answer. If the user asked a specific custom follow-up question
    # about a matched SKU, enrich via grounded chat LLM while preserving exact catalog facts.
    reply_text = format_vision_reply(
        recognition,
        user_question=user_q,
        language=language,
    )
    used_model = recognition.extraction.model or "vision"
    tools_used = ["product_vision_recognize"]
    lm = recognition.local_match
    if lm is not None and lm.backend_name != "unavailable":
        tools_used.insert(0, f"local_mobile_vision:{lm.backend_name}")
    if matched:
        tools_used.append("search_products")

    if matched and user_q and not is_default_identify_question(user_q):
        try:
            groq_svc = api_main.get_groq_service(settings)
            catalog_ctx = "\n".join(
                f"- {p.name} | brand={p.brand} | price={p.price_vnd}đ"
                + (f" | list_price={p.list_price_vnd}đ" if p.list_price_vnd else "")
                + (f" | promo={p.promotion}" if p.promotion else "")
                + (f" | category={p.category}" if p.category else "")
                for p in (*matched, *related)
            )
            custom_prompt = (
                "Bạn là Mai, trợ lý siêu thị Thanh Lam Mart. "
                "Khách vừa chụp ảnh sản phẩm và hỏi thêm.\n"
                "Sản phẩm nhận diện từ ảnh (đã xác minh trong danh mục Thanh Lam Mart):\n"
                f"{catalog_ctx}\n\n"
                f"Câu hỏi của khách: {user_q}\n\n"
                "Trả lời ngắn gọn (2-4 câu tự nhiên, thân thiện bằng tiếng Việt), "
                "chỉ dùng giá và thông tin đã xác minh ở trên, "
                "tuyệt đối không bịa tồn kho thực tế hay tư vấn y tế/kê đơn thuốc."
            )
            chat_res = await groq_svc.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "Bạn là Mai, trợ lý siêu thị Thanh Lam Mart. "
                            "Trả lời ngắn gọn (2-4 câu tiếng Việt tự nhiên), "
                            "chỉ dùng giá và thông tin đã xác minh, "
                            "không bịa tồn kho thật hay tư vấn y tế/thuốc."
                        ),
                    },
                    {"role": "user", "content": custom_prompt},
                ],
                max_tokens=220,
                reasoning_effort="none",
            )
            answer = str(chat_res or "").strip()
            if answer:
                reply_text = answer
                used_model = f"{used_model}+{chat_res.model}"
        except Exception as exc:
            logger.info("Custom image follow-up LLM fallback to deterministic reply: %s", exc)

    # Persist recognized product into session memory so subsequent voice/text turns
    # ("cái này có loại lớn hơn không?", "thêm vào giỏ đi") seamlessly resolve!
    current_rec = api_main.session_store.get(parsed_session_id)
    if current_rec.state == SessionState.NEW:
        api_main.session_store.transition(parsed_session_id, SessionState.BROWSING)
    api_main.session_store.add_message(
        parsed_session_id, "user", f"[Ảnh sản phẩm] {display_user_msg}"
    )
    api_main.session_store.add_message(parsed_session_id, "assistant", reply_text)
    if matched:
        top = matched[0]
        api_main.session_store.set_last_grounded_product(
            parsed_session_id, top.product_id, top.name
        )
        seen_ids = list(current_rec.shopping.seen_product_ids)
        for pid in all_shown_ids:
            if pid not in seen_ids:
                seen_ids.append(pid)
        api_main.session_store.update_shopping(
            parsed_session_id,
            current_intent=IntentType.PRODUCT_LOOKUP,
            active_product_id=top.product_id,
            active_category=top.category,
            selected_product_ids=list(all_shown_ids),
            seen_product_ids=seen_ids,
        )
        refreshed_token = api_main._sign_session_token(
            parsed_session_id,
            current_rec.kiosk_id,
            current_rec.expires_at,
            settings,
            last_product_id=top.product_id,
        )
        if refreshed_token:
            response.headers["X-Kiosk-Session-Token"] = refreshed_token

    elapsed_ms = round((perf_counter() - started) * 1000.0, 1)
    lat = recognition.latency_ms or {}
    logger.info(
        "Image recognition completed media=%s method=%s score=%.1f matched=%d total_ms=%.1f "
        "(video_extract_ms=%.1f local_embed_ms=%.1f local_match_ms=%.2f vlm_ms=%.1f) model=%s",
        "video" if is_video else "image",
        recognition.match_method,
        recognition.top_score,
        len(matched),
        elapsed_ms,
        float((video_meta or {}).get("extract_ms", 0.0)),
        float(lat.get("local_embed_ms", 0.0)),
        float(lat.get("local_match_ms", 0.0)),
        float(lat.get("vlm_ms", 0.0)),
        used_model,
    )

    return api_main.AgentRespondResponse(
        session_id=parsed_session_id,
        state=SessionState.BROWSING,
        intent=IntentType.PRODUCT_LOOKUP,
        text=reply_text,
        products=[api_main._product_card(p) for p in matched],
        related_products=[api_main._product_card(p) for p in related],
        grounded_product_ids=grounded_ids,
        tools_used=tools_used,
        model=used_model,
    )
