import { useEffect, useState } from 'react'
import { post } from './auth'
import type { Me } from './auth'
import { live } from './live'
import type { HelmEntry } from './live'

/** Whole seconds until a deadline given in server time; ticks once a second. */
function useCountdown(deadline: number | null): number {
  const [, setTick] = useState(0)
  useEffect(() => {
    if (deadline === null) return
    const timer = setInterval(() => setTick((n) => n + 1), 500)
    return () => clearInterval(timer)
  }, [deadline])
  return deadline === null ? 0 : Math.max(0, Math.ceil(deadline - live.serverNow()))
}

function useAct() {
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const act = async (path: string, body?: unknown) => {
    setBusy(true)
    setError('')
    try {
      await post(path, body)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return { act, busy, error }
}

type Props = { me: Me; helm: HelmEntry | undefined }

/** Who has control, and the button to take, request or release it. Sits in the top bar. */
export function HelmButton({ me, helm }: Props) {
  const { act, busy, error } = useAct()
  const holder = helm?.holder ?? null
  const request = helm?.request ?? null
  const iHold = me.user !== null && holder === me.user
  const iWait = me.user !== null && request?.user === me.user

  let status: string
  if (holder === null) status = 'Nobody has control'
  else if (iHold) status = 'You have control'
  else status = `${holder} has control`

  return (
    <div className="helm-top" role="group" aria-label="Control">
      <strong className={iHold ? 'helm-mine' : ''}>{status}</strong>
      {me.user !== null && !iHold && !iWait && (
        <button
          type="button"
          className="primary"
          disabled={busy || request !== null}
          onClick={() => act('/api/helm/request')}
        >
          {holder === null ? 'Take control' : 'Request control'}
        </button>
      )}
      {iWait && (
        <button type="button" disabled={busy} onClick={() => act('/api/helm/cancel')}>
          Cancel request
        </button>
      )}
      {iHold && request === null && (
        <button type="button" disabled={busy} onClick={() => act('/api/helm/release')}>
          Release control
        </button>
      )}
      {error && (
        <span className="form-error" role="alert">
          {error}
        </span>
      )}
    </div>
  )
}

/** A pending request for control, shown under the top bar on every tab. */
export function HelmNotice({ me, helm }: Props) {
  const { act, busy, error } = useAct()
  const holder = helm?.holder ?? null
  const request = helm?.request ?? null
  const seconds = useCountdown(request?.deadline ?? null)
  if (request === null) return null
  const iHold = me.user !== null && holder === me.user
  const iWait = me.user !== null && request.user === me.user

  if (!iHold) {
    return (
      <p className="strip" role="status">
        {iWait ? 'You have' : <><strong>{request.user}</strong> has</>} asked {holder} for control. Without an answer
        it transfers automatically in <strong>{seconds} s</strong>.
      </p>
    )
  }
  return (
    <div className="strip strip-decide" role="alert">
      <span>
        <strong>{request.user}</strong> requests control. It transfers automatically in <strong>{seconds} s</strong>{' '}
        unless you answer.
      </span>
      <button
        type="button"
        className="primary"
        disabled={busy}
        onClick={() => act('/api/helm/respond', { accept: true })}
      >
        Transfer control
      </button>
      <button type="button" disabled={busy} onClick={() => act('/api/helm/respond', { accept: false })}>
        Deny
      </button>
      {error && (
        <span className="form-error" role="alert">
          {error}
        </span>
      )}
    </div>
  )
}
