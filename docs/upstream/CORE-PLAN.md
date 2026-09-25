# SKALDIR AGENT — Core-Plan (konsolidiert)

**Nur die neuen Kern-Features — Plan 1 + Plan 2 in einem Dokument.**
v1.0 · 11.09.2026 · Dieses Dokument ist die *Feature-Master-Übersicht*; Detail-Tiefe bleibt in den verlinkten Docs.

---

## 0. Das Bild in einem Blick

```
                    ┌─────────────────────────────────────────────┐
                    │              THE VOICE                       │
                    │  lenkt das Live-Reasoning: Goal-Track,       │
                    │  Scope-/Slop-Guard, Korrektur, Evidenz,      │
                    │  Einspeisung von Lösungshinweisen            │
                    └───────▲───────────────┬─────────────────────┘
         liefert Belege │               │  lenkt & schützt
                    ┌───────┴───────────────▼─────────────────────┐
                    │   MEMORY & RESEARCH (neu)                    │
                    │   Incidents („hatten wir das schon?“) +      │
                    │   Research-Notes mit Provenienz + Ledger     │
                    └───────▲───────────────┬─────────────────────┘
      schreibt Neues   │               │  speist Werkzeuge
                    ┌───────┴───────────────▼─────────────────────┐
                    │   SKILL-KONZEPT   +   TOOL-SUITES            │
                    │   (aus Beobachtung gemintet, validiert)      │
                    └───────▲───────────────┬─────────────────────┘
        handelt mit  │               │  bedient
                    ┌───────┴───────────────▼─────────────────────┐
                    │   COMPUTER USE (Hands)   +   TREASURY        │
                    │   VM-first, API>Browser>Desktop  │ Vault:   │
                    │   Test-Suite-Generator           │ Keys,   │
                    │                                  │ Logins, │
                    │                                  │ Geld    │
                    └──────────────────────────────────────────────┘
```

Drei Sätze zum Zusammenwirken: **Memory & Research sind das Fundament** (Wissen überlebt Sessions), **die Voice ist der Dirigent** (nutzt das Fundament live während der Arbeit), **Skills & Tool-Suites sind der Output** (was gelernt wurde, wird wiederverwendbar), **Computer Use sind die Hände** (Ausführung überall, auch ohne API) und **die Treasury ist die Vertrauensgrenze** (der Besitzer entscheidet, was der Agent benutzen darf — und sieht es nie).

### Die 6 Säulen dieses Plans
| # | Säule | In einem Satz |
|---|---|---|
| 1 | **THE VOICE** | Lenkt das Live-Reasoning: hilft beim Feststecken, hält auf Kurs, verhindert Slop & Halluzinationen — mit Belegen. |
| 2 | **MEMORY & RESEARCH (neu)** | Problemmemory mit Fehler-Signaturen + Research-Notes mit Provenienz + Continuity Ledger = Arbeit über Kontextgrenzen hinweg. |
| 3 | **SKILL-KONZEPT (neu)** | Skills entstehen aus beobachteter Praxis: Vorschlag → Shadow-Test → Promotion → Drift-Monitoring. |
| 4 | **TOOL-SUITES** | Der Agent baut eigene Werkzeuge und ganze MCP-Suiten — mit Spec, Tests, Review-Gates. |
| 5 | **COMPUTER USE** | Apps testen und bedienen, die keine API haben — in VMs, mit Beweisen. |
| 6 | **TREASURY (neu fokussiert)** | Der Besitzer übergibt API-Keys, Logins und (gestuft) Geld — verschlüsselt, gescoped, auditiert, nie im Agenten-Kontext. |

### Plan-1-Elemente → wo sie jetzt leben
| Aus Plan 1 | Jetzt in |
|---|---|
| Longterm-Memory („wie Hermes") | Säule 2 (5 Schichten) |
| Research-Ergebnisse-Memory | Säule 2 (§2.3) |
| Neben-Agent, der bei Problemen mit Quellen hilft | Säule 1 (§1.1 „Job A") |
| Skills durch Beobachtung vorschlagen | Säule 3 |
| Toolvorschläge | Säule 4 |
| Night-Shift-Konsolidierung, Eval-Kernmetrik, Belegpflicht | Säule 2 / §9 / §1 |

---

## 0.5 Basis-Entscheidung: Hermes Agent ✔
*(entschieden 11.09.2026)*

**Entscheidung: Skaldir Agent baut auf Hermes Agent auf** — als Plugin + eigener Daemon (`mentord`) auf dem Hermes-Host. Der Skaldir Agent-Kern (Memory, Voice, Registry) bleibt hinter einer dünnen **Adapter-Schicht harness-agnostisch**, damit OpenClaw/DeepSeek Harness später über MCP andocken können, statt uns an einen Host zu ketten.

**Warum Hermes:**
1. **Python** — deckt den PyTorch-Use-Case: Tool-Suiten, Eval-Skripte, torch-Introspektion in derselben Sprache wie der Host.
2. **Die exakten Erweiterungspunkte, die wir brauchen, existieren schon:** Hooks (Lifecycle-Events für die Voice), Plugin-System mit `ctx.llm` (Triage/Synthese), **Memory-Provider-Plugins** (unser 5-Schicht-Memory als Provider), **Context-Engine-Plugins** (Kompression), Prompt-Assembly mit *ephemeral layers* (cache-sichere Einspeisung), Skills mit **Write-Approval-Gate**, **Credential-Vault + iron-proxy** (Secrets-Injection — exakt unser Treasury-Zonen-A/B-Prinzip), Computer-Use- und Browser-Toolsets, Cron/Delegation/Goals.
3. **TUI + Desktop existieren bereits** (Ink-TUI, Desktop-App, Desktop-Plugin-SDK) → „erst Terminal, dann Desktop" wird zum Plugin-Ausbau statt Neubau; die Energie fließt in Voice/Memory/Skills/Tools/Treasury.
4. **Keine Feature-Kollision:** Hermes hat *kein* Live-Steering, *kein* Incident-Memory, *kein* Tool-Mining. Der Curator (Skill-Pflege) ist komplementär — unser Skill-Mining füttert ihn, das Approval-Gate ist schon da.
5. **Lizenz & Momentum:** MIT, sehr aktiv, wir sind selbst täglicher Nutzer (Dogfooding) und können Patches upstream beitragen.

**Warum nicht OpenClaw 2.0:** TypeScript/Swift statt Python (Bruch zum PyTorch-Stack); Sicherheits-Posture kollidiert mit der Treasury (Kritik: Secrets nicht at-rest verschlüsselt, Sandbox nicht standardmäßig aktiv, keine Netz-/FS-Grenzen bei Shared Sessions — wir müssten gegen den Host arbeiten); sehr großer, schnell drehender Codebase; **Feature-Überlappung** (Skill Workshop + Dreaming sind post-run — unser In-Task-/Incident-Ansatz liefe gegen deren Lern-Design). Bleibt Interop-Ziel via MCP.
**Warum nicht DeepSeek Harness:** Developer Preview mit ausdrücklichem „core plugins and APIs will continue to evolve" (API-Churn als Fundament zu riskant); Node/TS-Stack; die eleganten Event-Log-/Plugin-Ideen bleiben Referenzarchitektur (`DESIGN-2-GODS-VOICE.md`) und sind via MCP anbindbar.

**Konkrete Integrationspunkte (Hermes):**
| Skaldir Agent-Baustein | Hermes-Anknüpfung |
|---|---|
| Voice-Beobachtung | Hooks/Plugin auf Loop-Events (Tool-Results, Fehler, Testläufe) + `ctx.llm` für Triage/Synthese |
| 5-Schicht-Memory | Memory-Provider-Plugin + eigener Store; Hermes-Session-Suche (FTS) weiter nutzbar |
| Kontext-Assembly | Prompt-Assembly (*ephemeral layers*) + Context-Engine-Plugin → cache-sichere Einspeisung |
| Skills | eigenes Shadow-Staging davor → Promotion über `skill_manage` + Write-Approval-Gate |
| Tool-Suiten | MCP (Client + eigener Server unsererseits) + Plugin-Tools |
| Treasury Zonen A/B | Credential-Vault + iron-proxy-Muster, eigene Policy-Engine davor |
| Computer Use | Hermes-Toolsets + cua/Playwright-Erweiterung, VM-first |
| UI | TUI jetzt; Desktop später als Desktop-Plugin oder eigener Tauri-Client am selben Protokoll |

**Politik:** Versionen pinnen; Erweiterungen wo möglich als Plugin oder Upstream-PR (kein Hard-Fork); Adapter-Schicht klein halten; monatlicher Upstream-Blick. Falls Hermes je kippt: **Kern ist harness-agnostisch — Wechsel = neuer Adapter, kein Neubau.**

---

## 1. THE VOICE — Live-Lenkung (wichtigste Säule)

### 1.1 Zwei Jobs, ein System
- **Job A — „Ich weiß was du gerade nicht weißt":** Wenn der Agent feststeckt (Fehler-Loop, Test-Churn, Budget-Burn), liefert die Voice **einen** knappen, **quellenbelegten** Hinweis mitten in die laufende Arbeit — aus Incidents („das gab es schon, Lösung war X"), Doku (versionsgenau), Research-Notes oder Skills.
- **Job B — „Ich sehe, was du nicht siehst":** Sie beobachtet das Reasoning und **lenkt**: auf dem Ziel-Pfad halten, Scope-Drift und Slop stoppen, Widersprüche/Fehler klar benennen, Halluzinationen verhindern, Arbeit über die Kontextgrenze hinaus ermöglichen (via Ledger).

### 1.2 Der Pakt (Aufgabenvertrag)
Zu Beginn jeder Aufgabe: Ziele, **Nicht-Ziele**, Abnahmekriterien, Constraints, Budgets. Ein Klick Bestätigung, danach ist der Pakt die Messlatte für alles Weitere.
```yaml
goal: "Cross-Validation für das Trainingsskript ergänzen"
non_goals: ["kein Refactor der Data-Loader", "keine neuen Dependencies"]
acceptance:
  - "python train.py --cv 5 läuft durch"
  - "suite/cv_test.py grün; Metriken im Log"
constraints: ["ruff clean", "kein Cloud-Upload"]
```

### 1.3 Der Ledger (Arbeit jenseits des Kontexts)
Kompakter Zustandsartikel (≤ 2 k Tokens), laufend gepflegt: Pakt-Status je Kriterium (offen/in Arbeit/**belegt erfüllt**/gefährdet), Entscheidungen + Begründung, offene Fragen, Artefakt-Landkarte, Test-Stand, nächste Schritte. **Regel: erst Ledger aktualisieren, dann Kontext komprimieren.** Bei jeder neuen Session/Kompaktierung wird re-hydriert — Sessions werden nahtlos, Wissen verdampft nicht mehr.

### 1.4 Beobachtung in drei Ebenen
| Ebene | Frequenz | Kosten | Prüft |
|---|---|---|---|
| Step (deterministisch) | jeder Schritt | ~0 | verbotene Ops, Slop-Muster, Scope-Mapping, Diff-/Dependency-Budget |
| Checkpoint (kleines LLM) | alle N Steps / nach Tests / bei Trigger | gering | Ziel-Drift, Pakt-Widerspruch, Halluzinationsverdacht, Tempo |
| Session (Night Shift) | nach der Arbeit | ein guter Call | Bilanz, Lerneinträge, Skill-/Tool-Kandidaten |

### 1.5 Einspeise-Protokoll
- **Form:** append-only Nachricht (nie System-Prompt/History ändern → Prompt-Cache bleibt intakt), klar markiert, ignorierbar, mit Quelle + Datum + Konfidenz.
```
[voice id=4711 sig=ef3a91 typ=repeat_error conf=0.74 pos=1/5]
Diagnose: CUDA-OOM — dasselbe Muster wie Session 2026-08-14 (Incident #1234, gelöst).
Vorschlag: (1) gradient checkpointing … (2) batch_size halbieren + empty_cache.
Belege: [1] Incident #1234 (Test grün) · [2] PyTorch-Doku 2.9 (offline, 30.08.) · [3] Note #89.
Hinweis, keine Anweisung — bitte selbst verifizieren.
```
- **Budget:** Default max. 5 Einspeisungen + 1 Abschluss-Review pro Task; Cooldown pro Problem-Signatur (nie zweimal dasselbe); Mindestabstand.
- **Modi:** `whisper` (Default) → `steer` → `halt` (nur bei Gefahr/Betrug/Verifikationslüge); `shadow` für Evaluation (loggt, spricht nicht).

### 1.6 Korrektur-Protokoll & Anti-Halluzination
Korrektur nur mit Evidenz, in drei Stufen: **„A ist falsch, weil B (Quelle); richtig ist C."** / **„A ist unbewiesen — ich finde keinen Beleg."** / **„A ist gefährlich — Stopp."** Nie dieselbe Korrektur zweimal; auch der Nutzer darf sachlich widersprochen bekommen.
Vier Gates: **(1) Behauptungs-Gate** (API-/Doku-Aussagen brauchen Retrieval-Beleg, sonst als `unbestätigt` markiert), **(2) Ausführungs-Gate** („getestet/grün" nur nach echtem Lauf), **(3) Kalibrierungs-Regel** (Unsicherheit markieren statt bluffsicher formulieren), **(4) Quellenpflicht** (Quelle + Datum + Versions-Scope).

### 1.7 Scope- & Slop-Guard (deterministisch, deshalb billig)
Erkannt u. a.: Platzhalter (TODO/`pass`/`NotImplemented`), Test-Theater (keine echten Assertions), Fehler-Verschlucken (bare `except`), **ungefragter Scope** (neue Deps, Refactors, Extras außerhalb des Pakts → „X ist nicht im Pakt — bewusst?"), Duplikation, tote Flags, **erfundene APIs** (Existenz-Check vor Verlass), Hardcodes/Secrets, Kommentar-Lügen, Über-Defensive. Eskalation: whisper → steer → halt. **Fragen an den Nutzer ist erwünscht** (nicht einschreiten).

### 1.8 Abnahme mit Beweis
Kein „fertig" ohne Beleg: Delivery-Report „welches Kriterium ist wodurch belegt" (Testname, Screenshot, Diff-Hash, Logzeile). Unbelegte Erfolgsmeldungen = halt + Report.

### 1.9 Warum das die stärkste Wette ist
OSWorld 2.0 (Long-Horizon, Stand 08/2026) nennt als Hauptfehler der besten Agenten (~31 % Erfolg): **„Constraints verlieren, Mid-Task-Infos verpassen, raten statt fragen, Verifikation überspringen, versteckten Zustand nicht rekonstruieren."** — exakt das Anforderungsprofil der Voice. Dazu: Selbstkorrektur ohne externe Evidenz funktioniert nachweislich nicht (arXiv 2310.01798) → die Voice liefert Evidenz statt „denk nochmal nach".

---

## 2. MEMORY & RESEARCH — das neue Konzept

### 2.1 Drei Ideen
1. **Problemmemory statt Chat-Memory:** Nicht Gespräche werden erinnert, sondern **Probleme und ihre Lösungen** — matchbar über Fehler-Signaturen. („Das hatten wir schon.")
2. **Research, die bleibt:** Jede Recherche (Doku, Web, Papers) wird als kuratierte Note mit Provenienz gespeichert statt zu verdampfen.
3. **Immer wissen, wo wir stehen:** Der Ledger (§1.3) macht Zustand unabhängig vom Kontextfenster.

### 2.2 Der Incident-Store (Herzstück)
```json
// Problem-Signatur
{ "kind": "error|failtest|loop|uncertainty|budget",
  "fingerprint": "sha1(Fehlerklasse + Top-3 Stackframes (gemappt) + Exitcode + Tool)",
  "embedding": [ /* Kurzfassung des Problems */ ],
  "context": { "repo": "…", "toolchain": "python 3.12, torch 2.9+cu126" } }
```
```sql
CREATE TABLE incidents(
  id … , sig_kind, sig_fp, sig_vec, repo, env_json,
  title, root_cause_md, resolution_md, evidence_json,
  status,            -- open | solved | regressed
  occurrences, first_seen, last_seen, confidence, source );
```
**Matching:** exakter Fingerprint → sofort; sonst Ähnlichkeitssuche + kleines „gleiches Problem?"-Judge-Modell. Treffer werden als Hinweis mit Belegkette geliefert (nie automatisch angewandt). Jede Anwendung erhöht `confidence`; Regression setzt `status=regressed` und die Voice warnt früher.

### 2.3 Research-Memory (Provenienz-first)
```sql
CREATE TABLE notes( id … , topic, claim_md,
  source_type, source_url, source_meta,     -- Version, Autor, Datum
  retrieved_at, valid_until, trust,
  repo_scope, status,        -- active | stale | contradicted | unverified
  contradicts_json );        -- Konflikte werden ANGEZEIGT, nicht versteckt
```
Zwei Mechanismen: **(a) opportunistisch** — alles, was der Agent ohnehin recherchiert, wird kuratiert abgelegt; **(b) Pre-Warming** — der Research-Agent legt vorab **versionsgenaue Doku** (exakt die installierte PyTorch-/CUDA-Version) und Vorab-Notes zu typischen Problemklassen der Domäne an. → Löst das Cold-Start-Problem des Incident-Stores ab Tag 1.

### 2.4 Schreib-Gates & Qualität (Anti-Müll)
Der Agent **liest immer, schreibt nie direkt** ins Langzeit-Memory — die Konsolidierung schreibt, und nur mit Provenienz + Verifikation (Lösung nachweislich angewandt / Test grün / Nutzer bestätigt). Externe Fremdinhalte bleiben `unverified`. Importance-Gating: dauerhaft wird nur, was sich in ≥ 2 Sessions als nützlich zeigte; Rest verfällt (Decay). Dedup über Fingerprint + Embedding.

### 2.5 Night Shift (Konsolidierung)
Nach Feierabend: Episoden zusammenfassen → Incidents extrahieren/verifizieren → Duplikate mergen → Research-Notes revalidieren (Expiry, Refetch, Konflikte) → Skill-/Tool-Kandidaten zählen → Ledger-Backup. Ergebnis als Diff-Report („Diese Nacht gelernt: …"), auditierbar. (Konzeptionell Superset von Letta Sleep-Time und OpenClaw Dreaming — dort: Memory-Pflege; hier zusätzlich: Problemmemory, Skill-/Tool-Mining, Ledger.)

### 2.6 Abgrenzung (warum neu)
| System | Speichert | Wann | Was fehlt |
|---|---|---|---|
| Hermes | Bounded Notizen + Sessions (FTS) | vor/nach Session | kein Problemmatching, kein Live-Zugriff |
| OpenClaw 2.0 | Chat-Kontext + „Dreaming"-Konsolidierung | idle | Gesprächs- statt Problemgedächtnis |
| Letta | Memory-Blocks, Sleep-Time-Agents | idle | kein Werkzeug-/Skill-Lernen, kein In-Task |
| **Skaldir Agent** | **Probleme+Lösungen, Research, Skills, Tools** | **live + idle** | — |

---

## 3. SKILL-KONZEPT (neu)

### 3.1 Prinzip
**Skills entstehen aus beobachteter Praxis, nicht aus Zuruf.** Wenn sich ein Vorgehen wiederholt (≥ 3×, in ≥ 2 Sessions), schlägt die Night Shift einen Skill-Entwurf vor — autorisiert **aus der echten Trajektorie**, inklusive der Fallstricke, die beim ersten Mal Fehler verursacht haben.

### 3.2 Lebenszyklus
`Beobachten` → `Kandidat` (Dedup gegen bestehende Skills) → `Draft` (SKILL.md: When-to-Use, Procedure, **Pitfalls**, Verification) → **`Shadow`** (wird im Retrieval testweise mitgenutzt; Erfolg wird gemessen) → `Promotion` (Default: **propose** — Nutzer bestätigt mit einem Klick; optional auto nach Vertrauensphase) → `Monitoring` (Nutzungs-/Erfolgsrate; Drift → Reparaturvorschlag; ungenutzt → Archiv).

### 3.3 Format & Interop
agentskills.io-kompatibles `SKILL.md` mit Progressive Disclosure (Index → Volltext → Referenzen) → Skills laufen **auch in Claude Code / Hermes / OpenClaw**. Skills werden versioniert, mit Quelle („abgeleitet aus Sessions A, B, C") und Rollback.

### 3.4 Beispiel
Drei Sessions lang wurde dasselbe Test-Setup per Inline-Skript gebaut → Skill-Entwurf **„torch-test-fixture"** (seed setzen, determinism, device sync, cleanup) mit Pitfalls „ohne Seed flaky; device nicht synchronisiert → Assertions flaky". Shadow-Test besteht → zur Bestätigung vorgelegt.

### 3.5 Abgrenzung & Guardrails
OpenClaw hat mit dem „Skill Workshop" bereits einen governed Post-Run-Workshop (Vorbild für Proposals/Scanner/Grader — übernehmen wir konzeptionell). Neu bei Skaldir Agent: **beobachtungsgetrieben während und über Sessions**, **incident-verknüpft** („Skill verhindert Fehlerklasse X"), Shadow-Validierung vor Promotion, Drift-Überwachung. Guardrails: keine Skill-Flut (dedup, Schwellen), kein Auto-Publish in fremde Systeme, Nutzer-Besitz bleibt unantastbar.

---

## 4. TOOL-SUITES — eigene Werkzeuge

### 4.1 Leiter (immer von unten)
1. **Compose:** MCP-Registry, Community-Server, ClawHub, dsh-Plugins durchsuchen — „gibt es das schon?"
2. **Einzel-Tool bauen** (Spec → Sandbox → Tests → Replay → MCP-Registrierung).
3. **Suite:** zusammenhängendes Domain-Paket als **ein MCP-Server** (fastmcp): gemeinsames Datenmodell, Auth, Versionierung, Telemetrie.

### 4.2 Spec-first-Pipeline (Gate-Reihenfolge)
`Spec (JSON-Schema/OpenAPI)` → `Implementierung (Python, Sandbox)` → `Tests (happy/edge, echte Verhaltenstests)` → `Replay gegen historische Fälle` → `Security-Scan (SSRF, Traversal, Secret-Leak, Injection-Sinks)` → **`Shadow-Rollout`** (nur vorschlagen) → `aktiv (versioniert, mit Changelog + Deprecation-Policy)`.

### 4.3 Suite-Anatomie
Auth nur via Treasury (Handles, nie Secrets, §6), Idempotenz/Retries, Rate-Limits, Quotas, Permissions pro Tool, Audit je Aufruf, Killswitch, LLM-freundliche Beschreibungen + Beispiele. Beispiel-Suiten: `gpu-suite` (status/reserve/profile/cleanup), `billing-suite` (invoice/remind/reconcile), `repo-suite` (snapshot/bench/clean-cache).

### 4.4 Grenzen
Tool-Sprawl (→ Night-Shift-Konsolidierung), Sicherheitslücken (→ Scanner + Review vor Aktivierung, minimale Rechte), Wartelast (→ nur Kern-Tools aktiv). Regel: **wer baut, begründet, warum kein Ökosystem-Tool passt.**

---

## 5. COMPUTER USE — die Hände

### 5.1 Reihenfolge & Zweck
**API > Browser (DOM) > Desktop (Vision).** Zweck: eigene/andere Apps testen, Dienste ohne API bedienen, GUI-E2E-Tests mit Beweisen. „Alle Applications" heißt realistisch: alle *relevanten*, gestufte Tiefe.

### 5.2 Architektur
Browser: Playwright/Patchright via CDP (DOM + Accessibility-Tree; Vision nur für Canvas). Desktop: Screenshot + AT-SPI-A11y-Tree + OCR-Fallback, Aktoren `xdotool`/`ydotool`, **Verifikation nach jeder Aktion** (Assertion/Pixel-Diff), Trajektorien-Aufzeichnung (Screenshots/Video = Beweismaterial für §1.8). Kostenbremse: Downscale, ROI-Crops, Delta-Frames, Text-first.

### 5.3 VM-first (nicht verhandelbar)
Alles in VMs (microsandbox/libvirt) mit Snapshot vor jeder Tour; dedizierte **Testidentitäten** (nie echte Konten); Egress-Filter; „Löschen/Kaufen/Senden" nur mit Approval.

### 5.4 Test-Suite-Generator
App → Touren (Smoke, Kernflows, Kantenfälle) → Assertions aus A11y/DOM → reproduzierbare Fehlerberichte (Video + DOM-Snapshot + Repro-Schritte).

### 5.5 Ehrliche Zahlen & Synergie
OSWorld Original: Mensch ~72 % vs. bestes Modell 12 %; OSWorld 2.0 (long-horizon): best ~31 % binär / ~68 % partiell (08/2026) — und die Fehlerbilder sind **genau die, die die Voice abfängt**. Computer Use bleibt teuer/langsam/flaky → gezielt einsetzen, wo Wert > Kosten. Deshalb: VM, Beweise, Budgets.

---

## 6. TREASURY — der Besitzer übergibt, gesichert

### 6.1 Die Idee
Der Besitzer gibt dem Agenten **API-Keys, Logins und (gestuft) Geld** zum Benutzen — aber der Agent **sieht die Secrets nie** und kann nur, was explizit erlaubt ist. Die Treasury ist kein Passwortzettel, sondern ein **Broker mit Vertrauensgrenze**.

### 6.2 Zone A — API-Keys & Credentials
- **Ablage:** verschlüsselt at rest (age/Vault-Klasse + OS-Keyring), getrennt vom Profil.
- **Nutzung via Broker/Injection:** Der Agent bekommt **Handles** (z. B. `secret://openrouter/main`); der Broker injiziert den Key erst am Ziel (Egress-Proxy-Prinzip — genau wie Hermes' iron-proxy und OpenClaws „protected credentials … without entering model-visible text").
- **Scopes:** pro Handle: welcher Dienst, welche Aktionen, Rate-Limit, Ablaufdatum, max. Nutzungen (`godsvoice grant openrouter --to tool:llm-router --scopes read --daily 500`).
- **Audit:** jeder Zugriff geloggt (wer, wann, wofür, Ergebnis); **Rotation** per Befehl; **Revoke** sofort.
- **Leak-Playbook:** Secrets-Scanner über alle Trajektorien/Logs — wenn ein Secret je im Kontext auftauchte: auto-quarantäne + Rotation + Alarm.
- Übergabe-UI: `godsvoice vault add …` über **sicheren Prompt** (nie in Chat/History), plus „Grant"-Flow aus laufender Session (Agent fragt an, Besitzer erteilt — wie ein Schlüsselübergabe-Dialog).

### 6.3 Zone B — Logins & Web-Sessions
Weblogins liegen im Vault; die Browser-Automation füllt über Injection (Agent tippt keine Passwörter), TOTP aus dem Vault, eigenes persistentes Browserprofil, neue Geräte/Einloggungen → Alert an den Besitzer. Passwortänderungen nur mit Approval. **Was der Agent nie bekommt:** Hauptmail, Recovery-Codes, Banking-Login des Besitzers.

### 6.4 Zone C — Geld (gestuft, optional)
| Stufe | Darf | Technik |
|---|---|---|
| 0 Vorschlag | Käufe vorbereiten; Besitzer bestätigt + zahlt | Browser mit unsichtbarer Zahlungsmethode, Approval |
| 1 Bestätigt | + vorab genehmigte Vendor-Auto-Recharges mit harten Caps | Vendor-Limits / virtuelle Karte, Whitelist |
| 2 Mandatiert | autonom ≤ 20 €/Tx, ≤ 50 €/Woche, Whitelist, nur „Infrastruktur" | AP2-Mandate (VDCs) oder Karte mit Limits + Anomalie-Monitor |
| 3 Größere Summen autonom | **nicht empfohlen** (Haftung) | — |

### 6.5 Invarianten
1. Nie Hauptkonten/Hauptidentität. 2. Secrets nie im Agenten-Kontext (Handles + Injection). 3. Geld nur per Mandat (Betrag/Empfänger/Zweck/Ablauf/Anzahl). 4. Vier-Augen grundsätzlich (Besitzer bestätigt per 2FA). 5. Alles auditiert, Belege archiviert. 6. Killswitch in einem Klick. 7. Kein Unbekanntes, keine Abos ohne Freigabe, kein Krypto ohne getrennte Wallet.

---

## 7. Wie alles zusammenspielt (ein Durchlauf)

**Szenario 1 — CUDA-OOM-Schleife:** Der Agent variiert batch_size erfolglos (Loop-Signal, §1.4). Die Voice fragt den Incident-Store (§2.2) → Treffer aus Session 08/2026 → Hinweis mit Belegen (§1.5) → Agent wendet solution an, Test grün → `confidence` steigt; Night Shift pflegt den Incident (§2.5). Nächste Woche dasselbe Muster beim Teamkollegen: Lösung ist da, bevor der Agent 20 Minuten verbrennt.
**Szenario 2 — Scope-Drift:** Der Agent beginnt „nebenbei" die Data-Loader zu refactoren (nicht im Pakt). Step-Monitor (§1.7) → Whisper: „nicht im Pakt — bewusst?" → Agent fragt den Besitzer (§1.8-konform) statt zu bauen. Kein Slop, keine Überraschungen.
**Szenario 3 — Login + Tool-Bau:** Der Agent braucht für ein neues Tool einen API-Zugang → `godsvoice grant` (§6.2) → Tool-Suite (§4) entsteht, wird in Shadow getestet, dann aktiviert; für den GUI-Test der Weboberfläche übernimmt Computer Use in der VM (§5).

---

## 8. Bau-Reihenfolge (lean)

| Stufe | Inhalt | Fertig, wenn … |
|---|---|---|
| **V0 — Voice-Skelett** | Pakt, Ledger, Step-Monitor (deterministische Regeln), Einspeise-Kanal | eine echte Sitzung läuft mit Pakt/Ledger; Slop-Regeln greifen nachweisbar |
| **V0.5 — Memory-Basis** | Incidents + Notes (Schemas, Retrieval, Gates), Night Shift v0 | „Hatten wir das schon?"-Query liefert belegte Treffer |
| **V1 — Voice voll + Skills** | Checkpoint-Monitor, Korrektur-/Halluzinations-Gates, Skill-Mining-Pipeline | erster shadow-validierter Skill; erste A/B-Zahlen (Voice an/aus/shadow) |
| **V1.5 — Tool-Suites** | Spec-first-Pipeline, erste Suite (MCP) | erste Suite im Einsatz, ≥ 80 % Aufgaben via Compose gelöst |
| **V2 — Computer Use** | Browser-first in VM, Test-Suite-Generator, Beweise | Web-E2E-Tour läuft reproduzierbar mit Video-Beleg |
| **V2.5 — Treasury A/B** | Vault + Broker + Grants; Logins; Approval-Flow | 0 Leaks, 0 unautorisierte Zugriffe; Grant-Dialog läuft |
| **V3 — Treasury C (Geld)** | Stufen 0→1, später AP2 (Stufe 2) | erste bestätigte Zahlung sauber auditiert |

---

## 9. Erfolgsmaße

| Maß | Ziel |
|---|---|
| Hint-Precision (nützliche Einspeisungen) | ≥ 70 % |
| Recurrence-Kill-Rate (gelöste Fehlerklassen wiederholen sich nicht) | ≥ 60 % nach 5 Sessions |
| Evidence-Coverage bei Abnahme | 100 % |
| Slop-Catches pro Session / Scope-Flags | steigend, dann sinkend (Lernen) |
| Credential-Leaks im Kontext | **0** (hart) |
| Unautorisierte Zugriffe/Zahlungen | **0** (hart) |
| Voice-Overhead (Tokens) | ≤ 15 % |

---

## 10. Design-Invarianten (aus „gut vs. schlecht")

**Immer:** Belege statt Meinungen · selten sprechen, nie zweimal dasselbe · fragen statt raten belohnen · Secrets nie im Kontext · VM-first · compose before create · propose-first bei Skills/Tools · Ledger vor Kompression · Budgets überall · der Mensch haftet und entscheidet bei Geld/Recht.
**Niemals:** autonome Echtgeld-Käufe in v1 · Hauptkonten/Hauptmail · Karten-/Bankdaten im Agenten-Kontext · Voice als Dauerquassler · „fertig" ohne Beweis · Tool-/Skill-Flut ohne Gates · Vision-only, wo DOM/A11y reicht · Krypto/Höhere Summen autonom.

---

## 11. Offene Entscheidungen
**Bereits entschieden (11.09.2026):** Basis = **Hermes-Plugin/Daemon**, Python (§0.5) · Startpunkt = V0 als Hermes-Plugin + `mentord`.
Noch offen:
1. Repo-Name/Handles zu „Skaldir Agent" (Marken-Check).
2. Default-Autonomie: `propose` (empfohlen) vs. `auto`.
3. Freigabe für den Start von V0 (Pakt + Ledger + Step-Monitor).

---

*Nicht in diesem Plan (bewusst ausgeklammert):* Basis-Mechanik (Agent-Loop, Event-Bus, TUI, Protokoll) → `DESIGN.md`; Repo-Clone-Liste → `REPOS.md`; menschliche Fähigkeitslücken → `CAPABILITY-GAPS.md`; Quellen/Belege → `SOURCES.md`; vollständige Ausbau-Details (Slop-Regelliste, Hands-Architektur, Treasury-Technik, AP2) → `DESIGN-2-GODS-VOICE.md`.
