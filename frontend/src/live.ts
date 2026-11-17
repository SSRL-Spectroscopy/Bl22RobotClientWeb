// Live state from the backend WebSocket: one snapshot, then batched updates.
// Current values are kept as an immutable object for React; plot history is
// kept in plain arrays that the plot reads directly.

import { useSyncExternalStore } from 'react'

export type SubsystemEntry = {
  connected: boolean
  state: string | null
  error: string | null
  failed: boolean
  ts: number
}

export type PvEntry = {
  connected: boolean
  value: number | string | null
  severity: number
  ts: number
}

export type ControlEntry = { connected: boolean; detail: string | null; ts: number }

export type HelmEntry = {
  holder: string | null
  request: { user: string; deadline: number } | null
  ts: number
}

export type Series = { t: number[]; v: number[] }

export type Link = 'connecting' | 'open' | 'closed'

export type LiveView = {
  link: Link
  state: Record<string, unknown>
}

type Message =
  | {
      type: 'snapshot'
      now: number
      window_s: number
      state: Record<string, unknown>
      history: Record<string, Series>
    }
  | {
      type: 'update'
      now: number
      changes: Record<string, unknown>
      samples: Record<string, Series>
    }

// The backend sends a heartbeat every 2 s; longer silence means a dead link.
const SILENCE_LIMIT_MS = 6000
const RECONNECT_MS = 2000

class LiveStore {
  private view: LiveView = { link: 'connecting', state: {} }
  private listeners = new Set<() => void>()
  private socket: WebSocket | null = null
  private lastMessageAt = 0
  private started = false

  /** Plot history per key, oldest first. Mutated in place. */
  readonly history = new Map<string, Series>()
  /** Seconds of history the backend keeps. */
  windowS = 300
  /** Server clock minus browser clock, in seconds. */
  clockOffsetS = 0

  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    this.start()
    return () => this.listeners.delete(listener)
  }

  getView = () => this.view

  /** Current time on the server's clock, in seconds. */
  serverNow() {
    return Date.now() / 1000 + this.clockOffsetS
  }

  /** Drop the connection and open a new one (after logging in or out). */
  reconnect() {
    const socket = this.socket
    if (!socket) return // already reconnecting
    this.socket = null
    socket.onclose = null
    socket.onmessage = null
    socket.close()
    this.connect()
  }

  private start() {
    if (this.started) return
    this.started = true
    this.connect()
    setInterval(() => {
      if (this.socket && this.view.link === 'open' && Date.now() - this.lastMessageAt > SILENCE_LIMIT_MS) {
        this.socket.close()
      }
    }, 1000)
  }

  private connect() {
    // Same host and scheme as the page, so this keeps working behind TLS.
    const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const socket = new WebSocket(`${scheme}//${window.location.host}/api/ws`)
    this.socket = socket
    socket.onmessage = (event) => {
      this.lastMessageAt = Date.now()
      this.apply(JSON.parse(event.data) as Message)
    }
    socket.onclose = () => {
      if (this.socket !== socket) return
      this.socket = null
      this.setView({ link: 'closed', state: this.view.state })
      setTimeout(() => this.connect(), RECONNECT_MS)
    }
    socket.onerror = () => socket.close()
  }

  private apply(message: Message) {
    this.clockOffsetS = message.now - Date.now() / 1000
    if (message.type === 'snapshot') {
      this.windowS = message.window_s
      this.history.clear()
      for (const [key, series] of Object.entries(message.history)) {
        this.history.set(key, { t: [...series.t], v: [...series.v] })
      }
      this.setView({ link: 'open', state: message.state })
      return
    }
    for (const [key, series] of Object.entries(message.samples)) {
      this.append(key, series)
    }
    if (Object.keys(message.changes).length > 0 || this.view.link !== 'open') {
      this.setView({ link: 'open', state: { ...this.view.state, ...message.changes } })
    }
  }

  private append(key: string, incoming: Series) {
    let series = this.history.get(key)
    if (!series) {
      series = { t: [], v: [] }
      this.history.set(key, series)
    }
    // A point taken just before the snapshot arrives again in the first
    // update, so only accept points newer than the last one held.
    let last = series.t.length ? series.t[series.t.length - 1] : -Infinity
    for (let i = 0; i < incoming.t.length; i++) {
      if (incoming.t[i] > last) {
        series.t.push(incoming.t[i])
        series.v.push(incoming.v[i])
        last = incoming.t[i]
      }
    }
    const cutoff = this.serverNow() - this.windowS
    let drop = 0
    while (drop < series.t.length && series.t[drop] < cutoff) drop++
    if (drop > 0) {
      series.t.splice(0, drop)
      series.v.splice(0, drop)
    }
  }

  private setView(view: LiveView) {
    this.view = view
    for (const listener of this.listeners) listener()
  }
}

export const live = new LiveStore()

export function useLive(): LiveView {
  return useSyncExternalStore(live.subscribe, live.getView)
}
