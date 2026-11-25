/**
 * Mobile-first product photo & video preprocessing.
 *
 * Designed for QR-scanned smartphones on 4G/5G:
 * - Photos are resized & JPEG-compressed client-side (<= 640px, ~25-45 KB).
 * - Videos (.mp4/.mov/.webm) are NEVER uploaded raw over mobile networks;
 *   instead we sample up to 5 candidate frames locally, score sharpness and
 *   lighting on a tiny 96x96 canvas, and emit only the single sharpest 640px
 *   JPEG (~25-45 KB) in ~150-350 ms.
 */

export interface PreprocessedMedia {
  blob: Blob
  sourceKind: 'image' | 'video'
  originalBytes: number
  finalBytes: number
  width: number
  height: number
  elapsedMs: number
  framesSampled: number
  durationSeconds?: number
}

export interface PreprocessOptions {
  maxSide?: number
  quality?: number
  skipImageBelowBytes?: number
  videoSampleCount?: number
  videoMaxWindowSeconds?: number
}

const DEFAULT_MAX_SIDE = 640
const DEFAULT_QUALITY = 0.78
const DEFAULT_SKIP_IMAGE_BYTES = 90 * 1024
const DEFAULT_VIDEO_SAMPLES = 5
const DEFAULT_VIDEO_MAX_WINDOW_S = 12
const SCORE_CANVAS_SIZE = 96

const VIDEO_EXT_RE = /\.(?:mp4|mov|webm|m4v|3gp|3gpp|mkv|avi)$/i

export function isVideoBlob(blob: Blob): boolean {
  const mime = (blob.type || '').toLowerCase()
  if (mime.startsWith('video/')) return true
  if (blob instanceof File && VIDEO_EXT_RE.test(blob.name)) return true
  return false
}

export function formatKilobytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const kb = bytes / 1024
  if (kb < 1024) return `${Math.round(kb)} KB`
  return `${(kb / 1024).toFixed(1)} MB`
}

/**
 * Compute a fast perceptual sharpness + exposure score for a small RGBA frame.
 * Higher is sharper & better lit; dark/overexposed/flat frames score near 0.
 */
export function scoreFrameSharpness(
  rgba: Uint8ClampedArray | Uint8Array,
  width: number,
  height: number,
): number {
  if (width < 4 || height < 4 || rgba.length < width * height * 4) return 0
  const pixelCount = width * height
  const luma = new Float32Array(pixelCount)
  let sum = 0
  let sumSq = 0
  for (let i = 0, p = 0; i < pixelCount; i += 1, p += 4) {
    const y = 0.299 * rgba[p] + 0.587 * rgba[p + 1] + 0.114 * rgba[p + 2]
    luma[i] = y
    sum += y
    sumSq += y * y
  }
  const mean = sum / pixelCount
  const variance = Math.max(0, sumSq / pixelCount - mean * mean)
  const std = Math.sqrt(variance)

  // Reject covered-lens dark frames, blown-out white frames, or blank walls.
  if (mean < 20 || mean > 245 || std < 8) return 0.01 * std

  let gradSum = 0
  let gradCount = 0
  for (let y = 1; y < height - 1; y += 1) {
    const row = y * width
    for (let x = 1; x < width - 1; x += 1) {
      const idx = row + x
      const gx = Math.abs(luma[idx + 1] - luma[idx - 1])
      const gy = Math.abs(luma[idx + width] - luma[idx - width])
      gradSum += gx + gy
      gradCount += 1
    }
  }
  const meanGradient = gradCount > 0 ? gradSum / gradCount : 0
  // Prefer well-exposed frames (peak around mid-brightness 60..200).
  const exposurePenalty = mean < 45 ? mean / 45 : mean > 220 ? (255 - mean) / 35 : 1.0
  return meanGradient * Math.max(0.25, exposurePenalty)
}

function renderToJpegBlob(
  source: CanvasImageSource,
  srcWidth: number,
  srcHeight: number,
  maxSide: number,
  quality: number,
): Promise<{ blob: Blob | null; width: number; height: number }> {
  const longest = Math.max(srcWidth, srcHeight)
  if (!longest || typeof document === 'undefined') {
    return Promise.resolve({ blob: null, width: srcWidth, height: srcHeight })
  }
  const scale = Math.min(1, maxSide / longest)
  const width = Math.max(1, Math.round(srcWidth * scale))
  const height = Math.max(1, Math.round(srcHeight * scale))
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')
  if (!ctx) return Promise.resolve({ blob: null, width, height })
  ctx.drawImage(source, 0, 0, width, height)
  return new Promise((resolve) => {
    canvas.toBlob((blob) => resolve({ blob, width, height }), 'image/jpeg', quality)
  })
}

async function loadImageElement(blob: Blob): Promise<HTMLImageElement | null> {
  if (typeof Image === 'undefined' || typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') {
    return null
  }
  const url = URL.createObjectURL(blob)
  try {
    return await new Promise<HTMLImageElement>((resolve, reject) => {
      const img = new Image()
      img.onload = () => resolve(img)
      img.onerror = () => reject(new Error('Image decode failed'))
      img.src = url
    })
  } finally {
    try { URL.revokeObjectURL(url) } catch { /* ignore */ }
  }
}

export async function preprocessImageForMobile(
  blob: Blob,
  options: PreprocessOptions = {},
): Promise<PreprocessedMedia> {
  const started = typeof performance !== 'undefined' ? performance.now() : Date.now()
  const maxSide = options.maxSide ?? DEFAULT_MAX_SIDE
  const quality = options.quality ?? DEFAULT_QUALITY
  const skipBelow = options.skipImageBelowBytes ?? DEFAULT_SKIP_IMAGE_BYTES

  const finish = (outBlob: Blob, width: number, height: number): PreprocessedMedia => ({
    blob: outBlob,
    sourceKind: 'image',
    originalBytes: blob.size,
    finalBytes: outBlob.size,
    width,
    height,
    elapsedMs: Math.round((typeof performance !== 'undefined' ? performance.now() : Date.now()) - started),
    framesSampled: 1,
  })

  if (blob.size <= skipBelow || typeof document === 'undefined') {
    return finish(blob, 0, 0)
  }

  try {
    if (typeof createImageBitmap === 'function') {
      const bitmap = await createImageBitmap(blob)
      try {
        const rendered = await renderToJpegBlob(bitmap, bitmap.width, bitmap.height, maxSide, quality)
        if (rendered.blob && rendered.blob.size < blob.size) {
          return finish(rendered.blob, rendered.width, rendered.height)
        }
        return finish(blob, bitmap.width, bitmap.height)
      } finally {
        bitmap.close()
      }
    }
    const img = await loadImageElement(blob)
    if (img) {
      const rendered = await renderToJpegBlob(img, img.naturalWidth, img.naturalHeight, maxSide, quality)
      if (rendered.blob && rendered.blob.size < blob.size) {
        return finish(rendered.blob, rendered.width, rendered.height)
      }
      return finish(blob, img.naturalWidth, img.naturalHeight)
    }
  } catch {
    // Fall back to original blob if browser canvas decoding fails.
  }
  return finish(blob, 0, 0)
}

function waitForEvent(target: EventTarget, eventName: string, timeoutMs: number): Promise<void> {
  return new Promise((resolve, reject) => {
    let settled = false
    const onOk = () => {
      if (settled) return
      settled = true
      cleanup()
      resolve()
    }
    const onErr = () => {
      if (settled) return
      settled = true
      cleanup()
      reject(new Error(`Video ${eventName} error`))
    }
    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      cleanup()
      reject(new Error(`Video ${eventName} timeout`))
    }, timeoutMs)
    const cleanup = () => {
      clearTimeout(timer)
      target.removeEventListener(eventName, onOk)
      target.removeEventListener('error', onErr)
    }
    target.addEventListener(eventName, onOk, { once: true })
    target.addEventListener('error', onErr, { once: true })
  })
}

export async function extractBestFrameFromVideo(
  videoBlob: Blob,
  options: PreprocessOptions = {},
): Promise<PreprocessedMedia> {
  const started = typeof performance !== 'undefined' ? performance.now() : Date.now()
  const maxSide = options.maxSide ?? DEFAULT_MAX_SIDE
  const quality = options.quality ?? DEFAULT_QUALITY
  const sampleCount = Math.max(1, Math.min(options.videoSampleCount ?? DEFAULT_VIDEO_SAMPLES, 8))
  const maxWindow = options.videoMaxWindowSeconds ?? DEFAULT_VIDEO_MAX_WINDOW_S

  if (typeof document === 'undefined' || typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') {
    throw new Error('Trình duyệt hiện tại không hỗ trợ trích xuất khung hình từ video.')
  }

  const videoUrl = URL.createObjectURL(videoBlob)
  const video = document.createElement('video')
  video.muted = true
  video.playsInline = true
  video.preload = 'auto'
  video.crossOrigin = 'anonymous'
  video.src = videoUrl

  try {
    const metadataReady = waitForEvent(video, 'loadeddata', 7_000)
    video.load()
    await metadataReady

    const srcW = video.videoWidth
    const srcH = video.videoHeight
    if (!srcW || !srcH) {
      throw new Error('Không đọc được kích thước khung hình của video.')
    }

    const rawDuration = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : 0
    const windowSec = rawDuration > 0 ? Math.min(rawDuration, maxWindow) : 0
    const timestamps: number[] = []
    if (windowSec <= 0.25) {
      timestamps.push(0)
    } else {
      const fractions = sampleCount === 1
        ? [0.35]
        : Array.from({ length: sampleCount }, (_, i) => 0.12 + (0.76 * i) / (sampleCount - 1))
      for (const f of fractions) {
        timestamps.push(Number(Math.max(0.05, Math.min(windowSec - 0.05, windowSec * f)).toFixed(2)))
      }
    }

    // Small scoring canvas + full output canvas for the sharpest winning frame.
    const scoreCanvas = document.createElement('canvas')
    const scoreScale = Math.min(1, SCORE_CANVAS_SIZE / Math.max(srcW, srcH))
    scoreCanvas.width = Math.max(8, Math.round(srcW * scoreScale))
    scoreCanvas.height = Math.max(8, Math.round(srcH * scoreScale))
    const scoreCtx = scoreCanvas.getContext('2d', { willReadFrequently: true })

    const outScale = Math.min(1, maxSide / Math.max(srcW, srcH))
    const outW = Math.max(1, Math.round(srcW * outScale))
    const outH = Math.max(1, Math.round(srcH * outScale))
    const bestCanvas = document.createElement('canvas')
    bestCanvas.width = outW
    bestCanvas.height = outH
    const bestCtx = bestCanvas.getContext('2d')
    if (!bestCtx) {
      throw new Error('Không khởi tạo được bộ xử lý hình ảnh trên trình duyệt.')
    }

    let bestScore = -1
    let sampled = 0
    for (const ts of timestamps) {
      try {
        if (Math.abs((video.currentTime || 0) - ts) > 0.04) {
          const seekDone = waitForEvent(video, 'seeked', 2_000)
          video.currentTime = ts
          await seekDone
        }
        sampled += 1
        let score = 1
        if (scoreCtx) {
          scoreCtx.drawImage(video, 0, 0, scoreCanvas.width, scoreCanvas.height)
          const imgData = scoreCtx.getImageData(0, 0, scoreCanvas.width, scoreCanvas.height)
          score = scoreFrameSharpness(imgData.data, scoreCanvas.width, scoreCanvas.height)
        }
        if (score > bestScore) {
          bestScore = score
          bestCtx.drawImage(video, 0, 0, outW, outH)
        }
      } catch {
        // Ignore individual seek hiccups and keep best captured frame.
      }
    }

    if (sampled === 0) {
      bestCtx.drawImage(video, 0, 0, outW, outH)
      sampled = 1
    }

    const bestBlob = await new Promise<Blob | null>((resolve) =>
      bestCanvas.toBlob(resolve, 'image/jpeg', quality),
    )
    if (!bestBlob) {
      throw new Error('Không xuất được ảnh từ video đã chọn.')
    }

    return {
      blob: bestBlob,
      sourceKind: 'video',
      originalBytes: videoBlob.size,
      finalBytes: bestBlob.size,
      width: outW,
      height: outH,
      elapsedMs: Math.round((typeof performance !== 'undefined' ? performance.now() : Date.now()) - started),
      framesSampled: sampled,
      durationSeconds: rawDuration || undefined,
    }
  } finally {
    video.pause()
    video.removeAttribute('src')
    video.load()
    try { URL.revokeObjectURL(videoUrl) } catch { /* ignore */ }
  }
}

export async function preprocessProductMedia(
  file: Blob,
  options: PreprocessOptions = {},
): Promise<PreprocessedMedia> {
  if (isVideoBlob(file)) {
    return extractBestFrameFromVideo(file, options)
  }
  return preprocessImageForMobile(file, options)
}
