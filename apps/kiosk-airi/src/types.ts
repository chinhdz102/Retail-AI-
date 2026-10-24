export type AssistantStatus = 'idle' | 'listening' | 'transcribing' | 'thinking' | 'speaking' | 'error'
export type LanguageCode = 'vi' | 'en' | 'zh'

export interface SessionResponse {
  session_id: string
  kiosk_id: string
  state: string
  expires_at: string
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
}

export interface ProductCard {
  product_id: string
  name: string
  category: string
  brand: string | null
  price_vnd: number
  stock: number
  stock_is_current?: boolean
  uom: string | null
  data_timestamp: string
  data_source: string
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

export interface TranscriptionResponse {
  session_id: string
  text: string
  language: string
  model: string
  speech_detected?: boolean
  quality?: Record<string, number>
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  model?: string
  groundedProductIds?: string[]
  products?: ProductCard[]
  shoppingPlan?: ShoppingPlan
  supportTicket?: SupportTicket
}
