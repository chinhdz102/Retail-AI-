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
  spicy: boolean
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
  cart?: ShoppingCart | null
  checkout_quote?: CheckoutQuote | null
  order?: SandboxOrder | null
  support_ticket?: SupportTicket | null
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
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  model?: string
  groundedProductIds?: string[]
  products?: ProductCard[]
  supportTicket?: SupportTicket
}
