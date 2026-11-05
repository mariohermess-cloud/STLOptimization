---
paths:
  - "frontend/**"
---
# Frontend (React 19, TypeScript 5.9, Vite, three.js / react-three-fiber)

- `src/api/` (Aufrufe an das Backend), `src/state/`, `src/components/`, `src/viewer/` (WebGL),
  `src/styles/`. Oberfläche Englisch.
- Der Viewer übernimmt die Euler-Winkel des Backends unverändert (`'XYZ'`, Grad → Bogenmaß);
  Konvention nicht im Frontend ändern.
- Keine neuen Abhängigkeiten ohne Auftrag.

## Prüfungen

```bash
cd frontend
npm ci --no-audit --no-fund   # einmalig
npm run build                 # tsc --noEmit + vite build
```

Browser-Gesamtprüfung (beide Server laufen, Playwright nötig):
`node scripts/e2e_check.mjs http://127.0.0.1:4173 test-data/bracket.stl`.
