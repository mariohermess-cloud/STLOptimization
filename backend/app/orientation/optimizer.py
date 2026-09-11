"""The orientation engine.

``OrientationOptimizer`` takes a mesh, a material, load cases, constraints and
optimisation weights, and returns ranked orientations with a full score
breakdown and a human readable justification for each one.

Search strategy (three stages, all configurable):

1. **Coarse** - a 15 degree grid of build directions on the unit sphere, plus
   the six axis-aligned directions, plus the directions that rest each large
   flat facet of the part on the plate.
2. **Refine** - a 5 degree grid inside a cone around the best coarse
   candidates.
3. **Fine** - a 1 degree grid around the best refined candidates (optional).

The spin about the build axis is not searched: it is solved exactly per
candidate as the minimum-area footprint rectangle. See
``app/orientation/search.py`` and ``docs/orientation-engine.md``.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import numpy as np

from app.core.config import settings
from app.geometry.prepared import PreparedMesh
from app.geometry.transforms import (
    euler_xyz_from_rotation,
    minimum_footprint_z_rotation,
    rotation_about_z,
    rotation_aligning_vector_to_z,
)
from app.mechanics.solver import MechanicalEvaluation, MechanicalModel
from app.orientation import search
from app.scoring import printability
from app.schemas import Material, OptimizationWeights, Printer, ProcessConfig

logger = logging.getLogger(__name__)

SCORE_KEYS = (
    "mechanical",
    "layer",
    "support",
    "overhang",
    "stability",
    "warping",
    "material",
    "print_time",
)

SCORE_LABELS = {
    "mechanical": "Mechanical",
    "layer": "Layer direction",
    "support": "Support",
    "overhang": "Overhang",
    "stability": "Bed stability",
    "warping": "Warping",
    "material": "Material usage",
    "print_time": "Print time",
}


@dataclass
class RawCandidate:
    direction: np.ndarray
    rotation: np.ndarray
    euler: tuple[float, float, float]
    overhangs: printability.OverhangResult
    bed: printability.BedResult
    warping: printability.WarpResult
    print_time: printability.TimeResult
    mechanical: MechanicalEvaluation | None
    scores: dict[str, float]
    fits_build_volume: bool
    footprint_mm: tuple[float, float]
    overall: float = 0.0
    components: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class OptimizationOutcome:
    candidates: list[RawCandidate]
    evaluated: int
    duration_seconds: float
    weights: dict[str, float]
    warnings: list[str]


class OrientationOptimizer:
    """Ranked print orientations for a part under load."""

    def __init__(
        self,
        prepared: PreparedMesh,
        material: Material,
        mechanical_model: MechanicalModel,
        process: ProcessConfig,
        printer: Printer,
        weights: OptimizationWeights,
        search_config,
    ) -> None:
        self.prepared = prepared
        self.material = material
        self.model = mechanical_model
        self.process = process
        self.printer = printer
        self.weights = weights
        self.search = search_config
        self.layer_height = process.layer_height_mm or self._default_layer_height()

    def _default_layer_height(self) -> float:
        recommended = self.material.print_defaults.recommended_layer_height.value
        if recommended:
            # Scale the material's recommendation (given for a 0.4 mm nozzle)
            # to the nozzle actually in use.
            return float(recommended) * (self.process.nozzle_diameter_mm / 0.4)
        return 0.5 * self.process.nozzle_diameter_mm

    # -- public -----------------------------------------------------------
    def optimize(
        self,
        extra_orientations: Sequence[Sequence[float]] = (),
        progress: Callable[[float, str], None] | None = None,
    ) -> OptimizationOutcome:
        started = time.perf_counter()
        warnings: list[str] = []

        normalized = self._effective_weights(warnings)

        def report(fraction: float, message: str) -> None:
            if progress:
                progress(fraction, message)

        # --- stage 1: coarse -------------------------------------------
        report(0.05, "Generating coarse orientation candidates")
        directions = [search.sphere_grid(self.search.coarse_step_deg), search.axis_aligned_directions()]
        if self.search.include_face_normal_candidates:
            directions.append(search.flat_face_directions(self.prepared.mesh))
        for euler in extra_orientations:
            rotation = _rotation_from_euler(euler)
            directions.append(np.array([rotation.T @ np.array([0.0, 0.0, 1.0])]))
        coarse = search.deduplicate(np.vstack([d for d in directions if len(d)]), tolerance_deg=2.0)

        report(0.10, f"Evaluating {len(coarse)} coarse orientations")
        evaluated: list[RawCandidate] = self._evaluate_many(coarse)
        total_evaluated = len(evaluated)

        # --- stage 2: refinement ----------------------------------------
        ranked = self._rank(evaluated, normalized)
        seeds = [c.direction for c in ranked[: self.search.refine_candidates]]
        if seeds:
            report(0.45, "Refining the best candidates at 5 degrees")
            refine_dirs = np.vstack(
                [
                    search.refine_around(
                        seed, self.search.coarse_step_deg, self.search.refine_step_deg
                    )
                    for seed in seeds
                ]
            )
            refine_dirs = self._drop_known(search.deduplicate(refine_dirs, 1.0), evaluated, 1.0)
            if len(refine_dirs):
                evaluated.extend(self._evaluate_many(refine_dirs))
                total_evaluated += len(refine_dirs)

        # --- stage 3: fine ----------------------------------------------
        if self.search.enable_fine_stage:
            ranked = self._rank(evaluated, normalized)
            seeds = [c.direction for c in ranked[: self.search.fine_candidates]]
            if seeds:
                report(0.75, "Final refinement at 1 degree")
                fine_dirs = np.vstack(
                    [
                        search.refine_around(
                            seed, self.search.refine_step_deg, self.search.fine_step_deg
                        )
                        for seed in seeds
                    ]
                )
                fine_dirs = self._drop_known(search.deduplicate(fine_dirs, 0.5), evaluated, 0.5)
                if len(fine_dirs):
                    evaluated.extend(self._evaluate_many(fine_dirs))
                    total_evaluated += len(fine_dirs)

        report(0.92, "Ranking orientations")
        ranked = self._rank(evaluated, normalized)
        unique = _spread(ranked, min_separation_deg=8.0, limit=self.search.max_results)

        if not any(candidate.fits_build_volume for candidate in ranked):
            warnings.append(
                f"No orientation fits the {self.printer.name} build volume "
                f"({'x'.join(str(int(v)) for v in self.printer.build_volume_mm)} mm). "
                "The part has to be split or scaled."
            )

        duration = time.perf_counter() - started
        logger.info(
            "orientation search finished",
            extra={
                "operation": "optimize_orientation",
                "evaluated": total_evaluated,
                "duration_ms": round(duration * 1000, 1),
                "faces": self.prepared.face_count,
            },
        )
        return OptimizationOutcome(
            candidates=unique,
            evaluated=total_evaluated,
            duration_seconds=duration,
            weights=normalized,
            warnings=warnings,
        )

    # -- weighting --------------------------------------------------------
    def _effective_weights(self, warnings: list[str]) -> dict[str, float]:
        """Normalised weights, with mechanical weight redistributed when there
        is no mechanical model to score against."""
        normalized = self.weights.normalized()
        if self.model.has_loads:
            return normalized

        mechanical_weight = normalized["mechanical"] + normalized["layer"]
        if mechanical_weight > 0:
            warnings.append(
                "No mechanical load was defined, so the mechanical and layer-direction "
                "weights were redistributed across the printability criteria. The "
                "result is a printability ranking, not a strength ranking."
            )
        remaining = {k: v for k, v in normalized.items() if k not in ("mechanical", "layer")}
        total = sum(remaining.values())
        if total <= 0:
            remaining = {k: 1.0 for k in remaining}
            total = float(len(remaining))
        rescaled = {k: v / total for k, v in remaining.items()}
        rescaled["mechanical"] = 0.0
        rescaled["layer"] = 0.0
        return {key: rescaled[key] for key in SCORE_KEYS}

    # -- evaluation -------------------------------------------------------
    def _evaluate_many(self, directions: np.ndarray) -> list[RawCandidate]:
        if len(directions) == 0:
            return []
        workers = max(1, int(settings.optimizer_workers))
        if workers == 1 or len(directions) < 8:
            return [self.evaluate(direction) for direction in directions]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(self.evaluate, directions))

    def evaluate(self, direction: np.ndarray) -> RawCandidate:
        """Full score set for a single build direction."""
        d = np.asarray(direction, dtype=float)
        d = d / max(np.linalg.norm(d), 1e-12)

        overhangs = printability.analyse_overhangs(
            self.prepared,
            d,
            threshold_deg=self.process.overhang_threshold_deg,
            bed_tolerance_mm=self.process.bed_contact_tolerance_mm,
        )
        bed = printability.analyse_bed(
            self.prepared,
            d,
            bed_tolerance_mm=self.process.bed_contact_tolerance_mm,
            overhangs=overhangs,
        )
        warp = printability.analyse_warping(
            bed,
            warp_tendency=self.material.properties.warp_tendency.value,
            enclosed=self.printer.enclosed,
            material_name=self.material.name,
        )
        time_result = printability.estimate_print_time(
            self.prepared,
            bed,
            overhangs,
            layer_height_mm=self.layer_height,
            max_volumetric_flow=self.material.properties.max_volumetric_flow.value,
        )

        mechanical = self.model.evaluate(d) if self.model.has_loads else None

        rotation = rotation_aligning_vector_to_z(d)
        aligned = self.prepared.hull_points @ rotation.T
        z_angle, _, footprint = minimum_footprint_z_rotation(aligned[:, :2])
        rotation = rotation_about_z(z_angle) @ rotation
        euler = euler_xyz_from_rotation(rotation)

        # The footprint rectangle can be spun freely on the plate, so compare
        # the sorted rectangle sides against the sorted plate sides.
        build_volume = self.printer.build_volume_mm
        plate = sorted(build_volume[:2])
        rectangle = sorted(footprint)
        fits = (
            rectangle[0] <= plate[0] + 1e-6
            and rectangle[1] <= plate[1] + 1e-6
            and bed.height_mm <= build_volume[2] + 1e-6
        )

        scores = {
            "mechanical": mechanical.mechanical_score if mechanical else 100.0,
            "layer": mechanical.layer_score if mechanical else 100.0,
            "support": overhangs.support_score,
            "overhang": overhangs.score,
            "stability": bed.score,
            "warping": warp.score,
            "material": printability.material_score(self.prepared, overhangs),
            "print_time": 0.0,  # filled in during ranking (pool-relative)
        }

        return RawCandidate(
            direction=d,
            rotation=rotation,
            euler=euler,
            overhangs=overhangs,
            bed=bed,
            warping=warp,
            print_time=time_result,
            mechanical=mechanical,
            scores=scores,
            fits_build_volume=bool(fits),
            footprint_mm=footprint,
        )

    # -- ranking ----------------------------------------------------------
    def _rank(self, candidates: list[RawCandidate], weights: dict[str, float]) -> list[RawCandidate]:
        """Score and sort. Print time is scored relative to the fastest
        orientation found, which is stated in the report."""
        if not candidates:
            return []
        times = np.array([c.print_time.minutes for c in candidates])
        fastest = float(times[times > 0].min()) if np.any(times > 0) else 1.0

        for candidate in candidates:
            candidate.scores["print_time"] = 100.0 * min(
                1.0, fastest / max(candidate.print_time.minutes, 1e-6)
            )
            components = []
            overall = 0.0
            for key in SCORE_KEYS:
                weight = weights.get(key, 0.0)
                value = float(np.clip(candidate.scores[key], 0.0, 100.0))
                candidate.scores[key] = value
                contribution = weight * value
                overall += contribution
                components.append(
                    {
                        "key": key,
                        "label": SCORE_LABELS[key],
                        "value": round(value, 1),
                        "weight": round(weight, 4),
                        "contribution": round(contribution, 2),
                    }
                )
            candidate.components = components
            candidate.overall = float(overall)

        fitting = [c for c in candidates if c.fits_build_volume]
        pool = fitting if fitting else candidates
        return sorted(pool, key=lambda c: c.overall, reverse=True)

    @staticmethod
    def _drop_known(
        directions: np.ndarray, known: list[RawCandidate], tolerance_deg: float
    ) -> np.ndarray:
        if len(directions) == 0 or not known:
            return directions
        existing = np.stack([c.direction for c in known])
        threshold = float(np.cos(np.radians(tolerance_deg)))
        keep = [i for i, d in enumerate(directions) if float((existing @ d).max()) <= threshold]
        return directions[keep] if keep else np.zeros((0, 3))


def _rotation_from_euler(euler: Sequence[float]) -> np.ndarray:
    from app.geometry.transforms import rotation_from_euler_xyz

    values = list(euler) + [0.0, 0.0, 0.0]
    return rotation_from_euler_xyz(values[0], values[1], values[2])


def _spread(ranked: list[RawCandidate], min_separation_deg: float, limit: int) -> list[RawCandidate]:
    """Pick the top candidates while keeping them visually distinct.

    Without this the top five would often be the same orientation five times,
    one degree apart, which tells the user nothing.
    """
    selected: list[RawCandidate] = []
    threshold = float(np.cos(np.radians(min_separation_deg)))
    for candidate in ranked:
        if any(float(np.dot(candidate.direction, other.direction)) > threshold for other in selected):
            continue
        selected.append(candidate)
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        chosen = {id(candidate) for candidate in selected}
        for candidate in ranked:
            if id(candidate) in chosen:
                continue
            selected.append(candidate)
            if len(selected) >= limit:
                break
    return selected
