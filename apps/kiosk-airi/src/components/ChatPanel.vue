<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import {
  PhArrowClockwise as ArrowClockwise,
  PhArrowUp as ArrowUp,
  PhBarcode as Barcode,
  PhBellRinging as BellRinging,
  PhCamera as Camera,
  PhCaretRight as CaretRight,
  PhCheckCircle as CheckCircle,
  PhImage as ImageIcon,
  PhMicrophone as Microphone,
  PhPause as Pause,
  PhPlus as Plus,
  PhMinus as Minus,
  PhReceipt as Receipt,
  PhShoppingBagOpen as ShoppingBagOpen,
  PhShoppingCart as ShoppingCartIcon,
  PhSparkle as Sparkle,
  PhSquaresFour as SquaresFour,
  PhStop as Stop,
  PhTrash as Trash,
  PhWaveform as Waveform,
  PhX as CloseIcon,
} from '@phosphor-icons/vue'

import {
  formatKilobytes,
  isVideoBlob,
  preprocessProductMedia,
  type PreprocessedMedia,
} from '../media-preprocess'
import type {
  AssistedCartItem,
  AssistantStatus,
  ChatMessage,
  CheckoutQuote,
  LanguageCode,
  ProductCard,
  SandboxOrder,
  ShoppingCart,
  SupportTicket,
} from '../types'

const props = defineProps<{
  messages: ChatMessage[]
  status: AssistantStatus
  draft: string
  isRecording: boolean
  isVoiceActive: boolean
  isHandsFree: boolean
  voiceHint: string
  language: LanguageCode
  errorMessage: string | null
  cart?: ShoppingCart | null
  checkoutQuote?: CheckoutQuote | null
  order?: SandboxOrder | null
  assistedItems?: AssistedCartItem[]
  assistedTotalCount?: number
  assistedTotalVnd?: number
  assistedOrdersCount?: number
  microphoneLevel?: number
  microphoneDevices?: Array<{ id: string; label: string }>
  selectedMicrophone?: string
  activeMicrophone?: string
  cameraTriggerToken?: number
  managerNotifyChannel?: 'zalo' | 'messenger' | 'gmail' | null
}>()

const conversation = ref<HTMLElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const nativeCameraInput = ref<HTMLInputElement | null>(null)
const cameraVideo = ref<HTMLVideoElement | null>(null)
const pendingImageFile = ref<Blob | null>(null)
const pendingImagePreview = ref<string | null>(null)
const pendingMediaInfo = ref<PreprocessedMedia | null>(null)
const isProcessingMedia = ref(false)
const mediaProcessError = ref<string | null>(null)
const isCameraOpen = ref(false)
const cameraError = ref<string | null>(null)
const isCameraStarting = ref(false)
const activeProductModal = ref<ProductCard | null>(null)
const brokenImageIds = ref<Record<string, boolean>>({})
let cameraStream: MediaStream | null = null

watch(() => [props.messages.length, props.status], async () => {
  await nextTick()
  if (conversation.value) conversation.value.scrollTop = conversation.value.scrollHeight
})

const emit = defineEmits<{
  'update:draft': [value: string]
  send: []
  'send-image': [payload: { file: Blob; previewUrl: string; message: string }]
  'quick-prompt': [value: string]
  'toggle-recording': []
  'stop-speaking': []
  'change-microphone': [deviceId: string]
  retry: []
  'add-product': [productId: string]
  'set-cart-quantity': [productId: string, quantity: number]
  'prepare-checkout': []
  'confirm-checkout': []
  'cancel-checkout': []
  'simulate-payment': [outcome: 'PAID' | 'FAILED']
  'clear-order': []
  'end-session': []
  'request-support': []
  'add-assisted-item': [product: ProductCard]
  'update-assisted-qty': [productId: string, quantity: number]
  'open-cart': [tab?: 'cart' | 'orders']
  'open-catalog': []
}>()

const copy = computed(() => {
  if (props.language === 'en') {
    return {
      eyebrow: 'Hands-free conversation',
      title: "Let's talk",
      emptyTitle: 'What can I help you find?',
      emptyBody: 'Speak naturally or type a question. I will use verified store information.',
      user: 'You',
      thinking: 'Finding the right answer',
      transcribing: 'Listening back to your voice',
      errorTitle: 'Assistant connection issue',
      placeholder: 'Try: How much is Coca Zero?',
      hint: 'Enter to send',
      hintSecond: 'Shift + Enter for a new line',
      retry: 'Try again',
      micStart: 'Start speaking',
      micStop: 'Stop recording',
      voiceStop: 'Stop listening',
      stopSpeaking: 'Stop Mai speaking',
      languageLabel: 'Conversation language',
      sectionLabel: 'Conversation with Mai',
      microphone: 'Input microphone',
      defaultMicrophone: 'Default microphone',
      activeMicrophone: 'Currently capturing from',
      microphoneAriaLabel: 'Choose an input microphone',
      microphoneLevel: 'Microphone signal level',
      inputLabel: 'Type a question for Mai',
      quickPromptsLabel: 'Suggested questions',
      sendLabel: 'Send question',
    }
  }
  if (props.language === 'zh') {
    return {
      eyebrow: '免提对话',
      title: '和我聊聊吧',
      emptyTitle: '想找什么商品？',
      emptyBody: '可以自然说话或输入问题。我只使用已验证的门店信息。',
      user: '你',
      thinking: '正在查找答案',
      transcribing: '正在识别你的声音',
      errorTitle: '助手连接出现问题',
      placeholder: '例如：Coca Zero 多少钱？',
      hint: '按 Enter 发送',
      hintSecond: 'Shift + Enter 换行',
      retry: '重试',
      micStart: '开始说话',
      micStop: '停止录音',
      voiceStop: '停止聆听',
      stopSpeaking: '停止 Mai 朗读',
      languageLabel: '对话语言',
      sectionLabel: '与 Mai 对话',
      microphone: '输入麦克风',
      defaultMicrophone: '默认麦克风',
      activeMicrophone: '当前正在使用',
      microphoneAriaLabel: '选择输入麦克风',
      microphoneLevel: '麦克风信号强度',
      inputLabel: '输入问题给 Mai',
      quickPromptsLabel: '推荐问题',
      sendLabel: '发送问题',
    }
  }
  return {
    eyebrow: 'Cùng Mai mua sắm',
    title: 'Mình cùng nói chuyện nhé',
    emptyTitle: 'Bạn muốn tìm gì hôm nay?',
    emptyBody: 'Nói tự nhiên hoặc nhập câu hỏi. Mình chỉ dùng thông tin cửa hàng đã xác minh.',
    user: 'Bạn',
    thinking: 'Mình đang tìm câu trả lời',
    transcribing: 'Mình đang nghe lại câu nói của bạn',
    errorTitle: 'Chưa kết nối được trợ lý',
    placeholder: 'Hỏi Mai về sản phẩm bạn cần…',
    hint: 'Enter để gửi',
    hintSecond: 'Shift + Enter để xuống dòng',
    retry: 'Thử lại',
    micStart: 'Bắt đầu nói',
    micStop: 'Dừng ghi âm',
    voiceStop: 'Dừng nghe',
    stopSpeaking: 'Dừng Mai nói',
    languageLabel: 'Ngôn ngữ hội thoại',
    sectionLabel: 'Trò chuyện với Mai',
    microphone: 'Micro đầu vào',
    defaultMicrophone: 'Micro mặc định',
    activeMicrophone: 'Đang thu từ',
    microphoneAriaLabel: 'Chọn micro đầu vào',
    microphoneLevel: 'Mức tín hiệu micro',
    inputLabel: 'Nhập câu hỏi cho Mai',
    quickPromptsLabel: 'Câu hỏi gợi ý',
    sendLabel: 'Gửi câu hỏi',
  }
})

const shoppingText = computed(() => {
  if (props.language === 'en') {
    return {
      cart: 'Demo cart',
      sandbox: 'SANDBOX',
      total: 'Total',
      checkout: 'Review checkout',
      confirm: 'Confirm demo order',
      cancel: 'Back to cart',
      order: 'Sandbox order',
      simulatePaid: 'Simulate payment success',
      simulateFailed: 'Simulate failure',
      add: 'Add',
      stock: 'available in demo',
      snapshotStock: 'recorded in snapshot · current stock unverified',
      referencePrice: 'Reference price',
      catalogDemo: 'Demo catalog',
      catalogThanhLam: 'Thanh Lam Mart catalog',
      catalogSnapshot: 'Thanh Lam Mart catalog',
      catalogUnknown: 'Catalog',
      end: 'End session',
      support: 'Call staff · demo',
      ticketLabel: 'Demo support request',
      ticketDemo: 'Demo only · no real staff was contacted',
    }
  }
  if (props.language === 'zh') {
    return {
      cart: '演示购物车', sandbox: '沙盒', total: '合计', checkout: '查看结账摘要',
      confirm: '确认演示订单', cancel: '返回购物车', order: '沙盒订单',
      simulatePaid: '模拟支付成功', simulateFailed: '模拟支付失败', add: '添加',
      stock: '演示库存', end: '结束会话',
      snapshotStock: '目录记录库存 · 当前库存未核实',
      referencePrice: '参考价',
      support: '联系店员 · 演示', ticketLabel: '演示服务请求',
      ticketDemo: '仅供演示 · 尚未联系真实店员',
      catalogDemo: '演示商品目录', catalogThanhLam: 'Thanh Lam Mart 商品目录',
      catalogSnapshot: 'Thanh Lam Mart 商品目录',
      catalogUnknown: '商品目录',
    }
  }
  return {
    cart: 'Giỏ hàng demo',
    sandbox: 'SANDBOX',
    total: 'Tổng',
    checkout: 'Xem lại checkout',
    confirm: 'Xác nhận đơn demo',
    cancel: 'Quay lại giỏ',
    order: 'Đơn sandbox',
    simulatePaid: 'Mô phỏng thanh toán thành công',
    simulateFailed: 'Mô phỏng thất bại',
    add: 'Thêm',
    stock: 'còn hàng',
    snapshotStock: 'tồn ghi nhận trong danh mục',
    referencePrice: 'Giá bán',
    catalogDemo: 'Catalog demo',
    catalogThanhLam: 'Danh mục Thanh Lam Mart',
    catalogSnapshot: 'Danh mục Thanh Lam Mart',
    catalogUnknown: 'Catalog',
    end: 'Kết thúc phiên',
    support: 'Gọi nhân viên · demo',
    ticketLabel: 'Yêu cầu hỗ trợ demo',
    ticketDemo: 'Chỉ là bản demo · chưa liên hệ nhân viên thật',
  }
})

const visibleCart = computed(() => props.checkoutQuote?.cart ?? props.cart ?? null)
const visibleOrder = computed(() => props.order ?? null)

const assistedQtyById = computed(() => {
  const map: Record<string, number> = {}
  for (const entry of props.assistedItems ?? []) {
    map[entry.product.product_id] = entry.quantity
  }
  return map
})

function assistedQty(productId: string): number {
  return assistedQtyById.value[productId] ?? 0
}

function channelDisplayName(channel: 'zalo' | 'messenger' | 'gmail' | null | undefined) {
  if (channel === 'zalo') return 'Zalo'
  if (channel === 'messenger') return 'Messenger'
  if (channel === 'gmail') return 'Gmail'
  return ''
}

const supportButtonLabel = computed(() => {
  const ch = channelDisplayName(props.managerNotifyChannel)
  if (!ch) return shoppingText.value.support
  if (props.language === 'en') return `Call manager · ${ch}`
  if (props.language === 'zh') return `联系店长 · ${ch}`
  return `Gọi quản lý · ${ch}`
})

function supportTicketTitle(ticket: SupportTicket) {
  if (ticket.notification_status === 'DELIVERED' && ticket.notified_channel) {
    const ch = channelDisplayName(ticket.notified_channel)
    if (props.language === 'en') return `Manager alerted via ${ch}`
    if (props.language === 'zh') return `已通过 ${ch} 通知店长`
    return `Đã gửi tin báo quản lý qua ${ch}`
  }
  if (ticket.notification_status === 'FAILED') {
    if (props.language === 'en') return 'Automatic alert could not be sent'
    if (props.language === 'zh') return '自动通知发送失败'
    return 'Chưa gửi được tin nhắn tự động'
  }
  return shoppingText.value.ticketLabel
}

function supportTicketSubnote(ticket: SupportTicket) {
  if (ticket.notification_status === 'DELIVERED') {
    if (props.language === 'en') return 'The store manager has received your request.'
    if (props.language === 'zh') return '店长已收到您的求助通知，请稍候。'
    return 'Quản lý đã nhận được thông báo và sẽ ra hỗ trợ bạn sớm nhất.'
  }
  if (ticket.notification_status === 'FAILED') {
    if (props.language === 'en') return 'Please ask a store associate at the counter.'
    if (props.language === 'zh') return '请直接联系柜台店员。'
    return 'Bạn vui lòng nhờ nhân viên tại quầy hỗ trợ trực tiếp nhé.'
  }
  return ticket.demo ? shoppingText.value.ticketDemo : ''
}

function formatVnd(amount: number) {
  return new Intl.NumberFormat(props.language === 'en' ? 'en-US' : 'vi-VN', {
    maximumFractionDigits: 0,
  }).format(amount) + (props.language === 'en' ? ' VND' : 'đ')
}

function productProvenance(product: ProductCard) {
  const source = product.data_source === 'demo_product_catalog'
    ? shoppingText.value.catalogDemo
    : product.data_source === 'thanh_lam_postgres_catalog'
      ? shoppingText.value.catalogThanhLam
      : product.data_source === 'thanh_lam_json_snapshot'
        ? shoppingText.value.catalogSnapshot
      : shoppingText.value.catalogUnknown
  const date = new Intl.DateTimeFormat(props.language === 'en' ? 'en-GB' : 'vi-VN', {
    day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'Asia/Ho_Chi_Minh',
  }).format(new Date(product.data_timestamp))
  return `${source} · ${date}`
}

function productStockLabel(product: ProductCard) {
  if (product.stock_is_current === false) return shoppingText.value.snapshotStock
  return `${product.stock} ${shoppingText.value.stock}`
}

function productPriceLabel(product: ProductCard) {
  return formatVnd(product.price_vnd)
}

function productSalesLabel(product: ProductCard) {
  const isSim = product.sales_data_source === 'SIMULATED'
  if (props.language === 'en') return isSim ? 'Demo sales' : 'Sold'
  if (props.language === 'zh') return isSim ? '演示销量' : '已售'
  return isSim ? 'Đã bán (demo)' : 'Đã bán'
}

const quickPrompts = computed(() => {
  if (props.language === 'en') {
    return [
      { label: 'Coca Zero price', icon: Barcode },
      { label: 'Recommend a non-spicy snack', icon: Sparkle },
      { label: 'Where is milk?', icon: CaretRight },
      { label: 'Call the manager please', icon: BellRinging },
    ]
  }
  if (props.language === 'zh') {
    return [
      { label: 'Coca Zero 价格', icon: Barcode },
      { label: '推荐不辣的零食', icon: Sparkle },
      { label: '牛奶在哪？', icon: CaretRight },
      { label: '请帮我找店长', icon: BellRinging },
    ]
  }
  return [
    { label: 'Giá Coca Zero', icon: Barcode },
    { label: 'Gợi ý snack không cay', icon: Sparkle },
    { label: 'Sữa ở kệ nào?', icon: CaretRight },
    { label: 'Gọi quản lý giúp mình', icon: BellRinging },
  ]
})

function clearPendingImage(revoke = true) {
  if (revoke && pendingImagePreview.value) {
    try {
      URL.revokeObjectURL(pendingImagePreview.value)
    } catch {
      // Ignore revoke issues in test environments.
    }
  }
  pendingImageFile.value = null
  pendingImagePreview.value = null
  pendingMediaInfo.value = null
  mediaProcessError.value = null
}

function attachImageBlob(blob: Blob, info?: PreprocessedMedia) {
  clearPendingImage(true)
  pendingImageFile.value = blob
  pendingMediaInfo.value = info ?? null
  if (typeof URL !== 'undefined' && typeof URL.createObjectURL === 'function') {
    pendingImagePreview.value = URL.createObjectURL(blob)
  } else {
    pendingImagePreview.value = ''
  }
}

const pendingMediaTitle = computed(() => {
  const info = pendingMediaInfo.value
  if (info?.sourceKind === 'video') {
    return `Đã trích khung hình rõ nhất từ video (${formatKilobytes(info.finalBytes)})`
  }
  if (info?.finalBytes) {
    return `Ảnh sản phẩm đã tối ưu (${formatKilobytes(info.finalBytes)})`
  }
  return 'Ảnh sản phẩm đã sẵn sàng'
})

const pendingMediaHint = computed(() => {
  const info = pendingMediaInfo.value
  if (info?.sourceKind === 'video') {
    return `Chọn tự động từ ${info.framesSampled} khung hình (${formatKilobytes(info.originalBytes)} → ${formatKilobytes(info.finalBytes)} · ${info.elapsedMs} ms)`
  }
  return 'Gõ câu hỏi thêm hoặc bấm Gửi để Mai nhận diện ngay'
})

function stopCameraStream() {
  if (cameraStream) {
    for (const track of cameraStream.getTracks()) {
      try {
        track.stop()
      } catch {
        // Ignore stop error.
      }
    }
    cameraStream = null
  }
  if (cameraVideo.value) {
    cameraVideo.value.srcObject = null
  }
}

function closeCameraModal() {
  stopCameraStream()
  isCameraOpen.value = false
  isCameraStarting.value = false
  cameraError.value = null
}

async function openCameraModal() {
  cameraError.value = null
  isCameraOpen.value = true
  const getUserMedia = navigator.mediaDevices?.getUserMedia?.bind(navigator.mediaDevices)
  if (!getUserMedia) {
    cameraError.value = 'Trình duyệt không hỗ trợ mở camera trực tiếp. Bạn chọn ảnh có sẵn từ máy bên dưới nhé.'
    return
  }
  isCameraStarting.value = true
  try {
    let stream: MediaStream
    try {
      stream = await getUserMedia({
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 960 } },
        audio: false,
      })
    } catch {
      stream = await getUserMedia({ video: true, audio: false })
    }
    if (!isCameraOpen.value) {
      stream.getTracks().forEach((t) => t.stop())
      return
    }
    cameraStream = stream
    await nextTick()
    if (cameraVideo.value) {
      cameraVideo.value.srcObject = stream
      await cameraVideo.value.play().catch(() => {})
    }
  } catch {
    cameraError.value = 'Chưa mở được camera (chưa cấp quyền hoặc không có camera). Bạn chọn ảnh từ thiết bị bên dưới nhé.'
  } finally {
    isCameraStarting.value = false
  }
}

async function attachProcessedMedia(file: Blob) {
  mediaProcessError.value = null
  isProcessingMedia.value = true
  try {
    const processed = await preprocessProductMedia(file)
    attachImageBlob(processed.blob, processed)
  } catch (error) {
    if (isVideoBlob(file) && file.size <= 4 * 1024 * 1024) {
      // Browser could not decode codec client-side; send compact video to backend PyAV fallback.
      attachImageBlob(file, {
        blob: file,
        sourceKind: 'video',
        originalBytes: file.size,
        finalBytes: file.size,
        width: 0,
        height: 0,
        elapsedMs: 0,
        framesSampled: 1,
      })
    } else {
      mediaProcessError.value = error instanceof Error
        ? error.message
        : 'Chưa đọc được ảnh/video này. Bạn thử chọn ảnh rõ hơn hoặc video MP4 ngắn nhé.'
    }
  } finally {
    isProcessingMedia.value = false
  }
}

async function captureCameraFrame() {
  const video = cameraVideo.value
  if (!video || !video.videoWidth || !video.videoHeight) return
  const started = typeof performance !== 'undefined' ? performance.now() : Date.now()
  const canvas = document.createElement('canvas')
  const maxSide = 640
  const scale = Math.min(1, maxSide / Math.max(video.videoWidth, video.videoHeight))
  canvas.width = Math.max(1, Math.round(video.videoWidth * scale))
  canvas.height = Math.max(1, Math.round(video.videoHeight * scale))
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.78))
  if (blob) {
    const elapsedMs = Math.round((typeof performance !== 'undefined' ? performance.now() : Date.now()) - started)
    attachImageBlob(blob, {
      blob,
      sourceKind: 'image',
      originalBytes: blob.size,
      finalBytes: blob.size,
      width: canvas.width,
      height: canvas.height,
      elapsedMs,
      framesSampled: 1,
    })
    closeCameraModal()
  }
}

function triggerFilePicker() {
  fileInput.value?.click()
}

function triggerNativeCamera() {
  nativeCameraInput.value?.click()
}

async function onFileSelected(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  closeCameraModal()
  await attachProcessedMedia(file)
}

function onComposerPaste(event: ClipboardEvent) {
  const items = event.clipboardData?.items
  if (!items) return
  for (const item of items) {
    if (item.type.startsWith('image/') || item.type.startsWith('video/')) {
      const file = item.getAsFile()
      if (file) {
        event.preventDefault()
        void attachProcessedMedia(file)
        return
      }
    }
  }
}

function submitComposer() {
  if (pendingImageFile.value) {
    const file = pendingImageFile.value
    const previewUrl = pendingImagePreview.value ?? ''
    const message = props.draft.trim()
    clearPendingImage(false)
    emit('send-image', { file, previewUrl, message })
    return
  }
  emit('send')
}

function formatProductName(name: string) {
  return name.replace(/\s+/g, ' ').trim()
}

function hasValidProductImage(product: ProductCard) {
  return Boolean(product.image_url && !brokenImageIds.value[product.product_id])
}

function onProductImageError(productId: string) {
  brokenImageIds.value = { ...brokenImageIds.value, [productId]: true }
}

function openProductModal(product: ProductCard) {
  activeProductModal.value = product
}

function closeProductModal() {
  activeProductModal.value = null
}

function askProductIngredients(product: ProductCard) {
  const cleanName = formatProductName(product.name)
  closeProductModal()
  const prompt = props.language === 'en'
    ? `What are the main ingredients of ${cleanName}?`
    : props.language === 'zh'
      ? `${cleanName} 的主要成分是什么？`
      : `Thành phần chính của ${cleanName} là gì?`
  emit('quick-prompt', prompt)
}

function onWindowKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && activeProductModal.value) {
    event.preventDefault()
    closeProductModal()
  }
}

watch(activeProductModal, (current) => {
  if (typeof window === 'undefined') return
  if (current) {
    window.addEventListener('keydown', onWindowKeydown)
  } else {
    window.removeEventListener('keydown', onWindowKeydown)
  }
})

watch(() => props.cameraTriggerToken, (token) => {
  if (token && token > 0) {
    void openCameraModal()
  }
})

onUnmounted(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('keydown', onWindowKeydown)
  }
  stopCameraStream()
  clearPendingImage(true)
})

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    submitComposer()
  }
}
</script>

<template>
  <section class="chat-panel" :aria-label="copy.sectionLabel">
    <header class="chat-header">
      <div class="chat-title-copy">
        <p class="panel-eyebrow">{{ copy.eyebrow }}</p>
        <h1>{{ copy.title }}</h1>
        <p class="chat-voice-hint" role="status" aria-live="polite">{{ voiceHint }}</p>
        <meter v-if="isVoiceActive" class="microphone-level" min="0" max="1" :value="microphoneLevel ?? 0" :aria-label="copy.microphoneLevel"></meter>
      </div>
      <div class="chat-header-tools">
        <div class="voice-mode-chip" :class="{ active: isHandsFree }">
          <Waveform :size="17" weight="duotone" aria-hidden="true" />
          <span>{{ isHandsFree ? 'AUTO' : 'MIC' }}</span>
        </div>
        <span class="voice-mode-chip" lang="vi">Tiếng Việt</span>
        <button
          type="button"
          class="header-cart-button"
          aria-label="Xem tất cả sản phẩm theo danh mục"
          @click="emit('open-catalog')"
        >
          <SquaresFour :size="15" weight="duotone" aria-hidden="true" />
          <span>Sản phẩm</span>
        </button>
        <button
          type="button"
          class="header-cart-button"
          :class="{ 'has-items': (assistedTotalCount ?? 0) > 0 }"
          :aria-label="(assistedTotalCount ?? 0) > 0 ? `Mở giỏ hàng (${assistedTotalCount} sản phẩm)` : 'Mở giỏ hàng'"
          @click="emit('open-cart', (assistedTotalCount ?? 0) === 0 && (assistedOrdersCount ?? 0) > 0 ? 'orders' : 'cart')"
        >
          <ShoppingBagOpen :size="15" weight="duotone" aria-hidden="true" />
          <span>Giỏ hàng</span>
          <span v-if="(assistedTotalCount ?? 0) > 0" class="header-cart-badge">{{ assistedTotalCount }}</span>
          <span v-else-if="(assistedOrdersCount ?? 0) > 0" class="header-cart-badge is-orders">{{ assistedOrdersCount }}</span>
        </button>
        <button type="button" class="end-session-button" @click="emit('end-session')">
          {{ shoppingText.end }}
        </button>
        <button type="button" class="support-request-button" @click="emit('request-support')">
          {{ supportButtonLabel }}
        </button>
      </div>
      <label v-if="microphoneDevices?.length" class="microphone-picker">
        <span>{{ copy.microphone }}</span>
        <select :value="selectedMicrophone || ''" :disabled="['thinking', 'transcribing', 'speaking'].includes(status)" :aria-label="copy.microphoneAriaLabel" @change="emit('change-microphone', ($event.target as HTMLSelectElement).value)">
          <option value="">{{ activeMicrophone || copy.defaultMicrophone }}</option>
          <option v-for="device in microphoneDevices" :key="device.id" :value="device.id">{{ device.label }}</option>
        </select>
        <small v-if="activeMicrophone" class="active-microphone-label">{{ copy.activeMicrophone }}: {{ activeMicrophone }}</small>
      </label>
    </header>

    <div ref="conversation" class="conversation" aria-live="polite" aria-label="Nội dung hội thoại">
      <div v-if="messages.length === 0" class="conversation-empty">
        <div class="empty-icon"><Sparkle :size="22" weight="duotone" aria-hidden="true" /></div>
        <h2>{{ copy.emptyTitle }}</h2>
        <p>{{ copy.emptyBody }}</p>
      </div>

      <article
        v-for="message in messages"
        :key="message.id"
        class="message-row"
        :class="`message-${message.role}`"
      >
        <div class="message-meta">
          <span class="message-author">{{ message.role === 'assistant' ? 'Mai' : copy.user }}</span>
        </div>
        <img
          v-if="message.imagePreviewUrl"
          :src="message.imagePreviewUrl"
          class="message-image-preview"
          alt="Ảnh sản phẩm đã gửi"
        />
        <p v-if="message.text" class="message-bubble">{{ message.text }}</p>
        <div v-else-if="message.role === 'assistant' && status === 'thinking'" class="message-bubble typing-bubble" role="status">
          <span>{{ copy.thinking }}</span>
          <span class="typing-dots" aria-hidden="true"><i></i><i></i><i></i></span>
        </div>
        <div
          v-if="message.supportTicket"
          class="support-ticket-card"
          :class="{
            'is-delivered': message.supportTicket.notification_status === 'DELIVERED',
            'is-failed': message.supportTicket.notification_status === 'FAILED',
          }"
          role="status"
        >
          <strong>{{ supportTicketTitle(message.supportTicket) }}</strong>
          <small>{{ supportTicketSubnote(message.supportTicket) }}</small>
        </div>
        <div
          v-if="message.assistedOrder"
          class="assisted-order-chat-card"
          :class="{ 'is-delivered': message.assistedOrder.payment_status === 'PAID' || message.assistedOrder.notification_status === 'DELIVERED' }"
          role="status"
        >
          <div class="assisted-order-chat-head">
            <div class="assisted-order-chat-title">
              <CheckCircle :size="18" weight="fill" aria-hidden="true" />
              <strong>{{ message.assistedOrder.payment_status === 'PAID' ? 'Đơn đã thanh toán' : 'Đơn hỗ trợ' }} #{{ message.assistedOrder.order_code }}</strong>
            </div>
            <span class="assisted-order-chat-pill">
              <template v-if="message.assistedOrder.payment_status === 'PAID'">
                Đã TT PayOS{{ message.assistedOrder.notification_status === 'DELIVERED' && message.assistedOrder.notified_channel ? ` · Đã báo ${channelDisplayName(message.assistedOrder.notified_channel)}` : '' }}
              </template>
              <template v-else-if="message.assistedOrder.notification_status === 'DELIVERED' && message.assistedOrder.notified_channel">
                Đã báo {{ channelDisplayName(message.assistedOrder.notified_channel) }}
              </template>
              <template v-else>Phiếu tại quầy</template>
            </span>
          </div>
          <p class="assisted-order-chat-meta">
            {{ message.assistedOrder.item_count }} sản phẩm · {{ message.assistedOrder.payment_status === 'PAID' ? 'Đã thanh toán' : 'Tạm tính' }} {{ formatVnd(message.assistedOrder.total_vnd) }}
            <template v-if="message.assistedOrder.pickup_location"> · {{ message.assistedOrder.pickup_location }}</template>
          </p>
          <button type="button" class="assisted-order-chat-btn" @click="emit('open-cart', 'orders')">
            <Receipt :size="15" weight="duotone" aria-hidden="true" />
            <span>Xem chi tiết phiếu chọn hàng</span>
          </button>
        </div>
        <section v-if="message.shoppingPlan" class="shopping-plan-card" :aria-label="language === 'en' ? 'Suggested shopping plan' : language === 'zh' ? '购物方案建议' : 'Phương án mua sắm gợi ý'">
          <article v-for="option in message.shoppingPlan.options" :key="option.option_id" class="shopping-plan-option">
            <header class="shopping-plan-option-header">
              <strong>{{ option.label }}</strong>
              <strong>{{ formatVnd(option.total_vnd) }}</strong>
            </header>
            <div v-for="item in option.items" :key="`${option.option_id}-${item.need_id}-${item.product_id}`" class="shopping-plan-item">
              <div class="shopping-plan-item-heading">
                <strong>{{ item.need_label }}</strong>
                <span>{{ item.source === 'EXPLICIT' ? (language === 'en' ? 'Requested' : language === 'zh' ? '已说明' : 'Bạn đã nêu') : (language === 'en' ? 'Optional idea' : language === 'zh' ? '可选建议' : 'Gợi ý thêm') }}</span>
              </div>
              <span>{{ item.name }} × {{ item.quantity }} · {{ formatVnd(item.line_total_vnd) }}</span>
              <small v-if="item.rationale">{{ item.rationale }}</small>
              <small>{{ item.stock_is_current ? (language === 'en' ? 'Stock checked by the active source' : language === 'zh' ? '已由当前数据源核实库存' : 'Tồn kho đã được nguồn dữ liệu hiện tại kiểm tra') : (language === 'en' ? 'Current stock is not verified' : language === 'zh' ? '当前库存尚未核实' : 'Tồn kho hiện tại chưa được xác minh') }}</small>
            </div>
            <p v-if="option.remaining_budget_vnd !== null" class="shopping-plan-remaining">
              {{ language === 'en' ? 'Remaining budget' : language === 'zh' ? '剩余预算' : 'Còn lại' }}: {{ formatVnd(option.remaining_budget_vnd) }}
            </p>
          </article>
          <p v-if="message.shoppingPlan.missing_required_needs.length" class="shopping-plan-note">
            {{ language === 'en' ? 'Could not verify' : language === 'zh' ? '未能核实' : 'Chưa xác minh được' }}: {{ message.shoppingPlan.missing_required_needs.join(', ') }}
          </p>
          <p v-if="message.shoppingPlan.optional_needs_not_selected.length" class="shopping-plan-note">
            {{ language === 'en' ? 'Optional needs not included' : language === 'zh' ? '未纳入的可选需求' : 'Chưa đưa vào phương án' }}: {{ message.shoppingPlan.optional_needs_not_selected.join(', ') }}
          </p>
          <p v-if="message.shoppingPlan.budget_shortfall_vnd" class="shopping-plan-note">
            {{ language === 'en' ? 'Minimum budget shortfall' : language === 'zh' ? '最低预算缺口' : 'Ngân sách tối thiểu còn thiếu' }}: {{ formatVnd(message.shoppingPlan.budget_shortfall_vnd) }}
          </p>
          <small v-if="!message.shoppingPlan.inventory_verified" class="shopping-plan-provenance">
            {{ language === 'en' ? 'Prices use the listed catalog snapshot; current store stock is not verified.' : language === 'zh' ? '价格来自目录快照；门店当前库存尚未核实。' : 'Giá lấy từ ảnh chụp catalog; tồn kho thực tế tại cửa hàng chưa được xác minh.' }}
          </small>
          <small v-if="!message.shoppingPlan.cart_mutated" class="shopping-plan-provenance">
            {{ language === 'en' ? 'This is a proposal and has not been added to your cart.' : language === 'zh' ? '这是购物建议，尚未加入购物车。' : 'Đây là phương án tư vấn, chưa được thêm vào giỏ hàng.' }}
          </small>
        </section>
        <div v-if="message.products?.length && !message.shoppingPlan" class="message-product-list">
          <article
            v-for="product in message.products"
            :key="product.product_id"
            class="message-product-card is-clickable"
            role="button"
            tabindex="0"
            :aria-label="`${language === 'en' ? 'View photo of' : language === 'zh' ? '查看商品图片' : 'Xem ảnh sản phẩm'} ${formatProductName(product.name)}`"
            @click="openProductModal(product)"
            @keydown.enter.self="openProductModal(product)"
            @keydown.space.self.prevent="openProductModal(product)"
          >
            <div class="product-card-thumb" aria-hidden="true">
              <img
                v-if="hasValidProductImage(product)"
                :src="product.image_url!"
                :alt="formatProductName(product.name)"
                loading="lazy"
                decoding="async"
                @error="onProductImageError(product.product_id)"
              />
              <ImageIcon v-else :size="20" weight="duotone" />
            </div>
            <div class="message-product-copy">
              <strong>{{ formatProductName(product.name) }}</strong>
              <small v-if="product.sales_data_source && product.demo_units_sold_30d" class="product-provenance">
                {{ productSalesLabel(product) }}:
                {{ product.demo_units_sold_30d.toLocaleString('vi-VN') }} / 30 {{ language === 'en' ? 'days' : language === 'zh' ? '天' : 'ngày' }}
              </small>
              <span>{{ productPriceLabel(product) }}<template v-if="product.stock_is_current !== false"> · {{ productStockLabel(product) }}</template></span>
              <details class="product-provenance" @click.stop>
                <summary>{{ language === 'en' ? 'Price and availability' : language === 'zh' ? '价格与库存说明' : 'Thông tin giá và hàng' }}</summary>
                <small v-if="product.stock_is_current === false">{{ productStockLabel(product) }}</small>
                <small>{{ productProvenance(product) }}</small>
              </details>
            </div>
            <button v-if="product.data_source === 'demo_product_catalog'" type="button" class="small-action-button" @click.stop="emit('add-product', product.product_id)">
              <ShoppingCartIcon :size="16" weight="duotone" aria-hidden="true" />
              {{ shoppingText.add }}
            </button>
            <div v-else-if="assistedQty(product.product_id) > 0" class="card-cart-stepper" @click.stop>
              <button
                type="button"
                class="card-stepper-btn"
                :aria-label="`Giảm số lượng ${formatProductName(product.name)}`"
                @click="emit('update-assisted-qty', product.product_id, assistedQty(product.product_id) - 1)"
              >
                <Minus :size="13" weight="bold" aria-hidden="true" />
              </button>
              <span class="card-stepper-qty">{{ assistedQty(product.product_id) }}</span>
              <button
                type="button"
                class="card-stepper-btn"
                :disabled="assistedQty(product.product_id) >= 20"
                :aria-label="`Tăng số lượng ${formatProductName(product.name)}`"
                @click="emit('update-assisted-qty', product.product_id, assistedQty(product.product_id) + 1)"
              >
                <Plus :size="13" weight="bold" aria-hidden="true" />
              </button>
            </div>
            <button
              v-else
              type="button"
              class="card-cart-btn"
              :aria-label="`Thêm ${formatProductName(product.name)} vào giỏ hàng`"
              @click.stop="emit('add-assisted-item', product)"
            >
              <Plus :size="14" weight="bold" aria-hidden="true" />
              <span>Giỏ</span>
            </button>
          </article>
        </div>
        <div v-if="message.relatedProducts?.length && !message.shoppingPlan" class="message-related-products">
          <div class="related-products-heading">
            {{
              language === 'en'
                ? 'Other matching items in catalog'
                : language === 'zh'
                  ? '目录中其他同类商品'
                  : 'Sản phẩm cùng loại khác trong catalog'
            }}
          </div>
          <div class="message-product-list related-product-list">
            <article
              v-for="product in message.relatedProducts"
              :key="product.product_id"
              class="message-product-card related-product-card is-clickable"
              role="button"
              tabindex="0"
              :aria-label="`${language === 'en' ? 'View photo of' : language === 'zh' ? '查看商品图片' : 'Xem ảnh sản phẩm'} ${formatProductName(product.name)}`"
              @click="openProductModal(product)"
              @keydown.enter.self="openProductModal(product)"
              @keydown.space.self.prevent="openProductModal(product)"
            >
              <div class="product-card-thumb" aria-hidden="true">
                <img
                  v-if="hasValidProductImage(product)"
                  :src="product.image_url!"
                  :alt="formatProductName(product.name)"
                  loading="lazy"
                  decoding="async"
                  @error="onProductImageError(product.product_id)"
                />
                <ImageIcon v-else :size="18" weight="duotone" />
              </div>
              <div class="message-product-copy">
                <strong>{{ formatProductName(product.name) }}</strong>
                <small v-if="product.sales_data_source && product.demo_units_sold_30d" class="product-provenance">
                  {{ productSalesLabel(product) }}:
                  {{ product.demo_units_sold_30d.toLocaleString('vi-VN') }} / 30 {{ language === 'en' ? 'days' : language === 'zh' ? '天' : 'ngày' }}
                </small>
                <span>{{ productPriceLabel(product) }}<template v-if="product.stock_is_current !== false"> · {{ productStockLabel(product) }}</template></span>
                <details class="product-provenance" @click.stop>
                  <summary>{{ language === 'en' ? 'Price and availability' : language === 'zh' ? '价格与库存说明' : 'Thông tin giá và hàng' }}</summary>
                  <small v-if="product.stock_is_current === false">{{ productStockLabel(product) }}</small>
                  <small>{{ productProvenance(product) }}</small>
                </details>
              </div>
              <button v-if="product.data_source === 'demo_product_catalog'" type="button" class="small-action-button" @click.stop="emit('add-product', product.product_id)">
                <ShoppingCartIcon :size="16" weight="duotone" aria-hidden="true" />
                {{ shoppingText.add }}
              </button>
              <div v-else-if="assistedQty(product.product_id) > 0" class="card-cart-stepper" @click.stop>
                <button
                  type="button"
                  class="card-stepper-btn"
                  :aria-label="`Giảm số lượng ${formatProductName(product.name)}`"
                  @click="emit('update-assisted-qty', product.product_id, assistedQty(product.product_id) - 1)"
                >
                  <Minus :size="13" weight="bold" aria-hidden="true" />
                </button>
                <span class="card-stepper-qty">{{ assistedQty(product.product_id) }}</span>
                <button
                  type="button"
                  class="card-stepper-btn"
                  :disabled="assistedQty(product.product_id) >= 20"
                  :aria-label="`Tăng số lượng ${formatProductName(product.name)}`"
                  @click="emit('update-assisted-qty', product.product_id, assistedQty(product.product_id) + 1)"
                >
                  <Plus :size="13" weight="bold" aria-hidden="true" />
                </button>
              </div>
              <button
                v-else
                type="button"
                class="card-cart-btn"
                :aria-label="`Thêm ${formatProductName(product.name)} vào giỏ hàng`"
                @click.stop="emit('add-assisted-item', product)"
              >
                <Plus :size="14" weight="bold" aria-hidden="true" />
                <span>Giỏ</span>
              </button>
            </article>
          </div>
        </div>
      </article>

      <div v-if="status === 'transcribing' || (status === 'thinking' && messages[messages.length - 1]?.role !== 'assistant')" class="message-row message-assistant">
        <div class="message-meta"><span class="message-author">Mai</span></div>
        <div class="message-bubble typing-bubble" role="status">
          <span>{{ status === 'transcribing' ? copy.transcribing : copy.thinking }}</span>
          <span class="typing-dots" aria-hidden="true"><i></i><i></i><i></i></span>
        </div>
      </div>
    </div>

    <section
      v-if="(visibleCart?.items.length ?? 0) > 0 || visibleOrder || checkoutQuote"
      class="shopping-panel"
      aria-live="polite"
    >
      <header class="shopping-panel-header">
        <div class="shopping-panel-title">
          <ShoppingCartIcon :size="17" weight="duotone" aria-hidden="true" />
          <strong>{{ visibleOrder ? shoppingText.order : shoppingText.cart }}</strong>
        </div>
        <span class="sandbox-chip">{{ shoppingText.sandbox }}</span>
      </header>

      <div v-if="visibleCart && !visibleOrder" class="shopping-cart-lines">
        <div v-for="item in visibleCart.items" :key="item.product_id" class="shopping-cart-line">
          <div class="shopping-cart-line-copy">
            <strong>{{ item.name }}</strong>
            <span>{{ formatVnd(item.unit_price_vnd) }} × {{ item.quantity }} = {{ formatVnd(item.line_total_vnd) }}</span>
          </div>
          <div v-if="!checkoutQuote" class="quantity-actions">
            <button type="button" :aria-label="`Reduce ${item.name}`" @click="emit('set-cart-quantity', item.product_id, Math.max(0, item.quantity - 1))">
              <Minus :size="15" aria-hidden="true" />
            </button>
            <button type="button" :aria-label="`Add one ${item.name}`" @click="emit('set-cart-quantity', item.product_id, item.quantity + 1)">
              <Plus :size="15" aria-hidden="true" />
            </button>
            <button type="button" :aria-label="`Remove ${item.name}`" @click="emit('set-cart-quantity', item.product_id, 0)">
              <Trash :size="15" aria-hidden="true" />
            </button>
          </div>
        </div>
        <div class="shopping-total">
          <span>{{ shoppingText.total }}</span>
          <strong>{{ formatVnd(visibleCart.total_vnd) }}</strong>
        </div>
      </div>

      <div v-if="visibleOrder" class="sandbox-order-status">
        <span>{{ visibleOrder.order_id }}</span>
        <strong>{{ visibleOrder.payment_status }}</strong>
        <span>{{ formatVnd(visibleOrder.amount_vnd) }}</span>
      </div>

      <div class="shopping-actions">
        <template v-if="checkoutQuote">
          <button type="button" class="small-action-button primary" @click="emit('confirm-checkout')">
            {{ shoppingText.confirm }}
          </button>
          <button type="button" class="small-action-button" @click="emit('cancel-checkout')">
            {{ shoppingText.cancel }}
          </button>
        </template>
        <button
          v-else-if="visibleCart && !visibleOrder && visibleCart.items.length"
          type="button"
          class="small-action-button primary"
          @click="emit('prepare-checkout')"
        >
          {{ shoppingText.checkout }}
        </button>
        <template v-if="visibleOrder?.payment_status === 'PENDING'">
          <button type="button" class="small-action-button primary" @click="emit('simulate-payment', 'PAID')">
            {{ shoppingText.simulatePaid }}
          </button>
          <button type="button" class="small-action-button" @click="emit('simulate-payment', 'FAILED')">
            {{ shoppingText.simulateFailed }}
          </button>
        </template>
        <button
          v-else-if="visibleOrder && visibleOrder.payment_status !== 'PAID'"
          type="button"
          class="small-action-button"
          @click="emit('clear-order')"
        >
          {{ shoppingText.cancel }}
        </button>
      </div>
      <p class="sandbox-note">{{ language === 'en' ? 'Demo only. No real payment is made.' : language === 'zh' ? '仅供演示，不会进行真实付款。' : 'Chỉ dùng để demo, không phát sinh thanh toán thật.' }}</p>
    </section>

    <div
      v-if="(assistedTotalCount ?? 0) > 0 && !visibleOrder && !checkoutQuote && !(visibleCart?.items.length)"
      class="assisted-cart-bar"
      role="region"
      aria-label="Tóm tắt giỏ hàng hiện tại"
    >
      <button type="button" class="assisted-cart-bar-btn" @click="emit('open-cart', 'cart')">
        <span class="assisted-cart-bar-left">
          <span class="assisted-cart-bar-icon">
            <ShoppingBagOpen :size="18" weight="duotone" aria-hidden="true" />
            <span class="assisted-cart-bar-count">{{ assistedTotalCount }}</span>
          </span>
          <span class="assisted-cart-bar-copy">
            <strong>{{ assistedTotalCount }} sản phẩm trong giỏ</strong>
            <small>Tạm tính {{ formatVnd(assistedTotalVnd ?? 0) }}</small>
          </span>
        </span>
        <span class="assisted-cart-bar-cta">
          <span>Xem giỏ &amp; Mua</span>
          <CaretRight :size="15" weight="bold" aria-hidden="true" />
        </span>
      </button>
    </div>

    <div v-if="errorMessage" class="chat-error" role="alert">
      <div>
        <strong>{{ copy.errorTitle }}</strong>
        <span>{{ errorMessage }}</span>
      </div>
      <button type="button" class="icon-button error-retry" :aria-label="copy.retry" @click="emit('retry')">
        <ArrowClockwise :size="18" weight="bold" aria-hidden="true" />
      </button>
    </div>

    <div v-if="messages.length === 0" class="quick-prompts" :aria-label="copy.quickPromptsLabel">
      <button
        v-for="prompt in quickPrompts"
        :key="prompt.label"
        type="button"
        class="quick-prompt"
        :data-quick-prompt="prompt.label"
        @click="emit('quick-prompt', prompt.label)"
      >
        <component :is="prompt.icon" :size="16" weight="duotone" aria-hidden="true" />
        <span>{{ prompt.label }}</span>
      </button>
    </div>

    <div v-if="isProcessingMedia" class="composer-attachment" role="status">
      <div class="composer-attachment-copy">
        <strong>Đang xử lý ảnh / trích khung hình rõ nhất từ video...</strong>
        <span>Đang tối ưu nhẹ ngay trên máy (~30 KB) để gửi cực nhanh</span>
      </div>
    </div>

    <div v-else-if="mediaProcessError" class="chat-error" role="alert">
      <div>
        <strong>Chưa xử lý được tệp</strong>
        <span>{{ mediaProcessError }}</span>
      </div>
    </div>

    <div v-if="pendingImageFile" class="composer-attachment" role="status">
      <img
        v-if="pendingImagePreview && !isVideoBlob(pendingImageFile)"
        :src="pendingImagePreview"
        class="composer-attachment-thumb"
        alt="Xem trước ảnh sản phẩm"
      />
      <div class="composer-attachment-copy">
        <strong>{{ pendingMediaTitle }}</strong>
        <span>{{ pendingMediaHint }}</span>
      </div>
      <button
        type="button"
        class="composer-attachment-remove"
        aria-label="Xóa ảnh đính kèm"
        @click="clearPendingImage(true)"
      >
        <CloseIcon :size="16" weight="bold" aria-hidden="true" />
      </button>
    </div>

    <input
      ref="fileInput"
      type="file"
      accept="image/*,video/*"
      class="sr-only"
      aria-label="Chọn ảnh hoặc video sản phẩm từ thiết bị"
      @change="onFileSelected"
    />
    <input
      ref="nativeCameraInput"
      type="file"
      accept="image/*"
      capture="environment"
      class="sr-only"
      aria-label="Chụp ảnh trực tiếp bằng camera điện thoại"
      @change="onFileSelected"
    />

    <form class="composer" @submit.prevent="submitComposer">
      <button
        type="button"
        class="camera-button"
        :class="{ 'has-image': Boolean(pendingImageFile) }"
        aria-label="Chụp hoặc chọn ảnh sản phẩm"
        :disabled="status === 'thinking' || status === 'transcribing'"
        @click="openCameraModal"
      >
        <Camera :size="21" weight="duotone" aria-hidden="true" />
      </button>
      <label class="sr-only" for="assistant-input">{{ copy.inputLabel }}</label>
      <textarea
        id="assistant-input"
        :value="draft"
        rows="1"
        maxlength="2000"
        :placeholder="pendingImageFile ? 'Hỏi về ảnh này (hoặc bấm Gửi luôn)...' : copy.placeholder"
        @input="emit('update:draft', ($event.target as HTMLTextAreaElement).value)"
        @keydown="onKeydown"
        @paste="onComposerPaste"
      ></textarea>
      <div class="composer-actions">
        <button
          type="button"
          class="mic-button"
          :class="{ recording: isRecording }"
          :aria-label="isVoiceActive || isHandsFree ? copy.voiceStop : copy.micStart"
          :disabled="!isHandsFree && (status === 'transcribing' || status === 'thinking')"
          @click="emit('toggle-recording')"
        >
          <Stop v-if="isRecording || isVoiceActive" :size="21" weight="fill" aria-hidden="true" />
          <Microphone v-else :size="21" weight="duotone" aria-hidden="true" />
        </button>
        <button
          v-if="status === 'speaking'"
          type="button"
          class="stop-speaking"
          :aria-label="copy.stopSpeaking"
          @click="emit('stop-speaking')"
        >
          <Pause :size="18" weight="fill" aria-hidden="true" />
        </button>
        <button
          type="submit"
          class="send-button"
          :aria-label="copy.sendLabel"
          :disabled="(!draft.trim() && !pendingImageFile) || status === 'thinking' || status === 'transcribing'"
        >
          <ArrowUp :size="20" weight="bold" aria-hidden="true" />
        </button>
      </div>
    </form>
    <p class="composer-hint">{{ copy.hint }} <span>•</span> {{ copy.hintSecond }}</p>

    <div v-if="isCameraOpen" class="camera-modal" role="dialog" aria-modal="true" aria-label="Chụp ảnh sản phẩm">
      <div class="camera-modal-card">
        <header class="camera-modal-header">
          <div>
            <strong>Quét &amp; nhận diện sản phẩm</strong>
            <span>Đưa mặt trước bao bì rõ chữ vào khung hình</span>
          </div>
          <button type="button" class="camera-modal-close" aria-label="Đóng camera" @click="closeCameraModal">
            <CloseIcon :size="18" weight="bold" aria-hidden="true" />
          </button>
        </header>

        <div class="camera-viewfinder">
          <video v-show="!cameraError" ref="cameraVideo" autoplay playsinline muted class="camera-video"></video>
          <div v-if="!cameraError" class="camera-reticle" aria-hidden="true"></div>
          <div v-if="isCameraStarting" class="camera-status-overlay">Đang mở camera...</div>
          <div v-if="cameraError" class="camera-error-box">
            <ImageIcon :size="32" weight="duotone" aria-hidden="true" />
            <p>{{ cameraError }}</p>
          </div>
        </div>

        <footer class="camera-modal-actions">
          <button type="button" class="camera-gallery-btn" @click="triggerFilePicker">
            <ImageIcon :size="18" weight="duotone" aria-hidden="true" />
            <span>Chọn ảnh / video</span>
          </button>
          <button
            v-if="!cameraError"
            type="button"
            class="camera-shutter-btn"
            :disabled="isCameraStarting"
            @click="captureCameraFrame"
          >
            <Camera :size="20" weight="fill" aria-hidden="true" />
            <span>Chụp ảnh</span>
          </button>
          <button
            v-else
            type="button"
            class="camera-shutter-btn"
            @click="triggerNativeCamera"
          >
            <Camera :size="20" weight="fill" aria-hidden="true" />
            <span>Camera máy</span>
          </button>
        </footer>
      </div>
    </div>

    <Teleport to="body">
      <div
        v-if="activeProductModal"
        class="product-modal"
        role="dialog"
        aria-modal="true"
        :aria-label="formatProductName(activeProductModal.name)"
        @click.self="closeProductModal"
      >
        <div class="product-modal-card">
          <header class="product-modal-header">
            <div class="product-modal-badges">
              <span v-if="activeProductModal.brand" class="product-modal-chip">{{ activeProductModal.brand }}</span>
              <span v-if="activeProductModal.category" class="product-modal-chip is-muted">{{ activeProductModal.category }}</span>
            </div>
            <button
              type="button"
              class="product-modal-close"
              :aria-label="language === 'en' ? 'Close product photo' : language === 'zh' ? '关闭商品图片' : 'Đóng ảnh sản phẩm'"
              @click="closeProductModal"
            >
              <CloseIcon :size="18" weight="bold" aria-hidden="true" />
            </button>
          </header>

          <div class="product-modal-media">
            <img
              v-if="hasValidProductImage(activeProductModal)"
              :src="activeProductModal.image_url!"
              :alt="formatProductName(activeProductModal.name)"
              class="product-modal-image"
              @error="onProductImageError(activeProductModal.product_id)"
            />
            <div v-else class="product-modal-placeholder">
              <ImageIcon :size="44" weight="duotone" aria-hidden="true" />
              <span>{{ language === 'en' ? 'Product photo is not available in catalog' : language === 'zh' ? '目录暂无该商品图片' : 'Sản phẩm chưa có ảnh trong catalog' }}</span>
            </div>
          </div>

          <div class="product-modal-body">
            <h2 class="product-modal-title">{{ formatProductName(activeProductModal.name) }}</h2>
            <div class="product-modal-pricing">
              <strong class="product-modal-price">{{ formatVnd(activeProductModal.price_vnd) }}</strong>
              <span v-if="activeProductModal.list_price_vnd && activeProductModal.list_price_vnd > activeProductModal.price_vnd" class="product-modal-list-price">
                {{ formatVnd(activeProductModal.list_price_vnd) }}
              </span>
              <span v-if="activeProductModal.uom" class="product-modal-uom">/ {{ activeProductModal.uom }}</span>
            </div>
            <p v-if="activeProductModal.promotion" class="product-modal-promo">{{ activeProductModal.promotion }}</p>
            <div class="product-modal-meta">
              <span v-if="activeProductModal.sales_data_source && activeProductModal.demo_units_sold_30d">
                {{ productSalesLabel(activeProductModal) }}:
                {{ activeProductModal.demo_units_sold_30d.toLocaleString('vi-VN') }} / 30 {{ language === 'en' ? 'days' : language === 'zh' ? '天' : 'ngày' }}
              </span>
              <span>{{ productProvenance(activeProductModal) }}</span>
            </div>
          </div>

          <footer class="product-modal-actions">
            <button
              v-if="activeProductModal.data_source !== 'demo_product_catalog'"
              type="button"
              class="product-modal-cart-btn"
              @click="emit('add-assisted-item', activeProductModal)"
            >
              <ShoppingBagOpen :size="17" weight="duotone" aria-hidden="true" />
              <span>
                {{
                  assistedQty(activeProductModal.product_id) > 0
                    ? `Trong giỏ: ${assistedQty(activeProductModal.product_id)} (+1)`
                    : 'Thêm vào giỏ'
                }}
              </span>
            </button>
            <button
              type="button"
              class="product-modal-ask-btn"
              @click="askProductIngredients(activeProductModal)"
            >
              <Sparkle :size="17" weight="duotone" aria-hidden="true" />
              <span>{{ language === 'en' ? 'Ask ingredients' : language === 'zh' ? '查看成分' : 'Hỏi thành phần' }}</span>
            </button>
            <button
              type="button"
              class="product-modal-dismiss-btn"
              @click="closeProductModal"
            >
              {{ language === 'en' ? 'Close' : language === 'zh' ? '关闭' : 'Đóng' }}
            </button>
          </footer>
        </div>
      </div>
    </Teleport>
  </section>
</template>
