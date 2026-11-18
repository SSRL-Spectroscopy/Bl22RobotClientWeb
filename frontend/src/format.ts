export function formatNumber(value: number): string {
  const size = Math.abs(value)
  if (size >= 100) return value.toFixed(1)
  if (size >= 1) return value.toFixed(3)
  return value.toFixed(4)
}

export function formatClock(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString()
}

/** Date and time, with the date left out when it is today: "10:49:50 AM" or "11/17/2026, 10:49:50 AM". */
export function formatWhen(epochSeconds: number): string {
  const when = new Date(epochSeconds * 1000)
  const today = when.toDateString() === new Date().toDateString()
  return today ? when.toLocaleTimeString() : when.toLocaleString()
}
