# AI Retail Assistant — Tool Registry Contract v1

**Trạng thái:** Frozen baseline  
**Ngày:** 2026-08-31  
**Nguồn:** `docs/requirements.md`, `docs/architecture.md`

## 1. Shared enums

Các value dưới đây là wire values ổn định; code dùng string enum tương ứng.

### SessionState

`NEW`, `BROWSING`, `CART_ACTIVE`, `CHECKOUT_CONFIRMATION`, `PAYMENT_PENDING`, `HANDOFF`, `CLOSED`, `EXPIRED`

### IntentType

`PRODUCT_LOOKUP`, `PRODUCT_KNOWLEDGE`, `RECOMMENDATION`, `CART_VIEW`, `CART_UPDATE`, `CHECKOUT`, `PAYMENT_STATUS`, `HANDOFF`, `PRODUCT_ISSUE`, `UNKNOWN`

### ToolName

`resolve_product`, `search_products`, `get_product_snapshot`, `get_product_knowledge`, `get_expiry_info`, `get_cart`, `update_cart`, `create_checkout`, `check_payment_status`, `handoff_to_staff`, `report_product_issue`

### ToolPermission

`READ_ONLY`, `CART_WRITE`, `CHECKOUT_CREATE`, `PAYMENT_VERIFY`, `STAFF_HANDOFF`, `ISSUE_CREATE`

### PaymentStatus

`CREATED`, `PENDING`, `PAID`, `FAILED`, `EXPIRED`, `CANCELLED`, `UNKNOWN`

### OrderStatus

`DRAFT`, `CHECKOUT_PENDING`, `AWAITING_PAYMENT`, `PAID`, `CANCELLED`, `EXPIRED`, `FAILED`

### IssueType

`EXPIRED_PRODUCT`, `DAMAGED_PACKAGING`, `WRONG_SHELF`, `WRONG_INFORMATION`, `PAYMENT_PROBLEM`, `OTHER`

## 2. Common rules

- Mỗi tool request phải có `session_id` khi tool tác động hoặc đọc state của customer.
- `product_id`, `order_id` và `session_id` phải được resolve/authorize ở backend; không tin ID do LLM tự đoán.
- Tiền tệ dùng integer VND, không dùng floating point.
- Tool output phải có schema version, source hoặc provenance phù hợp và timestamp nếu là dữ kiện thay đổi theo thời gian.
- `create_checkout` chỉ được gọi sau confirmation gateway cấp `confirmation_id` hợp lệ.
- Tool không được sửa giá, tồn kho, batch, promotion, role hoặc payment status trực tiếp.

## 3. Tool schemas

### `resolve_product`

**Permission:** `READ_ONLY`  
**Allowed state:** `BROWSING`, `CART_ACTIVE`  
**Confirmation:** Không

```json
{
  "input": {
    "query": "coca zero lon",
    "barcode": null
  },
  "output": {
    "product_id": "COKE_ZERO_330",
    "matched_name": "Coca-Cola Zero 330ml",
    "confidence": 0.96,
    "candidates": []
  }
}
```

`confidence < 0.85` không được dùng để gọi tool phụ thuộc SKU; hệ thống phải clarification.

### `search_products`

**Permission:** `READ_ONLY`  
**Allowed state:** `BROWSING`, `CART_ACTIVE`  
**Confirmation:** Không

```json
{
  "input": {
    "category": "snack",
    "max_price_vnd": 30000,
    "spicy": false,
    "in_stock": true,
    "limit": 10
  },
  "output": {
    "items": [
      {
        "product_id": "SNACK01",
        "name": "Snack khoai tây",
        "price_vnd": 18000,
        "stock": 12,
        "spicy": false
      }
    ],
    "applied_constraints": ["category", "max_price_vnd", "spicy", "in_stock"],
    "data_timestamp": "2026-08-31T00:00:00Z"
  }
}
```

Filter và limit do backend validate; LLM không được invent product.

### `get_product_snapshot`

**Permission:** `READ_ONLY`  
**Allowed state:** `BROWSING`, `CART_ACTIVE`  
**Confirmation:** Không

```json
{
  "input": {"product_id": "COKE330"},
  "output": {
    "product_id": "COKE330",
    "name": "Coca-Cola 330ml",
    "price_vnd": 13000,
    "stock": 16,
    "shelf": "A2-L2",
    "promotion": null,
    "data_timestamp": "2026-08-31T00:00:00Z"
  }
}
```

### `get_product_knowledge`

**Permission:** `READ_ONLY`  
**Allowed state:** `BROWSING`, `CART_ACTIVE`  
**Confirmation:** Không

```json
{
  "input": {
    "product_id": "MILK01",
    "question": "Sản phẩm có chứa dị ứng sữa không?",
    "top_k": 5
  },
  "output": {
    "answer": "...",
    "evidence": [
      {
        "document_id": "doc-001",
        "chunk_id": "chunk-004",
        "source": "approved-product-sheet",
        "relevance": 0.91
      }
    ],
    "confidence": 0.88
  }
}
```

Tool này không trả current price, stock, shelf hoặc expiry.

### `get_expiry_info`

**Permission:** `READ_ONLY`  
**Allowed state:** `BROWSING`, `CART_ACTIVE`  
**Confirmation:** Không

```json
{
  "input": {"product_id": "MILK01"},
  "output": {
    "product_id": "MILK01",
    "batches": [
      {
        "batch_id": "B-20260801",
        "quantity": 10,
        "expiry_at": "2026-09-15",
        "status": "AVAILABLE"
      }
    ],
    "selection_rule": "FEFO",
    "data_timestamp": "2026-08-31T00:00:00Z"
  }
}
```

### `get_cart`

**Permission:** `READ_ONLY`  
**Allowed state:** `CART_ACTIVE`, `CHECKOUT_CONFIRMATION`  
**Confirmation:** Không

```json
{
  "input": {"session_id": "session-uuid"},
  "output": {
    "cart_id": "cart-uuid",
    "session_id": "session-uuid",
    "items": [],
    "subtotal_vnd": 44000,
    "total_vnd": 44000,
    "currency": "VND",
    "version": 3
  }
}
```

`subtotal_vnd` và `total_vnd` luôn do cart backend tính.

### `update_cart`

**Permission:** `CART_WRITE`  
**Allowed state:** `BROWSING`, `CART_ACTIVE`  
**Confirmation:** Chỉ yêu cầu clarification/confirmation khi số lượng hoặc sản phẩm mơ hồ

```json
{
  "input": {
    "session_id": "session-uuid",
    "product_id": "COKE330",
    "operation": "ADD",
    "quantity": 2,
    "expected_version": 3
  },
  "output": {
    "cart_id": "cart-uuid",
    "items": [],
    "subtotal_vnd": 26000,
    "total_vnd": 26000,
    "version": 4
  }
}
```

`operation` chỉ nhận `ADD`, `REMOVE`, `SET_QUANTITY`; backend reject quantity âm, vượt tồn hoặc stale `expected_version`.

### `create_checkout`

**Permission:** `CHECKOUT_CREATE`  
**Allowed state:** `CHECKOUT_CONFIRMATION`  
**Confirmation:** Bắt buộc

```json
{
  "input": {
    "session_id": "session-uuid",
    "confirmation_id": "gateway-confirmation-uuid",
    "idempotency_key": "checkout-key"
  },
  "output": {
    "order_id": "order-uuid",
    "status": "AWAITING_PAYMENT",
    "amount_vnd": 44000,
    "payment_reference": "provider-ref",
    "qr_payload": "sandbox-payload"
  }
}
```

`confirmation_id` chỉ do Confirmation Gateway phát hành sau khi customer xác nhận tóm tắt deterministic từ backend.

### `check_payment_status`

**Permission:** `PAYMENT_VERIFY`  
**Allowed state:** `PAYMENT_PENDING`  
**Confirmation:** Không

```json
{
  "input": {"order_id": "order-uuid"},
  "output": {
    "order_id": "order-uuid",
    "payment_status": "PENDING",
    "provider_transaction_id": null,
    "verified_at": "2026-08-31T00:00:00Z"
  }
}
```

Customer statement, screenshot và LLM output không phải payment evidence.

### `handoff_to_staff`

**Permission:** `STAFF_HANDOFF`  
**Allowed state:** `BROWSING`, `CART_ACTIVE`, `CHECKOUT_CONFIRMATION`, `PAYMENT_PENDING`  
**Confirmation:** Không

```json
{
  "input": {
    "session_id": "session-uuid",
    "reason": "DATA_MISMATCH",
    "summary": "Giá hiển thị cần nhân viên kiểm tra"
  },
  "output": {
    "handoff_id": "handoff-uuid",
    "status": "OPEN",
    "created_at": "2026-08-31T00:00:00Z"
  }
}
```

### `report_product_issue`

**Permission:** `ISSUE_CREATE`  
**Allowed state:** `BROWSING`, `CART_ACTIVE`, `PAYMENT_PENDING`  
**Confirmation:** Không

```json
{
  "input": {
    "session_id": "session-uuid",
    "product_id": "MILK01",
    "issue_type": "EXPIRED_PRODUCT",
    "description": "Hộp trên kệ có ngày hết hạn đã qua"
  },
  "output": {
    "issue_id": "issue-uuid",
    "status": "OPEN",
    "source_of_truth_changed": false
  }
}
```

Tool chỉ tạo incident; không sửa product, batch, inventory hoặc promotion.

## 4. Tools bị cấm với Customer Agent

`update_price`, `modify_inventory`, `update_expiry`, `mark_payment_paid`, `change_bank_account`, `create_promotion`, `delete_product`, `delete_order`, `change_user_role`, `export_customer_data`, `execute_sql`, `execute_shell`.

## 5. Policy outcomes

| Outcome | Khi dùng |
|---|---|
| `ALLOW` | Tool, state, role, schema, goal và business rules đều hợp lệ. |
| `DENY` | Tool bị cấm, sai role/state, sai schema hoặc vi phạm giới hạn. |
| `REQUIRE_CONFIRMATION` | Checkout hoặc hành động consequential chưa có confirmation token. |
| `REQUIRE_CLARIFICATION` | Product/quantity/intent chưa đủ xác định. |
| `HANDOFF` | Mismatch, repeated failure, safety issue hoặc evidence không đủ. |
