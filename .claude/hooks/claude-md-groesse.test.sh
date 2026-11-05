#!/bin/sh
# Test für claude-md-groesse.sh. Aufruf: sh .claude/hooks/claude-md-groesse.test.sh
H="$(dirname "$0")/claude-md-groesse.sh"
T=$(mktemp -d); F="$T/CLAUDE.md"; FEHLER=0
pruefe() { # $1 Name, $2 erwarteter Exit, $3 JSON
  printf '%s' "$3" | sh "$H" 2>/dev/null; E=$?
  if [ "$E" = "$2" ]; then echo "ok   $1"; else echo "FEHL $1 (Exit $E, erwartet $2)"; FEHLER=1; fi
}
python3 -c "open('$F','w').write('x'*30000)"
pruefe "fremde Datei"            0 '{"tool_name":"Write","tool_input":{"file_path":"/tmp/a.md","content":"y"}}'
pruefe "Write wächst über Grenze" 2 "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"$F\",\"content\":\"$(python3 -c "print('y'*30001,end='')")\"}}"
pruefe "Edit verkleinert"        0 "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$F\",\"old_string\":\"xxxx\",\"new_string\":\"x\"}}"
pruefe "Edit vergrößert"         2 "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$F\",\"old_string\":\"x\",\"new_string\":\"xyz\"}}"
python3 -c "open('$F','w').write('x'*1000)"
pruefe "klein, wächst"           0 "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$F\",\"old_string\":\"x\",\"new_string\":\"xyz\"}}"
pruefe "MultiEdit über Grenze"   2 "{\"tool_name\":\"MultiEdit\",\"tool_input\":{\"file_path\":\"$F\",\"edits\":[{\"old_string\":\"x\",\"new_string\":\"x\",\"replace_all\":false},{\"old_string\":\"x\",\"new_string\":\"$(python3 -c "print('z'*26000,end='')")\"}]}}"
rm -rf "$T"; exit $FEHLER
