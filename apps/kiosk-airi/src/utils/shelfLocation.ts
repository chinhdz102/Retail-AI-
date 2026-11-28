interface AisleInfo {
  zone: string
  bays: number
  hint: string
}

const AISLE_MAP: Record<string, AisleInfo> = {
  A01: { zone: 'Dệt may', bays: 1, hint: 'góc trong cùng bên trái, sát A02' },
  A02: { zone: 'Hóa mỹ phẩm', bays: 8, hint: 'hàng kệ sau bên trái, cạnh A01' },
  A03: { zone: 'Hóa mỹ phẩm', bays: 8, hint: 'hàng kệ sau giữa, giữa A02 và A04' },
  A04: { zone: 'Hóa mỹ phẩm', bays: 2, hint: 'hàng kệ sau bên phải, sát Homeline A05' },
  A05: { zone: 'Homeline (Đồ gia dụng)', bays: 7, hint: 'góc trong phía sau giữa-phải' },
  A06: { zone: 'Thực phẩm công nghệ', bays: 8, hint: 'khu kệ giữa ngoài cùng bên trái' },
  A07: { zone: 'Thực phẩm công nghệ', bays: 6, hint: 'khu kệ giữa, dãy thứ 2 từ trái sang' },
  A08: { zone: 'Thực phẩm công nghệ', bays: 6, hint: 'khu kệ giữa hơi lệch trái' },
  A09: { zone: 'Thực phẩm công nghệ', bays: 8, hint: 'chính giữa khu kệ trung tâm' },
  A10: { zone: 'Thực phẩm công nghệ', bays: 8, hint: 'khu kệ giữa hơi lệch phải' },
  A11: { zone: 'Thực phẩm công nghệ', bays: 8, hint: 'bên phải khu giữa, phía trên thu ngân' },
  A12: { zone: 'Thực phẩm công nghệ', bays: 6, hint: 'rìa phải khu giữa, trước tủ mát A13' },
  A13: { zone: 'Thực phẩm tươi sống, chế biến', bays: 7, hint: 'dãy tủ mát dọc sát vách phải' },
}

const LEVEL_MAP: Record<number, string> = {
  1: 'tầng 1 (dưới cùng)',
  2: 'tầng 2 (dưới tầm mắt)',
  3: 'tầng 3 (ngang tầm mắt)',
  4: 'tầng 4 (trên tầm mắt)',
  5: 'tầng 5 (trên cùng)',
}

const CODE_RE = /^A(\d{2})-B(\d{2})-L(\d{2})-P(\d{2})$/

export function formatShelfLocation(
  code: string | null | undefined,
  options?: { detailed?: boolean },
): string {
  const raw = (code ?? '').trim()
  if (!raw) return ''
  const match = CODE_RE.exec(raw)
  if (!match) return raw

  const aisle = `A${match[1]}`
  const info = AISLE_MAP[aisle]
  if (!info) return raw

  const bayNum = Number(match[2])
  const levelNum = Number(match[3])
  const slotNum = Number(match[4])
  const levelText = LEVEL_MAP[levelNum] ?? `tầng ${levelNum}`

  let bayPos = ''
  if (info.bays === 1) {
    bayPos = 'khoang 1'
  } else if (info.bays === 2) {
    bayPos = bayNum === 1 ? 'khoang 1/2 (đầu dãy)' : 'khoang 2/2 (cuối dãy)'
  } else if (bayNum <= 2) {
    bayPos = `khoang ${bayNum}/${info.bays} (đầu dãy)`
  } else if (bayNum >= info.bays - 1) {
    bayPos = `khoang ${bayNum}/${info.bays} (cuối dãy)`
  } else {
    bayPos = `khoang ${bayNum}/${info.bays} (giữa dãy)`
  }

  if (options?.detailed) {
    return `Khu ${info.zone} · Dãy ${aisle} (${info.hint}), ${bayPos}, ${levelText}, ô ${slotNum} · Mã ${raw}`
  }
  return `Khu ${info.zone} · Dãy ${aisle}, ${bayPos}, ${levelText} (${raw})`
}
