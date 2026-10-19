from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from backend.services.vietnamese_intent import normalize_vietnamese
from backend.shared.contracts import IssueType


@dataclass(frozen=True)
class SupportIntent:
    issue_type: IssueType
    description: str


@dataclass(frozen=True)
class SupportTicket:
    ticket_id: UUID
    session_id: UUID
    kiosk_id: str
    issue_type: IssueType
    description: str
    status: str
    created_at: datetime
    demo: bool = True


class SupportTicketNotFoundError(LookupError):
    """Raised when a ticket is not owned by the requested anonymous session."""


class SupportIdempotencyConflictError(ValueError):
    """Raised when a retry key is reused for a different support request."""


def _safe_description(text: str) -> str:
    value = " ".join(text.split())[:500]
    value = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[email ẩn]", value)
    value = re.sub(r"(?<!\w)(?:\+?\d[\d .()-]{7,}\d)(?!\w)", "[số liên hệ ẩn]", value)
    value = re.sub(r"(?<!\d)(?:\d[ -]?){12,19}(?!\d)", "[dãy số ẩn]", value)
    return value[:500]


def classify_support_intent(text: str) -> SupportIntent | None:
    """Recognize a clear handoff or report request; policy questions remain questions."""
    raw = text.casefold()
    normalized = normalize_vietnamese(text)
    denial_or_injection = (
        "gia vo",
        "pretend",
        "ignore previous",
        "bo qua chi dan",
        "bo qua xac minh",
        "bo qua xac thuc",
        "skip verification",
        "bypass verification",
        "khong tao ticket",
        "khong ghi nhan",
        "do not create a ticket",
        "do not log",
    )
    if any(phrase in normalized for phrase in denial_or_injection):
        return None

    handoff = any(
        phrase in normalized
        for phrase in (
            "gap nhan vien",
            "goi nhan vien",
            "bao nhan vien",
            "noi chuyen voi quan ly",
            "gap quan ly",
            "speak to a manager",
            "talk to a manager",
            "call staff",
            "call a staff member",
            "talk to an employee",
            "speak to a human",
            "get me a manager",
            "我想联系店员",
            "请找店员",
            "我需要店员帮助",
        )
    )

    issue_type = IssueType.OTHER
    has_issue = False
    if any(token in normalized for token in ("het han", "qua han", "expired", "out of date")):
        issue_type = IssueType.EXPIRED_PRODUCT
        has_issue = True
    elif any(
        token in normalized
        for token in ("bi hong", "bi hu", "hang loi", "damaged", "broken", "defective")
    ) or re.search(r"\b(?:bị hỏng|bị hu|bi hong|bi hu)\b", raw):
        issue_type = IssueType.DAMAGED_PACKAGING
        has_issue = True
    elif any(
        token in normalized
        for token in ("sai gia", "gia tren ke", "wrong shelf price", "price tag")
    ):
        issue_type = IssueType.WRONG_SHELF
        has_issue = True
    elif any(
        token in normalized
        for token in (
            "thong tin sai",
            "tu van sai",
            "wrong information",
            "misleading information",
        )
    ):
        issue_type = IssueType.WRONG_INFORMATION
        has_issue = True
    elif any(
        token in normalized
        for token in (
            "thanh toan",
            "giao dich",
            "charged twice",
            "charged me twice",
            "tinh tien hai lan",
            "tru tien hai lan",
        )
    ):
        issue_type = IssueType.PAYMENT_PROBLEM
        has_issue = True
    elif any(
        token in normalized
        for token in ("khieu nai", "phan nan", "phan anh", "complaint", "complain", "feedback")
    ):
        issue_type = IssueType.OTHER
        has_issue = True

    reporting = any(
        phrase in normalized
        for phrase in (
            "bao giup",
            "ghi nhan",
            "toi muon khieu nai",
            "i want to report",
            "file a complaint",
            "report this",
            "please report",
            "please help",
        )
    )
    if not handoff and not has_issue:
        return None
    # A generic policy question such as “What is the return policy?” is not a ticket.
    if has_issue and not handoff and not reporting:
        descriptive_issue = any(
            phrase in normalized
            for phrase in (
                "mua phai",
                "toi mua",
                "san pham bi",
                "hang bi",
                "bi tinh tien",
                "bi tru tien",
                "i bought",
                "the item is",
                "i was charged",
                "giao dich loi",
                "thanh toan bi loi",
                "payment failed",
                "payment did not go through",
                "charged me twice",
            )
        )
        if not descriptive_issue:
            return None
    if handoff and not has_issue:
        issue_type = IssueType.OTHER
    return SupportIntent(issue_type=issue_type, description=_safe_description(text))


class SupportTicketStore:
    """Single-process staff simulator for demos; it does not notify a real employee."""

    def __init__(self) -> None:
        self._tickets: dict[UUID, SupportTicket] = {}
        self._idempotency: dict[tuple[UUID, str], tuple[str, UUID]] = {}
        self._lock = threading.RLock()

    def create(
        self,
        *,
        session_id: UUID,
        kiosk_id: str,
        issue_type: IssueType,
        description: str,
        idempotency_key: str,
    ) -> SupportTicket:
        safe_description = _safe_description(description)
        digest = hashlib.sha256(f"{issue_type.value}|{safe_description}".encode()).hexdigest()
        key = (session_id, idempotency_key)
        with self._lock:
            cached = self._idempotency.get(key)
            if cached:
                if cached[0] != digest:
                    raise SupportIdempotencyConflictError(
                        "Idempotency key reused for a different support request"
                    )
                return self._tickets[cached[1]]
            ticket = SupportTicket(
                ticket_id=uuid4(),
                session_id=session_id,
                kiosk_id=kiosk_id,
                issue_type=issue_type,
                description=safe_description,
                status="RECEIVED_DEMO",
                created_at=datetime.now(UTC),
            )
            self._tickets[ticket.ticket_id] = ticket
            self._idempotency[key] = (digest, ticket.ticket_id)
            return ticket

    def get(self, session_id: UUID, ticket_id: UUID) -> SupportTicket:
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None or ticket.session_id != session_id:
                raise SupportTicketNotFoundError("Support ticket not found")
            return ticket
