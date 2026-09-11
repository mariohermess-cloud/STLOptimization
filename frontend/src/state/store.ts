/**
 * Application state.
 *
 * One zustand store, split into clearly separated slices - model, geometry,
 * material, loads, constraints, optimisation, settings and UI. Components
 * subscribe to the exact fields they need, so nothing is passed down through
 * props and a change in one slice does not re-render the rest of the app.
 */

import { create } from 'zustand'
import { ApiError, api, waitForJob, type OrientationRequestBody } from '../api/client'
import type {
  Constraint,
  CriticalRegion,
  CustomMaterial,
  GeometryReport,
  Job,
  LoadCase,
  LoadType,
  LoadValidation,
  Material,
  ModelSummary,
  OptimizationWeights,
  OrientationResult,
  PresetId,
  PresetInfo,
  PrintSettings,
  Printer,
  ProcessConfig,
} from '../api/types'

const CUSTOM_MATERIAL_KEY = 'peo.customMaterials.v1'

export type SelectionMode = 'none' | 'fixation' | 'load'

export interface Toast {
  id: number
  kind: 'error' | 'info' | 'success'
  message: string
}

const DEFAULT_PROCESS: ProcessConfig = {
  printer_id: 'bambu_x1c',
  nozzle_diameter_mm: 0.4,
  layer_height_mm: null,
  overhang_threshold_deg: 45,
  bed_contact_tolerance_mm: 0.2,
  allow_supports: true,
}

const DEFAULT_WEIGHTS: OptimizationWeights = {
  mechanical: 0.35,
  layer: 0.25,
  support: 0.15,
  overhang: 0.1,
  stability: 0.05,
  warping: 0.05,
  material: 0.05,
  print_time: 0.0,
}

const DEFAULT_SEARCH = {
  coarse_step_deg: 15,
  refine_step_deg: 5,
  fine_step_deg: 1,
  enable_fine_stage: true,
  refine_candidates: 8,
  fine_candidates: 3,
  include_face_normal_candidates: true,
  max_results: 5,
}

function loadCustomMaterials(): CustomMaterial[] {
  try {
    const raw = window.localStorage.getItem(CUSTOM_MATERIAL_KEY)
    return raw ? (JSON.parse(raw) as CustomMaterial[]) : []
  } catch {
    return []
  }
}

function persistCustomMaterials(materials: CustomMaterial[]): void {
  try {
    window.localStorage.setItem(CUSTOM_MATERIAL_KEY, JSON.stringify(materials))
  } catch {
    /* private browsing or disabled storage - custom materials are then
       session-only, which is acceptable for this feature */
  }
}

let toastCounter = 0
let loadCounter = 0

export interface AppState {
  // --- catalogue ------------------------------------------------------
  materials: Material[]
  printers: Printer[]
  presets: PresetInfo[]
  materialDisclaimer: string
  catalogueLoaded: boolean

  // --- model ----------------------------------------------------------
  model: ModelSummary | null
  geometry: GeometryReport | null
  uploading: boolean

  // --- material -------------------------------------------------------
  materialId: string
  customMaterials: CustomMaterial[]
  activeCustomMaterial: CustomMaterial | null

  // --- process --------------------------------------------------------
  process: ProcessConfig

  // --- loads and constraints ------------------------------------------
  loads: LoadCase[]
  constraints: Constraint[]
  selectionMode: SelectionMode
  activeLoadId: string | null
  loadValidation: LoadValidation | null

  // --- optimisation ---------------------------------------------------
  preset: PresetId
  weights: OptimizationWeights
  search: typeof DEFAULT_SEARCH
  printabilityOnly: boolean
  job: Job | null
  optimizing: boolean
  result: OrientationResult | null
  selectedRank: number
  compareRank: number | null

  // --- print settings -------------------------------------------------
  settings: PrintSettings | null
  settingsLoading: boolean

  // --- ui --------------------------------------------------------------
  toasts: Toast[]
  highlightedRegion: CriticalRegion | null
  showOverlays: {
    buildDirection: boolean
    layerPlane: boolean
    forces: boolean
    fixations: boolean
    criticalRegions: boolean
    orientedPreview: boolean
  }
  mobileTab: 'input' | 'model' | 'analysis'

  // --- actions ---------------------------------------------------------
  loadCatalogue: () => Promise<void>
  upload: (file: File) => Promise<void>
  reset: () => void
  setMaterial: (id: string) => void
  saveCustomMaterial: (material: CustomMaterial) => void
  deleteCustomMaterial: (name: string) => void
  useCustomMaterial: (material: CustomMaterial | null) => void
  setProcess: (patch: Partial<ProcessConfig>) => void
  addLoad: (type: LoadType) => void
  updateLoad: (id: string, patch: Partial<LoadCase>) => void
  removeLoad: (id: string) => void
  setSelectionMode: (mode: SelectionMode, loadId?: string | null) => void
  applyFaceSelection: (faceIds: number[], additive: boolean) => void
  clearConstraint: () => void
  validateLoads: () => Promise<void>
  setPreset: (preset: PresetId) => void
  setWeight: (key: keyof OptimizationWeights, value: number) => void
  setSearch: (patch: Partial<typeof DEFAULT_SEARCH>) => void
  setPrintabilityOnly: (value: boolean) => void
  optimize: () => Promise<void>
  selectCandidate: (rank: number) => void
  setCompareRank: (rank: number | null) => void
  recommendSettings: () => Promise<void>
  exportJson: (includeReport: boolean) => Promise<void>
  toggleOverlay: (key: keyof AppState['showOverlays']) => void
  setHighlightedRegion: (region: CriticalRegion | null) => void
  setMobileTab: (tab: AppState['mobileTab']) => void
  pushToast: (kind: Toast['kind'], message: string) => void
  dismissToast: (id: number) => void
}

export const useStore = create<AppState>((set, get) => ({
  materials: [],
  printers: [],
  presets: [],
  materialDisclaimer: '',
  catalogueLoaded: false,

  model: null,
  geometry: null,
  uploading: false,

  materialId: 'petg',
  customMaterials: loadCustomMaterials(),
  activeCustomMaterial: null,

  process: { ...DEFAULT_PROCESS },

  loads: [],
  constraints: [],
  selectionMode: 'none',
  activeLoadId: null,
  loadValidation: null,

  preset: 'balanced',
  weights: { ...DEFAULT_WEIGHTS },
  search: { ...DEFAULT_SEARCH },
  printabilityOnly: false,
  job: null,
  optimizing: false,
  result: null,
  selectedRank: 1,
  compareRank: null,

  settings: null,
  settingsLoading: false,

  toasts: [],
  highlightedRegion: null,
  showOverlays: {
    buildDirection: true,
    layerPlane: true,
    forces: true,
    fixations: true,
    criticalRegions: true,
    orientedPreview: true,
  },
  mobileTab: 'input',

  // ------------------------------------------------------------------
  loadCatalogue: async () => {
    try {
      const [materials, printers, presets, disclaimer] = await Promise.all([
        api.materials(),
        api.printers(),
        api.presets(),
        api.materialsDisclaimer(),
      ])
      const balanced = presets.find((preset) => preset.id === 'balanced')
      set({
        materials,
        printers,
        presets,
        materialDisclaimer: disclaimer.disclaimer,
        catalogueLoaded: true,
        weights: balanced ? { ...balanced.weights } : get().weights,
      })
    } catch (error) {
      get().pushToast('error', error instanceof ApiError ? error.message : String(error))
    }
  },

  upload: async (file) => {
    set({ uploading: true })
    try {
      const model = await api.upload(file)
      const geometry = await api.analyze(model.id, get().materialId)
      set({
        model,
        geometry,
        loads: [],
        constraints: [],
        result: null,
        settings: null,
        job: null,
        loadValidation: null,
        selectedRank: 1,
        compareRank: null,
        highlightedRegion: null,
        mobileTab: 'model',
      })
      geometry.warnings.forEach((warning) => get().pushToast('info', warning))
    } catch (error) {
      get().pushToast('error', error instanceof ApiError ? error.message : String(error))
    } finally {
      set({ uploading: false })
    }
  },

  reset: () =>
    set({
      model: null,
      geometry: null,
      loads: [],
      constraints: [],
      result: null,
      settings: null,
      job: null,
      loadValidation: null,
      selectionMode: 'none',
      activeLoadId: null,
      highlightedRegion: null,
      selectedRank: 1,
      compareRank: null,
    }),

  setMaterial: (id) => {
    set({ materialId: id, activeCustomMaterial: null })
    const model = get().model
    if (model) {
      api
        .analyze(model.id, id)
        .then((geometry) => set({ geometry }))
        .catch(() => undefined)
    }
  },

  saveCustomMaterial: (material) => {
    const others = get().customMaterials.filter((entry) => entry.name !== material.name)
    const next = [...others, material]
    persistCustomMaterials(next)
    set({ customMaterials: next, activeCustomMaterial: material })
    get().pushToast('success', `Custom material "${material.name}" saved in this browser.`)
  },

  deleteCustomMaterial: (name) => {
    const next = get().customMaterials.filter((entry) => entry.name !== name)
    persistCustomMaterials(next)
    const active = get().activeCustomMaterial
    set({
      customMaterials: next,
      activeCustomMaterial: active?.name === name ? null : active,
    })
  },

  useCustomMaterial: (material) => set({ activeCustomMaterial: material }),

  setProcess: (patch) => set({ process: { ...get().process, ...patch } }),

  addLoad: (type) => {
    loadCounter += 1
    const id = `load-${loadCounter}-${Date.now().toString(36)}`
    const load: LoadCase = {
      id,
      name: `Load case ${get().loads.length + 1}`,
      type,
      force_n: type === 'torsion' ? [0, 0, 0] : [0, 0, -100],
      torque_nm: type === 'torsion' ? [0, 0, 10] : [0, 0, 0],
      weight: 1,
      application_face_ids: [],
    }
    set({ loads: [...get().loads, load], activeLoadId: id, loadValidation: null })
  },

  updateLoad: (id, patch) =>
    set({
      loads: get().loads.map((load) => (load.id === id ? { ...load, ...patch } : load)),
      loadValidation: null,
    }),

  removeLoad: (id) =>
    set({
      loads: get().loads.filter((load) => load.id !== id),
      activeLoadId: get().activeLoadId === id ? null : get().activeLoadId,
      loadValidation: null,
    }),

  setSelectionMode: (mode, loadId = null) =>
    set({ selectionMode: mode, activeLoadId: loadId ?? get().activeLoadId }),

  applyFaceSelection: (faceIds, additive) => {
    const { selectionMode, activeLoadId } = get()
    if (selectionMode === 'fixation') {
      const existing = additive ? (get().constraints[0]?.face_ids ?? []) : []
      const merged = Array.from(new Set([...existing, ...faceIds]))
      set({
        constraints: [{ id: 'fixture', name: 'Fixed surface', face_ids: merged, type: 'fixed' }],
        loadValidation: null,
      })
    } else if (selectionMode === 'load' && activeLoadId) {
      const load = get().loads.find((entry) => entry.id === activeLoadId)
      if (!load) return
      const existing = additive ? load.application_face_ids : []
      const merged = Array.from(new Set([...existing, ...faceIds]))
      get().updateLoad(activeLoadId, { application_face_ids: merged })
    }
  },

  clearConstraint: () => set({ constraints: [], loadValidation: null }),

  validateLoads: async () => {
    const { model } = get()
    if (!model) return
    try {
      const validation = await api.validateLoads(model.id, buildRequest(get()))
      set({ loadValidation: validation })
    } catch (error) {
      if (error instanceof ApiError && error.code !== 'no_load_case') {
        get().pushToast('error', error.message)
      }
    }
  },

  setPreset: (preset) => {
    const info = get().presets.find((entry) => entry.id === preset)
    set({ preset, weights: info ? { ...info.weights } : get().weights })
  },

  setWeight: (key, value) =>
    set({ preset: 'custom', weights: { ...get().weights, [key]: value } }),

  setSearch: (patch) => set({ search: { ...get().search, ...patch } }),

  setPrintabilityOnly: (value) => set({ printabilityOnly: value }),

  optimize: async () => {
    const state = get()
    if (!state.model) return
    set({ optimizing: true, job: null, settings: null })
    try {
      const job = await api.startOptimization(state.model.id, buildRequest(state))
      set({ job })
      const finished = await waitForJob(job.id, (update) => set({ job: update }))
      if (finished.status === 'failed') {
        set({ optimizing: false })
        get().pushToast('error', finished.error?.message ?? 'The optimisation failed.')
        return
      }
      const result = await api.jobResult(finished.id)
      set({ result, selectedRank: 1, compareRank: null, mobileTab: 'analysis' })
      result.warnings.forEach((warning) => get().pushToast('info', warning))
      await get().recommendSettings()
    } catch (error) {
      get().pushToast('error', error instanceof ApiError ? error.message : String(error))
    } finally {
      set({ optimizing: false })
    }
  },

  selectCandidate: (rank) => {
    set({ selectedRank: rank })
    void get().recommendSettings()
  },

  setCompareRank: (rank) => set({ compareRank: rank }),

  recommendSettings: async () => {
    const state = get()
    if (!state.model || !state.result) return
    set({ settingsLoading: true })
    try {
      const settings = await api.recommendSettings(state.model.id, {
        material: materialSelection(state),
        process: state.process,
        preset: state.preset === 'custom' ? 'balanced' : state.preset,
        candidate_rank: state.selectedRank,
        loads: state.loads,
        constraints: state.constraints,
      })
      set({ settings })
    } catch (error) {
      get().pushToast('error', error instanceof ApiError ? error.message : String(error))
    } finally {
      set({ settingsLoading: false })
    }
  },

  exportJson: async (includeReport) => {
    const state = get()
    if (!state.model) return
    try {
      const payload = await api.exportSettings(state.model.id, state.selectedRank, includeReport)
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      const base = state.model.filename.replace(/\.stl$/i, '')
      anchor.href = url
      anchor.download = includeReport
        ? `${base}-analysis-report.json`
        : `${base}-print-settings.json`
      anchor.click()
      URL.revokeObjectURL(url)
      get().pushToast('success', 'Export downloaded.')
    } catch (error) {
      get().pushToast('error', error instanceof ApiError ? error.message : String(error))
    }
  },

  toggleOverlay: (key) =>
    set({ showOverlays: { ...get().showOverlays, [key]: !get().showOverlays[key] } }),

  setHighlightedRegion: (region) => set({ highlightedRegion: region }),

  setMobileTab: (tab) => set({ mobileTab: tab }),

  pushToast: (kind, message) => {
    toastCounter += 1
    const toast: Toast = { id: toastCounter, kind, message }
    set({ toasts: [...get().toasts, toast].slice(-5) })
    if (kind !== 'error') {
      window.setTimeout(() => get().dismissToast(toast.id), 7000)
    }
  },

  dismissToast: (id) => set({ toasts: get().toasts.filter((toast) => toast.id !== id) }),
}))

function materialSelection(state: AppState) {
  return state.activeCustomMaterial
    ? { custom: state.activeCustomMaterial }
    : { material_id: state.materialId }
}

export function buildRequest(state: AppState): OrientationRequestBody {
  return {
    material: materialSelection(state),
    loads: state.loads,
    constraints: state.constraints,
    weights: state.weights,
    preset: state.preset,
    search: state.search,
    process: state.process,
    printability_only: state.printabilityOnly,
  }
}

/** The candidate the user currently has selected, if any. */
export function selectedCandidate(state: AppState) {
  return state.result?.candidates.find((candidate) => candidate.rank === state.selectedRank) ?? null
}

export function activeMaterial(state: AppState): Material | null {
  if (state.activeCustomMaterial) return null
  return state.materials.find((material) => material.id === state.materialId) ?? null
}

export function activePrinter(state: AppState): Printer | null {
  return state.printers.find((printer) => printer.id === state.process.printer_id) ?? null
}

export const READY_TO_OPTIMIZE = (state: AppState): string | null => {
  if (!state.model) return 'Upload an STL file first.'
  if (state.printabilityOnly) return null
  if (state.loads.length === 0) return 'Define at least one load case.'
  if (state.constraints.length === 0 || state.constraints[0].face_ids.length === 0) {
    return 'Select at least one fixed surface in the viewer.'
  }
  return null
}
