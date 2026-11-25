import { describe, expect, it } from 'vitest'

import {
  formatKilobytes,
  isVideoBlob,
  preprocessImageForMobile,
  scoreFrameSharpness,
} from './media-preprocess'

function makeSolidRgba(width: number, height: number, value: number): Uint8ClampedArray {
  const data = new Uint8ClampedArray(width * height * 4)
  for (let i = 0; i < data.length; i += 4) {
    data[i] = value
    data[i + 1] = value
    data[i + 2] = value
    data[i + 3] = 255
  }
  return data
}

function makeSharpStripesRgba(width: number, height: number): Uint8ClampedArray {
  const data = new Uint8ClampedArray(width * height * 4)
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const idx = (y * width + x) * 4
      const val = (x % 4 < 2) ? 210 : 40
      data[idx] = val
      data[idx + 1] = val
      data[idx + 2] = val
      data[idx + 3] = 255
    }
  }
  return data
}

function makeSoftGradientRgba(width: number, height: number): Uint8ClampedArray {
  const data = new Uint8ClampedArray(width * height * 4)
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const idx = (y * width + x) * 4
      const val = Math.round(90 + (x / width) * 60)
      data[idx] = val
      data[idx + 1] = val
      data[idx + 2] = val
      data[idx + 3] = 255
    }
  }
  return data
}

describe('media-preprocess', () => {
  it('detects video files by MIME type or mobile file extension', () => {
    expect(isVideoBlob(new Blob(['x'], { type: 'video/mp4' }))).toBe(true)
    expect(isVideoBlob(new File(['x'], 'clip.MOV', { type: '' }))).toBe(true)
    expect(isVideoBlob(new File(['x'], 'scan.webm', { type: '' }))).toBe(true)
    expect(isVideoBlob(new File(['x'], 'photo.jpg', { type: 'image/jpeg' }))).toBe(false)
  })

  it('scores sharp well-lit packaging far higher than dark, blank, or blurry frames', () => {
    const dark = scoreFrameSharpness(makeSolidRgba(32, 32, 8), 32, 32)
    const flatWall = scoreFrameSharpness(makeSolidRgba(32, 32, 140), 32, 32)
    const blurry = scoreFrameSharpness(makeSoftGradientRgba(32, 32), 32, 32)
    const sharp = scoreFrameSharpness(makeSharpStripesRgba(32, 32), 32, 32)

    expect(dark).toBeLessThan(1)
    expect(flatWall).toBeLessThan(1)
    expect(sharp).toBeGreaterThan(blurry * 10)
  })

  it('skips re-encoding small already-optimized images', async () => {
    const small = new Blob([new Uint8Array(12_000)], { type: 'image/jpeg' })
    const result = await preprocessImageForMobile(small)
    expect(result.sourceKind).toBe('image')
    expect(result.blob).toBe(small)
    expect(result.finalBytes).toBe(12_000)
  })

  it('formats byte sizes readably', () => {
    expect(formatKilobytes(512)).toBe('512 B')
    expect(formatKilobytes(34_816)).toBe('34 KB')
    expect(formatKilobytes(2_621_440)).toBe('2.5 MB')
  })
})
