#!/bin/sh
# Agenten-Hook des helfer (PreToolUse, Matcher Edit|Write|MultiEdit, eingetragen
# im Frontmatter von .claude/agents/helfer.md). Erlaubt Schreiben nur auf
# Markdown-Dateien (*.md) im Projekt. Pfad wird normalisiert; .claude/, .github/,
# node_modules/, test-data/, Pfade ausserhalb des Projekts und CLAUDE.md sind immer
# gesperrt (Vergleich ohne Gross-/Kleinschreibung, wegen macOS/Windows).
# Alles andere (backend/, frontend/, desktop/, scripts/ ...): Exit 2 mit Begruendung.
command -v python3 >/dev/null 2>&1 || { echo 'helfer-nur-doku: python3 fehlt, Schreiben gesperrt' >&2; exit 2; }
INPUT=$(cat)
printf '%s' "$INPUT" | python3 -c '
import json, os, sys
try: pfad = (json.load(sys.stdin).get("tool_input") or {}).get("file_path", "")
except Exception: pfad = ""
basis = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
norm = os.path.normpath(os.path.join(basis, pfad))
rel = os.path.relpath(norm, basis).replace(os.sep, "/")
klein = rel.lower()
teile = klein.split("/")
ok = (pfad != "" and not klein.startswith("..") and ".claude" not in teile and ".github" not in teile
      and "node_modules" not in teile and teile[0] != "test-data"
      and os.path.basename(klein) != "claude.md" and klein.endswith(".md"))
if ok: sys.exit(0)
sys.stderr.write("helfer darf nur Markdown-Doku (*.md, nicht CLAUDE.md, .claude/, .github/, test-data/, node_modules/) schreiben, nicht: " + pfad + "\n")
sys.exit(2)
'
