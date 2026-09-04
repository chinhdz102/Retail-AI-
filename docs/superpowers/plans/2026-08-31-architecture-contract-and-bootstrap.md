# Architecture Contract and Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chuyển requirements v1 thành architecture/tool/database contracts và tạo backend skeleton chạy được với dependencies nền cho các bước tiếp theo.

**Architecture:** FastAPI là API boundary stateless. Shared Pydantic contracts chứa enum/schema dùng chung; PostgreSQL là business truth, Redis là ephemeral session store, còn Qwen và RAG sẽ được nối qua các interface riêng ở các milestone sau. Docker Compose mô tả PostgreSQL có pgvector và Redis, nhưng việc chạy container phụ thuộc Docker runtime của máy.

**Tech Stack:** Python 3.13, uv, FastAPI, Uvicorn, Pydantic v2, SQLAlchemy async, asyncpg, Alembic, pgvector, redis-py, OpenAI-compatible Qwen adapter, pytest, httpx, Ruff, PostgreSQL, Redis.

## Global Constraints

- Một kiosk demo; khách anonymous-by-default; mỗi shopping episode là một session độc lập có TTL.
- Catalog demo 100–200 SKU; giao tiếp chính bằng tiếng Việt; payment chỉ dùng VietQR sandbox/test.
- LLM chỉ điều phối/ngôn ngữ; PostgreSQL, Redis/session service, cart/order backend và payment provider giữ nguồn sự thật tương ứng.
- Customer agent không có admin tools, raw SQL, shell access hoặc quyền tự sửa giá/tồn kho/hạn dùng/`PAID`.
- Tool call phải dùng static registry, schema validation và policy/confirmation gateway ở các bước tiếp theo.
- Không commit secret; `.env.example` chỉ chứa placeholder không nhạy cảm.
- Docker/Compose có thể chưa cài trên máy; phải kiểm tra và báo rõ thay vì bỏ qua verification.

---

### Task 1: Architecture contracts

**Files:**
- Create: `docs/architecture.md`
- Create: `docs/tool_contracts.md`
- Create: `docs/database_erd.md`

**Interfaces:**
- Consumes: `docs/requirements.md` và kiến trúc V4.
- Produces: quyết định về layer/data flow, state/tool enums, 11 tool schemas, API mapping và ERD để các task code dùng đúng tên.

- [ ] **Step 1: Viết architecture contract**

Ghi rõ flow `customer → kiosk → API → session → router/context → Qwen → policy → tools/RAG → validator → response`, trust boundaries, state ownership, dynamic tool exposure và các endpoint tối thiểu. Mỗi nguồn sự thật phải được gắn với một service cụ thể.

- [ ] **Step 2: Viết tool registry contract**

Định nghĩa chính xác 11 tool: `resolve_product`, `search_products`, `get_product_snapshot`, `get_product_knowledge`, `get_expiry_info`, `get_cart`, `update_cart`, `create_checkout`, `check_payment_status`, `handoff_to_staff`, `report_product_issue`. Mỗi tool có input, output, permission, state được phép và confirmation requirement; liệt kê rõ tool cấm expose cho Customer Agent.

- [ ] **Step 3: Viết database ERD**

Dùng Mermaid ER diagram cho các bảng products/catalog, inventory/batches/shelves/promotions, carts/orders/payments, knowledge, issues/handoffs và audit/evaluation. Ghi invariant FEFO, session ownership, money representation và payment idempotency.

### Task 2: Runtime and dependency bootstrap

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `docker-compose.yml`
- Create: `README.md`

**Interfaces:**
- Consumes: technology stack và endpoint names trong `docs/architecture.md`.
- Produces: lệnh `uv sync --all-groups`, `uv run pytest`, `uv run ruff check .`, cấu hình local Redis/PostgreSQL và biến môi trường đã đặt tên.

- [ ] **Step 1: Tạo dependency manifest**

`pyproject.toml` phải khai báo runtime: `fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `pgvector`, `redis[hiredis]`, `openai`; dev: `pytest`, `pytest-asyncio`, `httpx`, `ruff`.

- [ ] **Step 2: Tạo cấu hình môi trường an toàn**

`.env.example` khai báo `APP_ENV`, `APP_VERSION`, `DATABASE_URL`, `REDIS_URL`, `SESSION_TTL_SECONDS`, `QWEN_BASE_URL`, `QWEN_API_KEY`, `QWEN_MODEL`; `.gitignore` loại `.venv`, `.env`, cache và build artifacts.

- [ ] **Step 3: Tạo Compose và hướng dẫn**

Compose dùng PostgreSQL image có pgvector, Redis 7, healthcheck và volume named. README hướng dẫn Windows dùng `uv`, ghi rõ Docker không bắt buộc cho unit tests nhưng bắt buộc cho integration với DB/Redis.

### Task 3: Shared contracts (TDD)

**Files:**
- Create: `tests/test_contracts.py`
- Create: `backend/__init__.py`
- Create: `backend/shared/__init__.py`
- Create: `backend/shared/contracts.py`

**Interfaces:**
- Consumes: enum names và tool names trong `docs/tool_contracts.md`.
- Produces: `SessionState`, `IntentType`, `ToolName`, `ToolPermission`, `PaymentStatus`, `OrderStatus`, `IssueType`, `ToolCallRequest`, `HealthResponse`.

- [ ] **Step 1: Viết test đỏ**

```python
def test_tool_call_request_rejects_unknown_tool():
    with pytest.raises(ValidationError):
        ToolCallRequest(tool="execute_sql", arguments={})

def test_health_response_has_stable_shape():
    response = HealthResponse(status="ok", service="ai-retail-backend", version="0.1.0")
    assert response.model_dump() == {
        "status": "ok",
        "service": "ai-retail-backend",
        "version": "0.1.0",
    }
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `uv run pytest tests/test_contracts.py -q`  
Expected: FAIL vì `backend.shared.contracts` chưa tồn tại.

- [ ] **Step 3: Viết implementation tối thiểu**

```python
class ToolName(str, Enum):
    RESOLVE_PRODUCT = "resolve_product"
    SEARCH_PRODUCTS = "search_products"
    GET_PRODUCT_SNAPSHOT = "get_product_snapshot"
    GET_PRODUCT_KNOWLEDGE = "get_product_knowledge"
    GET_EXPIRY_INFO = "get_expiry_info"
    GET_CART = "get_cart"
    UPDATE_CART = "update_cart"
    CREATE_CHECKOUT = "create_checkout"
    CHECK_PAYMENT_STATUS = "check_payment_status"
    HANDOFF_TO_STAFF = "handoff_to_staff"
    REPORT_PRODUCT_ISSUE = "report_product_issue"

class ToolCallRequest(BaseModel):
    tool: ToolName
    arguments: dict[str, Any] = Field(default_factory=dict)
```

Các enum còn lại dùng string values ổn định trong docs; `HealthResponse` dùng Pydantic model với `status`, `service`, `version`.

- [ ] **Step 4: Chạy test để xác nhận pass**

Run: `uv run pytest tests/test_contracts.py -q`  
Expected: PASS.

### Task 4: Health API skeleton (TDD)

**Files:**
- Create: `tests/test_health.py`
- Create: `backend/api/__init__.py`
- Create: `backend/api/main.py`

**Interfaces:**
- Consumes: `HealthResponse` từ `backend.shared.contracts`.
- Produces: FastAPI `app` và `GET /health` trả JSON schema ổn định với HTTP 200.

- [ ] **Step 1: Viết test đỏ**

```python
def test_health_endpoint_returns_service_status():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "ai-retail-backend",
        "version": "0.1.0",
    }
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `uv run pytest tests/test_health.py -q`  
Expected: FAIL vì `backend.api.main` chưa tồn tại.

- [ ] **Step 3: Viết implementation tối thiểu**

```python
app = FastAPI(title="AI Retail Assistant API", version="0.1.0")

@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="ai-retail-backend", version="0.1.0")
```

- [ ] **Step 4: Chạy test để xác nhận pass**

Run: `uv run pytest tests/test_health.py -q`  
Expected: PASS.

### Task 5: Full verification and handoff

**Files:**
- Modify: `README.md` if setup commands differ from the installed environment.

**Interfaces:**
- Consumes: all artifacts from Tasks 1–4.
- Produces: verified local commands and an explicit Docker availability status.

- [ ] **Step 1: Resolve and install dependencies**

Run: `uv sync --all-groups`  
Expected: `.venv` and `uv.lock` are created/updated with exit code 0.

- [ ] **Step 2: Run tests and lint**

Run: `uv run pytest -q` and `uv run ruff check .`  
Expected: all tests pass and Ruff exits 0.

- [ ] **Step 3: Verify import and OpenAPI contract**

Run: `uv run python -c "from backend.api.main import app; print(app.title); print(sorted(route.path for route in app.routes))"`  
Expected: title `AI Retail Assistant API` and `/health` in routes.

- [ ] **Step 4: Check optional local infrastructure**

Run: `docker compose config` if Docker exists.  
Expected: valid Compose output; if Docker is unavailable, record the blocker and leave Compose ready for Day 3.

