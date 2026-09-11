/** Small shared presentational pieces. */

import { useState, type ReactNode } from 'react'

export function Panel({
  title,
  badge,
  defaultOpen = true,
  children,
}: {
  title: string
  badge?: ReactNode
  defaultOpen?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <section className="panel">
      <button className="panel-header" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="chevron">{open ? '▼' : '▶'}</span>
        {title}
        {badge !== undefined && <span className="badge">{badge}</span>}
      </button>
      {open && <div className="panel-body">{children}</div>}
    </section>
  )
}

export function KeyValue({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([key, value]) => (
        <div key={key} style={{ display: 'contents' }}>
          <dt title={key}>{key}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  )
}

export function ScoreBar({
  label,
  value,
  weight,
  contribution,
}: {
  label: string
  value: number
  weight?: number
  contribution?: number
}) {
  const muted = weight !== undefined && weight <= 0
  return (
    <div className={`score-row ${muted ? 'muted' : ''}`} title={
      weight !== undefined
        ? `${label}: ${value.toFixed(1)} x weight ${(weight * 100).toFixed(0)} % = ${(contribution ?? 0).toFixed(2)} points`
        : `${label}: ${value.toFixed(1)}`
    }>
      <span className="name">
        {label}
        {weight !== undefined && (
          <span className="contribution"> {(weight * 100).toFixed(0)}%</span>
        )}
      </span>
      <span className="bar">
        <span style={{ width: `${Math.max(0, Math.min(100, value))}%`, background: scoreColor(value) }} />
      </span>
      <span className="num">{value.toFixed(0)}</span>
    </div>
  )
}

export function scoreColor(value: number): string {
  if (value >= 80) return '#3fb950'
  if (value >= 55) return '#d29922'
  return '#f85149'
}

export function Vector3Input({
  value,
  onChange,
  unit,
  disabled,
}: {
  value: [number, number, number]
  onChange: (next: [number, number, number]) => void
  unit: string
  disabled?: boolean
}) {
  return (
    <div className="row">
      {(['X', 'Y', 'Z'] as const).map((axis, index) => (
        <label key={axis} style={{ display: 'block' }}>
          <span className="field-label">
            {axis} [{unit}]
          </span>
          <input
            type="number"
            step="any"
            disabled={disabled}
            value={Number.isFinite(value[index]) ? value[index] : 0}
            onChange={(event) => {
              const next: [number, number, number] = [...value]
              next[index] = Number(event.target.value)
              onChange(next)
            }}
          />
        </label>
      ))}
    </div>
  )
}

export function ConfidenceTag({ confidence }: { confidence: string }) {
  const className = confidence === 'high' ? 'tag high' : confidence === 'low' || confidence === 'unknown' ? 'tag low' : 'tag'
  return <span className={className}>{confidence}</span>
}

export function formatMinutes(minutes: number): string {
  if (!Number.isFinite(minutes)) return '-'
  if (minutes < 90) return `${minutes.toFixed(0)} min`
  const hours = Math.floor(minutes / 60)
  return `${hours} h ${Math.round(minutes - hours * 60)} min`
}

export function formatNumber(value: number | null | undefined, digits = 2, unit = ''): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '-'
  const formatted =
    Math.abs(value) >= 100000 ? value.toExponential(2) : value.toFixed(digits)
  return unit ? `${formatted} ${unit}` : formatted
}
