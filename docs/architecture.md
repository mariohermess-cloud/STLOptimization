# Architecture

## Shape of the system

```
browser ──HTTP──> nginx ──/api──> FastAPI ──> engines
   │                 │
   │                 └── static bundle (React + three.js)
   └── WebGL viewer
```

Two containers. No database, no broker, no cache — none of them would carry
state that matters for the MVP, and every one of them would have to be
operated. The one piece of state, an uploaded model plus its analysis, lives
in the API process and on a volume, bounded by a TTL and a count.

## Backend layout

```
backend/app/
├── main.py                 FastAPI app, middleware, error handlers, janitor
├── api/routes.py           HTTP surface; validation and status codes only
├── core/                   config, structured logging, user-facing error types
├── schemas/                Pydantic request/response models (the API contract)
├── geometry/
│   ├── loader.py           safe STL ingestion (format sniffing, limits)
│   ├── analyzer.py         validation, repair, mass properties, decimation
│   ├── sections.py         cross-section properties, shell/core split
│   ├── features.py         thin walls, concave features, necks, overhang clusters
│   ├── regions.py          face-region flood fill for the viewer
│   ├── prepared.py         precomputed per-mesh arrays
│   └── transforms.py       rotation conventions, minimum-area footprint
├── materials/              material database + derived strengths
├── mechanics/
│   ├── solver.py           MechanicalSolver ABC + ApproximateMechanicalSolver
│   └── anisotropy.py       weak-plane criterion, Hankinson, von Mises
├── orientation/
│   ├── search.py           build-direction candidate generation
│   ├── optimizer.py        the three-stage search and the scoring pipeline
│   ├── presets.py          priority presets (weights only)
│   └── explain.py          human-readable justification
├── printing/
│   ├── printers.py         printer profiles
│   └── settings_optimizer.py   layer height, walls, infill, supports, brim
├── scoring/
│   ├── printability.py     overhang, bed, warping, print time
│   └── confidence.py       analysis confidence
├── services/analysis.py    orchestration: the only module that knows the order
└── storage/                model store, job manager
```

Dependencies point one way: `api → services → engines → geometry`. The
engines never import the API or the store.

## The seam a real FEM backend plugs into

`MechanicalSolver.analyze(mesh, material, loads, constraints)` returns a
`MechanicalModel`: a list of **stress states in part coordinates**, each a full
3×3 stress tensor at a located point, plus the load weights and the stated
assumptions.

That representation is solver-agnostic on purpose. Everything downstream — the
anisotropy criterion, the mechanical score, the layer score, the safety
factors, the reporting — consumes only stress tensors and the build direction
`d`. A CalculiX or FEniCS backend produces the same `MechanicalModel` from
element results, and no scoring, optimisation or API code changes. The
confidence report already has the branch for it (`solver_is_approximation`),
which is the only place the difference is visible to the user.

`ApproximateMechanicalSolver` is explicitly not FEM and says so in its
assumptions, which are carried through to the exported report.

## Why the orientation search is affordable

Three design decisions do all the work:

1. **The mesh is never rotated.** A build direction in the part frame is
   enough for every printability metric, so an orientation costs a handful of
   vectorised passes over precomputed arrays instead of a transform of the
   mesh.
2. **The stress tensors are orientation-independent.** They are computed once,
   before the search; evaluating an orientation against them is one
   `einsum` over all stress points at once.
3. **Only two of three degrees of freedom are searched.** Overhangs, layer
   alignment, bed contact and part height depend only on the build direction.
   The spin about the build axis is solved exactly as the minimum-area
   footprint rectangle. Searching a 2-sphere at 15° is ~250 candidates rather
   than ~14 000 Euler triples, and nothing physically distinct is skipped.

Large meshes are simplified to a face budget for the search only; the full
mesh is still used for metrics, cross-sections and face selection, and the
confidence report records that the simplification happened.

## Face indices are a contract

The viewer loads `/api/models/{id}/mesh.stl`, which serves the mesh **after**
repair. Triangle `i` in the browser is therefore triangle `i` in the solver,
and a face selection made by clicking round-trips exactly. Region picking runs
server-side, where the adjacency graph already exists.

When the analysis mesh has been simplified, face indices produced against it
no longer address the served mesh, so they are dropped rather than used to
highlight the wrong triangles. Positions are still reported.

## Jobs

Orientation optimisation is CPU-bound and can take minutes on a large mesh, so
`POST /api/models/{id}/optimize-orientation` returns a job handle and the
client polls `/api/jobs/{id}`. The job manager is an in-process thread pool
with the same interface a Celery or RQ backend exposes (`submit` returning an
id, `get` returning status and result). Redis and a broker are not justified
for a single-container MVP; when they are, only `storage/jobs.py` changes.

A synchronous variant exists for small models, tests and scripting.

## Frontend layout

```
frontend/src/
├── api/            typed client and the schema mirror
├── state/store.ts  zustand store, one slice per concern
├── viewer/         Canvas, overlays, mesh loading and face subsets
├── components/     input column, analysis column, shared widgets
└── styles/
```

State is namespaced by concern — model, geometry, material, process, loads,
constraints, optimisation, settings, UI — and components subscribe to the
exact fields they use, so nothing is passed down through props and a change in
one slice does not re-render the rest.

The viewer is Z-up, matching the analysis: build plate on XY, build direction
+Z. Placement is `world = offset + R · part`, built as an outer group carrying
the offset and an inner group carrying the rotation, so every annotation in
part coordinates follows the part into the recommended orientation.

## Security posture

* Upload size is checked while streaming, before the body is buffered.
* The format is decided by inspecting the bytes, not the file extension.
* Nothing from an uploaded file is executed, evaluated or used as a path.
* Storage keys are server-generated UUIDs; the client filename is kept only as
  a sanitised display label, so path traversal is not a reachable class of bug.
* Triangle counts are bounded before and during parsing.
* Uploads are deleted by TTL and by an LRU bound; the janitor runs in the app
  lifespan and on shutdown.
* No subprocess is spawned anywhere in the request path.
* Errors reach the client as a stable code plus a sentence written for the
  user; stack traces are logged, never returned.

## Logging

Structured JSON on stdout. Every record carries `operation`, and where
relevant `model_id`, `duration_ms`, `evaluated`, `faces`, `status`. File
contents and raw filenames are never logged.

## Known architectural limits

* The model store is per process. Running several API workers would give each
  its own store; that is why the compose file runs one. Moving to several means
  replacing `storage/store.py` with shared storage.
* Jobs are in-process, so a restart loses running jobs.
* The mechanical analysis is a section solver, not FEM — by design, and
  labelled everywhere it surfaces.
