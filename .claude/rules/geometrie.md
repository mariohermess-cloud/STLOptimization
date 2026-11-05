---
paths:
  - "backend/**"
  - "scripts/**"
  - "desktop/**"
  - "docker/**"
---
# Backend und Engines (Python 3.12, FastAPI, trimesh, scipy, shapely, manifold3d)

Ausführlich: `docs/architecture.md`, `docs/physics.md`, `docs/orientation-engine.md`,
`docs/materials.md` (Englisch; nur den passenden Abschnitt lesen).

- **Richtung der Abhängigkeiten:** `api → services → engines → geometry`. Engines importieren
  nie API oder Store. `api/routes.py` macht nur Validierung und Statuscodes;
  `services/analysis.py` ist das einzige Modul, das die Reihenfolge kennt.
- **Einheiten:** STL hat keine Einheiten, angenommen werden Millimeter. Lasten und feste Flächen
  gelten im Koordinatensystem der STL-Datei, nicht der Druckplatte; gedreht wird das Teil, nicht
  die Last. Euler-Winkel intrinsisch X→Y→Z in Grad (wie three.js `'XYZ'`).
- **Keine zertifizierte Festigkeitsberechnung:** Balkentheorie auf echten Querschnitten, keine FEM.
  Der Hinweis (`services/analysis.py` `DISCLAIMER`, `main.py`, Materialdatenbank) bleibt in API,
  Oberfläche und Export erhalten. Jede Näherung sagt, dass sie eine ist.
- **Kein Black-Box-Score:** jede Gesamtnote zeigt Komponenten, Gewichte und Rechnung; die
  Begründungstexte entstehen aus denselben Zahlen mit festen Schwellen.
- **Tests prüfen Verhältnisse, keine festen Punktzahlen** (z. B. Biegespannung in der
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
