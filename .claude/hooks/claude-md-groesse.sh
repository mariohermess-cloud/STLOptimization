#!/bin/sh
# PreToolUse-Hook (Matcher: Edit|Write|MultiEdit). Verhindert, dass eine
# CLAUDE.md wieder zum Änderungsprotokoll wird (CLAUDE.md, Kopf).
#
# Blockiert (Exit 2), wenn eine Datei namens CLAUDE.md nach der Änderung
# größer als GRENZE Bytes wäre UND größer als vorher. Verkleinern ist immer
# erlaubt, auch wenn die Datei noch über der Grenze liegt.
# Andere Dateien: sofort Exit 0, ohne python3-Start.

GRENZE=10240

INPUT=$(cat)

case "$INPUT" in
  *CLAUDE.md*) ;;
  *) exit 0 ;;
esac

printf '%s' "$INPUT" | GRENZE="$GRENZE" python3 -c '
import json, os, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
ti = d.get("tool_input") or {}
pfad = ti.get("file_path") or ""
if os.path.basename(pfad) != "CLAUDE.md":
    sys.exit(0)
grenze = int(os.environ["GRENZE"])
try:
    alt = open(pfad, encoding="utf-8").read()
except OSError:
    alt = ""
tool = d.get("tool_name", "")
if tool == "Write":
    neu = ti.get("content") or ""
elif tool == "Edit":
    o, n = ti.get("old_string") or "", ti.get("new_string") or ""
    neu = alt.replace(o, n) if ti.get("replace_all") else alt.replace(o, n, 1)
elif tool == "MultiEdit":
    neu = alt
    for e in ti.get("edits") or []:
        o, n = e.get("old_string") or "", e.get("new_string") or ""
        neu = neu.replace(o, n) if e.get("replace_all") else neu.replace(o, n, 1)
else:
    sys.exit(0)
b_alt, b_neu = len(alt.encode("utf-8")), len(neu.encode("utf-8"))
if b_neu > grenze and b_neu > b_alt:
    sys.stderr.write(
        f"Gesperrt: {pfad} wuerde von {b_alt} auf {b_neu} Bytes wachsen (Grenze {grenze}). "
        "CLAUDE.md enthaelt nur Regeln. Aenderungsberichte gehoeren nach "
        "Commits, Bereichsregeln nach .claude/rules/, Architektur nach docs/. Regel kuerzen statt anhaengen.\n")
    sys.exit(2)
sys.exit(0)
'
