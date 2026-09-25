# SKALDIR AGENT — Ausbaustufe 2: Lenkung, Hände, Werkstatt, Tresor

**Live-Reasoning-Steering · Computer Use · Tool-Suiten & APIs · Echtgeld & Identität**
Design v0.2 · 11.09.2026 · Detail-Dokument zum Feature-Master `CORE-PLAN.md`; Erweiterung von `DESIGN.md`
Arbeitstitel war „Metis" → **Entscheidung: der Harness heißt „Skaldir Agent".**

> Namenshinweis: Vor Veröffentlichung Marken-/Domain-Check („SkaldirAgent", „Skaldir Agent AI"). Der Name setzt die Messlatte hoch — die beste Antwort darauf ist: die Stimme spricht selten, aber wenn sie spricht, hat sie recht und Belege.

**Subsystem-Namen (Arbeitstitel im Code):**

| Name | Rolle |
|---|---|
| **The Voice** | Live-Lenkung: Goal-Track, Scope-/Slop-Guard, Korrektur, Anti-Halluzination |
| **The Pact** | Aufgabenvertrag: Ziele, Nicht-Ziele, Abnahmekriterien (vom Nutzer bestätigt) |
| **The Ledger** | Continuity Ledger: „Stand der Dinge" — macht Arbeit jenseits des Kontextfensters möglich |
| **The Archive** | Das 5-Schicht-Memory aus DESIGN.md (Incidents, Notes, Skills, Tools) |
| **The Forge** | Selbstbau von Tools und ganzen Tool-Suiten (MCP) |
| **The Hands** | Computer Use (Browser + Desktop, in VMs) |
| **The Treasury** | Echtgeld mit Mandaten, Caps, Approvals, Audit |
| **Night Shift** | Konsolidierung: Incidents, Skill-/Tool-Kandidaten, Ledger-Pflege, Research-Revalidierung |

---

## 0. Was diese Ausbaustufe ändert

Ausbaustufe 1 (DESIGN.md) beschreibt den Mentor, der bei Problemen **reaktiv** hilft.
Ausbaustufe 2 macht daraus eine **dauerhafte, aktive Lenkungsschicht** („die Stimme"), gibt dem Agenten **Hände** (Computer Use), eine **Werkstatt** (eigene Tool-Suiten) und streng begrenzten Zugang zu **Geld und Identität** — genau die drei Dinge, die einem Agenten heute zur „erwachsenen" Handlungsfähigkeit fehlen. Das ist ein sehr großer Scope: §6 sortiert die Reihenfolge, §8 sagt klar, was gute und was schlechte Ideen sind.

---

## 1. THE VOICE — Live-Reasoning-Lenkung (Herzstück)

### 1.1 Prinzipien
1. **Selten sprechen, immer belegt:** Die Stimme hat Pro-Task-Budgets (Default: 5 Einspeisungen + 1 Abschluss-Review). Jede Einspeisung: Beleg (Doku, Memory, Ausführung, Test) — oder sie schweigt. Keine Meinungen ohne Quelle.
2. **Drei Regler:** `whisper` (leise Hinweise, Default) → `steer` (klare Richtungsvorgabe mit Alternativen) → `halt` (nur bei Gefahr/Verifikationsfälschung). `halt` ist ein Stopp mit Begründung, kein Befehlston.
3. **Die Stimme kennt den Pakt** — sie lenkt relativ zu Zielen und Abnahmekriterien, nicht nach Geschmack.
4. **Ignorierbar bleibt ignorierbar:** Jede Einspeisung ist als Rat markiert; der Hauptagent trägt die Entscheidung.

### 1.2 The Pact (Aufgabenvertrag)
Zu Task-Beginn erzeugt die Voice einen kurzen Vertrag:
```yaml
goal: "Cross-Validation für das Trainingsskript ergänzen"
non_goals: ["kein Refactor der Data-Loader", "keine neuen Dependencies"]
acceptance:
  - "python train.py --cv 5 läuft durch"
  - "Test suite/cv_test.py grün; Metriken im Log"
constraints: ["projekt-Stil: ruff clean", "kein Cloud-Upload"]
budget: {tokens: 150k, zeit_min: 60, money_eur: 0}
```
Bestätigung per 1 Klick (TUI), Änderungen nur explizit und versioniert. **Der Pakt ist die Messlatte für alles Folgende.**

### 1.3 The Ledger (Arbeit jenseits des Kontexts)
Ein kompakter (≤ 2 k Tokens) Zustandsartikel, den die Voice laufend pflegt:
- Pakt-Status je Kriterium (offen / in Arbeit / **belegt erfüllt** / gefährdet)
- getroffene Entscheidungen + Begründung („warum wurde Ansatz A verworfen")
- offene Fragen an den Nutzer
- Artefakt-Landkarte (Dateien, Commits, Sessions, Research-Notes)
- Test-Stand, nächste Schritte

Der Ledger wird bei jeder Kompaktierung/neuen Session **re-hydriert** — deshalb geht Wissen nicht mehr verloren, wenn das Kontextfenster voll läuft, und die Arbeit „über den Kontext hinaus" wird Normalzustand. Details (Diffs, Quellen) liegen im Archive und werden on demand geholt. **Regel: Erst Ledger schreiben, dann komprimieren.**

### 1.4 Was die Voice beobachtet
Reasoning-Stream (Thoughts/Messages), Tool-Calls + Ergebnisse, Diffs, Testläufe, Plan-Dateien, Nutzer-Signale — **plus statische Quellen**, die billig und deterministisch sind: Linter (ruff), AST-Checks, Abhängigkeits-Diff, Symbol-/Import-Auflösung (LSP), Test-Anti-Pattern-Regeln, Diff-Budget. Das ist die Voraussetzung dafür, „Fehler machen" zu erkennen, ohne bei jedem Schritt ein großes Modell zu bezahlen.

### 1.5 Drei Monitoring-Ebenen
| Ebene | Frequenz | Kosten | Was sie prüft |
|---|---|---|---|
| **Step** | jeder Schritt | ~0 (deterministisch) | verbotene Ops, Slop-Muster, Scope-Mapping, Abhängigkeits-/Diff-Budget |
| **Checkpoint** | alle N Steps / nach Tests / bei Trigger | 1 kleiner LLM-Call | Plan-Drift, Pakt-Widerspruch, Halluzinationsverdacht, Fortschrittstempo, Mid-Task-Infos |
| **Session** | Night Shift | 1 guter Call | Bilanz vs. Abnahmekriterien, Lerneinträge, „was lief schief" |

### 1.6 Goal-Track & Abnahme mit Evidenz
- Live-Status je Pakt-Item; die Voice zeigt ihn in der TUI (kleine Leiste: `✓✓~—`, Tooltip = Details).
- **Kein „fertig" ohne Beleg:** Abschluss heißt Delivery-Report „welches Kriterium ist wodurch belegt" (Testname, Screenshot, Diff-Hash, Logzeile). Verifikationsbehauptungen ohne Lauf werden als Lüge behandelt (halt + Report). Das adressiert direkt die OSWorld-2.0-Erkenntnis „agents … skip verification".
- „Gefährdet" löst früh einen Whisper aus („Kriterium 2 hängt an API X, die laut Doku Y nicht kann — Alternativen: …").

### 1.7 Scope- & Slop-Guard (was du explizit wolltest)
**Slop-Signale, die deterministisch erkannt werden** (Beispiele, wird zur gepflegten Regelliste ausgebaut):
- **Platzhalter:** TODO/FIXME/`pass`/`NotImplementedError`/„…" in neuem Code
- **Test-Theater:** Tests ohne Assertions, `assert True`, Mocks, die das Getestete mocken, Flaky-Toleranz statt Fix
- **Fehler-Verschlucken:** bare `except`, `except: pass`, Fehler loggen und normal weitermachen
- **Ungefragter Scope:** neue Dependencies, Refactors, Umbenennungen, Config-Optionen, „nice-to-have"-Features **außerhalb des Pakts** → Whisper: „X ist nicht im Pakt — bewusst?"
- **Duplikation/Parallelimplementierung** desselben Verhaltens
- **Tote Pfade:** Flags, die immer an/aus sind; ungenutzte Exporte
- **Erfundene APIs:** Symbole/Flags/Pfade, die nicht existieren → automatischer Existenz-Check (Import auflösen, Doku-Index) **bevor** der Agent sich darauf verlässt
- **Hardcodes:** Secrets, Pfade, magische Werte, Testdaten im Prod-Pfad
- **Kommentar-Lügen:** Kommentar behauptet anderes Verhalten als der Code
- **Über-Defensive:** 15 Validierungen für unmögliche Fälle, während der Normalfall bricht

Eskalationsregeln: Erst `whisper` mit Beleg; Wiederholung → `steer`; Pakt-/Sicherheitsbruch → `halt`. Bei **ehrlicher** Scope-Frage des Agenten an den Nutzer: nicht einschreiten (Fragen ist erwünscht — OSWorld 2.0 nennt „guess rather than ask the user" als Top-Fehler).

### 1.8 Fehler und Widersprüche klar benennen (das „Skaldir Agent-Prinzip")
Korrektur-Protokoll: **(1)** Beleg sammeln (Doku/Memory/Ausführung). **(2)** Dreistufige Aussage: „A ist falsch, weil B (Quelle). Richtig ist C." / „A ist unbewiesen — ich finde keinen Beleg." / „A ist gefährlich — Stopp." **(3)** Nie dieselbe Korrektur zweimal (Cooldown je Signatur). **(4)** Auch der **Nutzer** darf widersprochen bekommen — sachlich, mit Beleg.
Wichtig: Die Stimme korrigiert nicht „aus dem Gefühl", sondern nur mit Evidenz — sonst wird sie zum störenden Besserwisser und die Precision-Metrik (3.1.4 in DESIGN.md) fällt.

### 1.9 Anti-Halluzination (vier Gates)
1. **Behauptungs-Gate:** Aussagen über APIs/Doku/Verhalten brauchen Retrieval-Beleg — sonst wird die Aussage als `unbestätigt` markiert und der Agent muss sie verifizieren (Doku-Check, Mini-Repro).
2. **Ausführungs-Gate:** „funktioniert/getestet/grün" nur nach echtem Lauf mit sichtbarem Ergebnis.
3. **Unsicherheits-Kalibrierung:** Der Hauptagent wird angehalten, Unsicherheit zu markieren („weiß ich nicht, prüfe ich"); die Voice verlangt Kalibrierung statt Bluff — und markiert unevidenzielle Sicherheit („definitiv" ohne Beleg).
4. **Quellenpflicht:** Jede Fakten-Einspeisung der Voice trägt Quelle + Datum + (bei Doku) Versions-Scope.

### 1.10 Betriebsmodi & Metriken
Modi: `off / whisper / steer / halt` (Default whisper), Shadow (loggen, nicht sprechen), Budgets & Thumbs wie Mentor-Log. Metriken: **False-Correction-Rate** (< 5 % anvisiert), Scope-Flag-Precision (≥ 70 %), Slop-Catches pro Session, **Evidence-Coverage bei Abnahme** (Ziel 100 %), User-Bestätigungsrate der Einspeisungen.

---

## 2. THE HANDS — Computer Use

### 2.1 Wofür — und in welcher Reihenfolge
1. **Eigene/andere Apps testen** (Web zuerst, dann Desktop) — GUI-E2E-Tests, die eine API-Abdeckung nicht bietet.
2. **Dienste ohne API** bedienen (Legacy-Tools, lokale Software).
3. Letzte Instanz: alles, was nur über UI geht. **Reihenfolge immer: API > Browser (DOM) > Desktop (Vision).** Jede Stufe ist teurer, langsamer und flakier als die vorige.

### 2.2 Architektur (Hybrid-Wahrnehmung + Verifikationsschleife)
- **Browser:** Playwright/Patchright via CDP — DOM + Accessibility-Tree schlagen Vision fast immer (billiger, präziser). Vision nur für Canvas/Unbekanntes.
- **Desktop:** Screenshot + **AT-SPI-Accessibility-Tree** (Linux) + OCR-Fallback + VLM; Aktoren: `xdotool` (X11), `ydotool` (Wayland), `pyautogui`; Clipboard kontrolliert.
- **Nach jeder Aktion Verifikation** (Assertion aus A11y-Tree, Pixel-Diff, Textcheck) — keine blinden Klickketten. Trajektorie wird aufgezeichnet (Screenshots/Video) → Beweismaterial für Delivery-Reports und Debugging.
- **Kostenbremse:** Screenshots downscalen, ROI-Crops, Delta-Frames, Text-first (A11y) — Vision-Tokens sind der Kostentreiber Nr. 1.

### 2.3 Isolation (nicht verhandelbar)
Computer Use läuft **ausschließlich in VMs** (microsandbox/libvirt, Snapshot vor jeder Tour), nie auf dem Host. Test-VMs bekommen **dedizierte Testidentitäten** (nie echte Konten), Egress-Filter, keine Familien-Secrets. „Löschen/Kaufen/Senden"-Aktionen: Approval-Pflicht.

### 2.4 Modelle
Optionen: großes VLM (Claude/GPT-Klasse) als Generalist; spezialisierter Grounder (UI-TARS-Klasse, lokal); **Hybrid** Planner (starkes Modell) + Grounder (schnell/spezialisiert) — nach OSWorld-Analyse der Standard für gute Ergebnisse. Entscheidung als Benchmark-Paket bei M4 (gleiche Tasks durch alle Varianten).

### 2.5 Test-Suite-Generator
App → Touren generieren (Smoke, Kernflows, Kantenfälle), Assertions aus A11y/DOM, reproduzierbare Fehlerberichte mit Video + DOM-Snapshot + Repro-Schritten. „Alle Applications testen" heißt realistisch: **alle Apps, die fürs Projekt relevant sind** — in abgestufter Tiefe, nicht jede App der Welt.

### 2.6 Ehrliche Zahlen (Stand recherchiert 11.09.2026)
- OSWorld (Original): Mensch ~72,4 % vs. bestes Modell 12,2 % (Paper-Stand 2024).
- **OSWorld 2.0** (long-horizon, 108 Workflows, ~1,6 h Mensch-Median): Bestwert **31,4 % binär / 68,3 % partiell** (Claude Opus 5, Stand 08.08.2026; GPT-5.6 Sol 27,3 %). Dokumentierte Hauptfehler: **Constraints verlieren, Mid-Task-Infos verpassen, raten statt fragen, Verifikation überspringen, versteckten Zustand nicht rekonstruieren.**
- → Genau diese Fehlerklassen adressiert THE VOICE (Goal-Track, Ledger, Eskalation, Evidence-Gates). Computer Use + Voice ist also keine Addition, sondern eine Synergie — aber Computer Use bleibt 2026 teuer, langsam und flaky. Einsatz: gezielt, mit VM, mit Beweisen.

---

## 3. THE FORGE — Tool-Suiten & APIs selbst bauen

### 3.1 Leiter (immer von unten nach oben)
1. **Compose:** MCP-Registry, ClawHub, dsh-Plugins, Hermes-Hub nach existierendem Server/Tool durchsuchen. (Tool-Search als First-Class-Feature.)
2. **Einzel-Tool** bauen (aus DESIGN.md 3.4: Signale → Spec → Sandbox → Tests → Replay → MCP-Registrierung).
3. **Suite:** ein zusammenhängendes Domain-Paket als **ein MCP-Server** (fastmcp) mit gemeinsamem Datenmodell, Auth, Versionierung — z. B. `billing-suite`: `create_invoice`, `list_invoices`, `send_reminder`, `reconcile` oder `gpu-suite`: `status`, `reserve`, `profile_run`, `cleanup`.

### 3.2 Spec-first-Pipeline
`Spec (JSON-Schema/OpenAPI)` → `Code (Python)` → `Tests (happy/edge, echte Verhaltenstests)` → **Replay gegen historische Fälle** aus dem Event-Log → **Security-Scan** (SSRF, Path-Traversal, Secret-Leak, Injection-Sinks) → **Shadow-Rollout** (nur vorschlagen, Telemetrie sammeln) → Registry aktiv. Version + Changelog + Deprecation-Policy.

### 3.3 Suite-Standards
Auth nur über Broker (Agent sieht Handles, nie Secrets), Idempotenz + Retries, Rate-Limits, Migrationspfad, Beispiele fürs LLM (Tool-Beschreibung = Prompt!), Quotas, Killswitch, Audit pro Aufruf.

### 3.4 Was schiefgehen kann (und die Gegenmittel)
**Tool-Sprawl** (50 Tools, 5 benutzt) → Night-Shift-Konsolidierung schlägt Merges/Deprecation vor. **Sicherheitslücken in generiertem Code** → Scanner + Review-Gate vor Aktivierung, Rechte minimal. **Wartelast** → nur Kern-Tools aktiv halten, Rest archiviert. **Eigenbau-Falle** → Regel „compose before create": wer baut, muss begründen, warum kein Ökosystem-Tool passt.

---

## 4. THE TREASURY — Echtgeld, streng geregelt

### 4.1 Warum das gefährlich ist (ehrlich)
Ein Agent mit (a) Zugriff auf Echtgeld, (b) Browser-Session im eingeloggten Konto und (c) Fremdinhalten aus dem Web ist **der Worst-Case-Injection-Vektor**: Eine präparierte Seite, ein vergiftetes Tool-Result oder ein „Research"-Fund kann eine Zahlung umlenken. Dazu kommen: Fraud-Systeme der Kartenanbieter blocken Bot-Muster, 3DS/SCA verlangt oft einen Menschen, manche AGB verbieten Automatisierung, und **die Haftung bleibt beim Menschen**. Deshalb: Geld wird nicht „freigegeben", sondern **mandatiert**.

### 4.2 Die 7 Regeln (Design-Invarianten)
1. **Nie Hauptkonten.** Dediziertes Zweitkonto mit Minimal-Ballast; Hauptkarten/-konten sind für den Agenten tabu.
2. **Credentials nie im Agenten-Kontext.** Secrets liegen im Broker (z. B. Credential-Injection-Proxy); der Agent bekommt nur Scoped-Handles, die nur für eine erlaubte Aktion funktionieren.
3. **Geld nur per Mandat:** Betrag, Empfänger, Zweck, Ablauf, Anzahl — signiert/gespeichert, maschinell prüfbar. Nichts „auf Zuruf aus dem Trajectory-Text".
4. **Vier-Augen über X €** (Default 0 € in Stufe 0/1): Mensch bestätigt per 2FA; der Agent kann Vorschläge vorbereiten, nie selbst ausführen.
5. **Alles auditiert:** jede Zahlung mit Beleg (Rechnung/Receipt), Ledger, monatlicher Report, Reconciliation Konto↔Ledger.
6. **Killswitch:** Karte sperren / Budget auf 0 in einem Klick (TUI-Taste + CLI).
7. **Kein Kauf von Unbekanntem, keine Abos ohne explizite Freigabe, kein Krypto ohne getrennte Wallet.**

### 4.3 Stufenmodell (wir starten bei 0 und gehen nur mit Daten weiter)
| Stufe | Was der Agent darf | Technik | Wann |
|---|---|---|---|
| **0 — Vorschlag** | Käufe **vorbereiten** (Warenkorb, Formular, Vergleich), Mensch bestätigt + zahlt (2FA) | Browser mit gespeicherter Zahlungsmethode (unsichtbar), Approval-Flow | Sofort (M4) |
| **1 — Vorbereitet + bestätigt** | wie 0, plus: Agent triggert vorab genehmigte **Vendor-Auto-Recharges** mit harten Vendor-Caps (z. B. API-Guthaben) | Vendor-Billing-Limits, virtuelle Karte mit Monats-Cap nur für Whitelist | M4/M5 |
| **2 — Mandatierte Mikrokäufe** | autonom bis ≤ 20 €/Transaktion, ≤ 50 €/Woche, nur Whitelist-Händler, nur Kategorie „Infrastruktur/API" | AP2-Style VDCs (Checkout/Payment-Mandate) oder virtuelle Karte mit Per-Merchant-Limits; Anomalie-Monitoring; Auto-Freeze | M5+, erst nach 3 Monaten sauberer Stufe-1-Daten |
| **3 — Größere Summen autonom** | **nicht empfohlen** | — | nur mit geklärter Haftung/Versicherung, praktisch: nein |

### 4.4 Technik
Treasury-Service mit **Policy-Engine** (YAML): `allowlist` (Händler), `caps` (pro Tx/Tag/Woche/Monat, pro Kategorie), `time_windows`, `approval_matrix`, `dry_run`. Bestellbestätigung → Receipt-Capture → Ledger → Reconciliation → Anomalie-Alarme (neuer Händler, Betrag > Schwelle, Frequenz, unmögliche Uhrzeit). Jede Policy-Änderung ist ein Audit-Event.

### 4.5 AP2 als Goldpfad (Stand: verifiziert 11.09.2026)
**Agent Payments Protocol (Google, mit A2A/Universal Commerce Protocol-Integration, v0.2, Standardisierung läuft bei der FIDO Alliance).** Kernidee: **Verifiable Digital Credentials** — *Checkout-Mandate* (was darf gekauft werden, offen/geschlossen) und *Payment-Mandate* (welches Instrument, welcher Betrag; offen/geschlossen), kryptografisch signiert, auditierbar. Motto: „**Verifiable Intent, Not Inferred Action**" — exakt die Antwort auf „Agent halluziniert einen Kauf". Dazu `x402` (HTTP-natives Bezahlen, auch Stablecoins) als zweiter Pfad. **Plan:** Stufen 0/1 ohne AP2-Abhängigkeit bauen; AP2-Mandate als eigenes Arbeitspaket ab M5 evaluieren (und mittelfristig Stufe 2 darauf stellen, statt auf rohe Karten).

### 4.6 Die ehrliche Empfehlung zum „API-Credits kaufen"
In 90 % der Fälle unnötig: API-Anbieter (OpenRouter & Co.) sind **prepaid** — der Mensch lädt auf, der Agent konsumiert. Der Agent braucht dazu **keinen** Geldzugriff, nur Keys. Echte Geld-Zugriffe bleiben Ausnahmen (Domain, API ohne Prepaid, Hosting, Tools) → dafür ist Stufe 0/1 perfekt: Agent recherchiert und bereitet vor, Mensch bestätigt in Sekunden. **Autonomes Geld ist kein Feature, das man „auch noch" anbaut — es ist das gefährlichste Feature überhaupt und kommt zuletzt, klein und mandatiert.**

---

## 5. THE IDENTITY — Google & Konten (streng geregelt)

1. **Dedizierte Identität, nie das Hauptkonto.** Ein separates Google-Konto (Name z. B. `godsv.agent@…`) für Dinge, die ein Google-Login brauchen. Das Hauptkonto des Nutzers bleibt unangetastet.
2. **2FA: TOTP im Tresor**, Recovery beim Menschen; keine Recovery-Codes im Agenten-Kontext.
3. **Zahlungsmethode:** keine — oder in Stufe 1 eine gedeckelte virtuelle Karte nur für Whitelist.
4. **Eigenes Browserprofil** (persistent, isoliert), Login nur dort; neue Geräte/Logins erzeugen Alerts an den Menschen.
5. **Für Sign-ups primär Masked-E-Mails** (Alias-Dienst) + Passwortmanager; „Login with Google" nur, wo nötig; **verbundene Apps periodisch auditieren** (OAuth-Grants können Datenzugriff bedeuten).
6. **Ban-Risiko akzeptieren:** Google kann automatisierte Nutzung sperren → kritische Artefakte regelmäßig exportieren; das Konto nie als einziger Identitäts-Anker für Wichtiges.
7. **Was der Agent nie bekommt:** Haupt-Mail, Haupt-Cloud, Haupt-Kalender, Haupt-Recovery.

---

## 6. Roadmap-Update (Ausbaustufe 2 eingefügt)

| Phase | Inhalt | Abhängig von |
|---|---|---|
| M2 (wie gehabt) | Mentor v1 + Shadow-Eval | M1 |
| **M2+ Voice v1** | Pact, Goal-Track, Step-Monitor (deterministisch), Korrektur-Protokoll, Evidence-Rules | M2 |
| **M2++ Ledger** | Continuity Ledger + Re-Hydration (Arbeit über Kontextgrenzen) | M2+ |
| M3 Forge v1 | Tool-Mining → Panels; erste Suite als MCP-Server | M1/M2 |
| **M3+ Voice v2** | Checkpoint-Monitor (LLM), Slop-Guard v2, „fragen statt raten"-Regeln | M2+ |
| **M3.5 Hands v0** | Browser-first (Playwright/A11y), VM-Setup, Test-Suite-Generator (Web) | M0 |
| **M4 Hands v1** | Desktop in VM (A11y+VLM), Trajektorien-Beweise, OSWorld-Subset-Eval | M3.5 |
| **M4 Treasury v0** | Konto/Identität dediziert, Policy-Engine, Approval-Flow (Stufe 0), Audit | M2 |
| **M4.5 Identity** | Dediziertes Google-Konto + Regeln aus §5, Masked-Mails | M4 |
| M5 Desktop | Tauri-App + Ledger-/Voice-/Memory-Views | M2++ |
| **M5 Treasury v1** | Stufe 1 (Vendor-Caps), Reconciliation, Monatsreport | M4 |
| **M6 (optional)** | AP2-Mandate (Stufe 2), OSWorld-2.0-Vollsubset, fremde Harness-Interop erweitert | M5 |

---

## 7. Metriken-Update

- **Voice:** False-Correction-Rate < 5 %; Scope-Flag-Precision ≥ 70 %; Evidence-Coverage bei Abnahme = 100 %; Einspeisungen pro Task ≤ 6; Nutzer-Bestätigungsrate.
- **Hands:** Task-Success auf interner Web-E2E-Suite (Start: Baseline messen); OSWorld-Subset-Score (Fortschritt, kein Absolutvergleich); Kosten pro GUI-Task (Ziel: nur einsetzen, wo Wert > Kosten).
- **Forge:** Anteil Aufgaben, die von Ökosystem-Tools vs. Eigenbau gelöst wurden (compose-Rate ≥ 80 %); Tool-Fehlerrate; Sprawl-Kennzahl (Tools ohne Nutzung in 60 Tagen).
- **Treasury:** 0 unautorisierte Transaktionen (hart); 100 % Belege; Median-Approval-Zeit < 1 min; Anomalie-False-Positives < 1/Monat.
- **Gesamt (wie gehabt):** Recurrence-Kill-Rate, Hint-Precision, Overhead ≤ 15 %.

---

## 8. WAS IST EINE GUTE, WAS EINE SCHLECHTE IDEE? (deine Frage, klar beantwortet)

### Klar gute Ideen
1. **Live-Steering mit Belegpflicht (Voice/Pact/Goal-Track)** — der wertvollste Baustein. Die OSWorld-2.0-Fehlerliste (Constraints verlieren, Mid-Task-Infos verpassen, raten statt fragen, Verifikation überspringen) liest sich wie die Anforderungsliste für die Voice. Auch die Forschung stützt es: ohne externe Verifikation keine zuverlässige Selbstkorrektur (arXiv 2310.01798).
2. **Continuity Ledger für Arbeit über Kontextgrenzen** — direkt machbar, sofort spürbar, geringe Kosten, großer Effekt (Multi-Session-Arbeit ohne Wiederholung).
3. **Computer Use mit VM-first + API-first-Priorität** — schließt eine echte Lücke (Apps ohne API, GUI-Tests). Als *gezieltes Werkzeug* stark; übertriebene Erwartungen sind das Risiko, nicht die Idee.
4. **Tool-Suiten selbst bauen — mit Spec-first + Gates + „compose before create"** — sinnvoll ab M3, besonders für Domänen-Tools (GPU-Status, Training-Dashboards, Billing).
5. **Streng geregeltes Treasury-Konzept (Stufen, Mandate, Caps, Audit)** — die *Regeln* sind die gute Idee; AP2 zeigt, dass die Industrie genau das standardisiert (Verifiable Intent statt Inferred Action).
6. **Dedizierter Google-Account als Arbeitsidentität** — nützlich (Sign-ups, Formulare, YouTube/Docs-Hilfsdienste) und mit den §5-Regeln beherrschbar.

### Klar schlechte / gefährliche Ideen (bitte nicht so bauen)
1. **„Agent darf mit Echtgeld autonom einkaufen" als v1-Feature.** Kombination Konto+Zahlung+Webinhalte ist der Worst-Case-Injection-Vektor; Fraud-Systeme und 3DS blocken ohnehin; Haftung bleibt beim Menschen. → Stufenmodell, Mandate, Caps, Approval. Autonom nur ≤ Kleinbeträge, erst spät, erst nach sauberen Daten.
2. **Der Agent sieht echte Karten-/Bankdaten oder Passwörter.** Sobald ein Secret im Kontext/Trajectory war, ist es für Injection- exfiltrierbar. → Broker/Injection-Proxy, Handles statt Secrets, Payment-Methoden unsichtbar im Browserprofil.
3. **Google-Hauptkonto (oder gar Mail-Zugriff) freigeben.** Account-Recovery + Mail = Identität. Ein kompromittierter Agent kann damit alles zurücksetzen. → Nur dediziertes Konto; Hauptkonto tabu.
4. **Autonome Käufe ohne Whitelist/Caps/Anomalie-Monitoring.** Halluzination + fehlende Grenze = Schaden mit Ansage. → Jede Zahlung gegen Policy prüfen; neue Händler = immer Approval.
5. **„Der Agent muss so stark wie ein Mensch werden" als Zieldefinition.** Nicht messbar, nicht erreichbar in einem Schritt — und verleitet zu falschen Prioritäten. → Ersetze es durch die Capability-Liste (siehe `CAPABILITY-GAPS.md`) + Metriken pro Fähigkeit. Der ehrliche Anspruch: *Der Agent wird in definierten Fähigkeiten menschlich vergleichbar, in anderen (Speed, Ausdauer, Gedächtnis) überlegen — und in Verantwortung, Urteilsvermögen und physischer Welt bleibt der Mensch vorerst unersetzlich.*
6. **„Alle Applications testen" wörtlich.** GUI-Tests über Vision sind teuer/flaky; die besten Systeme liegen bei OSWorld 2.0 bei ~31 % binär (Stand 08/2026). → API > Browser-DOM > Desktop-Vision; Tests für *relevante* Apps, gestufte Tiefe, VM-Isolation, Beweise.
7. **Voice, die dauernd dazwischenredet.** Eine einspeisende Stimme bei jedem Schritt zerstört Vertrauen, verteuert alles und macht den Agenten schlechter. → Precision-first, Budgets, Cooldowns, Shadow-Tuning, „nie dieselbe Korrektur zweimal".
8. **Der Agent als juristische Person/alleiniger Kontoinhaber.** Konten müssen Menschen gehören (AGB, Haftung, Steuer). Der Agent ist Werkzeug des Kontoinhabers — auch in der Architektur muss das sichtbar bleiben (Approval, Audit, Mandat).

---

## 9. Kurz: was ich NICHT einbauen würde (Anti-Scope)
Kein Cloud-Zwang; kein „Agent kauft selbstständig alles"; keine Vision-only-Desktop-Steuerung, wo DOM/A11y existieren; kein Tool-Zoo (compose first); keine Voice ohne Budget; keine Geld-Features vor Memory/Voice-Grundlagen; kein Desktop-UI vor M5.

---

*Quellen (Repos, OSWorld, AP2, Papers): siehe `SOURCES.md` und `REPOS.md`. Dies ist ein lebendes Dokument — Änderungen mit Datum pflegen.*
