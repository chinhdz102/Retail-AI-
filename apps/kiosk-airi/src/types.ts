export type AssistantStatus = 'idle' | 'listening' | 'transcribing' | 'thinking' | 'speaking' | 'error'
export type LanguageCode = 'vi' | 'en' | 'zh'

export interface SessionResponse {
  session_id: string
  kiosk_id: string
  state: string
  expires_at: string
  cart_available?: boolean
  session_token?: string | null
  streaming_stt_available?: boolean
  direct_azure_stt_available?: boolean
}

export interface AzureSpeechTokenResponse {
  enabled: boolean
  token?: string | null
  region?: string | null
  expires_in_seconds?: number
  phrases_vi?: string[]
}

export interface ProductSnapshot {
  product_id: string
  name: string
  category: string
  price_vnd: number
  stock: number
  shelf: string
  promotion: string | null
  spicy: boolean | null
  stock_is_current?: boolean
  ingredients: string
  expiry_at: string
  data_timestamp: string
}

export interface ProductSearchResponse {
  items: ProductSnapshot[]
  data_timestamp: string
}

export interface AgentResponse {
  session_id: string
  text: string
  model: string
  grounded_product_ids: string[]
  tools_used?: string[]
  data_timestamp?: string | null
  data_source?: string | null
  state: string
  products?: ProductCard[]
  related_products?: ProductCard[]
  shopping_plan?: ShoppingPlan
  cart?: ShoppingCart | null
  checkout_quote?: CheckoutQuote | null
  order?: SandboxOrder | null
  support_ticket?: SupportTicket | null
}

export interface ShoppingPlanItem {
  need_id: string
  need_label: string
  rationale: string
  category: string
  source: 'EXPLICIT' | 'INFERRED'
  required: boolean
  product_id: string
  name: string
  brand: string | null
  quantity: number
  unit_price_vnd: number
  line_total_vnd: number
  uom: string | null
  stock_is_current: boolean
  data_timestamp: string
  data_source: string
  budget_share_vnd: number | null
}

export interface ShoppingPlanOption {
  option_id: string
  label: string
  items: ShoppingPlanItem[]
  covered_need_ids: string[]
  total_vnd: number
  remaining_budget_vnd: number | null
  budget_fit: boolean
}

export interface ShoppingPlanNeed {
  need_id: string
  label: string
  rationale: string
  requested_category: string
  resolved_category: string | null
  source: 'EXPLICIT' | 'INFERRED'
  required: boolean
  quantity: number
  candidate_count: number
  candidate_product_ids: string[]
  alternative_categories: Array<{
    category: string
    products: Array<{
      product_id: string
      name: string
      brand: string | null
      price_vnd: number
      stock_is_current: boolean
      data_timestamp: string
      data_source: string
    }>
  }>
  blocked_reasons: string[]
}

export interface ShoppingPlan {
  mission_id: string
  revision: number
  goal: string
  use_case: string
  occasion: string
  party_size: number | null
  budget_vnd: number | null
  budget_exclusive: boolean
  confirmed_needs: string[]
  inferred_needs: string[]
  needs: ShoppingPlanNeed[]
  options: ShoppingPlanOption[]
  missing_required_needs: string[]
  missing_optional_needs: string[]
  optional_needs_not_selected: string[]
  budget_shortfall_vnd: number | null
  pending_decision: string | null
  inventory_verified: boolean
  data_source: string
  data_timestamp: string | null
  cart_mutated: boolean
  unresolved_details: string[]
  unknown_details: string[]
}

export interface SupportTicket {
  ticket_id: string
  session_id: string
  kiosk_id: string
  issue_type: string
  description: string
  status: string
  created_at: string
  demo: boolean
  target_role?: 'manager' | 'staff'
  notified_channel?: 'zalo' | 'messenger' | 'gmail' | null
  notified_provider?: string | null
  notification_status?: 'DELIVERED' | 'FAILED' | 'NOT_CONFIGURED' | null
  notification_detail?: string | null
  notified_at?: string | null
}

export interface ManagerNotifierStatus {
  enabled: boolean
  store_name: string
  primary_channel: 'zalo' | 'messenger' | 'gmail' | null
  primary_provider: string | null
  priority: string[]
  configured_providers: string[]
  channels: Record<string, boolean>
}

export interface ProductCard {
  product_id: string
  name: string
  category: string
  brand: string | null
  price_vnd: number
  list_price_vnd?: number | null
  promotion?: string | null
  shelf?: string | null
  image_url?: string | null
  stock: number
  stock_is_current?: boolean
  uom: string | null
  data_timestamp: string
  data_source: string
  demo_units_sold_30d?: number | null
  sales_data_source?: 'SIMULATED' | 'FRN_STORE_18' | 'CATALOG_MONTHLY' | null
}

export interface CartLine {
  product_id: string
  name: string
  category: string
  brand: string | null
  unit_price_vnd: number
  quantity: number
  line_total_vnd: number
  available_stock: number
  uom: string | null
  data_timestamp: string
}

export interface ShoppingCart {
  cart_id: string
  session_id: string
  items: CartLine[]
  item_count: number
  subtotal_vnd: number
  total_vnd: number
  version: number
  sandbox: boolean
  data_source: string
}

export interface CheckoutQuote {
  confirmation_token: string
  cart: ShoppingCart
  expires_at: string
  sandbox: boolean
}

export interface SandboxOrder {
  order_id: string
  session_id: string
  cart_id: string
  items: CartLine[]
  amount_vnd: number
  order_status: string
  payment_status: string
  created_at: string
  expires_at: string
  sandbox: boolean
  provider_transaction_id: string | null
}

export interface AssistedCartItem {
  product: ProductCard
  quantity: number
}

export interface PayOSStatus {
  enabled: boolean
  provider: string
  store_name: string
}

export type CatalogSortMode = 'popular' | 'promo' | 'price_asc' | 'price_desc' | 'name'

export interface CatalogSubcategorySummary {
  name: string
  count: number
}

export interface CatalogDepartmentSummary {
  name: string
  count: number
  subcategories: CatalogSubcategorySummary[]
}

export interface ProductCatalogBrowseResponse {
  total: number
  total_catalog: number
  offset: number
  limit: number
  has_more: boolean
  active_department: string | null
  active_category: string | null
  departments: CatalogDepartmentSummary[]
  items: ProductCard[]
  data_timestamp: string
}

export type CompactCatalogRow = [
  string,          // 0: product_id
  string,          // 1: name
  number,          // 2: dept_idx
  string,          // 3: category
  string | null,   // 4: brand
  number,          // 5: price_vnd
  number | null,   // 6: list_price_vnd
  string | null,   // 7: promotion
  string | null,   // 8: shelf
  string | null,   // 9: image_url
  number,          // 10: stock
  string | null,   // 11: uom
  number | null,   // 12: demo_units_sold_30d
]

export interface ProductCatalogIndexResponse {
  total: number
  data_source: string
  stock_is_current: boolean
  data_timestamp: string
  sales_data_source?: 'SIMULATED' | 'FRN_STORE_18' | 'CATALOG_MONTHLY' | null
  departments: CatalogDepartmentSummary[]
  rows: CompactCatalogRow[]
}

export interface AssistedOrderCreatePayload {
  items: Array<{ product_id: string; quantity: number }>
  payment_method?: 'counter' | 'payos'
  customer_name?: string
  customer_phone?: string
  pickup_location?: string
  customer_note?: string
  return_url?: string
  cancel_url?: string
  idempotency_key?: string
}

export interface AssistedOrderItem {
  product_id: string
  name: string
  brand?: string | null
  category: string
  uom?: string | null
  image_url?: string | null
  unit_price_vnd: number
  quantity: number
  line_total_vnd: number
  shelf_location?: string | null
  snapshot_stock: number
  stock_is_current: boolean
}

export interface AssistedOrderResponse {
  order_id: string
  order_code: string
  session_id: string
  kiosk_id: string
  store_name: string
  items: AssistedOrderItem[]
  item_count: number
  subtotal_vnd: number
  total_vnd: number
  customer_name?: string | null
  customer_phone?: string | null
  pickup_location?: string | null
  customer_note?: string | null
  status: 'AWAITING_PAYMENT' | 'SENT_TO_STAFF' | 'RECORDED_PENDING_STAFF' | 'CANCELLED'
  ticket_id: string
  created_at: string
  data_source: string
  stock_is_current: boolean
  payment_method?: 'counter' | 'payos'
  payment_status?: 'UNPAID' | 'PENDING_PAYOS' | 'PAID' | 'CANCELLED' | 'EXPIRED'
  payos_order_code?: number | null
  payos_payment_link_id?: string | null
  payos_checkout_url?: string | null
  payos_qr_code?: string | null
  payos_account_number?: string | null
  payos_account_name?: string | null
  payos_bin?: string | null
  payos_description?: string | null
  payos_expired_at?: number | null
  payos_paid_amount_vnd?: number | null
  payos_transaction_ref?: string | null
  paid_at?: string | null
  payment_verify_token?: string | null
  notified_channel?: 'zalo' | 'messenger' | 'gmail' | null
  notified_provider?: string | null
  notification_status: 'DELIVERED' | 'FAILED' | 'NOT_CONFIGURED'
  notification_detail?: string | null
  notified_at?: string | null
  customer_message: string
}

export interface TranscriptionResponse {
  session_id: string
  text: string
  language: string
  model: string
  speech_detected?: boolean
  needs_confirmation?: boolean
  quality?: Record<string, number>
  diagnostics?: { raw_text: string; corrections: Array<{ source: string; replacement: string }> }
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  imagePreviewUrl?: string
  model?: string
  groundedProductIds?: string[]
  products?: ProductCard[]
  relatedProducts?: ProductCard[]
  shoppingPlan?: ShoppingPlan
  supportTicket?: SupportTicket
  assistedOrder?: AssistedOrderResponse
}
