import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import type { Me } from './auth'

type Props = {
  me: Me
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

export function Login({ me, login, logout }: Props) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  if (me.user) {
    return (
      <div className="who">
        <span>
          Operator <strong>{me.user}</strong>
        </span>
        <button type="button" onClick={() => logout().catch((e: Error) => window.console.error(e))}>
          Log out
        </button>
      </div>
    )
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      await login(username, password)
      dialog.current?.close()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setPassword('')
      setBusy(false)
    }
  }

  return (
    <div className="who">
      <span>Observer</span>
      <button
        type="button"
        onClick={() => {
          setError('')
          dialog.current?.showModal()
        }}
      >
        Operator login
      </button>
      <dialog ref={dialog} aria-labelledby="login-title">
        <form onSubmit={submit}>
          <h2 id="login-title">Operator login</h2>
          <label>
            Username
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus
              required
            />
          </label>
          <label>
            Password
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <div className="dialog-actions">
            <button type="button" onClick={() => dialog.current?.close()}>
              Cancel
            </button>
            <button type="submit" className="primary" disabled={busy}>
              {busy ? 'Logging in…' : 'Log in'}
            </button>
          </div>
        </form>
      </dialog>
    </div>
  )
}
