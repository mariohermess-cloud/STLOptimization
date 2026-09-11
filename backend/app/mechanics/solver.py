"""Mechanical analysis abstraction and the approximate section solver.

Architecture
------------
``MechanicalSolver`` is the seam that a real FEM backend plugs into. A solver
turns (mesh, material, loads, constraints) into a :class:`MechanicalModel`: a
set of **stress states in part coordinates**, each one a full 3x3 stress
tensor at a located point.

That representation is deliberately solver agnostic. Everything downstream -
the anisotropy criterion, the mechanical score, the layer score, the safety
factors - only consumes stress tensors and the build direction. Swapping
``ApproximateMechanicalSolver`` for a CalculiX or FEniCS backend means
producing the same ``MechanicalModel`` from element results; no scoring,
optimisation or API code changes. See ``docs/architecture.md``.

THIS IS NOT FEM. ``ApproximateMechanicalSolver`` evaluates classical beam
formulae on the real cross-sections of the model along the load path. It has
no elements, no stiffness matrix and no compatibility conditions. It captures
section geometry and load direction; it does not capture stress
concentrations, contact, buckling, large deflection or redundant load paths.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
import trimesh

from app.geometry.sections import SectionProperties, section_at
from app.materials import database as materials_db
from app.mechanics import anisotropy
from app.schemas import Constraint, LoadCase, LoadType, Material

logger = logging.getLogger(__name__)

#: Transverse shear form factor for a solid section (tau_max = k * V / A).
#: k = 1.5 is exact for a rectangle and close for most printed profiles.
#: APPROXIMATION for arbitrary cross-sections.
TRANSVERSE_SHEAR_FACTOR = 1.5

#: Number of cross-sections cut along each load path.
DEFAULT_SECTION_SAMPLES = 12


@dataclass
class StressPoint:
    """A stress state in part coordinates produced by a solver."""

    load_case_id: str
    load_case_name: str
    position: np.ndarray
    tensor: np.ndarray
    label: str
    section_area_mm2: float
    section_modulus_mm3: float
    section_position: np.ndarray
    section_normal: np.ndarray
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class LoadPath:
    """Resolved geometry of one load case: where it is applied and reacted."""

    load_case_id: str
    name: str
    fixed_point: np.ndarray
    application_point: np.ndarray
    axis: np.ndarray
    lever_length_mm: float
    force_n: np.ndarray
    torque_nmm: np.ndarray
    declared_type: LoadType
    sections: list[SectionProperties] = field(default_factory=list)


@dataclass
class MechanicalEvaluation:
    """Result of evaluating a model for one build direction."""

    mechanical_score: float
    layer_score: float
    max_utilization: float
    min_safety_factor: float
    critical_load_case: str | None
    critical_point: StressPoint | None
    critical_detail: dict[str, Any]
    per_load_case: list[dict[str, Any]]


@dataclass
class MechanicalModel:
    solver_name: str
    is_approximation: bool
    points: list[StressPoint]
    load_weights: dict[str, float]
    material: Material
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    load_paths: list[LoadPath] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._tensors = (
            np.stack([point.tensor for point in self.points])
            if self.points
            else np.zeros((0, 3, 3))
        )
        self._sigma_par = materials_db.in_plane_tensile_strength(self.material)
        self._sigma_perp = materials_db.interlayer_tensile_strength(self.material)
        self._tau_par = materials_db.in_plane_shear_strength(self.material)
        self._tau_perp = materials_db.interlayer_shear_strength(self.material)
        self._von_mises = np.array([anisotropy.von_mises(t) for t in self._tensors])
        self._dominant = [anisotropy.dominant_stress_direction(t)[0] for t in self._tensors]

    @property
    def has_loads(self) -> bool:
        return bool(self.points)

    @property
    def strengths(self) -> dict[str, float]:
        return {
            "sigma_parallel_mpa": self._sigma_par,
            "sigma_perpendicular_mpa": self._sigma_perp,
            "tau_parallel_mpa": self._tau_par,
            "tau_perpendicular_mpa": self._tau_perp,
        }

    # -- evaluation --------------------------------------------------------
    def evaluate(self, build_dir: np.ndarray) -> MechanicalEvaluation:
        """Score one build direction against every stress state.

        Vectorised over all stress points: the traction on the layer plane is
        ``S d`` for every tensor at once.
        """
        if not self.points:
            return MechanicalEvaluation(100.0, 100.0, 0.0, float("inf"), None, None, {}, [])

        d = np.asarray(build_dir, dtype=float)
        d = d / max(np.linalg.norm(d), 1e-12)

        tractions = np.einsum("pij,j->pi", self._tensors, d)
        sigma_n = tractions @ d
        shear_vectors = tractions - sigma_n[:, None] * d[None, :]
        tau_s = np.linalg.norm(shear_vectors, axis=1)

        u_interlayer = np.sqrt(
            (np.clip(sigma_n, 0.0, None) / max(self._sigma_perp, 1e-9)) ** 2
            + (tau_s / max(self._tau_perp, 1e-9)) ** 2
        )
        u_bulk = self._von_mises / max(self._sigma_par, 1e-9)
        u_total = np.maximum(u_interlayer, u_bulk)

        with np.errstate(divide="ignore", invalid="ignore"):
            penalty = np.where(u_total > 1e-12, np.minimum(1.0, u_bulk / u_total), 1.0)

        alignment = np.array([float(np.dot(vec, d)) for vec in self._dominant])
        in_plane = 1.0 - alignment**2

        per_case: list[dict[str, Any]] = []
        weighted_mech = 0.0
        weighted_layer = 0.0
        total_weight = 0.0
        global_max_u = 0.0
        critical_index: int | None = None

        for case_id, weight in self.load_weights.items():
            indices = [i for i, p in enumerate(self.points) if p.load_case_id == case_id]
            if not indices:
                continue
            local = np.array(indices)
            governing = int(local[int(np.argmax(u_total[local]))])
            case_u = float(u_total[governing])
            case_penalty = float(penalty[governing])
            case_in_plane = float(in_plane[governing])

            stress_direction = self._dominant[governing]
            allowable = anisotropy.hankinson_allowable(
                self._sigma_par, self._sigma_perp, float(np.dot(stress_direction, d))
            )

            per_case.append(
                {
                    "load_case_id": case_id,
                    "load_case_name": self.points[governing].load_case_name,
                    "weight": weight,
                    "utilization": case_u,
                    "safety_factor": float("inf") if case_u <= 1e-12 else 1.0 / case_u,
                    "mechanical_score": 100.0 * case_penalty,
                    "layer_score": 100.0 * case_in_plane,
                    "interlayer_governs": bool(u_interlayer[governing] > u_bulk[governing]),
                    "interface_normal_stress_mpa": float(sigma_n[governing]),
                    "interface_shear_stress_mpa": float(tau_s[governing]),
                    "von_mises_mpa": float(self._von_mises[governing]),
                    "allowable_stress_mpa": allowable,
                    "angle_to_layer_plane_deg": float(
                        np.degrees(np.arcsin(min(1.0, abs(float(np.dot(stress_direction, d))))))
                    ),
                    "stress_direction": [float(v) for v in stress_direction],
                    "critical_point_label": self.points[governing].label,
                    "critical_point_position": [float(v) for v in self.points[governing].position],
                    "section_area_mm2": self.points[governing].section_area_mm2,
                    "section_modulus_mm3": self.points[governing].section_modulus_mm3,
                }
            )

            weighted_mech += weight * 100.0 * case_penalty
            weighted_layer += weight * 100.0 * case_in_plane
            total_weight += weight
            if case_u > global_max_u:
                global_max_u = case_u
                critical_index = governing

        if total_weight <= 0:
            return MechanicalEvaluation(100.0, 100.0, 0.0, float("inf"), None, None, {}, [])

        critical_point = self.points[critical_index] if critical_index is not None else None
        critical_case = per_case[int(np.argmax([c["utilization"] for c in per_case]))]

        return MechanicalEvaluation(
            mechanical_score=float(weighted_mech / total_weight),
            layer_score=float(weighted_layer / total_weight),
            max_utilization=float(global_max_u),
            min_safety_factor=float("inf") if global_max_u <= 1e-12 else float(1.0 / global_max_u),
            critical_load_case=critical_case["load_case_name"],
            critical_point=critical_point,
            critical_detail=critical_case,
            per_load_case=per_case,
        )


class MechanicalSolver(ABC):
    """Abstract mechanical backend. See module docstring."""

    name: str = "abstract"
    is_approximation: bool = True

    @abstractmethod
    def analyze(
        self,
        mesh: trimesh.Trimesh,
        material: Material,
        loads: Sequence[LoadCase],
        constraints: Sequence[Constraint],
    ) -> MechanicalModel:
        raise NotImplementedError


class ApproximateMechanicalSolver(MechanicalSolver):
    """Section-based beam analysis on the real geometry of the part.

    For every load case the solver

    1. locates the reaction (area-weighted centroid of the fixed faces) and the
       point of application (centroid of the loaded faces, an explicit point,
       or the material point furthest from the reaction),
    2. cuts ``section_samples`` cross-sections perpendicular to the load path,
    3. computes the internal force resultants at each section and turns them
       into a stress tensor at two representative points of the section - the
       extreme fibre (bending plus axial plus torsion) and the neutral axis
       (transverse shear plus torsion).

    The resulting stress tensors are in part coordinates and do not depend on
    the print orientation, which is exactly why the orientation search can
    evaluate hundreds of orientations cheaply.
    """

    name = "ApproximateMechanicalSolver"
    is_approximation = True

    def __init__(self, section_samples: int = DEFAULT_SECTION_SAMPLES) -> None:
        self.section_samples = max(3, int(section_samples))

    # -- public API --------------------------------------------------------
    def analyze(
        self,
        mesh: trimesh.Trimesh,
        material: Material,
        loads: Sequence[LoadCase],
        constraints: Sequence[Constraint],
    ) -> MechanicalModel:
        assumptions = [
            "APPROXIMATION: classical beam theory evaluated on real cross-sections of the part. This is not a finite element analysis.",
            "Fixed faces are treated as an ideally rigid clamp; support stiffness, bolt preload and contact are not modelled.",
            "Stress concentrations at fillets, holes and sharp transitions are not resolved. Geometric risk regions are reported separately.",
            f"Transverse shear uses tau = {TRANSVERSE_SHEAR_FACTOR} * V / A (exact for a rectangular section).",
            "Torsion uses tau = T * r / J with J the polar second moment of area (exact for circular sections only).",
            "Material is treated as linear elastic up to its tensile strength; buckling and creep are not evaluated.",
        ]
        warnings: list[str] = []
        points: list[StressPoint] = []
        load_paths: list[LoadPath] = []

        fixed_point, fixed_area = self._fixed_reference(mesh, constraints)
        if fixed_point is None:
            warnings.append(
                "No fixed surface was given; the centre of mass is used as the reaction point."
            )
            fixed_point = np.asarray(mesh.center_mass, dtype=float)
        elif fixed_area is not None and fixed_area < 1e-6:
            warnings.append("The selected fixed faces have negligible area.")

        weights: dict[str, float] = {}
        for index, load in enumerate(loads):
            case_id = load.id or f"load-{index + 1}"
            path = self._resolve_path(mesh, load, case_id, fixed_point)
            if path is None:
                warnings.append(
                    f"Load case '{load.name}' could not be resolved onto the geometry and was skipped."
                )
                continue
            load_paths.append(path)
            case_points = self._stress_points(mesh, path)
            if not case_points:
                warnings.append(
                    f"No usable cross-section was found along the load path of '{load.name}'. "
                    "A single section through the centre of mass is used instead."
                )
                case_points = self._fallback_points(mesh, path)
            points.extend(case_points)
            if case_points:
                weights[case_id] = max(float(load.weight), 0.0)

            self._check_declared_type(path, warnings)

        total = sum(weights.values())
        if total <= 0 and weights:
            weights = {key: 1.0 / len(weights) for key in weights}
        elif total > 0:
            weights = {key: value / total for key, value in weights.items()}

        return MechanicalModel(
            solver_name=self.name,
            is_approximation=True,
            points=points,
            load_weights=weights,
            material=material,
            assumptions=assumptions,
            warnings=warnings,
            load_paths=load_paths,
        )

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _face_centroid(mesh: trimesh.Trimesh, face_ids: Sequence[int]) -> tuple[np.ndarray | None, float]:
        valid = [int(f) for f in face_ids if 0 <= int(f) < len(mesh.faces)]
        if not valid:
            return None, 0.0
        areas = np.asarray(mesh.area_faces, dtype=float)[valid]
        centers = np.asarray(mesh.triangles_center, dtype=float)[valid]
        total = float(areas.sum())
        if total <= 1e-12:
            return centers.mean(axis=0), 0.0
        return (centers * areas[:, None]).sum(axis=0) / total, total

    def _fixed_reference(
        self, mesh: trimesh.Trimesh, constraints: Sequence[Constraint]
    ) -> tuple[np.ndarray | None, float | None]:
        face_ids: list[int] = []
        for constraint in constraints:
            face_ids.extend(constraint.face_ids)
        if not face_ids:
            return None, None
        centroid, area = self._face_centroid(mesh, face_ids)
        return centroid, area

    def _resolve_path(
        self,
        mesh: trimesh.Trimesh,
        load: LoadCase,
        case_id: str,
        fixed_point: np.ndarray,
    ) -> LoadPath | None:
        force = np.asarray(load.force_n, dtype=float)
        torque = np.asarray(load.torque_nm, dtype=float) * 1000.0  # Nm -> N*mm

        application: np.ndarray | None = None
        if load.application_face_ids:
            application, _ = self._face_centroid(mesh, load.application_face_ids)
        if application is None and load.application_point is not None:
            application = np.asarray(load.application_point, dtype=float)
        if application is None:
            # Default: the material point furthest from the reaction, which is
            # the worst case lever arm for the given fixture.
            vertices = np.asarray(mesh.vertices, dtype=float)
            distances = np.linalg.norm(vertices - fixed_point, axis=1)
            application = vertices[int(np.argmax(distances))]

        lever = application - fixed_point
        length = float(np.linalg.norm(lever))
        if length < 1e-6:
            # Load applied at the fixture: use the force direction as the path.
            direction = force if np.linalg.norm(force) > 1e-9 else torque
            if np.linalg.norm(direction) < 1e-9:
                return None
            axis = direction / np.linalg.norm(direction)
            extent = float(np.max(mesh.extents))
            application = fixed_point + axis * extent * 0.5
            lever = application - fixed_point
            length = float(np.linalg.norm(lever))
        axis = lever / length

        if np.linalg.norm(force) < 1e-12 and np.linalg.norm(torque) < 1e-12:
            return None

        return LoadPath(
            load_case_id=case_id,
            name=load.name,
            fixed_point=np.asarray(fixed_point, dtype=float),
            application_point=application,
            axis=axis,
            lever_length_mm=length,
            force_n=force,
            torque_nmm=torque,
            declared_type=load.type,
        )

    def _stress_points(self, mesh: trimesh.Trimesh, path: LoadPath) -> list[StressPoint]:
        points: list[StressPoint] = []
        fractions = np.linspace(0.03, 0.97, self.section_samples)
        for index, fraction in enumerate(fractions):
            origin = path.fixed_point + path.axis * (path.lever_length_mm * float(fraction))
            section = section_at(mesh, origin, path.axis)
            if section is None or section.area < 1e-6:
                continue
            path.sections.append(section)
            points.extend(self._section_stress(path, section, index))
        return points

    def _fallback_points(self, mesh: trimesh.Trimesh, path: LoadPath) -> list[StressPoint]:
        force = path.force_n
        normal = force / np.linalg.norm(force) if np.linalg.norm(force) > 1e-9 else path.axis
        section = section_at(mesh, np.asarray(mesh.center_mass, dtype=float), normal)
        if section is None:
            return []
        fallback = LoadPath(
            load_case_id=path.load_case_id,
            name=path.name,
            fixed_point=path.fixed_point,
            application_point=path.application_point,
            axis=normal,
            lever_length_mm=path.lever_length_mm,
            force_n=path.force_n,
            torque_nmm=path.torque_nmm,
            declared_type=path.declared_type,
        )
        return self._section_stress(fallback, section, 0)

    def _section_stress(
        self, path: LoadPath, section: SectionProperties, index: int
    ) -> list[StressPoint]:
        axis = section.normal
        centroid = section.centroid_3d
        area = section.area

        # Internal resultants carried by this section: everything on the load
        # side of the cut acts on it.
        lever = path.application_point - centroid
        moment = np.cross(lever, path.force_n) + path.torque_nmm  # N*mm

        axial = float(np.dot(path.force_n, axis))
        transverse = path.force_n - axial * axis
        torsion = float(np.dot(moment, axis))
        bending = moment - torsion * axis

        basis = section.to_3d[:3, :2]  # columns: section frame X and Y in 3D
        bending_2d = np.array([float(np.dot(bending, basis[:, 0])), float(np.dot(bending, basis[:, 1]))])
        bending_magnitude = float(np.linalg.norm(bending))

        second_moment = section.second_moment_about(bending_2d)
        c_distance, fibre_dir_2d = section.extreme_fibre_distance(bending_2d)
        fibre_dir = section.axis_2d_to_3d(fibre_dir_2d)

        sigma_axial = axial / area if area > 1e-9 else 0.0
        if second_moment > 1e-9 and bending_magnitude > 1e-9:
            sigma_bending = bending_magnitude * c_distance / second_moment
            section_modulus = second_moment / max(c_distance, 1e-9)
        else:
            sigma_bending = 0.0
            section_modulus = 0.0

        polar = section.polar_moment
        tau_torsion = (
            abs(torsion) * section.max_radius / polar if polar > 1e-9 and abs(torsion) > 1e-9 else 0.0
        )
        shear_magnitude = float(np.linalg.norm(transverse))
        tau_shear = (
            TRANSVERSE_SHEAR_FACTOR * shear_magnitude / area if area > 1e-9 else 0.0
        )

        # Tangential direction at the extreme fibre (torsional shear direction).
        tangential = np.cross(axis, fibre_dir)
        tangential_norm = np.linalg.norm(tangential)
        tangential = tangential / tangential_norm if tangential_norm > 1e-9 else np.zeros(3)

        results: list[StressPoint] = []

        # Point A - extreme fibre: axial + bending normal stress, torsional shear.
        sigma_total = abs(sigma_axial) + sigma_bending if sigma_bending > 0 else sigma_axial
        tensor_a = sigma_total * np.outer(axis, axis)
        if tau_torsion > 0 and tangential_norm > 1e-9:
            tensor_a = tensor_a + tau_torsion * (
                np.outer(axis, tangential) + np.outer(tangential, axis)
            )
        if np.abs(tensor_a).max() > 1e-9:
            results.append(
                StressPoint(
                    load_case_id=path.load_case_id,
                    load_case_name=path.name,
                    position=centroid + fibre_dir * c_distance,
                    tensor=tensor_a,
                    label=f"section {index + 1}, extreme fibre",
                    section_area_mm2=float(area),
                    section_modulus_mm3=float(section_modulus),
                    section_position=np.asarray(centroid, dtype=float),
                    section_normal=np.asarray(axis, dtype=float),
                    detail={
                        "sigma_axial_mpa": float(sigma_axial),
                        "sigma_bending_mpa": float(sigma_bending),
                        "tau_torsion_mpa": float(tau_torsion),
                        "bending_moment_nmm": bending_magnitude,
                        "axial_force_n": axial,
                        "torsion_moment_nmm": float(torsion),
                        "second_moment_mm4": float(second_moment),
                        "extreme_fibre_mm": float(c_distance),
                    },
                )
            )

        # Point B - neutral axis: transverse shear + torsional shear.
        if tau_shear > 1e-9 or tau_torsion > 1e-9:
            shear_dir = (
                transverse / shear_magnitude if shear_magnitude > 1e-9 else tangential
            )
            tensor_b = np.zeros((3, 3))
            if tau_shear > 0 and shear_magnitude > 1e-9:
                tensor_b = tensor_b + tau_shear * (
                    np.outer(axis, shear_dir) + np.outer(shear_dir, axis)
                )
            if tau_torsion > 0 and tangential_norm > 1e-9:
                tensor_b = tensor_b + tau_torsion * (
                    np.outer(axis, tangential) + np.outer(tangential, axis)
                )
            if np.abs(tensor_b).max() > 1e-9:
                results.append(
                    StressPoint(
                        load_case_id=path.load_case_id,
                        load_case_name=path.name,
                        position=np.asarray(centroid, dtype=float),
                        tensor=tensor_b,
                        label=f"section {index + 1}, neutral axis",
                        section_area_mm2=float(area),
                        section_modulus_mm3=float(section_modulus),
                        section_position=np.asarray(centroid, dtype=float),
                        section_normal=np.asarray(axis, dtype=float),
                        detail={
                            "tau_transverse_mpa": float(tau_shear),
                            "tau_torsion_mpa": float(tau_torsion),
                            "shear_force_n": shear_magnitude,
                            "torsion_moment_nmm": float(torsion),
                        },
                    )
                )
        return results

    @staticmethod
    def _check_declared_type(path: LoadPath, warnings: list[str]) -> None:
        """Flag a mismatch between the declared load type and the vectors.

        The analysis always follows the actual force and torque vectors; the
        declared type is a label. When they disagree the user should know.
        """
        force = path.force_n
        magnitude = float(np.linalg.norm(force))
        if magnitude < 1e-9:
            return
        along = abs(float(np.dot(force, path.axis))) / magnitude
        if path.declared_type in (LoadType.tension, LoadType.compression) and along < 0.5:
            warnings.append(
                f"Load case '{path.name}' is declared as {path.declared_type.value}, but the force "
                "is mostly transverse to the load path, so it is dominated by bending. "
                "The analysis follows the force vector."
            )
        if path.declared_type is LoadType.bending and along > 0.9:
            warnings.append(
                f"Load case '{path.name}' is declared as bending, but the force is nearly parallel "
                "to the load path, so it acts as an axial load. The analysis follows the force vector."
            )
