/**
 * Typed API client.
 *
 * The backend reports failures as `{ error: { code, message, details } }`.
 * Those are turned into an `ApiError` carrying the machine readable code, so
 * the UI can react to a specific condition ("no load case") while still
 * showing the sentence the backend wrote for the user.
 */

import type {
  ConfidenceReport,
  Constraint,
  CustomMaterial,
  GeometryReport,
  Job,
  LoadCase,
  LoadValidation,
  Material,
  ModelSummary,
  OptimizationWeights,
  OrientationResult,
  PresetInfo,
  PrintSettings,
  Printer,
  ProcessConfig,
  RegionPick,
  SearchConfig,
} from './types'

export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly details: Record<string, unknown>

  constructor(message: string, code: string, status: number, details: Record<string, unknown> = {}) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.details = details
  }
}

const BASE = ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, init)
  } catch {
    throw new ApiError(
      'The backend could not be reached. Check that the API container is running.',
      'network_error',
      0,
    )
  }

  if (!response.ok) {
    let code = 'internal_error'
    let message = `Request failed with status ${response.status}.`
    let details: Record<string, unknown> = {}
    try {
      const body = await response.json()
      if (body?.error) {
        code = body.error.code ?? code
        message = body.error.message ?? message
        details = body.error.details ?? {}
      }
    } catch {
      /* the body was not JSON - keep the generic message */
    }
    throw new ApiError(message, code, response.status, details)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export interface MaterialSelection {
  material_id?: string
  custom?: CustomMaterial
}

export interface OrientationRequestBody {
  material: MaterialSelection
  loads: LoadCase[]
  constraints: Constraint[]
  weights: OptimizationWeights
  preset: string
  search: SearchConfig
  process: ProcessConfig
  printability_only: boolean
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  health: () => request<{ status: string; version: string }>('/health'),
  materials: () => request<Material[]>('/api/materials'),
  materialsDisclaimer: () => request<{ disclaimer: string }>('/api/materials/disclaimer'),
  printers: () => request<Printer[]>('/api/printers'),
  presets: () => request<PresetInfo[]>('/api/presets'),
  limits: () =>
    request<{
      max_upload_bytes: number
      max_faces: number
      analysis_face_budget: number
      model_ttl_seconds: number
      accepted_extensions: string[]
    }>('/api/limits'),

  upload: async (file: File): Promise<ModelSummary> => {
    const form = new FormData()
    form.append('file', file)
    return request<ModelSummary>('/api/models/upload', { method: 'POST', body: form })
  },

  model: (id: string) => request<ModelSummary>(`/api/models/${id}`),
  meshUrl: (id: string) => `${BASE}/api/models/${id}/mesh.stl`,

  analyze: (id: string, materialId?: string) =>
    request<GeometryReport>(
      `/api/models/${id}/analyze${materialId ? `?material_id=${encodeURIComponent(materialId)}` : ''}`,
      { method: 'POST' },
    ),

  pickRegion: (id: string, faceId: number, tolerance = 20) =>
    request<RegionPick>(
      `/api/models/${id}/pick-region`,
      json({ face_id: faceId, angle_tolerance_deg: tolerance }),
    ),

  validateLoads: (id: string, body: OrientationRequestBody) =>
    request<LoadValidation>(`/api/models/${id}/loads`, json(body)),

  startOptimization: (id: string, body: OrientationRequestBody) =>
    request<Job>(`/api/models/${id}/optimize-orientation`, json(body)),

  job: (jobId: string) => request<Job>(`/api/jobs/${jobId}`),
  jobResult: (jobId: string) => request<OrientationResult>(`/api/jobs/${jobId}/result`),
  results: (id: string) => request<OrientationResult>(`/api/models/${id}/results`),

  recommendSettings: (
    id: string,
    body: {
      material: MaterialSelection
      process: ProcessConfig
      preset: string
      candidate_rank: number
      loads: LoadCase[]
      constraints: Constraint[]
    },
  ) => request<PrintSettings>(`/api/models/${id}/recommend-settings`, json(body)),

  exportSettings: (id: string, candidateRank: number, includeReport: boolean) =>
    request<Record<string, unknown>>(
      `/api/models/${id}/export`,
      json({ candidate_rank: candidateRank, include_report: includeReport }),
    ),

  report: (id: string, candidateRank: number) =>
    request<Record<string, unknown>>(`/api/models/${id}/report?candidate_rank=${candidateRank}`),
}

/** Poll a job until it finishes, reporting progress on the way. */
export async function waitForJob(
  jobId: string,
  onProgress: (job: Job) => void,
  intervalMs = 400,
  signal?: AbortSignal,
): Promise<Job> {
  for (;;) {
    if (signal?.aborted) throw new ApiError('Cancelled.', 'cancelled', 0)
    const job = await api.job(jobId)
    onProgress(job)
    if (job.status === 'completed' || job.status === 'failed') return job
    await new Promise((resolve) => setTimeout(resolve, intervalMs))
  }
}

export type { ConfidenceReport }
