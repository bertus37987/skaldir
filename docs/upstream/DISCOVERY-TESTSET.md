# Discovery-Testset (eingefroren)

Version 1.0.0 — eingefroren am 2026-09-14 durch die Zentrale.
Maschinenlesbare Datei: `tests/fixtures/discovery_cases.json` (30+ Formulierungen).

## Zweck
Abnahme §8 („Zusätzliche Abnahme für Builder Discovery und automatische Verwendung"):
Recall@3, Falsch-Eignung bei Aufgaben ohne Lösung, Scope-Leaks, tatsächliche
Auswahl/Ausführung, Suchlatenz, zusätzliche Kontexttokens.

## Freeze-Regel (bindend)
Dieses Testset wird NICHT zur Optimierung von Tool-Beschreibungen, Suchheuristiken
oder Ranking verwendet. Es wurde vor jeder Discovery-Implementierung eingefroren.
Änderungen nur als neue Version mit Begründung; die Abnahmeformulierungen sind
Beispiele, keine Optimierungsvorlage.

## Felder je Fall
`id` · `lang` · `need` (Formulierung) · `kind`
(`match|composition|none|state|degraded|build_candidate|reuse|guard`) ·
`allowed` (Glob-Muster, case-insensitiv) · `excluded` (dürfen NICHT erscheinen) ·
`expect_state` (optional) · `must_not_build` / `must_report_error` (optional) ·
`category` · `note`. Symbolische `fixture:*`-Muster bindet der Runner an die
Fixture-Projekte (Projekt-Isolation).

## Konsum (Welle 4)
Ein Runner in der echten Discovery (§4c) führt die Fälle gegen den realen
Suchvertrag aus und berichtet je Metrik Zähler/Nenner. Automatisierte
Harness-Läufe und Live-Produktbelege werden getrennt ausgewiesen. Kritische
Scope-/Quarantäne-Fälle (`s*`, `q*`) müssen bestehen; sonst keine Abnahme.
