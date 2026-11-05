#!/bin/sh
# Agenten-Hook des coder (PreToolUse, Matcher Bash; im Frontmatter eingetragen).
# Committen, pushen, mergen und jede andere Aenderung am Git-Zustand macht nur der
# Chef (CLAUDE.md, Abschnitt Rollen). Erlaubt: git mit einem LESENDEN Unterbefehl (Positivliste)
# ohne -c/--exec-path/Aliase. Gesperrt (Exit 2): jeder andere git-Aufruf, auch
# ueber Pfad (/usr/bin/git), Quotes ("git"), Backslash (\git), Backticks, Variablen ($x),
# command/exec/env/stdbuf/sudo/npx-Umwege, eval, sh/bash -c (auch -lc), find -exec,
# Zeilenumbrueche, { }, if/then, for/do, !, sowie gh (GitHub-CLI) und hub.
# Fehlt python3, wird gesperrt. Grenze: Schutz vor Versehen, kein Sandkasten.
command -v python3 >/dev/null 2>&1 || { echo 'kein-git-schreiben: python3 fehlt, Bash gesperrt' >&2; exit 2; }
INPUT=$(cat)
case "$INPUT" in *git*|*gh*|*hub*) ;; *) exit 0 ;; esac
printf '%s' "$INPUT" | REF="Abschnitt Rollen" python3 -c '
import json, os, re, shlex, sys
try: cmd = (json.load(sys.stdin).get("tool_input") or {}).get("command", "")
except Exception: sys.exit(0)

def fehler(g):
    sys.stderr.write("Git-/GitHub-Schreibbefehle macht nur der Chef (CLAUDE.md, " + os.environ.get("REF", "") + "; " + g + "): " + cmd[:160] + "\n"); sys.exit(2)

GITWORT = re.compile(r"\b(git|gh|hub)\b")
LESEND = {"status","diff","log","show","ls-files","ls-tree","blame","rev-parse","grep","cat-file",
          "describe","shortlog","rev-list","merge-base","branch","help","version","--version",
          "stash","config","remote","worktree"}
try:
    lex = shlex.shlex(cmd, posix=True, punctuation_chars="|&;()\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    tokens = list(lex)
except ValueError:
    if GITWORT.search(cmd): fehler("nicht lesbar")
    sys.exit(0)
segs, akt = [], []
for t in tokens:
    if t and set(t) <= set("|&;()\n"):
        if akt: segs.append(akt); akt = []
    else: akt.append(t)
if akt: segs.append(akt)

UMWEG = {"command", "exec", "env", "nohup", "time", "timeout", "xargs", "sudo", "doas", "nice", "builtin",
         "stdbuf", "npx", "{", "}", "!", "if", "then", "elif", "else", "fi", "do", "done", "while", "until"}
for w in segs:
    if any("`" in x for x in w) and GITWORT.search(" ".join(w)) and "`" in " ".join(w): fehler("git in Backticks")
    if w and w[0] == "for":  # for x in ...; do -> Kopf ist harmlos, Rumpf ist eigenes Segment
        continue
    vorher = None
    while w and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w[0]) or w[0].rsplit("/", 1)[-1] in UMWEG
                 or (vorher in UMWEG and (w[0].startswith("-") or re.fullmatch(r"[0-9.]+[smhd]?", w[0])))):
        if w[0].rsplit("/", 1)[-1] in UMWEG: vorher = w[0].rsplit("/", 1)[-1]
        w = w[1:]
        # Optionen mit Wert (sudo -u x, stdbuf -o 0, timeout -s KILL): Wert mit ueberspringen
        MIT_WERT = {"sudo": ("-u", "-g", "-C", "-U", "-h", "-p", "-r", "-t"), "doas": ("-u", "-C"),
                    "stdbuf": ("-i", "-o", "-e"), "timeout": ("-s", "-k"), "env": ("-u", "-C", "-S")}
        while len(w) >= 2 and w[0] in MIT_WERT.get(vorher, ()):
            w = w[2:]
    if not w: continue
    name = w[0].rsplit("/", 1)[-1].lstrip("\\`")
    if w[0].startswith("$"): fehler("Befehl aus Variable")
    if name in ("gh", "hub"): fehler(name)
    if name == "eval" and GITWORT.search(" ".join(w)): fehler("git in eval")
    if name in ("bash", "sh", "zsh", "dash", "ksh") and any(re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", x) for x in w[1:]) \
       and GITWORT.search(" ".join(w)): fehler("git in Unter-Shell")
    if name == "find" and any(x in ("-exec", "-execdir", "-ok", "-okdir") for x in w) and GITWORT.search(" ".join(w)):
        fehler("git in find -exec")
    if name != "git": continue
    a = w[1:]
    while a and a[0].startswith("-"):
        if a[0] in ("-c", "--exec-path", "--config-env") or a[0].startswith("--exec-path=") or a[0].startswith("--config-env="): fehler("git " + a[0])
        a = a[2:] if a[0] in ("-C", "--git-dir", "--work-tree", "--namespace") else a[1:]
    if not a: continue
    sub, rest = a[0], a[1:]
    if sub not in LESEND: fehler("git " + sub)
    if sub == "branch":
        erlaubt_wert = {"--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--list", "-l", "--sort", "--format"}
        i, liste = 0, False
        while i < len(rest):
            x = rest[i]
            if x in ("-d","-D","-m","-M","-c","-C","-f","--delete","--move","--copy","--force","-u","--set-upstream-to",
                     "--unset-upstream","--edit-description","--track","-t") or x.startswith("--set-upstream-to="):
                fehler("git branch schreibend")
            if x in ("--list", "-l"): liste = True; i += 1; continue  # danach Muster, Optionen weiter pruefen
            if x in erlaubt_wert: i += 2; continue
            if not x.startswith("-") and not liste: fehler("git branch schreibend")
            i += 1
    if sub in ("diff","show","log") and any(x == "--output" or x.startswith("--output=") or x == "--ext-diff" for x in rest): fehler("git " + sub + " --output")
    if sub == "grep" and any(re.match(r"^-[A-Za-z]*O", x) or x.startswith("--open-files-in-pager") for x in rest): fehler("git grep -O")
    if sub == "stash" and not (rest[:1] in (["list"], ["show"])): fehler("git stash")
    if sub == "config" and not any(x in ("--get", "--get-all", "--get-regexp", "--list", "-l", "--show-origin") for x in rest[:2]): fehler("git config schreibend")
    if sub == "config" and any(x in ("--add", "--unset", "--unset-all", "--replace-all", "--rename-section", "--remove-section", "-e", "--edit") for x in rest): fehler("git config schreibend")
    if sub == "remote" and not (rest == [] or rest in (["-v"], ["--verbose"]) or rest[:1] in (["show"], ["get-url"])): fehler("git remote schreibend")
    if sub == "worktree" and rest[:1] != ["list"]: fehler("git worktree")
sys.exit(0)
'
