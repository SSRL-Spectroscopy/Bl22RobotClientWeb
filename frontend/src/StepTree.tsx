import { useEffect, useRef, useState } from 'react'

export type StepNode = {
  id: number
  kind: 'section' | 'line'
  title: string
  state: 'planned' | 'running' | 'done' | 'failed' | 'stopped' | 'skipped'
  started: number | null
  finished: number | null
  children: StepNode[]
}

const MARKS: Record<StepNode['state'], { icon: string; word: string }> = {
  planned: { icon: '', word: 'not started' },
  running: { icon: '➤', word: 'running now' },
  done: { icon: '✓', word: 'done' },
  failed: { icon: '✕', word: 'failed' },
  stopped: { icon: '■', word: 'stopped' },
  skipped: { icon: '–', word: 'not needed' },
}

function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s} s`
  const pad = (n: number) => String(n).padStart(2, '0')
  if (s < 3600) return `${Math.floor(s / 60)}:${pad(s % 60)}`
  return `${Math.floor(s / 3600)}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`
}

function elapsed(node: StepNode, now: number): string {
  if (node.started === null) return ''
  if (node.finished !== null) return formatElapsed(node.finished - node.started)
  return node.state === 'running' ? formatElapsed(now - node.started) : ''
}

function sectionIds(nodes: StepNode[]): number[] {
  return nodes.flatMap((n) => (n.kind === 'section' ? [n.id, ...sectionIds(n.children)] : []))
}

function runningLine(nodes: StepNode[]): number | null {
  for (const node of nodes) {
    if (node.kind === 'line' && node.state === 'running') return node.id
    const inside = runningLine(node.children)
    if (inside !== null) return inside
  }
  return null
}

/**
 * The steps of a sequence as a numbered tree. A section opens by itself while
 * the run is inside it (or stopped inside it) and closes again afterwards;
 * a click on its heading overrides that. The running line carries the pointer.
 */
export function StepTree({ nodes, label }: { nodes: StepNode[]; label: string }) {
  // sections the operator opened or closed by hand: id -> open
  const [chosen, setChosen] = useState<Record<number, boolean>>({})
  const [now, setNow] = useState(() => Date.now() / 1000)
  const box = useRef<HTMLDivElement>(null)
  const current = runningLine(nodes)

  useEffect(() => {
    if (current === null) return
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(timer)
  }, [current])

  useEffect(() => {
    // keep the running line in view, inside the box only (the page does not jump)
    const container = box.current
    const row = container?.querySelector<HTMLElement>('[data-current="true"]')
    if (!container || !row) return
    const top = row.offsetTop - container.offsetTop
    if (top < container.scrollTop || top + row.offsetHeight > container.scrollTop + container.clientHeight) {
      container.scrollTop = Math.max(0, top - container.clientHeight / 2)
    }
  }, [current])

  const isOpen = (node: StepNode) =>
    chosen[node.id] ?? (node.state === 'running' || node.state === 'failed' || node.state === 'stopped')

  const setAll = (open: boolean) => setChosen(Object.fromEntries(sectionIds(nodes).map((id) => [id, open])))

  const render = (list: StepNode[], prefix: string) => (
    <ol className="steps">
      {list.map((node, index) => {
        const number = `${prefix}${index + 1}`
        const mark = MARKS[node.state]
        const time = elapsed(node, now)
        const row = (
          <>
            <span className="step-mark" aria-hidden="true">
              {mark.icon}
            </span>
            <span className="step-number">{number}</span>
            <span className="step-title">{node.title}</span>
            <span className="visually-hidden"> ({mark.word})</span>
            {time && <span className="step-time">{time}</span>}
          </>
        )
        if (node.kind === 'line') {
          return (
            <li key={node.id} className={`step step-${node.state}`} data-current={node.id === current}>
              <div className="step-row">{row}</div>
            </li>
          )
        }
        const open = isOpen(node)
        return (
          <li key={node.id} className={`step step-section step-${node.state}`}>
            <button
              type="button"
              className="step-row step-toggle"
              aria-expanded={open}
              onClick={() => setChosen({ ...chosen, [node.id]: !open })}
            >
              <span className="step-caret" aria-hidden="true">
                {open ? '▾' : '▸'}
              </span>
              {row}
            </button>
            {open && node.children.length > 0 && render(node.children, `${number}.`)}
            {open && node.children.length === 0 && <p className="hint step-empty">Nothing planned here.</p>}
          </li>
        )
      })}
    </ol>
  )

  if (nodes.length === 0) return null
  return (
    <div className="step-tree">
      <div className="step-tools">
        <strong>{label}</strong>
        <button type="button" onClick={() => setAll(true)}>
          Expand all
        </button>
        <button type="button" onClick={() => setAll(false)}>
          Collapse all
        </button>
        <button type="button" onClick={() => setChosen({})} title="Open only the section the run is in">
          Follow the run
        </button>
      </div>
      <div className="step-box" ref={box} role="group" aria-label={label}>
        {render(nodes, '')}
      </div>
    </div>
  )
}
