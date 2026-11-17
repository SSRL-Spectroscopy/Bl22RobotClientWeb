// Who is using this browser: an Observer (not logged in) or an Operator.

import { useCallback, useEffect, useState } from 'react'
import { live } from './live'

export type Me = { user: string | null; role: 'observer' | 'operator' }

const OBSERVER: Me = { user: null, role: 'observer' }

/** POST JSON to the backend. Throws an Error carrying the backend's message. */
export function post(path: string, body: unknown = {}): Promise<unknown> {
  return call('POST', path, body)
}

/** Any request to the backend. Throws an Error carrying the backend's message. */
export async function call(method: string, path: string, body?: unknown): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(path, {
      method,
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new Error('The backend cannot be reached.')
  }
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = data && typeof data.detail === 'string' ? data.detail : `Request failed (${response.status}).`
    throw new Error(detail)
  }
  return data
}

export function useAuth() {
  const [me, setMe] = useState<Me>(OBSERVER)

  const refresh = useCallback(async () => {
    try {
      const response = await fetch('/api/me')
      if (response.ok) setMe(await response.json())
    } catch {
      // keep the last known identity; the page shows the lost connection
    }
  }, [])

  useEffect(() => {
    // The page asks once whenever the live connection opens (see App); this
    // repeats the check so an expired session is noticed.
    const timer = setInterval(refresh, 30000)
    return () => clearInterval(timer)
  }, [refresh])

  const login = useCallback(async (username: string, password: string) => {
    setMe((await post('/api/login', { username, password })) as Me)
    // The live connection tells the backend this operator has a page open,
    // so it has to be reopened with the new session.
    live.reconnect()
  }, [])

  const logout = useCallback(async () => {
    await post('/api/logout')
    setMe(OBSERVER)
    live.reconnect()
  }, [])

  return { me, login, logout, refresh }
}
