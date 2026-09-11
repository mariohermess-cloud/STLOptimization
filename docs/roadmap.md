# Roadmap

The MVP is complete as described in the README. This page records what was
deliberately left out, and what the architecture is already shaped for.

## Now (MVP)

STL in, ranked and explained orientations out, with recommended print settings
and a JSON export. Section-based mechanical analysis, FDM anisotropy from the
material's layer adhesion factor, printability metrics, geometric risk
regions, analysis confidence. Docker deployment.

## V2 — measured instead of estimated

The theme: replace the three labelled approximations with real numbers.

**Slicer-backed print time and material.** The largest single gap. The
flow-limited estimate ranks orientations adequately but will not match a real
G-code time. `docs/bambu-integration.md` §7 sets out the path: export the
oriented mesh, emit a Bambu process preset, invoke the CLI, read back the
slicing data. This also makes the material figure exact.

**A real FEM backend.** The seam already exists: a solver returns stress
tensors in part coordinates and nothing downstream knows how they were
produced. CalculiX (ccx) is the pragmatic first target — it is a standalone
solver with a stable input deck. Tetrahedral meshing (gmsh) and the boundary
condition mapping from the existing face selections are the real work. When it
lands, `solver_is_approximation` flips and the confidence ceiling rises from
45 to 80.

**STEP and 3MF import.** STEP brings exact surfaces, which would make the thin
wall and hole detection exact rather than sampled, and gives real fillet radii
for a stress-concentration factor. 3MF brings multi-body and per-object
settings.

**Support-aware overhang analysis.** The current support volume sweeps each
overhang face to the plate. Casting against the geometry below it would give a
real support volume and would stop over-counting on stacked features.

**Per-face results in the viewer.** Utilisation painted onto the surface,
which only becomes meaningful once a real FEM backend produces a field rather
than section states.

**PDF analysis report.** The report content is already assembled and exported
as JSON; a PDF is a rendering layer over the same document, and is worth
having the moment someone has to hand the result to a colleague who will not
open a JSON file.

**Automatic modifier regions.** Once the critical regions carry a real stress
field, the natural next output is a modifier volume around the governing
section - extra walls and solid infill exactly where the analysis says the
load goes, rather than raising the settings for the whole part.

## V3 — inference

**AI part classification.** Recognising a bracket, a housing, a gear from the
geometry, and proposing a load case for it.

**Automatic load inference.** Mounting features already get detected; the next
step is inferring that a bolt hole pattern is the fixture and the remaining
free surface is where the load arrives, then proposing a load case for the
user to confirm. The inference must stay a proposal — a guessed load case
presented as fact would be exactly the kind of false authority this project
avoids.

**Topology optimisation.** Only sensible once a real FEM backend exists.

## V4 — the closed loop

```
analyse → find the weak region → propose a modification →
modify the geometry → re-analyse → compare
```

Every stage of that loop except the geometry modification already exists in
some form: the analysis, the weak-region detection, the comparison view. What
is missing is a geometry kernel able to add a fillet or thicken a wall, and a
meaningful convergence criterion. This is the most speculative item on the
page and it depends on V2's FEM backend being real.

## Infrastructure, when justified

These are listed so the reasoning is on record, not as planned work:

* **Shared model storage** (Redis + object storage) — needed the moment more
  than one API worker runs. Today the compose file runs one worker precisely
  because the store is per process.
* **A real job queue** (Celery or RQ) — needed when jobs must survive a
  restart or span workers. The `submit`/`get` interface already matches.
* **A database** — needed when analyses have to persist across sessions or be
  shared between users. Nothing in the MVP requires it.

None of them is justified by the MVP, and adding them early would mean
operating infrastructure that carries no state anyone needs.

## Deliberately out of scope

**Sending a print job to a printer.** There is no public, supported API. The
community path is reverse engineered and was gated by firmware in January
2025. Building on it would produce a feature that breaks on a vendor's
schedule. See `docs/bambu-integration.md` §5.

**Certification claims.** The application is an engineering-oriented estimator.
No amount of additional modelling in this roadmap turns it into a certified
structural analysis system, and the disclaimer stays regardless of what gets
built.

## Known limitations carried by the MVP

| Limitation | Where it is stated to the user |
| --- | --- |
| Beam theory, not FEM; no stress concentrations | Mechanical panel, confidence factors, exported report |
| Fixtures are ideally rigid clamps | Fixation panel, solver assumptions |
| Support volume, warping risk and print time are approximations | Each labelled at the point of display |
| Material properties are literature-typical, not measured | Material panel, confidence factors |
| The layer adhesion factor is a low-confidence heuristic | Per-orientation warning when a load is defined |
| Large meshes are simplified for the search | Model panel, confidence factors |
| Print time is scored relative to the evaluated set | Physics documentation, report |
| Single-process store; jobs do not survive a restart | Architecture documentation |
