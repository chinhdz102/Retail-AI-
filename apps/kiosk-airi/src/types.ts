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
  state: string
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
}
