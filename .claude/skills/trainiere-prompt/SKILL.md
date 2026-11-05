---
name: trainiere-prompt
description: Verbessert einen Arbeitsauftrag und zeigt Plan und offene Entscheidungen, ohne irgendetwas auszuführen. Auslösen, wenn der Nutzer „trainiere Prompt“ schreibt oder sagt (auch „trainier den Prompt“, „Prompt trainieren“).
---

# trainiere Prompt

**Nur zeigen, nichts ausführen.** Keine Dateien anlegen oder ändern, keine
Agenten starten, nichts committen, keine Live-Tools. Lesen im Repo ist erlaubt,
wenn es für einen belastbaren Plan nötig ist.

Antworte auf Deutsch, genau in dieser Gliederung:

## 1. Verbesserter Prompt

- **Ziel:** was am Ende anders sein soll, in einem Satz.
- **Kontext:** betroffene Dateien/Bereiche, relevanter Ist-Zustand.
- **Randbedingungen:** was nicht passieren darf (Live-Systeme, Stil,
  Oberfläche, Code und Doku Englisch, nichts überschreiben …).
- **Erfolgskriterium:** woran man prüfbar erkennt, dass es fertig ist
  (Befehl + erwartetes Ergebnis).
- **Interpretation deines Wortlauts:** Der Nutzer diktiert oft per
  Spracheingabe. Nenne offen, welche Wörter du wie gedeutet hast
  (z. B. „‚Überhang Winkel‘ → ich lese overhang_threshold“), und was mehrdeutig war.

## 2. Plan

| # | Schritt | Wer | Live? | Risiko |
|---|---|---|---|---|

- **Wer:** `Chef` (Hauptsession), `coder` oder `helfer` (Rollen in CLAUDE.md).
- **Live?:** `ja` nur bei Schritten, die auf Live-Systeme schreiben — diese
  macht immer der `Chef`.
- **Risiko:** gering/mittel/hoch, mit Stichwort.

## 3. Offene Entscheidungen

Jede Entscheidung mit deinem Vorschlag und kurzer Begründung. Keine → „keine“.

## 4. Abschluss

Schließe wörtlich mit:

Mit OK starte ich, oder schreib, was ich ändern soll.
