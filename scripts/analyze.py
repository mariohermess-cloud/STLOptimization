#!/usr/bin/env python3
"""Headless analysis: STL in, orientation ranking and print settings out.

The web UI selects the fixed and loaded faces by clicking them. Without a
viewer those regions have to be described some other way, so this tool takes
**face selectors** - simple predicates on the position of a triangle's
centroid in the STL's own coordinate system:

    z<2        every triangle whose centroid lies below z = 2 mm
    x>55       every triangle whose centroid lies beyond x = 55 mm
    y<=-10     less-or-equal and greater-or-equal also work

That is deliberately crude but unambiguous: for the usual case - a part
clamped at one end and loaded at the other - it is exactly enough, and the
tool prints how many triangles and how much area each selector matched so a
wrong selector is obvious rather than silent.

Examples
--------
    # Cantilever: clamped at x < -55, 200 N downwards at the far end
    python scripts/analyze.py test-data/beam.stl \\
        --material petg-cf --preset max_strength \\
        --fix "x<-55" --load-at "x>55" --force 0 0 -200 --load-type bending

    # Printability only, no mechanical objective
    python scripts/analyze.py test-data/bracket.stl --printability-only

    python scripts/analyze.py --list-materials
"""

from __future__ import annotations

import argparse
import json
import operator
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import numpy as np  # noqa: E402

from app.materials import database as materials_db  # noqa: E402
from app.printing.printers import all_printers  # noqa: E402
from app.schemas import (  # noqa: E402
    Constraint,
    LoadCase,
    LoadType,
    MaterialSelection,
    OrientationRequest,
    Preset,
    ProcessConfig,
    SearchConfig,
    SettingsRequest,
)
from app.services import analysis  # noqa: E402

SELECTOR = re.compile(r"^\s*([xyz])\s*(<=|>=|<|>)\s*(-?\d+(?:\.\d+)?)\s*$", re.IGNORECASE)
OPERATORS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}
AXES = {"x": 0, "y": 1, "z": 2}


def select_faces(mesh, expression: str) -> list[int]:
    """Triangle indices whose centroid satisfies the selector expression."""
    match = SELECTOR.match(expression)
    if not match:
        raise SystemExit(
            f"Could not read the face selector {expression!r}. "
            "Expected something like 'z<2', 'x>55' or 'y<=-10'."
        )
    axis, op, value = match.group(1).lower(), match.group(2), float(match.group(3))
    centroids = np.asarray(mesh.triangles_center, dtype=float)[:, AXES[axis]]
    mask = OPERATORS[op](centroids, value)
    return [int(index) for index in np.flatnonzero(mask)]


def describe_selection(mesh, face_ids: list[int], label: str) -> None:
    if not face_ids:
        print(f"  {label:<18} no triangles matched")
        return
    areas = np.asarray(mesh.area_faces, dtype=float)[face_ids]
    centers = np.asarray(mesh.triangles_center, dtype=float)[face_ids]
    total = float(areas.sum())
    centroid = (centers * areas[:, None]).sum(axis=0) / total if total > 0 else centers.mean(axis=0)
    print(
        f"  {label:<18} {len(face_ids):>6} triangles, {total:>9.1f} mm^2, "
        f"centroid ({centroid[0]:.1f}, {centroid[1]:.1f}, {centroid[2]:.1f})"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="analyze.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("stl", nargs="?", help="Path to the STL file")

    catalogue = parser.add_argument_group("catalogue")
    catalogue.add_argument("--list-materials", action="store_true")
    catalogue.add_argument("--list-printers", action="store_true")

    setup = parser.add_argument_group("material and process")
    setup.add_argument("--material", default="petg", help="Material id (default: petg)")
    setup.add_argument("--printer", default="bambu_x1c", help="Printer id (default: bambu_x1c)")
    setup.add_argument("--nozzle", type=float, default=0.4, help="Nozzle diameter in mm")
    setup.add_argument("--layer-height", type=float, default=None, help="Override layer height in mm")
    setup.add_argument(
        "--overhang-threshold",
        type=float,
        default=45.0,
        help="Face inclination from the plate below which support is needed (default: 45)",
    )
    setup.add_argument(
        "--preset",
        default="balanced",
        choices=[preset.value for preset in Preset if preset is not Preset.custom],
        help="Optimisation priority (default: balanced)",
    )

    mechanics = parser.add_argument_group("load case")
    mechanics.add_argument("--fix", action="append", default=[], metavar="SELECTOR",
                           help="Face selector for a fixed surface; repeatable")
    mechanics.add_argument("--load-at", action="append", default=[], metavar="SELECTOR",
                           help="Face selector for the loaded surface; repeatable")
    mechanics.add_argument("--force", nargs=3, type=float, metavar=("FX", "FY", "FZ"),
                           help="Force vector in newtons, in STL coordinates")
    mechanics.add_argument("--torque", nargs=3, type=float, metavar=("TX", "TY", "TZ"),
                           help="Torque vector in newton metres, in STL coordinates")
    mechanics.add_argument("--load-type", default="custom",
                           choices=[load.value for load in LoadType],
                           help="Label for the load case (the analysis follows the vectors)")
    mechanics.add_argument("--printability-only", action="store_true",
                           help="Rank printability without a mechanical objective")

    search = parser.add_argument_group("search")
    search.add_argument("--coarse-step", type=float, default=15.0, help="Stage 1 step in degrees")
    search.add_argument("--results", type=int, default=5, help="Number of orientations to report")
    search.add_argument("--no-fine-stage", action="store_true", help="Skip the 1 degree stage")

    output = parser.add_argument_group("output")
    output.add_argument("-o", "--output", type=Path, default=None,
                        help="Write the full analysis report as JSON to this path")
    output.add_argument("--settings-output", type=Path, default=None,
                        help="Write the compact print-settings export to this path")
    return parser


def print_catalogue(args: argparse.Namespace) -> None:
    if args.list_materials:
        print(f"{'id':<10} {'name':<18} {'density':>8} {'tensile':>8} {'adhesion':>9}")
        for material in materials_db.all_materials():
            properties = material.properties
            print(
                f"{material.id:<10} {material.name:<18} "
                f"{properties.density.value or 0:>8.2f} "
                f"{properties.tensile_strength.value or 0:>8.0f} "
                f"{properties.layer_adhesion_factor.value or 0:>9.2f}"
            )
        print("\nDensity in g/cm^3, tensile strength in MPa (XY), adhesion = Z/XY ratio.")
        print("See docs/materials.md for provenance and confidence levels.")
    if args.list_printers:
        for printer in all_printers():
            volume = " x ".join(f"{value:.0f}" for value in printer.build_volume_mm)
            nozzles = ", ".join(f"{nozzle:.1f}" for nozzle in printer.available_nozzles_mm)
            print(f"{printer.id:<20} {printer.name:<34} {volume} mm  nozzles: {nozzles}")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.list_materials or args.list_printers:
        print_catalogue(args)
        return 0
    if not args.stl:
        parser.error("an STL path is required (or use --list-materials / --list-printers)")

    stl_path = Path(args.stl)
    if not stl_path.is_file():
        raise SystemExit(f"No such file: {stl_path}")

    # --- ingest ---------------------------------------------------------
    record = analysis.ingest(stl_path.read_bytes(), stl_path.name)
    geometry = analysis.geometry_report(record, args.material)
    metrics = record.metrics

    print(f"\nModel: {stl_path.name}")
    print(f"  {metrics.triangle_count:,} triangles, "
          f"{' x '.join(f'{value:.1f}' for value in metrics.dimensions)} mm, "
          f"{metrics.volume_mm3 / 1000:.2f} cm^3")
    if geometry.mass_estimate_g is not None:
        print(f"  solid mass estimate {geometry.mass_estimate_g:.1f} g "
              f"({geometry.mass_material})")
    if not geometry.validation["is_watertight"]:
        print("  WARNING: the mesh is not watertight; volume and section results are approximate")
    for warning in geometry.warnings:
        print(f"  note: {warning}")

    # --- boundary conditions ---------------------------------------------
    constraints: list[Constraint] = []
    loads: list[LoadCase] = []

    if not args.printability_only:
        if not args.fix:
            raise SystemExit(
                "No fixed surface given. Add --fix with a selector such as \"z<2\", "
                "or pass --printability-only to rank printability without a load."
            )
        if not args.force and not args.torque:
            raise SystemExit(
                "No load given. Add --force FX FY FZ (newtons) or --torque TX TY TZ "
                "(newton metres), or pass --printability-only."
            )

        fixed_faces: list[int] = []
        for expression in args.fix:
            fixed_faces.extend(select_faces(record.mesh, expression))
        fixed_faces = sorted(set(fixed_faces))
        if not fixed_faces:
            raise SystemExit(
                f"The fixed-surface selector(s) {args.fix} matched no triangles. "
                f"The model spans "
                f"x {metrics.bounding_box_min[0]:.1f}..{metrics.bounding_box_max[0]:.1f}, "
                f"y {metrics.bounding_box_min[1]:.1f}..{metrics.bounding_box_max[1]:.1f}, "
                f"z {metrics.bounding_box_min[2]:.1f}..{metrics.bounding_box_max[2]:.1f} mm."
            )

        loaded_faces: list[int] = []
        for expression in args.load_at:
            loaded_faces.extend(select_faces(record.mesh, expression))
        loaded_faces = sorted(set(loaded_faces))
        if args.load_at and not loaded_faces:
            raise SystemExit(
                f"The loaded-surface selector(s) {args.load_at} matched no triangles."
            )

        print("\nBoundary conditions:")
        describe_selection(record.mesh, fixed_faces, "fixed surface")
        if loaded_faces:
            describe_selection(record.mesh, loaded_faces, "loaded surface")
        else:
            print("  loaded surface     not given - the load is applied at the point "
                  "furthest from the fixture (worst-case lever)")

        constraints = [Constraint(id="fixture", name="Fixed surface", face_ids=fixed_faces)]
        loads = [
            LoadCase(
                id="load-1",
                name="Load case 1",
                type=LoadType(args.load_type),
                force_n=list(args.force) if args.force else [0.0, 0.0, 0.0],
                torque_nm=list(args.torque) if args.torque else [0.0, 0.0, 0.0],
                application_face_ids=loaded_faces,
            )
        ]
    else:
        print("\nPrintability only: no mechanical objective.")

    # --- optimise ---------------------------------------------------------
    request = OrientationRequest(
        material=MaterialSelection(material_id=args.material),
        loads=loads,
        constraints=constraints,
        preset=Preset(args.preset),
        printability_only=args.printability_only,
        process=ProcessConfig(
            printer_id=args.printer,
            nozzle_diameter_mm=args.nozzle,
            layer_height_mm=args.layer_height,
            overhang_threshold_deg=args.overhang_threshold,
        ),
        search=SearchConfig(
            coarse_step_deg=args.coarse_step,
            max_results=args.results,
            enable_fine_stage=not args.no_fine_stage,
        ),
    )

    print(f"\nSearching orientations (preset: {args.preset})…")
    result = analysis.run_optimization(record, request)
    settings = analysis.recommend_settings(
        record,
        SettingsRequest(
            material=MaterialSelection(material_id=args.material),
            process=request.process,
            preset=Preset(args.preset),
            candidate_rank=1,
        ),
    )

    # --- report -----------------------------------------------------------
    print(f"  {result.evaluated_candidates:,} orientations evaluated in "
          f"{result.duration_seconds:.1f} s\n")

    header = (
        f"{'#':>2} {'score':>6} {'X/deg':>7} {'Y/deg':>7} {'Z/deg':>7} "
        f"{'mech':>6} {'layer':>6} {'sup':>6} {'stab':>6} {'warp':>6} "
        f"{'h/mm':>7} {'t/min':>7}"
    )
    print(header)
    print("-" * len(header))
    for candidate in result.candidates:
        # With no load case the mechanical and layer weights are zero, so
        # printing their neutral 100 would suggest a result that was not
        # computed.
        mechanical = f"{candidate.mechanical_score:>6.1f}" if loads else f"{'-':>6}"
        layer = f"{candidate.layer_score:>6.1f}" if loads else f"{'-':>6}"
        print(
            f"{candidate.rank:>2} {candidate.overall_score:>6.1f} "
            f"{candidate.rotation_x:>7.1f} {candidate.rotation_y:>7.1f} {candidate.rotation_z:>7.1f} "
            f"{mechanical} {layer} "
            f"{candidate.support_score:>6.1f} {candidate.stability_score:>6.1f} "
            f"{candidate.warping_score:>6.1f} "
            f"{candidate.bed.height_mm:>7.1f} {candidate.print_time.estimated_minutes:>7.0f}"
        )

    best = result.candidates[0]
    print(f"\nBest orientation (#1), score {best.overall_score:.1f}:")
    for reason in best.reasons:
        print(f"  + {reason}")
    for warning in best.warnings:
        print(f"  ! {warning}")

    if best.mechanical is not None:
        mechanical = best.mechanical
        safety = mechanical.min_safety_factor
        print("\nMechanical result (APPROXIMATION - beam theory on real cross-sections):")
        print(f"  utilisation {mechanical.max_utilization:.2f}, safety factor "
              f"{'no stress' if safety >= 1e8 else f'{safety:.2f}'}")
        print(f"  governing load case: {mechanical.critical_load_case}")
        if mechanical.angle_to_layer_plane_deg is not None:
            print(f"  principal stress {mechanical.angle_to_layer_plane_deg:.0f} deg "
                  f"from the layer plane")

    print("\nRecommended print settings:")
    print(f"  layer height      {settings.layer_height:.2f} mm "
          f"(first layer {settings.first_layer_height:.2f} mm)")
    print(f"  wall loops        {settings.wall_loops}")
    print(f"  top / bottom      {settings.top_layers} / {settings.bottom_layers}")
    print(f"  infill            {settings.infill_density * 100:.0f} % {settings.infill_pattern}")
    print(f"  supports          "
          f"{f'{settings.support_type} @ {settings.support_angle:.0f} deg' if settings.supports else 'none'}")
    print(f"  brim              "
          f"{f'{settings.brim_width_mm:.0f} mm' if settings.brim else 'no'}")
    for reason in settings.reasons:
        print(f"  + {reason}")
    for warning in settings.warnings:
        print(f"  ! {warning}")

    if result.critical_regions:
        print("\nPotentially critical geometric regions (not stress results):")
        for region in result.critical_regions[:10]:
            print(f"  [{region.severity}] {region.label}")

    print(f"\nAnalysis confidence: {result.confidence.score:.0f} %")
    for factor in result.confidence.factors:
        print(f"  {factor.label:<28} {factor.score:>5.0f}  {factor.detail}")

    print("\nLimitations:")
    for limitation in result.confidence.limitations:
        print(f"  - {limitation}")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(analysis.build_report(record, 1), indent=2), encoding="utf-8"
        )
        print(f"\nFull analysis report written to {args.output}")
    if args.settings_output:
        args.settings_output.parent.mkdir(parents=True, exist_ok=True)
        args.settings_output.write_text(
            json.dumps(analysis.build_export(record, 1), indent=2), encoding="utf-8"
        )
        print(f"Print settings export written to {args.settings_output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
