/**
 * Types mirroring the backend Pydantic schemas.
 *
 * Every vector is in **part coordinates** - the coordinate system of the
 * uploaded STL. Loads and fixed faces are defined on the part; the optimiser
 * searches for the rotation that places it on the build plate.
 */

export type Confidence = 'high' | 'medium' | 'low' | 'unknown'

export interface PropertyValue {
  value: number | null
  min: number | null
  max: number | null
  confidence: Confidence
  source: string | null
  basis: string | null
}

export interface RangeValue {
  value: number | null
  min: number | null
  max: number | null
  note: string | null
}

export interface Material {
  id: string
  name: string
  family: string
  fiber_filled: boolean
  requires_enclosure: boolean
  is_custom: boolean
  properties: {
    density: PropertyValue
    young_modulus: PropertyValue
    tensile_strength: PropertyValue
    layer_adhesion_factor: PropertyValue
    thermal_limit: PropertyValue
    max_volumetric_flow: PropertyValue
    warp_tendency: PropertyValue
  }
  print_defaults: {
    recommended_layer_height: RangeValue
    recommended_wall_count: RangeValue
    recommended_infill: RangeValue
    nozzle_temp_c: RangeValue
    bed_temp_c: RangeValue
  }
  notes: string[]
}

export interface CustomMaterial {
  name: string
  density_g_cm3: number
  young_modulus_mpa: number
  tensile_strength_mpa: number
  layer_adhesion_factor: number
  thermal_limit_c?: number | null
  warp_tendency?: number | null
  base_material_id?: string | null
}

export interface Printer {
  id: string
  name: string
  vendor: string
  build_volume_mm: [number, number, number]
  bed_size_mm: [number, number]
  default_nozzle_mm: number
  available_nozzles_mm: number[]
  enclosed: boolean
  notes: string[]
  source: string | null
}

export type LoadType =
  | 'tension'
  | 'compression'
  | 'bending'
  | 'shear'
  | 'torsion'
  | 'custom'

export interface LoadCase {
  id: string
  name: string
  type: LoadType
  force_n: [number, number, number]
  torque_nm: [number, number, number]
  weight: number
  application_face_ids: number[]
  application_point?: [number, number, number] | null
}

export interface Constraint {
  id: string
  name: string
  face_ids: number[]
  type: 'fixed'
}

export type PresetId = 'max_strength' | 'balanced' | 'fast' | 'lightweight' | 'custom'

export interface OptimizationWeights {
  mechanical: number
  layer: number
  support: number
  overhang: number
  stability: number
  warping: number
  material: number
  print_time: number
}

export interface PresetInfo {
  id: PresetId
  label: string
  weights: OptimizationWeights
  normalized: OptimizationWeights
}

export interface ProcessConfig {
  printer_id: string
  nozzle_diameter_mm: number
  layer_height_mm: number | null
  overhang_threshold_deg: number
  bed_contact_tolerance_mm: number
  allow_supports: boolean
}

export interface SearchConfig {
  coarse_step_deg: number
  refine_step_deg: number
  fine_step_deg: number
  enable_fine_stage: boolean
  refine_candidates: number
  fine_candidates: number
  include_face_normal_candidates: boolean
  max_results: number
}

export interface ModelSummary {
  id: string
  filename: string
  size_bytes: number
  stl_format: string
  uploaded_at: number
  triangle_count: number
  analysis_triangle_count: number
  decimated: boolean
  duplicate_of: string | null
}

export interface GeometryReport {
  validation: {
    is_watertight: boolean
    is_winding_consistent: boolean
    is_volume: boolean
    euler_number: number
    body_count: number
    boundary_edge_count: number
    degenerate_face_count: number
    duplicate_face_count: number
    repairs_applied: string[]
    volume_is_reliable: boolean
  }
  metrics: {
    triangle_count: number
    vertex_count: number
    bounding_box_min: [number, number, number]
    bounding_box_max: [number, number, number]
    dimensions: [number, number, number]
    volume_mm3: number
    surface_area_mm2: number
    convex_hull_volume_mm3: number
    solidity: number
    center_of_mass: [number, number, number]
    centroid: [number, number, number]
    aspect_ratio: number
    principal_inertia_components: [number, number, number]
    principal_inertia_axes: [number, number, number][]
    pca_axes: [number, number, number][]
    pca_extents: [number, number, number]
  }
  mass_estimate_g: number | null
  mass_material: string | null
  warnings: string[]
}

export interface ScoreComponent {
  key: string
  label: string
  value: number
  weight: number
  contribution: number
  detail: string | null
}

export interface MechanicalMetrics {
  max_utilization: number
  min_safety_factor: number
  critical_load_case: string | null
  critical_section_position_mm: [number, number, number] | null
  critical_section_area_mm2: number | null
  critical_section_modulus_mm3: number | null
  max_stress_mpa: number | null
  allowable_stress_mpa: number | null
  stress_direction: [number, number, number] | null
  angle_to_layer_plane_deg: number | null
  per_load_case: Record<string, unknown>[]
  method: string
  is_approximation: boolean
}

export interface OrientationCandidate {
  rank: number
  rotation_x: number
  rotation_y: number
  rotation_z: number
  build_direction_part_frame: [number, number, number]
  mechanical_score: number
  layer_score: number
  support_score: number
  overhang_score: number
  stability_score: number
  warping_score: number
  print_time_score: number
  material_score: number
  overall_score: number
  components: ScoreComponent[]
  support: {
    overhang_area_mm2: number
    overhang_area_fraction: number
    estimated_support_volume_mm3: number
    estimated_support_contact_area_mm2: number
    steepest_overhang_deg: number
    threshold_deg: number
  }
  bed: {
    contact_area_mm2: number
    footprint_area_mm2: number
    footprint_size_mm: [number, number]
    height_mm: number
    com_height_mm: number
    com_offset_from_footprint_center_mm: number
    com_inside_footprint: boolean
    slenderness: number
    fits_build_volume: boolean
  }
  warping: {
    risk_index: number
    level: 'LOW' | 'MEDIUM' | 'HIGH'
    max_bed_span_mm: number
    drivers: string[]
  }
  mechanical: MechanicalMetrics | null
  print_time: {
    estimated_minutes: number
    layer_count: number
    extruded_volume_mm3: number
    support_volume_mm3: number
    method: string
  }
  reasons: string[]
  warnings: string[]
}

export interface ConfidenceFactor {
  key: string
  label: string
  score: number
  weight: number
  detail: string
}

export interface ConfidenceReport {
  score: number
  factors: ConfidenceFactor[]
  limitations: string[]
}

export type CriticalRegionKind =
  | 'thin_wall'
  | 'narrow_section'
  | 'overhang'
  | 'bending_zone'
  | 'hole'
  | 'mounting_area'
  | 'weak_transition'

export interface CriticalRegion {
  id: string
  kind: CriticalRegionKind
  label: string
  severity: 'info' | 'warning' | 'critical'
  description: string
  face_ids: number[]
  position: [number, number, number] | null
  measurement_mm: number | null
  metric: Record<string, number | string | boolean>
}

export interface OrientationResult {
  model_id: string
  generated_at: number
  candidates: OrientationCandidate[]
  confidence: ConfidenceReport
  critical_regions: CriticalRegion[]
  weights: OptimizationWeights
  process: ProcessConfig
  material: Material
  evaluated_candidates: number
  duration_seconds: number
  warnings: string[]
  disclaimer: string
}

export interface PrintSettings {
  printer: string
  material: string
  nozzle: number
  orientation: { x: number; y: number; z: number }
  layer_height: number
  first_layer_height: number
  wall_loops: number
  top_layers: number
  bottom_layers: number
  infill_density: number
  infill_pattern: string
  supports: boolean
  support_angle: number
  support_type: string
  brim: boolean
  brim_width_mm: number
  reasons: string[]
  warnings: string[]
  wall_vs_infill: Record<string, unknown>
}

export interface Job {
  id: string
  model_id: string
  kind: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  progress: number
  message: string
  created_at: number
  updated_at: number
  error: { code: string; message: string } | null
  result_available: boolean
}

export interface RegionPick {
  face_ids: number[]
  area_mm2: number
  centroid: [number, number, number]
  normal: [number, number, number]
  is_planar: boolean
}

export interface LoadPathInfo {
  load_case_id: string
  name: string
  fixed_point: [number, number, number]
  application_point: [number, number, number]
  axis: [number, number, number]
  lever_length_mm: number
  force_n: [number, number, number]
  torque_nmm: [number, number, number]
  type: LoadType
  section_count: number
}

export interface LoadValidation {
  load_paths: LoadPathInfo[]
  stress_point_count: number
  warnings: string[]
  assumptions: string[]
  strengths_mpa: Record<string, number>
}
