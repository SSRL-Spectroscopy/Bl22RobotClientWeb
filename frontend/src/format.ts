export function formatNumber(value: number): string {
  const size = Math.abs(value)
  if (size >= 100) return value.toFixed(1)
  if (size >= 1) return value.toFixed(3)
  return value.toFixed(4)
}

export function formatClock(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString()
}
