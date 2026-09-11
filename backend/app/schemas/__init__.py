"""Pydantic request/response models for the public API.

Coordinate convention for every vector in this module: **part coordinates**,
i.e. the coordinate system of the uploaded STL file. Loads and constraints are
defined on the part, and the optimiser searches for the rotation that places
the part on the build plate. This is what makes the search meaningful - if
loads were defined in world coordinates they would rotate with the part.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Vector3 = list[float]


# --------------------------------------------------------------------------
# materials
# --------------------------------------------------------------------------
class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"
    unknown = "unknown"


class PropertyValue(BaseModel):
    """A material property with its uncertainty and provenance."""

    value: float | None = None
    min: float | None = None
    max: float | None = None
    confidence: Confidence = Confidence.unknown
    source: str | None = None
    basis: str | None = None

    @property
    def known(self) -> bool:
        return self.value is not None


class RangeValue(BaseModel):
    value: float | None = None
    min: float | None = None
    max: float | None = None
    note: str | None = None


class PrintDefaults(BaseModel):
    recommended_layer_height: RangeValue = RangeValue()
    recommended_wall_count: RangeValue = RangeValue()
    recommended_infill: RangeValue = RangeValue()
    nozzle_temp_c: RangeValue = RangeValue()
    bed_temp_c: RangeValue = RangeValue()


class MaterialProperties(BaseModel):
    density: PropertyValue = PropertyValue()
    young_modulus: PropertyValue = PropertyValue()
    tensile_strength: PropertyValue = PropertyValue()
    layer_adhesion_factor: PropertyValue = PropertyValue()
    thermal_limit: PropertyValue = PropertyValue()
    max_volumetric_flow: PropertyValue = PropertyValue()
    warp_tendency: PropertyValue = PropertyValue()


class Material(BaseModel):
    id: str
    name: str
    family: str = "custom"
    fiber_filled: bool = False
    requires_enclosure: bool = False
    is_custom: bool = False
    properties: MaterialProperties = MaterialProperties()
    print_defaults: PrintDefaults = PrintDefaults()
    notes: list[str] = Field(default_factory=list)


class CustomMaterial(BaseModel):
    """User supplied material. Only the four properties the mechanical model
    actually needs are mandatory."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=64)
    density_g_cm3: float = Field(gt=0.1, lt=10.0)
    young_modulus_mpa: float = Field(gt=0.1, lt=500_000)
    tensile_strength_mpa: float = Field(gt=0.1, lt=5_000)
    layer_adhesion_factor: float = Field(gt=0.01, le=1.0)
    thermal_limit_c: float | None = Field(default=None, gt=-50, lt=500)
    warp_tendency: float | None = Field(default=None, ge=0.0, le=1.0)
    max_volumetric_flow_mm3_s: float | None = Field(default=None, gt=0.1, lt=100)
    base_material_id: str | None = None


class MaterialSelection(BaseModel):
    material_id: str | None = None
    custom: CustomMaterial | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "MaterialSelection":
        if (self.material_id is None) == (self.custom is None):
            raise ValueError("Provide exactly one of 'material_id' or 'custom'.")
        return self


# --------------------------------------------------------------------------
# printer
# --------------------------------------------------------------------------
class Printer(BaseModel):
    id: str
    name: str
    vendor: str
    build_volume_mm: Vector3
    bed_size_mm: list[float]
    default_nozzle_mm: float
    available_nozzles_mm: list[float]
    enclosed: bool = False
    heated_bed_max_c: float | None = None
    max_acceleration_mm_s2: float | None = None
    max_print_speed_mm_s: float | None = None
    travel_speed_mm_s: float | None = None
    notes: list[str] = Field(default_factory=list)
    source: str | None = None


# --------------------------------------------------------------------------
# loads and constraints
# --------------------------------------------------------------------------
class LoadType(str, Enum):
    tension = "tension"
    compression = "compression"
    bending = "bending"
    shear = "shear"
    torsion = "torsion"
    custom = "custom"


class LoadCase(BaseModel):
    """A single load applied to the part, in part coordinates.

    ``force_n`` is a force in newtons. ``torque_nm`` is a moment in newton
    metres about the axis given by its direction (right hand rule). A torsion
    load case uses ``torque_nm``; every other type uses ``force_n``.
    """

    id: str | None = None
    name: str = "Load case"
    type: LoadType = LoadType.custom
    force_n: Vector3 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    torque_nm: Vector3 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    weight: float = Field(default=1.0, ge=0.0)
    application_face_ids: list[int] = Field(default_factory=list)
    application_point: Vector3 | None = None

    @model_validator(mode="after")
    def _check_magnitude(self) -> "LoadCase":
        for field_name in ("force_n", "torque_nm"):
            vec = getattr(self, field_name)
            if len(vec) != 3:
                raise ValueError(f"'{field_name}' must have exactly three components.")
        if self.type is LoadType.torsion:
            if max(abs(v) for v in self.torque_nm) <= 0:
                raise ValueError("A torsion load case needs a non-zero torque vector.")
        elif max(abs(v) for v in self.force_n) <= 0 and max(abs(v) for v in self.torque_nm) <= 0:
            raise ValueError("A load case needs a non-zero force or torque.")
        return self


class Constraint(BaseModel):
    """A fixed boundary.

    MVP limitation: the selected faces are treated as an ideally rigid,
    fully clamped support. No support stiffness, bolt preload or contact
    behaviour is modelled. See ``docs/physics.md``.
    """

    id: str | None = None
    name: str = "Fixed surface"
    face_ids: list[int] = Field(default_factory=list)
    type: Literal["fixed"] = "fixed"


# --------------------------------------------------------------------------
# optimisation configuration
# --------------------------------------------------------------------------
class Preset(str, Enum):
    max_strength = "max_strength"
    balanced = "balanced"
    fast = "fast"
    lightweight = "lightweight"
    custom = "custom"


class OptimizationWeights(BaseModel):
    """Relative importance of each score component.

    Values are normalised to sum to 1 before use; the numbers below are the
    documented defaults from ``docs/orientation-engine.md`` and are the only
    place in the code where default weights are defined.
    """

    mechanical: float = Field(default=0.35, ge=0.0)
    layer: float = Field(default=0.25, ge=0.0)
    support: float = Field(default=0.15, ge=0.0)
    overhang: float = Field(default=0.10, ge=0.0)
    stability: float = Field(default=0.05, ge=0.0)
    warping: float = Field(default=0.05, ge=0.0)
    material: float = Field(default=0.05, ge=0.0)
    print_time: float = Field(default=0.00, ge=0.0)

    def normalized(self) -> dict[str, float]:
        raw = self.model_dump()
        total = sum(raw.values())
        if total <= 0:
            raise ValueError("At least one optimisation weight must be greater than zero.")
        return {key: value / total for key, value in raw.items()}


class SearchConfig(BaseModel):
    coarse_step_deg: float = Field(default=15.0, ge=5.0, le=45.0)
    refine_step_deg: float = Field(default=5.0, ge=1.0, le=15.0)
    fine_step_deg: float = Field(default=1.0, ge=0.5, le=5.0)
    enable_fine_stage: bool = True
    refine_candidates: int = Field(default=8, ge=1, le=32)
    fine_candidates: int = Field(default=3, ge=1, le=10)
    include_face_normal_candidates: bool = True
    max_results: int = Field(default=5, ge=1, le=25)


class ProcessConfig(BaseModel):
    """Process parameters the geometric analysis depends on."""

    printer_id: str = "bambu_x1c"
    nozzle_diameter_mm: float = Field(default=0.4, gt=0.05, le=2.0)
    layer_height_mm: float | None = Field(default=None, gt=0.02, le=1.2)
    overhang_threshold_deg: float = Field(default=45.0, ge=20.0, le=80.0)
    bed_contact_tolerance_mm: float = Field(default=0.2, ge=0.0, le=5.0)
    allow_supports: bool = True


class OrientationRequest(BaseModel):
    material: MaterialSelection
    loads: list[LoadCase] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    weights: OptimizationWeights = OptimizationWeights()
    preset: Preset = Preset.custom
    search: SearchConfig = SearchConfig()
    process: ProcessConfig = ProcessConfig()
    printability_only: bool = False
    fixed_orientations: list[Vector3] = Field(
        default_factory=list,
        description="Optional extra Euler XYZ orientations (degrees) to evaluate and rank alongside the search results.",
    )


# --------------------------------------------------------------------------
# results
# --------------------------------------------------------------------------
class ScoreComponent(BaseModel):
    key: str
    label: str
    value: float
    weight: float
    contribution: float
    detail: str | None = None


class SupportMetrics(BaseModel):
    overhang_area_mm2: float
    overhang_area_fraction: float
    estimated_support_volume_mm3: float
    estimated_support_contact_area_mm2: float
    steepest_overhang_deg: float
    threshold_deg: float


class BedMetrics(BaseModel):
    contact_area_mm2: float
    footprint_area_mm2: float
    footprint_size_mm: list[float]
    height_mm: float
    com_height_mm: float
    com_offset_from_footprint_center_mm: float
    com_inside_footprint: bool
    slenderness: float
    fits_build_volume: bool


class WarpingMetrics(BaseModel):
    risk_index: float
    level: Literal["LOW", "MEDIUM", "HIGH"]
    max_bed_span_mm: float
    drivers: list[str] = Field(default_factory=list)


class MechanicalMetrics(BaseModel):
    max_utilization: float
    min_safety_factor: float
    critical_load_case: str | None = None
    critical_section_position_mm: Vector3 | None = None
    critical_section_area_mm2: float | None = None
    critical_section_modulus_mm3: float | None = None
    max_stress_mpa: float | None = None
    allowable_stress_mpa: float | None = None
    stress_direction: Vector3 | None = None
    angle_to_layer_plane_deg: float | None = None
    per_load_case: list[dict[str, Any]] = Field(default_factory=list)
    method: str = "approximate-beam-section"
    is_approximation: bool = True


class PrintTimeMetrics(BaseModel):
    estimated_minutes: float
    layer_count: int
    extruded_volume_mm3: float
    support_volume_mm3: float
    method: str = "flow-limited estimate (APPROXIMATION)"


class OrientationCandidate(BaseModel):
    rank: int
    rotation_x: float
    rotation_y: float
    rotation_z: float
    build_direction_part_frame: Vector3
    mechanical_score: float
    layer_score: float
    support_score: float
    overhang_score: float
    stability_score: float
    warping_score: float
    print_time_score: float
    material_score: float
    overall_score: float
    components: list[ScoreComponent] = Field(default_factory=list)
    support: SupportMetrics
    bed: BedMetrics
    warping: WarpingMetrics
    mechanical: MechanicalMetrics | None = None
    print_time: PrintTimeMetrics
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ConfidenceFactor(BaseModel):
    key: str
    label: str
    score: float
    weight: float
    detail: str


class ConfidenceReport(BaseModel):
    score: float
    factors: list[ConfidenceFactor]
    limitations: list[str]


class CriticalRegion(BaseModel):
    id: str
    kind: Literal[
        "thin_wall",
        "narrow_section",
        "overhang",
        "bending_zone",
        "hole",
        "mounting_area",
        "weak_transition",
    ]
    label: str
    severity: Literal["info", "warning", "critical"]
    description: str
    face_ids: list[int] = Field(default_factory=list)
    position: Vector3 | None = None
    measurement_mm: float | None = None
    metric: dict[str, Any] = Field(default_factory=dict)


class PrintSettings(BaseModel):
    printer: str
    material: str
    nozzle: float
    orientation: dict[str, float]
    layer_height: float
    first_layer_height: float
    wall_loops: int
    top_layers: int
    bottom_layers: int
    infill_density: float
    infill_pattern: str
    supports: bool
    support_angle: float
    support_type: str
    brim: bool
    brim_width_mm: float
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    wall_vs_infill: dict[str, Any] = Field(default_factory=dict)


class GeometryReport(BaseModel):
    validation: dict[str, Any]
    metrics: dict[str, Any]
    mass_estimate_g: float | None = None
    mass_material: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ModelSummary(BaseModel):
    id: str
    filename: str
    size_bytes: int
    stl_format: str
    uploaded_at: float
    triangle_count: int
    analysis_triangle_count: int
    decimated: bool
    duplicate_of: str | None = None


class OrientationResult(BaseModel):
    model_id: str
    generated_at: float
    candidates: list[OrientationCandidate]
    confidence: ConfidenceReport
    critical_regions: list[CriticalRegion]
    weights: dict[str, float]
    process: ProcessConfig
    material: Material
    evaluated_candidates: int
    duration_seconds: float
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class Job(BaseModel):
    id: str
    model_id: str
    kind: str
    status: JobStatus
    progress: float = 0.0
    message: str = ""
    created_at: float
    updated_at: float
    error: dict[str, Any] | None = None
    result_available: bool = False


class RegionPickRequest(BaseModel):
    face_id: int = Field(ge=0)
    angle_tolerance_deg: float = Field(default=20.0, ge=0.0, le=90.0)
    max_faces: int = Field(default=20000, ge=1, le=500000)


class RegionPickResponse(BaseModel):
    face_ids: list[int]
    area_mm2: float
    centroid: Vector3
    normal: Vector3
    is_planar: bool


class SettingsRequest(BaseModel):
    material: MaterialSelection
    process: ProcessConfig = ProcessConfig()
    preset: Preset = Preset.balanced
    candidate_rank: int = Field(default=1, ge=1)
    loads: list[LoadCase] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)


class ExportRequest(BaseModel):
    candidate_rank: int = Field(default=1, ge=1)
    include_report: bool = True
