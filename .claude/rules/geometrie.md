---
paths:
  - "backend/**"
  - "scripts/**"
  - "desktop/**"
  - "docker/**"
  - "docker-compose.yml"
  - ".devcontainer/**"
---
# Backend und Engines (Python 3.12, FastAPI, trimesh, scipy, shapely, manifold3d)

Ausführlich: `docs/architecture.md`, `docs/physics.md`, `docs/orientation-engine.md`,
`docs/materials.md` (Englisch; nur den passenden Abschnitt lesen).

- **Richtung der Abhängigkeiten** (Soll laut `docs/architecture.md`): `api → services → engines →
  geometry`; Engines importieren nie API oder Store (eingehalten). `services/analysis.py` kennt als
  einziges Modul die Reihenfolge der Analyse. Ausnahmen heute: `api/routes.py` ruft
  `geometry.regions.pick_region` direkt, liest Kataloge (Materialien, Drucker, Presets) direkt und
  baut die Antwort von `validate_loads` selbst. Keine neuen Ausnahmen ohne Auftrag.
- **Einheiten:** STL hat keine Einheiten, angenommen werden Millimeter. Lasten und feste Flächen
  gelten im Koordinatensystem der STL-Datei, nicht der Druckplatte; gedreht wird das Teil, nicht
  die Last. Euler-Winkel intrinsisch X→Y→Z in Grad (wie three.js `'XYZ'`).
- **Keine zertifizierte Festigkeitsberechnung:** Balkentheorie auf echten Querschnitten, keine FEM.
  Der Hinweis (`services/analysis.py` `DISCLAIMER`, `main.py`, `scoring/confidence.py`,
  `frontend/src/components/InputColumn.tsx`) bleibt in API, Oberfläche und Export erhalten. Die
  Materialdatenbank hat einen eigenen Hinweis (Kennwerte gegen das Datenblatt prüfen). Jede
  Näherung sagt, dass sie eine ist.
- **Kein Black-Box-Score:** jede Gesamtnote zeigt Komponenten, Gewichte und Rechnung; die
  Begründungstexte entstehen aus denselben Zahlen mit festen Schwellen.
- **Tests prüfen Verhältnisse und Grenzfälle, keine eingefangenen Punktwerte** (z. B. Biegespannung in der
  Schichtebene schlägt dieselbe quer zu den Schichten; höhere Überhangschwelle findet nie weniger
  Überhang). Neue Tests nach demselben Muster.
- Abhängigkeiten in `backend/requirements*.txt` sind gepinnt; keine neuen ohne Auftrag.

## Prüfungen (wie `.github/workflows/ci.yml`)

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r backend/requirements-dev.txt pyflakes   # einmalig
.venv/bin/python scripts/make_test_data.py test-data                                       # Testdaten erzeugen
.venv/bin/python -m pyflakes backend/app backend/tests scripts/*.py
cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest -q        # ohne Benchmark
```

`-m slow -s` ist der Leistungs-Benchmark, kein Pflichtlauf.
