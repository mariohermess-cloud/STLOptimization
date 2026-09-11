/** Left column: everything the user provides before an optimisation runs. */

import { useEffect, useRef, useState } from 'react'
import type { CustomMaterial, LoadType, OptimizationWeights, PresetId } from '../api/types'
import {
  READY_TO_OPTIMIZE,
  activeMaterial,
  activePrinter,
  useStore,
} from '../state/store'
import {
  ConfidenceTag,
  KeyValue,
  Panel,
  Vector3Input,
  formatNumber,
} from './common'

export function InputColumn() {
  return (
    <div className="column left">
      <UploadPanel />
      <ModelInfoPanel />
      <MaterialPanel />
      <ProcessPanel />
      <FixationPanel />
      <LoadPanel />
      <ObjectivePanel />
      <RunPanel />
      <p className="disclaimer">
        This tool provides engineering-oriented estimates and FDM print optimisation guidance. It
        is not a certified structural analysis system.
      </p>
    </div>
  )
}

/* ------------------------------------------------------------------ upload */
function UploadPanel() {
  const upload = useStore((state) => state.upload)
  const uploading = useStore((state) => state.uploading)
  const model = useStore((state) => state.model)
  const reset = useStore((state) => state.reset)
  const inputRef = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)

  return (
    <Panel title="1 · STL model" badge={model ? model.filename : undefined}>
      <div
        className={`dropzone ${over ? 'over' : ''}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(event) => {
          event.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => {
          event.preventDefault()
          setOver(false)
          const file = event.dataTransfer.files?.[0]
          if (file) void upload(file)
        }}
      >
        {uploading ? 'Uploading…' : model ? 'Drop another STL to replace' : 'Drop an STL file, or click to browse'}
      </div>
      <input
        ref={inputRef}
        type="file"
        accept=".stl,model/stl"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0]
          if (file) void upload(file)
          event.target.value = ''
        }}
      />
      {model && (
        <div className="btn-group" style={{ marginTop: 8 }}>
          <button className="btn small danger" onClick={reset}>
            Clear model
          </button>
        </div>
      )}
      <p className="note">
        Binary and ASCII STL. The file is validated and repaired on the server; the viewer shows
        the repaired mesh so that face selections match the analysis exactly.
      </p>
    </Panel>
  )
}

/* -------------------------------------------------------------- model info */
function ModelInfoPanel() {
  const geometry = useStore((state) => state.geometry)
  const model = useStore((state) => state.model)
  if (!geometry || !model) return null

  const metrics = geometry.metrics
  const validation = geometry.validation

  return (
    <Panel title="Model information" badge={`${metrics.triangle_count.toLocaleString()} tri`}>
      <KeyValue
        items={[
          [
            'Dimensions X × Y × Z',
            `${metrics.dimensions.map((value) => value.toFixed(1)).join(' × ')} mm`,
          ],
          ['Volume', `${formatNumber(metrics.volume_mm3 / 1000, 2)} cm³`],
          ['Surface area', `${formatNumber(metrics.surface_area_mm2 / 100, 2)} cm²`],
          [
            'Mass estimate',
            geometry.mass_estimate_g !== null ? (
              <span title="Solid volume × material density. Estimate: a printed part with sparse infill weighs less.">
                {formatNumber(geometry.mass_estimate_g, 1)} g <span className="tag approx">est</span>
              </span>
            ) : (
              '-'
            ),
          ],
          ['Triangles', metrics.triangle_count.toLocaleString()],
          [
            'Centre of mass',
            metrics.center_of_mass.map((value) => value.toFixed(1)).join(', '),
          ],
          ['Aspect ratio', formatNumber(metrics.aspect_ratio, 2)],
          ['Solidity (vs hull)', formatNumber(metrics.solidity, 3)],
          [
            'Watertight',
            validation.is_watertight ? (
              <span className="good">yes</span>
            ) : (
              <span className="warn">no</span>
            ),
          ],
          ['Bodies', validation.body_count],
        ]}
      />
      {validation.repairs_applied.length > 0 && (
        <p className="note">Repairs applied: {validation.repairs_applied.join('; ')}.</p>
      )}
      {model.decimated && (
        <p className="note">
          The orientation search runs on a mesh simplified to{' '}
          {model.analysis_triangle_count.toLocaleString()} triangles. Dimensions, volume and
          cross-sections use the full mesh.
        </p>
      )}
      <p className="note">
        Principal axes (area-weighted PCA), extents{' '}
        {metrics.pca_extents.map((value) => value.toFixed(1)).join(' / ')} mm.
      </p>
    </Panel>
  )
}

/* ---------------------------------------------------------------- material */
function MaterialPanel() {
  const materials = useStore((state) => state.materials)
  const materialId = useStore((state) => state.materialId)
  const setMaterial = useStore((state) => state.setMaterial)
  const custom = useStore((state) => state.activeCustomMaterial)
  const customMaterials = useStore((state) => state.customMaterials)
  const useCustom = useStore((state) => state.useCustomMaterial)
  const deleteCustom = useStore((state) => state.deleteCustomMaterial)
  const disclaimer = useStore((state) => state.materialDisclaimer)
  const material = useStore(activeMaterial)
  const [showCustom, setShowCustom] = useState(false)

  return (
    <Panel title="2 · Material" badge={custom ? custom.name : material?.name}>
      <label className="field">
        <span>Material</span>
        <select
          value={custom ? '__custom__' : materialId}
          onChange={(event) => {
            if (event.target.value === '__custom__') return
            setMaterial(event.target.value)
          }}
        >
          {materials.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.name}
            </option>
          ))}
          {custom && <option value="__custom__">{custom.name} (custom)</option>}
        </select>
      </label>

      {material && (
        <>
          <KeyValue
            items={[
              [
                'Density',
                <>
                  {formatNumber(material.properties.density.value, 2, 'g/cm³')}{' '}
                  <ConfidenceTag confidence={material.properties.density.confidence} />
                </>,
              ],
              [
                "Young's modulus",
                <>
                  {formatNumber(material.properties.young_modulus.value, 0, 'MPa')}{' '}
                  <ConfidenceTag confidence={material.properties.young_modulus.confidence} />
                </>,
              ],
              [
                'Tensile strength (XY)',
                <>
                  {formatNumber(material.properties.tensile_strength.value, 0, 'MPa')}{' '}
                  <ConfidenceTag confidence={material.properties.tensile_strength.confidence} />
                </>,
              ],
              [
                'Layer adhesion factor',
                <>
                  {formatNumber(material.properties.layer_adhesion_factor.value, 2)}{' '}
                  <ConfidenceTag
                    confidence={material.properties.layer_adhesion_factor.confidence}
                  />
                </>,
              ],
              [
                'Thermal limit',
                formatNumber(material.properties.thermal_limit.value, 0, '°C'),
              ],
              [
                'Warping index',
                <>
                  {formatNumber(material.properties.warp_tendency.value, 2)}{' '}
                  <span className="tag approx">heuristic</span>
                </>,
              ],
            ]}
          />
          <p className="note">
            Z strength is modelled as <span className="mono">layer&nbsp;adhesion&nbsp;factor ×
            XY&nbsp;strength</span> ={' '}
            {formatNumber(
              (material.properties.tensile_strength.value ?? 0) *
                (material.properties.layer_adhesion_factor.value ?? 0),
              0,
              'MPa',
            )}
            . {material.properties.layer_adhesion_factor.source}
          </p>
          {material.requires_enclosure && (
            <p className="note warn">This material normally needs an enclosed chamber.</p>
          )}
        </>
      )}

      {custom && (
        <KeyValue
          items={[
            ['Density', `${custom.density_g_cm3} g/cm³`],
            ["Young's modulus", `${custom.young_modulus_mpa} MPa`],
            ['Tensile strength', `${custom.tensile_strength_mpa} MPa`],
            ['Layer adhesion factor', `${custom.layer_adhesion_factor}`],
          ]}
        />
      )}

      <div className="btn-group" style={{ marginTop: 8 }}>
        <button className="btn small" onClick={() => setShowCustom(!showCustom)}>
          {showCustom ? 'Hide custom material' : 'Define custom material'}
        </button>
        {custom && (
          <button className="btn small" onClick={() => useCustom(null)}>
            Use catalogue material
          </button>
        )}
      </div>

      {showCustom && <CustomMaterialForm onDone={() => setShowCustom(false)} />}

      {customMaterials.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <span className="field-label">Saved in this browser</span>
          {customMaterials.map((entry) => (
            <div key={entry.name} className="row" style={{ marginBottom: 4 }}>
              <button className="btn small" onClick={() => useCustom(entry)}>
                {entry.name}
              </button>
              <button
                className="btn small danger"
                style={{ flex: '0 0 auto' }}
                onClick={() => deleteCustom(entry.name)}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}

      {disclaimer && <p className="note">{disclaimer}</p>}
    </Panel>
  )
}

function CustomMaterialForm({ onDone }: { onDone: () => void }) {
  const save = useStore((state) => state.saveCustomMaterial)
  const materials = useStore((state) => state.materials)
  const [form, setForm] = useState<CustomMaterial>({
    name: 'My material',
    density_g_cm3: 1.24,
    young_modulus_mpa: 3000,
    tensile_strength_mpa: 50,
    layer_adhesion_factor: 0.6,
    base_material_id: null,
  })

  const update = (patch: Partial<CustomMaterial>) => setForm({ ...form, ...patch })

  return (
    <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
      <label className="field">
        <span>Name</span>
        <input
          type="text"
          value={form.name}
          onChange={(event) => update({ name: event.target.value })}
        />
      </label>
      <div className="row">
        <label className="field">
          <span>Density [g/cm³]</span>
          <input
            type="number"
            step="0.01"
            value={form.density_g_cm3}
            onChange={(event) => update({ density_g_cm3: Number(event.target.value) })}
          />
        </label>
        <label className="field">
          <span>E [MPa]</span>
          <input
            type="number"
            step="10"
            value={form.young_modulus_mpa}
            onChange={(event) => update({ young_modulus_mpa: Number(event.target.value) })}
          />
        </label>
      </div>
      <div className="row">
        <label className="field">
          <span>Tensile XY [MPa]</span>
          <input
            type="number"
            step="1"
            value={form.tensile_strength_mpa}
            onChange={(event) => update({ tensile_strength_mpa: Number(event.target.value) })}
          />
        </label>
        <label className="field">
          <span>Layer adhesion [-]</span>
          <input
            type="number"
            step="0.01"
            min="0.01"
            max="1"
            value={form.layer_adhesion_factor}
            onChange={(event) => update({ layer_adhesion_factor: Number(event.target.value) })}
          />
        </label>
      </div>
      <label className="field">
        <span>Inherit print defaults from</span>
        <select
          value={form.base_material_id ?? ''}
          onChange={(event) => update({ base_material_id: event.target.value || null })}
        >
          <option value="">(none)</option>
          {materials.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.name}
            </option>
          ))}
        </select>
      </label>
      <button
        className="btn primary block"
        onClick={() => {
          save(form)
          onDone()
        }}
      >
        Save and use
      </button>
      <p className="note">
        Custom materials are stored in this browser only. The layer adhesion factor is the ratio of
        Z strength to XY strength; it drives the whole anisotropy model.
      </p>
    </div>
  )
}

/* ----------------------------------------------------------------- process */
function ProcessPanel() {
  const process = useStore((state) => state.process)
  const setProcess = useStore((state) => state.setProcess)
  const printers = useStore((state) => state.printers)
  const printer = useStore(activePrinter)

  return (
    <Panel title="3 · Printer and process" badge={printer?.name} defaultOpen={false}>
      <label className="field">
        <span>Printer</span>
        <select
          value={process.printer_id}
          onChange={(event) => setProcess({ printer_id: event.target.value })}
        >
          {printers.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.name}
            </option>
          ))}
        </select>
      </label>
      <div className="row">
        <label className="field">
          <span>Nozzle [mm]</span>
          <select
            value={process.nozzle_diameter_mm}
            onChange={(event) =>
              setProcess({ nozzle_diameter_mm: Number(event.target.value) })
            }
          >
            {(printer?.available_nozzles_mm ?? [0.2, 0.4, 0.6, 0.8]).map((nozzle) => (
              <option key={nozzle} value={nozzle}>
                {nozzle.toFixed(1)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Overhang threshold [°]</span>
          <select
            value={process.overhang_threshold_deg}
            onChange={(event) =>
              setProcess({ overhang_threshold_deg: Number(event.target.value) })
            }
          >
            {[45, 50, 55, 60].map((angle) => (
              <option key={angle} value={angle}>
                {angle}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="check">
        <input
          type="checkbox"
          checked={process.allow_supports}
          onChange={(event) => setProcess({ allow_supports: event.target.checked })}
        />
        Supports allowed
      </label>
      {printer && (
        <KeyValue
          items={[
            ['Build volume', `${printer.build_volume_mm.join(' × ')} mm`],
            ['Enclosed', printer.enclosed ? 'yes' : 'no'],
          ]}
        />
      )}
      <p className="note">
        A face is treated as an overhang needing support when its inclination from the build plate
        is below the threshold. The same angle is used for the recommended support setting.
      </p>
    </Panel>
  )
}

/* --------------------------------------------------------------- fixation */
function FixationPanel() {
  const constraints = useStore((state) => state.constraints)
  const selectionMode = useStore((state) => state.selectionMode)
  const setSelectionMode = useStore((state) => state.setSelectionMode)
  const clearConstraint = useStore((state) => state.clearConstraint)
  const validateLoads = useStore((state) => state.validateLoads)
  const model = useStore((state) => state.model)

  const faces = constraints[0]?.face_ids.length ?? 0

  useEffect(() => {
    if (faces > 0) void validateLoads()
  }, [faces, validateLoads])

  return (
    <Panel title="4 · Fixed surfaces" badge={faces ? `${faces} faces` : 'none'}>
      <div className="btn-group">
        <button
          className={`btn small ${selectionMode === 'fixation' ? 'active' : ''}`}
          disabled={!model}
          onClick={() =>
            setSelectionMode(selectionMode === 'fixation' ? 'none' : 'fixation', null)
          }
        >
          {selectionMode === 'fixation' ? 'Stop picking' : 'Pick in viewer'}
        </button>
        <button className="btn small danger" disabled={!faces} onClick={clearConstraint}>
          Clear
        </button>
      </div>
      <p className="note">
        The selected faces are treated as an ideally rigid clamp. Support stiffness, bolt preload
        and contact are not modelled - a documented MVP limitation.
      </p>
    </Panel>
  )
}

/* ------------------------------------------------------------------ loads */
const LOAD_TYPES: { id: LoadType; label: string }[] = [
  { id: 'tension', label: 'Tension' },
  { id: 'compression', label: 'Compression' },
  { id: 'bending', label: 'Bending' },
  { id: 'shear', label: 'Shear' },
  { id: 'torsion', label: 'Torsion' },
  { id: 'custom', label: 'Custom' },
]

function LoadPanel() {
  const loads = useStore((state) => state.loads)
  const addLoad = useStore((state) => state.addLoad)
  const updateLoad = useStore((state) => state.updateLoad)
  const removeLoad = useStore((state) => state.removeLoad)
  const selectionMode = useStore((state) => state.selectionMode)
  const activeLoadId = useStore((state) => state.activeLoadId)
  const setSelectionMode = useStore((state) => state.setSelectionMode)
  const validateLoads = useStore((state) => state.validateLoads)
  const validation = useStore((state) => state.loadValidation)
  const model = useStore((state) => state.model)

  const totalWeight = loads.reduce((sum, load) => sum + load.weight, 0) || 1

  useEffect(() => {
    if (loads.length > 0) void validateLoads()
  }, [loads, validateLoads])

  return (
    <Panel title="5 · Load cases" badge={loads.length ? `${loads.length}` : 'none'}>
      {loads.map((load) => {
        const isTorsion = load.type === 'torsion'
        return (
          <div
            key={load.id}
            style={{
              marginBottom: 12,
              paddingBottom: 10,
              borderBottom: '1px solid var(--border)',
            }}
          >
            <div className="row" style={{ marginBottom: 6 }}>
              <input
                type="text"
                value={load.name}
                onChange={(event) => updateLoad(load.id, { name: event.target.value })}
              />
              <button
                className="btn small danger"
                style={{ flex: '0 0 auto' }}
                onClick={() => removeLoad(load.id)}
              >
                ×
              </button>
            </div>
            <div className="row">
              <label className="field">
                <span>Type</span>
                <select
                  value={load.type}
                  onChange={(event) =>
                    updateLoad(load.id, { type: event.target.value as LoadType })
                  }
                >
                  {LOAD_TYPES.map((type) => (
                    <option key={type.id} value={type.id}>
                      {type.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Weight ({((load.weight / totalWeight) * 100).toFixed(0)} %)</span>
                <input
                  type="number"
                  step="0.1"
                  min="0"
                  value={load.weight}
                  onChange={(event) =>
                    updateLoad(load.id, { weight: Number(event.target.value) })
                  }
                />
              </label>
            </div>

            {isTorsion ? (
              <Vector3Input
                value={load.torque_nm}
                unit="Nm"
                onChange={(next) => updateLoad(load.id, { torque_nm: next })}
              />
            ) : (
              <Vector3Input
                value={load.force_n}
                unit="N"
                onChange={(next) => updateLoad(load.id, { force_n: next })}
              />
            )}

            <div className="btn-group" style={{ marginTop: 6 }}>
              <button
                className={`btn small ${
                  selectionMode === 'load' && activeLoadId === load.id ? 'active' : ''
                }`}
                disabled={!model}
                onClick={() =>
                  setSelectionMode(
                    selectionMode === 'load' && activeLoadId === load.id ? 'none' : 'load',
                    load.id,
                  )
                }
              >
                {load.application_face_ids.length
                  ? `Applied to ${load.application_face_ids.length} faces`
                  : 'Pick loaded surface'}
              </button>
              {load.application_face_ids.length > 0 && (
                <button
                  className="btn small"
                  onClick={() => updateLoad(load.id, { application_face_ids: [] })}
                >
                  Clear
                </button>
              )}
            </div>
          </div>
        )
      })}

      <div className="btn-group">
        {LOAD_TYPES.slice(0, 5).map((type) => (
          <button key={type.id} className="btn small" onClick={() => addLoad(type.id)}>
            + {type.label}
          </button>
        ))}
      </div>

      {validation && validation.load_paths.length > 0 && (
        <>
          <p className="note">
            Resolved load paths (used by the solver):{' '}
            {validation.load_paths
              .map((path) => `${path.name} · lever ${path.lever_length_mm.toFixed(0)} mm`)
              .join(', ')}
            . {validation.stress_point_count} stress states evaluated per orientation.
          </p>
          {validation.warnings.map((warning) => (
            <p key={warning} className="note warn">
              {warning}
            </p>
          ))}
        </>
      )}
      <p className="note">
        Forces are defined in the coordinate system of the STL file, not of the build plate - the
        optimiser rotates the part, not the load. Leaving the loaded surface unselected makes the
        solver apply the load at the point furthest from the fixture, which is the worst case
        lever.
      </p>
    </Panel>
  )
}

/* -------------------------------------------------------------- objective */
const WEIGHT_LABELS: [keyof OptimizationWeights, string][] = [
  ['mechanical', 'Mechanical'],
  ['layer', 'Layer direction'],
  ['support', 'Support'],
  ['overhang', 'Overhang'],
  ['stability', 'Bed stability'],
  ['warping', 'Warping'],
  ['material', 'Material usage'],
  ['print_time', 'Print time'],
]

function ObjectivePanel() {
  const presets = useStore((state) => state.presets)
  const preset = useStore((state) => state.preset)
  const setPreset = useStore((state) => state.setPreset)
  const weights = useStore((state) => state.weights)
  const setWeight = useStore((state) => state.setWeight)
  const search = useStore((state) => state.search)
  const setSearch = useStore((state) => state.setSearch)

  const total = Object.values(weights).reduce((sum, value) => sum + value, 0) || 1

  return (
    <Panel title="6 · Optimisation objective" badge={preset}>
      <div className="btn-group" style={{ marginBottom: 10 }}>
        {presets
          .filter((entry) => entry.id !== 'custom')
          .map((entry) => (
            <button
              key={entry.id}
              className={`btn small ${preset === entry.id ? 'active' : ''}`}
              onClick={() => setPreset(entry.id as PresetId)}
            >
              {entry.label}
            </button>
          ))}
      </div>

      {WEIGHT_LABELS.map(([key, label]) => (
        <div key={key} style={{ marginBottom: 6 }}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              fontSize: 11,
              color: 'var(--text-dim)',
            }}
          >
            <span>{label}</span>
            <span className="mono">{((weights[key] / total) * 100).toFixed(0)} %</span>
          </div>
          <input
            type="range"
            min={0}
            max={1}
            step={0.01}
            value={weights[key]}
            onChange={(event) => setWeight(key, Number(event.target.value))}
          />
        </div>
      ))}
      <p className="note">
        Weights are normalised before use, so only their ratios matter. Moving any slider switches
        the preset to "custom".
      </p>

      <div className="row" style={{ marginTop: 8 }}>
        <label className="field">
          <span>Coarse step [°]</span>
          <input
            type="number"
            min={5}
            max={45}
            step={1}
            value={search.coarse_step_deg}
            onChange={(event) => setSearch({ coarse_step_deg: Number(event.target.value) })}
          />
        </label>
        <label className="field">
          <span>Results</span>
          <input
            type="number"
            min={1}
            max={10}
            value={search.max_results}
            onChange={(event) => setSearch({ max_results: Number(event.target.value) })}
          />
        </label>
      </div>
      <label className="check">
        <input
          type="checkbox"
          checked={search.enable_fine_stage}
          onChange={(event) => setSearch({ enable_fine_stage: event.target.checked })}
        />
        Final 1° refinement stage
      </label>
    </Panel>
  )
}

/* -------------------------------------------------------------------- run */
function RunPanel() {
  const optimize = useStore((state) => state.optimize)
  const optimizing = useStore((state) => state.optimizing)
  const job = useStore((state) => state.job)
  const printabilityOnly = useStore((state) => state.printabilityOnly)
  const setPrintabilityOnly = useStore((state) => state.setPrintabilityOnly)
  const blocker = useStore(READY_TO_OPTIMIZE)

  return (
    <Panel title="7 · Run">
      <label className="check">
        <input
          type="checkbox"
          checked={printabilityOnly}
          onChange={(event) => setPrintabilityOnly(event.target.checked)}
        />
        Printability only (no mechanical objective)
      </label>
      <button
        className="btn primary block"
        disabled={!!blocker || optimizing}
        onClick={() => void optimize()}
      >
        {optimizing ? 'Optimising…' : 'Optimise orientation'}
      </button>
      {blocker && <p className="note warn">{blocker}</p>}
      {optimizing && job && (
        <div style={{ marginTop: 10 }}>
          <div className="progress">
            <span style={{ width: `${Math.round(job.progress * 100)}%` }} />
          </div>
          <p className="note">{job.message}</p>
        </div>
      )}
    </Panel>
  )
}
