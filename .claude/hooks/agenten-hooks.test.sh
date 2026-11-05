#!/bin/sh
# Test der Agenten-Hooks kein-git-schreiben.sh (coder) und helfer-nur-doku.sh (helfer).
# Aufruf: sh .claude/hooks/agenten-hooks.test.sh   (Exit 0 = alle Faelle richtig)
cd "$(dirname "$0")/../.." || exit 1
export CLAUDE_PROJECT_DIR="$(pwd)"
F=0; N=0
bash_fall() { # erwartet befehl
  N=$((N+1))
  j=$(python3 -c 'import json,sys; print(json.dumps({"tool_input":{"command":sys.argv[1]}}))' "$2")
  printf '%s' "$j" | sh .claude/hooks/kein-git-schreiben.sh >/dev/null 2>&1; e=$?
  [ "$e" = "$1" ] || { F=$((F+1)); echo "FEHLER coder: erwartet $1, bekam $e: $2"; }
}
datei_fall() { # erwartet pfad
  N=$((N+1))
  j=$(python3 -c 'import json,sys; print(json.dumps({"tool_input":{"file_path":sys.argv[1]}}))' "$2")
  printf '%s' "$j" | sh .claude/hooks/helfer-nur-doku.sh >/dev/null 2>&1; e=$?
  [ "$e" = "$1" ] || { F=$((F+1)); echo "FEHLER helfer: erwartet $1, bekam $e: $2"; }
}
bash_fall 0 'git status'
bash_fall 0 'git diff --stat && git log --oneline -5'
bash_fall 0 'cd backend && PYTHONPATH=. python -m pytest -q'
bash_fall 0 'cd frontend && npm run build'
bash_fall 0 'git branch --show-current'
bash_fall 2 'git commit -m x'
bash_fall 2 'git push'
bash_fall 2 '/usr/bin/git push'
bash_fall 2 '"git" push'
bash_fall 2 'env FOO=1 git push'
bash_fall 2 'timeout 5 git push'
bash_fall 2 'git -c alias.x=push x'
bash_fall 2 'git checkout -b neu'
bash_fall 2 'git branch neu'
bash_fall 2 'git diff --output=x'
bash_fall 2 'gh pr create'
bash_fall 2 'sh -c "git push"'
bash_fall 2 'bash -lc "git push"'
bash_fall 2 'eval "git push"'
bash_fall 2 'x=git; $x push'
bash_fall 2 'git branch --list x -D y'
bash_fall 0 'git branch --list "harness*"'
bash_fall 2 'env -i git push'
bash_fall 2 'echo `git push`'
bash_fall 0 'git diff --output-indicator-new=+'
bash_fall 0 'git branch --contains HEAD'
bash_fall 0 'git stash list'
bash_fall 0 'git remote -v'
bash_fall 2 "$(printf 'cd backend\ngit commit -am x')"
bash_fall 2 '{ git push; }'
bash_fall 2 'if true; then git push; fi'
bash_fall 2 'for i in 1; do git push; done'
bash_fall 2 '! git push'
bash_fall 2 'stdbuf -o0 git push'
bash_fall 2 'find . -name x -exec git push \;'
bash_fall 2 'git grep -Ovim foo'
bash_fall 2 '`git push`'
bash_fall 2 'npx gh pr create'
bash_fall 2 'sudo -u x git push'
datei_fall 0 'README.md'
datei_fall 0 'docs/physics.md'
datei_fall 2 'CLAUDE.md'
datei_fall 2 '.claude/agents/coder.md'
datei_fall 2 '.github/workflows/x.md'
datei_fall 2 'backend/app/main.py'
datei_fall 2 'frontend/src/main.tsx'
datei_fall 2 'test-data/bracket.stl'
datei_fall 2 '../ausserhalb.md'
datei_fall 2 'claude.md'
datei_fall 2 '.CLAUDE/agents/coder.md'
datei_fall 2 'frontend/node_modules/x/README.md'
datei_fall 2 'README.txt'
echo "agenten-hooks: $((N-F)) von $N richtig"
[ "$F" = 0 ]
