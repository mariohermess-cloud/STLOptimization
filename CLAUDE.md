# CLAUDE.md – Print Engineering Optimizer (STLOptimization)

Findet für ein STL-Bauteil die beste Druckausrichtung und Druckeinstellungen (FDM) aus
Druckbarkeit und mechanischer Last. Backend FastAPI mit Geometrie- und Mechanik-Engines
(`backend/`), Frontend React + three.js (`frontend/`), Portable-Build (`desktop/`). Nur
Grundregeln; Änderungsberichte gehören in Commits, nie hierher (ein Hook blockiert Wachstum
dieser Datei über 10 KB).

## Sprache

Antworten, Commits und diese Regeln Deutsch. Code, Oberfläche, `README.md` und `docs/` Englisch.
Bestehende Namen erhalten.

## Karte

| Pfad | Inhalt | Regeln (laden automatisch) |
|---|---|---|
| `backend/app/`, `backend/tests/` | API, Engines (geometry, mechanics, orientation, printing, scoring), Tests | `.claude/rules/geometrie.md` |
| `frontend/` | Oberfläche, WebGL-Viewer | `.claude/rules/frontend.md` |
| `scripts/` | `make_test_data.py`, `analyze.py` (CLI), `e2e_check.mjs`, `dev.sh` | `geometrie.md` |
| `desktop/`, `docker/`, `docker-compose.yml`, `.devcontainer/` | Portable-Build (PyInstaller), Container | `geometrie.md` |
| `docs/` | Architektur, Physik, Orientierung, Materialien, Roadmap | nur gezielt lesen |
| `test-data/` | erzeugt mit `scripts/make_test_data.py` | – |

**Immer gültig:** Das Werkzeug ist **keine zertifizierte Festigkeitsberechnung**; der Hinweis
bleibt in API, Oberfläche und Export. Einheiten mm, Lasten im STL-Koordinatensystem. Tests
prüfen Verhältnisse und Grenzfälle, keine eingefangenen Punktwerte. Details in `geometrie.md`.

## Rollen

Die Hauptsession ist der **Chef** (Opus, in `.claude/settings.json` gesetzt; `/model` hat Vorrang).

| Rolle | Modell | Aufgabe | Darf nicht (Hook = technisch gesperrt, Schutz vor Versehen) |
|---|---|---|---|
| Chef | Opus | planen, delegieren, jeden Diff prüfen, Checks nachfahren, committen, pushen, Live-Schritte | eigene Arbeit ungeprüft durchwinken |
| `coder` | Sonnet | abgegrenzte Umsetzung mit Diff und Prüfnachweis | Git-Schreibbefehle, `gh` (Hook `kein-git-schreiben.sh`); Live, Auftrag erweitern |
| `helfer` | Haiku | suchen, lesen, zusammenfassen; Doku nur auf ausdrücklichen Auftrag | kein Bash; schreibt nur `*.md` außer CLAUDE.md (Hook `helfer-nur-doku.sh`) |

- ≤ 1 Datei, ≤ 10 Zeilen, keine Logik: macht der Chef selbst.
- Unabhängige Aufträge in **einer** Nachricht parallel; keine parallelen Schreibaufträge für
  dieselben Dateien. Scheitert derselbe Auftrag dreimal an derselben Rolle: Mario fragen.

## Live (nur der Chef, nur nach ausdrücklichem OK für genau diesen Schritt)

- Tag `v*` pushen (startet den Portable-Build in `portable.yml` auf drei Runnern), Releases,
  veröffentlichte Images. `portable.yml` läuft auch bei PRs, die `desktop/`, die Abhängigkeiten oder
  den Workflow ändern (kostet Actions-Minuten, veröffentlicht nichts).
- Workflows von Hand starten (`ci.yml`, `analyze.yml`, `portable.yml` per `workflow_dispatch`).
- Jeder schreibende Zugriff über ein MCP-Tool, gleich welcher Dienst; Artifact-Veröffentlichung.
- Push, PR und Merge brauchen ein eigenes OK; die CI (`ci.yml`) läuft bei jedem Push und PR, prüft
  nur und veröffentlicht nichts. Merge macht Mario selbst.

## Ablauf bei jedem Arbeitsauftrag

1. Auftrag klären: nur unklare, mehrdeutige oder diktierte Aufträge umformulieren und die
   Deutung offenlegen (bei Bedarf Skill `trainiere-prompt`), klare nicht.
2. Plan zeigen: `# | Schritt | Wer | Live? | Risiko`, offene Entscheidungen mit Vorschlag.
3. Auf OK warten.
4. Delegieren (Rollen oben).
5. Kontrolle durch den Chef: Diff lesen, Checks selbst nachfahren.
6. Abschluss getrennt nach **Verifiziert** und **Nicht verifiziert**.

Reine Fragen und Smalltalk direkt beantworten, ohne Plan. Jede Antwort während eines Auftrags
endet mit `📋 Planstand ✅ erledigt · ▶️ läuft (wer) · ⬜ offen`.

## Prüfungen

Die Befehle stehen in den Pfadregeln (`geometrie.md`: pyflakes + pytest, `frontend.md`: Build)
und entsprechen `.github/workflows/ci.yml`. Immer `git diff --check`. Bei Änderungen an
`.claude/`: `sh .claude/hooks/agenten-hooks.test.sh` und
`sh .claude/hooks/claude-md-groesse.test.sh`. Keine Testzahlen in Doku schreiben, sie veralten.

## Große Dateien

Fürs Lesen gesperrt (`permissions.deny`): `.env*`, große erzeugte STLs (`test-data/large_*.stl`),
`*.3mf`, `node_modules/`, `frontend/dist/`, `build/`, `dist/`, `.venv/`. Die kleinen Testteile in
`test-data/` sind lesbar; STLs besser mit `scripts/analyze.py` oder Python (trimesh) untersuchen.

## Hooks und Skills

- Hooks (`.claude/settings.json`): `ablauf-erinnerung.sh` (eine Zeile je Prompt),
  `claude-md-groesse.sh` (Größensperre); Agenten-Hooks im Frontmatter von coder und helfer.
  Abschalten nur auf Auftrag: Eintrag entfernen oder lokal `"disableAllHooks": true` in
  `.claude/settings.local.json`.
- Skill `trainiere-prompt`: auf Zuruf verbesserter Prompt plus Plan, führt nichts aus.
- Hooks, Agenten und Skills greifen erst in einer neuen Session.
