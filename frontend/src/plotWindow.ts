import { useEffect, useState } from 'react'

export const WINDOWS_MIN = [1, 2, 5]
const STORAGE_KEY = 'plotWindowMin'

function readStoredWindow(): number {
  try {
    const stored = Number(localStorage.getItem(STORAGE_KEY))
    if (WINDOWS_MIN.includes(stored)) return stored
  } catch {
    // storage unavailable; fall through to the default
  }
  return 2
}

/** The time window shared by the plots, remembered in this browser. */
export function usePlotWindow(): [number, (minutes: number) => void] {
  const [windowMin, setWindowMin] = useState(readStoredWindow)
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, String(windowMin))
    } catch {
      // not being able to remember the choice is harmless
    }
  }, [windowMin])
  return [windowMin, setWindowMin]
}
