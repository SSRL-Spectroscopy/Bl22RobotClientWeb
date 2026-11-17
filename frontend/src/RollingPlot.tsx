import { useEffect, useRef, useState } from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import { live } from './live'
import type { PvDef } from './pvs'

const WINDOWS_MIN = [1, 2, 5]
const REDRAW_MS = 250
const HEIGHT = 300
const STORAGE_KEY = 'plotWindowMin'

// Series colours are assigned by position and come from CSS (--series-1..3),
// so light and dark mode each get their own validated step.
const SERIES_VARS = ['--series-1', '--series-2', '--series-3']

function cssVar(element: Element, name: string): string {
  return getComputedStyle(element).getPropertyValue(name).trim()
}

function readStoredWindow(): number {
  try {
    const stored = Number(localStorage.getItem(STORAGE_KEY))
    if (WINDOWS_MIN.includes(stored)) return stored
  } catch {
    // storage unavailable; fall through to the default
  }
  return 2
}

export function RollingPlot({ pvs }: { pvs: PvDef[] }) {
  const holder = useRef<HTMLDivElement>(null)
  const [windowMin, setWindowMin] = useState(readStoredWindow)
  const windowRef = useRef(windowMin)
  // Rebuild the plot when the colour scheme changes, to pick up the new colours.
  const [scheme, setScheme] = useState(0)

  useEffect(() => {
    windowRef.current = windowMin
    try {
      localStorage.setItem(STORAGE_KEY, String(windowMin))
    } catch {
      // not being able to remember the choice is harmless
    }
  }, [windowMin])

  useEffect(() => {
    const query = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => setScheme((n) => n + 1)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])

  useEffect(() => {
    const element = holder.current
    if (!element || pvs.length === 0) return

    const axisInk = cssVar(element, '--text-muted')
    const grid = cssVar(element, '--grid')
    const unit = pvs[0].unit
    const axis: uPlot.Axis = {
      stroke: axisInk,
      grid: { stroke: grid, width: 1 },
      ticks: { stroke: grid, width: 1 },
    }

    const plot = new uPlot(
      {
        width: element.clientWidth,
        height: HEIGHT,
        scales: { x: { time: true, auto: false } },
        axes: [axis, { ...axis, size: 70, label: unit, labelSize: 20 }],
        series: [
          { label: 'Time' },
          ...pvs.map((pv, i) => ({
            label: pv.label,
            stroke: cssVar(element, SERIES_VARS[i % SERIES_VARS.length]),
            width: 2,
            spanGaps: true,
            points: { show: false },
            value: (_: uPlot, v: number | null) => (v == null ? '–' : `${v.toFixed(5)} ${unit}`),
          })),
        ],
        cursor: { drag: { x: false, y: false } },
        legend: { live: true },
      },
      [[], ...pvs.map(() => [])],
      element,
    )

    const redraw = () => {
      const now = live.serverNow()
      const from = now - windowRef.current * 60
      // Each PV has its own timestamps; join them onto one time axis.
      const tables = pvs.map((pv) => {
        const series = live.history.get(pv.key) ?? { t: [], v: [] }
        let start = 0
        while (start < series.t.length && series.t[start] < from) start++
        // keep one point before the window so the line reaches the left edge
        start = Math.max(0, start - 1)
        return [series.t.slice(start), series.v.slice(start)] as uPlot.AlignedData
      })
      const filled = tables.filter((table) => table[0].length > 0)
      if (filled.length === pvs.length) {
        plot.setData(uPlot.join(tables), false)
      } else if (filled.length === 0) {
        plot.setData([[], ...pvs.map(() => [])], false)
      }
      plot.setScale('x', { min: from, max: now })
    }

    redraw()
    const timer = setInterval(redraw, REDRAW_MS)
    const resize = new ResizeObserver(() => {
      plot.setSize({ width: element.clientWidth, height: HEIGHT })
    })
    resize.observe(element)

    return () => {
      clearInterval(timer)
      resize.disconnect()
      plot.destroy()
    }
  }, [pvs, scheme])

  return (
    <section aria-label="Ion chamber signals">
      <div className="plot-head">
        <h2>Ion chambers</h2>
        <div className="segmented" role="group" aria-label="Time window">
          {WINDOWS_MIN.map((minutes) => (
            <button
              key={minutes}
              type="button"
              aria-pressed={minutes === windowMin}
              onClick={() => setWindowMin(minutes)}
            >
              {minutes} min
            </button>
          ))}
        </div>
      </div>
      <div className="plot" ref={holder} />
    </section>
  )
}
