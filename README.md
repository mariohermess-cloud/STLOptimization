# Print Engineering Optimizer

Upload an STL, describe how the part is held and loaded, and get ranked print
orientations with the reasoning behind them and concrete slicer settings.

The point is not the 3D viewer. It is that an FDM part is **not isotropic**:
material inside a layer is a continuous extrudate, material between layers is
a weld. Which way a part is printed can change what it carries by a factor of
two or more. This tool searches print orientations against the actual load,
the actual cross-sections of the model and the material's layer adhesion, and
then says why it chose what it chose.

> This tool provides engineering-oriented estimates and FDM print optimisation
> guidance. **It is not a certified structural analysis system.** The
> mechanical analysis is beam theory on real cross-sections, not a finite
> element analysis; stress concentrations at holes, fillets and sharp
> transitions are not resolved.

---

## Quick start

```bash
docker compose up --build
```

Then open <http://localhost:8080>. The API is also published on
<http://127.0.0.1:8000> with interactive docs at `/docs`.

## What it does

```
upload STL → geometry analysis → material → load cases → fixed surfaces →
orientation search → scoring → top orientations → print settings → export
```

* **Geometry** — validation and conservative repair, volume, area, mass
  estimate, centre of mass, principal axes, cross-section properties. Invalid
  and corrupted files are reported, not crashed on.
* **Mechanics** — classical beam theory evaluated on the *real* cross-sections
  along each load path, producing stress tensors in part coordinates. Multiple
  weighted load cases (tension, compression, bending, shear, torsion, custom).
* **Anisotropy** — a weak-plane failure criterion on the layer interfaces,
  driven by the material's layer adhesion factor, plus a Hankinson allowable
  stress for reporting.
* **Orientation search** — three stages (15° / 5° / 1°) over build directions,
  with the spin about the build axis solved exactly as the minimum-area
  footprint rectangle.
* **Printability** — exact overhang geometry, bed contact and static tipping
  stability, a heuristic warping index, a flow-limited print-time estimate.
* **Print settings** — layer height, wall loops, top/bottom layers, infill
  density and pattern, supports, brim. The wall-versus-infill decision is
  *computed*: the critical section is offset inwards by the wall thickness and
  the share of the second moment of area the walls carry is measured.
* **Critical regions** — thin walls for the nozzle in use, necks, abrupt
  section changes, unsupported overhang clusters, small internal radii,
  mounting features. Labelled as *geometric* risk indicators, not stress
  results.
* **Confidence** — computed from the quality of the actual input: mesh,
  material data provenance, completeness of the boundary conditions, the
  method used, and how clearly the winner beats the runner-up.

### No black-box score

An overall score of 91 is shown with every component, its weight and its
contribution, and the arithmetic that produced it. The justification sentences
are generated from those same numbers with fixed thresholds, so the text
cannot disagree with the analysis.

Anything that is an approximation says so, in the UI and in the exported
report.

---

## Local development

Backend (Python 3.12+):

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
cd backend && PYTHONPATH=. ../.venv/bin/uvicorn app.main:app --reload --port 8000
```

Frontend (Node 22+):

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173, proxies /api to :8000
```

The two run independently. The dev server proxies `/api` to the backend, so
the browser only ever talks to one origin.

Test fixtures:

```bash
.venv/bin/python scripts/make_test_data.py test-data
```

## Tests

```bash
cd backend
PYTHONPATH=. ../.venv/bin/python -m pytest -q          # 132 tests, ~50 s
PYTHONPATH=. ../.venv/bin/python -m pytest -m slow -s  # performance benchmark
```

The orientation tests assert engineering *relationships*, not captured score
values: a bending stress in the layer plane must beat the same stress across
layers; a higher overhang threshold can never find less overhang; a standing
beam must be less stable than a lying one; the mechanical score floor must
equal the material's layer adhesion factor.

Full-workflow check in a real browser (needs both servers running):

```bash
npm install --no-save playwright
node scripts/e2e_check.mjs http://127.0.0.1:4173 test-data/bracket.stl
```

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/models/upload` | Upload an STL |
| `GET` | `/api/models/{id}` | Model summary |
| `GET` | `/api/models/{id}/mesh.stl` | The repaired mesh the viewer loads |
| `POST` | `/api/models/{id}/analyze` | Geometry report |
| `POST` | `/api/models/{id}/pick-region` | Flood-fill a face region for selection |
| `POST` | `/api/models/{id}/loads` | Validate loads, return resolved load paths |
| `POST` | `/api/models/{id}/optimize-orientation` | Start the search (returns a job) |
| `POST` | `/api/models/{id}/optimize-orientation/sync` | Synchronous variant |
| `GET` | `/api/jobs/{id}` · `/api/jobs/{id}/result` | Job status and result |
| `GET` | `/api/models/{id}/results` | Last stored result |
| `POST` | `/api/models/{id}/recommend-settings` | Print settings for a candidate |
| `POST` | `/api/models/{id}/export` | Machine-readable export |
| `GET` | `/api/models/{id}/report` | Full analysis report |
| `GET` | `/api/materials` · `/api/printers` · `/api/presets` · `/api/limits` | Catalogue |

Errors are returned as `{"error": {"code", "message", "details"}}` with a
stable code and a sentence written for the user. Stack traces are logged, not
returned.

### Export

```json
{
  "printer": "Bambu Lab X1 Carbon",
  "material": "PETG-CF",
  "nozzle": 0.4,
  "orientation": { "x": 37.0, "y": 8.0, "z": 12.0 },
  "layer_height": 0.16,
  "first_layer_height": 0.2,
  "wall_loops": 5,
  "top_layers": 6,
  "bottom_layers": 5,
  "infill_density": 0.35,
  "infill_pattern": "gyroid",
  "supports": true,
  "support_angle": 45.0,
  "support_type": "tree(auto)",
  "brim": true,
  "brim_width_mm": 5.0,
  "scores": { "overall": 91.0, "mechanical": 94.0, "...": 0 },
  "analysis_confidence": 78.0,
  "disclaimer": "..."
}
```

Every value is the calculated one for the selected orientation. The full
report additionally carries the model metrics, the material with its property
provenance, the loads and constraints, all candidates, the critical regions,
the solver assumptions and the stated limitations.

## Configuration

Environment variables, prefix `PEO_`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `PEO_STORAGE_DIR` | `/tmp/peo-storage` | Where uploads are written |
| `PEO_MAX_UPLOAD_BYTES` | `157286400` | Upload size limit (150 MB) |
| `PEO_MAX_FACES` | `6000000` | Refusal limit for triangle count |
| `PEO_ANALYSIS_FACE_BUDGET` | `60000` | Faces used for the orientation search |
| `PEO_MODEL_TTL_SECONDS` | `21600` | Upload lifetime |
| `PEO_OPTIMIZER_WORKERS` | `4` | Threads evaluating orientations |
| `PEO_LOG_LEVEL` | `INFO` | Log level |
| `PEO_CORS_ORIGINS` | `["*"]` | Allowed origins |

## Coordinates and units

STL carries no units; millimetres are assumed. Loads and fixed surfaces are
defined in the **coordinate system of the STL file**, not of the build plate —
the optimiser rotates the part, not the load. Reported Euler angles are
intrinsic X→Y→Z in degrees, the same composition three.js applies for
`Euler(x, y, z, 'XYZ')`.

## Documentation

| Document | Contents |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | Module layout, the FEM seam, why the search is affordable, security posture |
| [docs/physics.md](docs/physics.md) | Every formula and constant, with the approximations labelled |
| [docs/materials.md](docs/materials.md) | Where the material numbers come from and how far to trust them |
| [docs/orientation-engine.md](docs/orientation-engine.md) | The search, the scores, the presets, measured cost |
| [docs/bambu-integration.md](docs/bambu-integration.md) | What Bambu Studio integration is actually possible, with sources |
| [docs/roadmap.md](docs/roadmap.md) | What was left out and why |

## Licence and provenance

Material properties are literature-typical values compiled from public vendor
data sheets, each with an explicit range, confidence level and source note.
They are not measurements of any particular filament — verify against the
technical data sheet of the spool you print.
