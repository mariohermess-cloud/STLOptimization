/**
 * Right column: the result.
 *
 * Nothing here shows a number without the means to take it apart. The overall
 * score is rendered with the weighted contribution of every component, the
 * justification sentences come from the same numbers, and the confidence
 * figure lists the factors it was computed from.
 */

import { useMemo, useState } from 'react'
import type { OrientationCandidate } from '../api/types'
import { selectedCandidate, useStore } from '../state/store'
import { KeyValue, Panel, ScoreBar, formatMinutes, formatNumber, scoreColor } from './common'

export function AnalysisColumn() {
  const result = useStore((state) => state.result)

  if (!result) {
    return (
      <div className="column right">
        <Panel title="Analysis">
          <p className="note">
            Upload a model, define the material, the fixed surfaces and at least one load case,
            then run the optimisation. The result appears here with a full score breakdown, the
            reasoning behind it, the recommended print settings and an export.
          </p>
        </Panel>
      </div>
    )
  }

  return (
    <div className="column right">
      <ResultHeader />
      <CandidatesPanel />
      <BreakdownPanel />
      <ReasoningPanel />
      <ComparePanel />
      <MechanicalPanel />
      <SettingsPanel />
      <RegionsPanel />
      <ConfidencePanel />
      <ExportPanel />
    </div>
  )
}

function ResultHeader() {
  const result = useStore((state) => state.result)!
  const candidate = useStore(selectedCandidate)
  if (!candidate) return null

  return (
    <div style={{ padding: 12, borderBottom: '1px solid var(--border)' }}>
      <div className="score-hero">
        <span className="value" style={{ color: scoreColor(candidate.overall_score) }}>
          {candidate.overall_score.toFixed(0)}
        </span>
        <div>
          <div className="label">Overall score · orientation #{candidate.rank}</div>
          <div className="mono dim" style={{ fontSize: 11 }}>
            X {candidate.rotation_x.toFixed(1)}° · Y {candidate.rotation_y.toFixed(1)}° · Z{' '}
            {candidate.rotation_z.toFixed(1)}°
          </div>
        </div>
        <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
          <div className="label">Confidence</div>
          <div className="mono" style={{ fontSize: 18, color: scoreColor(result.confidence.score) }}>
            {result.confidence.score.toFixed(0)} %
          </div>
        </div>
      </div>
      <p className="note">
        {result.evaluated_candidates.toLocaleString()} orientations evaluated in{' '}
        {result.duration_seconds.toFixed(1)} s · material {result.material.name}
      </p>
    </div>
  )
}

function CandidatesPanel() {
  const result = useStore((state) => state.result)!
  const selectedRank = useStore((state) => state.selectedRank)
  const select = useStore((state) => state.selectCandidate)
  const compareRank = useStore((state) => state.compareRank)
  const setCompareRank = useStore((state) => state.setCompareRank)

  return (
    <Panel title={`Top ${result.candidates.length} orientations`}>
      {result.candidates.map((candidate) => (
        <div
          key={candidate.rank}
          className={`candidate ${candidate.rank === selectedRank ? 'selected' : ''}`}
          onClick={() => select(candidate.rank)}
          role="button"
          tabIndex={0}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') select(candidate.rank)
          }}
        >
          <span className="rank">#{candidate.rank}</span>
          <span>
            <span className="angles">
              X {candidate.rotation_x.toFixed(0)}° Y {candidate.rotation_y.toFixed(0)}° Z{' '}
              {candidate.rotation_z.toFixed(0)}°
            </span>
            <br />
            <span className="contribution">
              {candidate.bed.height_mm.toFixed(0)} mm tall ·{' '}
              {formatMinutes(candidate.print_time.estimated_minutes)} ·{' '}
              {candidate.support.estimated_support_volume_mm3 > 0
                ? `${candidate.support.estimated_support_volume_mm3.toFixed(0)} mm³ support`
                : 'no support'}
              {!candidate.bed.fits_build_volume && ' · does not fit'}
            </span>
          </span>
          <span style={{ textAlign: 'right' }}>
            <span className="total" style={{ color: scoreColor(candidate.overall_score) }}>
              {candidate.overall_score.toFixed(0)}
            </span>
            <br />
            <button
              className={`btn small compare ${compareRank === candidate.rank ? 'active' : ''}`}
              onClick={(event) => {
                event.stopPropagation()
                setCompareRank(compareRank === candidate.rank ? null : candidate.rank)
              }}
            >
              compare
            </button>
          </span>
        </div>
      ))}
    </Panel>
  )
}

function BreakdownPanel() {
  const candidate = useStore(selectedCandidate)
  if (!candidate) return null
  const total = candidate.components.reduce((sum, component) => sum + component.contribution, 0)

  return (
    <Panel title="Score breakdown">
      {candidate.components.map((component) => (
        <ScoreBar
          key={component.key}
          label={component.label}
          value={component.value}
          weight={component.weight}
          contribution={component.contribution}
        />
      ))}
      <p className="note">
        Overall = Σ (component × weight) ={' '}
        <span className="mono">
          {candidate.components
            .filter((component) => component.weight > 0)
            .map(
              (component) =>
                `${component.value.toFixed(0)}×${(component.weight * 100).toFixed(0)}%`,
            )
            .join(' + ')}
        </span>{' '}
        = <span className="mono">{total.toFixed(1)}</span>
      </p>
    </Panel>
  )
}

function ReasoningPanel() {
  const candidate = useStore(selectedCandidate)
  if (!candidate) return null

  return (
    <Panel title="Why this orientation?">
      <ul className="reasons">
        {candidate.reasons.map((reason) => (
          <li key={reason}>{reason}</li>
        ))}
        {candidate.warnings.map((warning) => (
          <li key={warning} className="warning">
            {warning}
          </li>
        ))}
      </ul>
    </Panel>
  )
}

const COMPARE_ROWS: {
  label: string
  get: (candidate: OrientationCandidate) => number
  digits?: number
  lowerIsBetter?: boolean
  unit?: string
}[] = [
  { label: 'Overall', get: (c) => c.overall_score, digits: 1 },
  { label: 'Mechanical', get: (c) => c.mechanical_score, digits: 1 },
  { label: 'Layer direction', get: (c) => c.layer_score, digits: 1 },
  { label: 'Support', get: (c) => c.support_score, digits: 1 },
  { label: 'Overhang', get: (c) => c.overhang_score, digits: 1 },
  { label: 'Stability', get: (c) => c.stability_score, digits: 1 },
  { label: 'Warping', get: (c) => c.warping_score, digits: 1 },
  { label: 'Material', get: (c) => c.material_score, digits: 1 },
  { label: 'Print time', get: (c) => c.print_time_score, digits: 1 },
  {
    label: 'Print time [min]',
    get: (c) => c.print_time.estimated_minutes,
    digits: 0,
    lowerIsBetter: true,
  },
  {
    label: 'Support volume [mm³]',
    get: (c) => c.support.estimated_support_volume_mm3,
    digits: 0,
    lowerIsBetter: true,
  },
  { label: 'Height [mm]', get: (c) => c.bed.height_mm, digits: 1, lowerIsBetter: true },
  { label: 'Bed contact [mm²]', get: (c) => c.bed.contact_area_mm2, digits: 0 },
]

function ComparePanel() {
  const result = useStore((state) => state.result)!
  const selected = useStore(selectedCandidate)
  const compareRank = useStore((state) => state.compareRank)
  const other = result.candidates.find((candidate) => candidate.rank === compareRank)

  if (!selected || !other || other.rank === selected.rank) return null

  return (
    <Panel title={`Compare #${selected.rank} with #${other.rank}`}>
      <div className="table-scroll">
      <table className="compare">
        <thead>
          <tr>
            <th>Criterion</th>
            <th>#{selected.rank}</th>
            <th>#{other.rank}</th>
            <th>Δ</th>
          </tr>
        </thead>
        <tbody>
          {COMPARE_ROWS.map((row) => {
            const a = row.get(selected)
            const b = row.get(other)
            const delta = a - b
            const better = row.lowerIsBetter ? delta < 0 : delta > 0
            return (
              <tr key={row.label}>
                <td>{row.label}</td>
                <td>{a.toFixed(row.digits ?? 1)}</td>
                <td>{b.toFixed(row.digits ?? 1)}</td>
                <td
                  className={`delta ${Math.abs(delta) < 1e-6 ? '' : better ? 'pos' : 'neg'}`}
                >
                  {delta >= 0 ? '+' : ''}
                  {delta.toFixed(row.digits ?? 1)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      </div>
    </Panel>
  )
}

function MechanicalPanel() {
  const candidate = useStore(selectedCandidate)
  const mechanical = candidate?.mechanical
  if (!candidate || !mechanical) return null

  const safety = mechanical.min_safety_factor
  const safetyLabel = safety >= 1e8 ? 'no stress' : safety.toFixed(2)
  const safetyClass = safety < 1 ? 'bad' : safety < 1.5 ? 'warn' : 'good'

  return (
    <Panel title="Mechanical result" badge="approximation">
      <KeyValue
        items={[
          ['Governing load case', mechanical.critical_load_case ?? '-'],
          [
            'Utilisation',
            <span className={safetyClass}>{formatNumber(mechanical.max_utilization, 2)}</span>,
          ],
          ['Safety factor', <span className={safetyClass}>{safetyLabel}</span>],
          ['Equivalent stress', formatNumber(mechanical.max_stress_mpa, 1, 'MPa')],
          [
            'Allowable in that direction',
            formatNumber(mechanical.allowable_stress_mpa, 1, 'MPa'),
          ],
          [
            'Stress angle to layer plane',
            formatNumber(mechanical.angle_to_layer_plane_deg, 0, '°'),
          ],
          ['Critical section area', formatNumber(mechanical.critical_section_area_mm2, 0, 'mm²')],
          [
            'Section modulus',
            formatNumber(mechanical.critical_section_modulus_mm3, 0, 'mm³'),
          ],
        ]}
      />
      <p className="note">
        Beam theory evaluated on the real cross-sections along the load path, with an anisotropic
        failure check on the layer interfaces. <strong>This is not a finite element analysis</strong>:
        stress concentrations at holes, fillets and sharp transitions are not resolved, and the
        fixture is treated as ideally rigid.
      </p>
    </Panel>
  )
}

function SettingsPanel() {
  const settings = useStore((state) => state.settings)
  const loading = useStore((state) => state.settingsLoading)
  const [showReasons, setShowReasons] = useState(true)

  if (loading) {
    return (
      <Panel title="Print settings">
        <p className="note">Computing…</p>
      </Panel>
    )
  }
  if (!settings) return null

  const wall = (settings.wall_vs_infill?.selected ?? null) as
    | { loops: number; shell_inertia_fraction: number; section_area_mm2: number }
    | null

  return (
    <Panel title="Recommended print settings" badge={settings.material}>
      <KeyValue
        items={[
          ['Layer height', `${settings.layer_height.toFixed(2)} mm`],
          ['First layer', `${settings.first_layer_height.toFixed(2)} mm`],
          ['Wall loops', settings.wall_loops],
          ['Top / bottom layers', `${settings.top_layers} / ${settings.bottom_layers}`],
          ['Infill', `${(settings.infill_density * 100).toFixed(0)} % ${settings.infill_pattern}`],
          [
            'Supports',
            settings.supports
              ? `${settings.support_type} @ ${settings.support_angle}°`
              : 'none',
          ],
          ['Brim', settings.brim ? `${settings.brim_width_mm.toFixed(0)} mm` : 'no'],
          ['Nozzle', `${settings.nozzle.toFixed(1)} mm`],
        ]}
      />
      {wall && (
        <p className="note">
          Walls versus infill: {wall.loops} perimeters carry{' '}
          <span className="mono">{(wall.shell_inertia_fraction * 100).toFixed(0)} %</span> of the
          second moment of area of the {wall.section_area_mm2.toFixed(0)} mm² critical section.
        </p>
      )}
      <button
        className="btn small"
        style={{ marginTop: 6 }}
        onClick={() => setShowReasons(!showReasons)}
      >
        {showReasons ? 'Hide reasoning' : 'Show reasoning'}
      </button>
      {showReasons && (
        <ul className="reasons" style={{ marginTop: 8 }}>
          {settings.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
          {settings.warnings.map((warning) => (
            <li key={warning} className="warning">
              {warning}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}

function RegionsPanel() {
  const result = useStore((state) => state.result)!
  const highlighted = useStore((state) => state.highlightedRegion)
  const setHighlighted = useStore((state) => state.setHighlightedRegion)

  const grouped = useMemo(() => {
    const order = ['critical', 'warning', 'info']
    return [...result.critical_regions].sort(
      (a, b) => order.indexOf(a.severity) - order.indexOf(b.severity),
    )
  }, [result.critical_regions])

  if (grouped.length === 0) {
    return (
      <Panel title="Potentially critical geometric regions" badge="none">
        <p className="note">No thin walls, necks or large unsupported overhangs were detected.</p>
      </Panel>
    )
  }

  return (
    <Panel title="Potentially critical geometric regions" badge={grouped.length}>
      {grouped.map((region) => (
        <button
          key={region.id}
          className={`region ${region.severity} ${highlighted?.id === region.id ? 'active' : ''}`}
          onClick={() => setHighlighted(highlighted?.id === region.id ? null : region)}
        >
          <div className="title">{region.label}</div>
          <div className="desc">{region.description}</div>
        </button>
      ))}
      <p className="note">
        These are geometric risk indicators, not stress results. Quantifying a stress concentration
        needs a finite element analysis.
      </p>
    </Panel>
  )
}

function ConfidencePanel() {
  const result = useStore((state) => state.result)!
  const confidence = result.confidence

  return (
    <Panel title="Analysis confidence" badge={`${confidence.score.toFixed(0)} %`}>
      {confidence.factors.map((factor) => (
        <div key={factor.key} style={{ marginBottom: 6 }}>
          <ScoreBar label={factor.label} value={factor.score} weight={factor.weight} />
          <div className="contribution" style={{ paddingLeft: 2 }}>
            {factor.detail}
          </div>
        </div>
      ))}
      <h4 style={{ margin: '10px 0 6px', fontSize: 11, color: 'var(--text-dim)' }}>Limitations</h4>
      <ul className="reasons limitations">
        {confidence.limitations.map((limitation) => (
          <li key={limitation}>{limitation}</li>
        ))}
      </ul>
    </Panel>
  )
}

function ExportPanel() {
  const exportJson = useStore((state) => state.exportJson)
  const settings = useStore((state) => state.settings)

  return (
    <Panel title="Export">
      <div className="btn-group">
        <button className="btn" disabled={!settings} onClick={() => void exportJson(false)}>
          Print settings (JSON)
        </button>
        <button className="btn" disabled={!settings} onClick={() => void exportJson(true)}>
          Full analysis report (JSON)
        </button>
      </div>
      <p className="note">
        The export contains the calculated values for the selected orientation: rotation, layer
        height, wall loops, infill, supports and brim, plus the score breakdown and the analysis
        confidence. The full report adds the model metrics, material provenance, loads,
        constraints, every candidate, the critical regions and the stated limitations.
      </p>
    </Panel>
  )
}
