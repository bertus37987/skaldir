# STATUS — Skaldir Agent (Meilenstein-Tracker)

Stand: 2026-09-11 · Legende: ✅ fertig & verifiziert · 🚧 in Arbeit · ⏳ geplant

## Release 1.0.0 — ✅ ausgeliefert (12.09.2026)

- [x] **Desktop-App (Fedora, user-lokal):** `godsvoice desktop install|status|start|uninstall` — App-Menü-Eintrag „Skaldir Agent", hicolor-Icons aus `assets/icon.png`, Deployment-Check vor dem Start. **Desktop-Panel** (Unified Package): `desktop/plugin.js` (Sidebar + Vollseite `/gods-voice` + ⌘K) mit read-only Backend `dashboard/plugin_api.py` (`/api/plugins/godsv/`: Pausen, Guard-Checks, Ereignis-Typen, Testgeld-Allowance — kein Vault, keine Writes). Live: installiert, `desktop-file-validate` clean (ohne Hint), `godsvoice desktop start --check-only` → „Deployment OK", Panel deployt (`desktop status` zeigt „Desktop-Panel ✓"), JS-Syntaxcheck via node. UI bleibt logo-frei; ANSI-Logo nur CLI-Banner.
- [x] **CLI fertig:** `godsvoice doctor` (10 Checks: Python, Paket, 13 Prompts, Hermes, Plugin, Entry, Runtime, bwrap/podman/convert) — live exit 0; Version 1.0.0; dazu `eval`, `metrics`, `desktop`, `vault/spend/allowance`, `cu`, `mcp`, `skills`, `tools`, `nightshift`, `init/pact/ledger`, `scan`.
- [x] **Treasury-Re-Audit:** 10 Findings gehärtet (TOTP-Grant-Pflicht, Intent-Mandat für Auto-Approve, Betrags-Validierung NaN/inf/≤0, 0600/0700-Permissions, flock, Scrub-Varianten, redigierter Event-Fallback statt Crash, Env-Isolation-Canary) — 10 neue Audit-Tests, Detail-Tabelle in `docs/SECURITY.md` (2026-09-12).
- [x] **Constraints gehärtet:** Hook-Robustheit (kein Hook wirft je — Constraint-Tests), „off" als Kill-Switch, Adapter fail-safe (Events nie mit Klartext, Redaktions-Fallback), file_edit-Spiegelung + Tool-Result-Normalisierung.
- [x] **Repo/Release:** `CHANGELOG.md` (1.0.0/1.0.1), Version-Bump, 3rd-Party-Notices siehe `THIRD_PARTY_NOTICES.md`; **CI grün** (v1.0.1: Lint + `[dev,mcp]`-Extra + Panel-Load-Smoke mit node in CI).
- [ ] Rest (Kalibrierung, kein Release-Blocker): Pause-Schwellen an Fehlalarm-Zahlen; Skill-Konsumption über mehr Sessions; Vision-Runner / echte Broker nur mit Freigabe.

## Release-Härtung — Completion-Guards · Pause→Research→Redirect · Kontext — ✅ (11.09.)

- [x] **Completion-/Evidence-Guards** (`guard.py`): Claim-Extraktion (tests/done/works/ui/api; Negationen/Fragen nie als Claim) gegen Pact-Acceptance **und** Beleg-Index (Test-/UI-/API-/Ausführungs-Belege nach der letzten Änderung; Schreib-Tools zählen **nie** als Beleg). Kanäle: `pre_verify`-Nudge (einmalig, attempt-gated, bounded) + `transform_llm_output`-Footer (erreicht die finale Antwort auch ohne weiteren Tool-Call). LLM-Zweitmeinung (`completion_check`) nur additiv, fail-open. Widerspruchs-Erkennung (Claim → derselbe Fehler erneut). Shadow-Modus beobachtet nur, steuert nie.
- [x] **Pause → Research → Redirect → Resume** (`pause.py`/`research.py`): Lease mit TTL + Heartbeat + DB-Persistenz; Watchdog-Thread **unabhängig** von der Recherche; Exactly-once-Delivery (bedingter ready→delivered-Übergang); Stale-Verwerfung per base_step; Research mit hartem Zeit-/Token-Budget, redigiertes Voll-Archiv unter `godsv/research/`, in den Loop geht nur der verdichtete Redirect (Problem/Evidenz/Richtung/Vermeiden/Unsicherheit); Block als `pre_tool_call`-Directive **vor** der Ausführung; `voice`/`user`/`safety` strikt getrennt (nur voice wird je auto-freigegeben, kein Pause-Pfad erteilt Berechtigungen).
- [x] **Live-E2E (echtes Hermes, `--yolo -z`):** Session `…183209_ec14e0` — `pause_requested → research_started → research_done → pause_delivered` (recovery 27 s, genau **ein** Block); Transcript enthält das geblockte Tool-Result mit vollem `[voice·pause]`-Redirect; Guard-Kanal: `pre_verify NUDGE` → Agent verifiziert nach → `[voice·guard]`-Footer (Trace + Events auditierbar).
- [x] **Adapter-Lücken live geschlossen:** `file_edit`-Events aus `write_file`/`patch` (Slop/Scope/Dependency laufen jetzt live) + Tool-Result-Normalisierung (exit_code/stdout/error aus dem JSON-String).
- [x] **Monitor-Tiefe:** Tool-Fehlerklassen (auth/rate_limit/timeout/invalid_args/unsupported + ursachengerechte Recovery), Fortschritts-Stall, Revert-Erkennung, Dependency-/Build-Datei-Änderungen.
- [x] **Kontext-Stabilität** (`context.py`): je Entscheidung frischer, budgetierter Kontext; Pact/Constraints priorisiert; jede Zeile mit Quellen-Ref; explizite Kürzungsmarker; nach Neustart rekonstruierbar.
- [x] **Memory/Skill-Integration geschlossen:** Research-Events → atomare Notes (+ Widerspruchs-Links), gelöste Error-Klassen → Incident-Kandidaten, Skill-**Konsumption** (nur `promoted`, einmal/Session, Demoted nie); `nightshift` zeigt alle Vorschläge (dry-run Default).
- [x] **Interventions-Metriken** (`metrics`): Pausen/Deliveries/Expiry/Stale, Research-Dauer + Token-**Schätzung**, Recovery-Zeit, Guard-Checks/Nudges/Footer, Claims, Widersprüche, gehalten/wiederkehrend (12-Step-Fenster, Schätzung).
- [x] **Eval-Harness 6 → 15 Episoden** (inkl. falsche Fertigmeldung, unbelegte Aussage, Widerspruch, Fehlerklassen, Stall, Dependency, Revert, kompletter Pause-Flow, langer Task mit begrenztem Kontext) — **15/15 PASS**, Suite grün.
- [x] Icon (Desktop-App) + ANSI-Logo (CLI-Banner; UI bleibt logo-frei); Treasury-`allowance` zeigt dem Agenten Tier/Caps/Restbudget — **ausschließlich Testgeld**, kein Echtgeld-Pfad im Code.
- [ ] Rest: Pause-Schwellen an echten Fehlalarm-Zahlen nachjustieren · Skill-Konsumption über mehr Sessions beobachten · `hermes godsvoice`-Subcommand

## V0 — Voice-Skelett — ✅ Kern abgeschlossen

- [x] Pact (goal / non-goals / acceptance / budget / scope) — `src/godsv/pact.py` + 7 Tests
- [x] Ledger (Continuity, evidenz-pflichtig, Markdown-Render, Guard 2k Tokens) — `ledger.py` + 6 Tests
- [x] Event-Store (append-only SQLite/WAL, Replay, Payload-Limit) — `events.py` + Tests
- [x] Secret-Gate (Scan + Redaction am Store) — `secretscan.py` + Tests
- [x] Step-Monitor (Loop, Error-Burst, 6 Slop-Regeln, Scope-Drift) — `monitor.py` + Tests
- [x] Voice v0 (Budget 5/Task, Dedup, Severity-Ranking, Templates) — `voice.py` + Tests
- [x] Injection-Format (append-only, sanitized, Advisory-Footer) — `inject.py` + Tests
- [x] Demo-Pipeline deterministisch (`godsvoice demo`: 8 Events → 8 Signale → 4 Hints) — Tests
- [x] System-Prompts v1 (8 Stück) + Registry + Tests — `prompts/`, `docs/PROMPTS.md`
- [x] Security-Baseline + Audit-Log — `docs/SECURITY.md`
- [x] CI-Workflow (ruff + pytest + bandit) — `.github/workflows/ci.yml`
- [x] Repo-Struktur, README, DEV-Doku, LICENSE (Apache-2.0)
- [x] Plugin-Adapter `integrations/hermes/godsv_plugin/` (Hooks → Events, Shadow-Inbox, Tests)
- [x] Deployment nach `~/.hermes/plugins/godsv/` (godsv im Hermes-venv; `plugins doctor` OK, 5 Hooks)
- [x] Live-Smoke-Test (Shadow): echte Session → Events im Store + Hint im Inbox-Log (11.09.)
- [x] Delivery (Whisper) live verifiziert: `transform_tool_result` hängt den Hint additiv ans Tool-Ergebnis; `hint_delivered`-Event im Store (Session `…165030_a4d2db`)
- [x] Thread-Safety-Fix (Hook-Worker-Threads → SQLite `check_same_thread=False` + RLock) + Regressionstests
- [x] GitHub-Publish: privat + 6 Meilenstein-Issues (#1–#6) + Tag `v0.1.0` + Hermes-Fork `bertus37987/hermes-agent`

## V0.5 — Memory-Basis — ✅ Kern abgeschlossen (Notes-Konsolidierung folgt mit Research-Events in V1)

- [x] Incidents: Fingerprint (+ Embedding-Feld + Cosine-Suche) + Root-Cause + Resolution + Evidenz; propose-first (`candidate` → `active`), `archived` nie sichtbar
- [x] Retrieval: Exact-Signatur → FTS5-Textsuche → Similarity; `Evidence.render()` spotlightet Notes (untrusted); `judge_same_problem` implementiert + getestet
- [x] Research-Notes: atomar + Provenienz + Trust + Expiry (`valid_until`) + Widerspruch-Links; `curate_finding` über `research_note` verdrahtet
- [x] LLM-Client (Tiers small/mid/main) + `BudgetTracker` (harte Limits) + `run_prompt` (JSON-Extraktion, Repair-Retry)
- [x] Night-Shift: deterministische Episode-Summary + Incident-Proposals; Dry-Run Default, `--apply` schreibt Kandidaten; CLI `nightshift`/`memory-stats`/`memory-search`
- [x] Plugin live verifiziert: Hints tragen Belege aus dem Memory (Exact-Match, Session `…165731_1fe245`); archived-Lücke im Live-Test gefunden + gefixt
- [ ] Rest (V1): Notes-Konsolidierung aus Research-Events · `same_problem_judge` im Plugin (via `ctx.llm`) · echter Embedding-Provider (API/lokal)

## V1 — Voice v2 + Skill-Mining — ✅ Kern komplett (11.09.)

- [x] Voice v2: `triage_step`/`synthesize_hint` (Prompts v1) + `ctx.llm`-Adapter (`ContextLLM`, host-auditiert, keine Overrides) im Plugin — **asynchron** (Daemon-Thread; Hook blockiert nie: live von 25 s Stall auf sofort); Fallback = Template
- [x] **Triage-Gate** (`GODSV_TRIAGE=1`): not-stuck-Verdikt unterdrückt Loop-Fehlalarme — **live verifiziert** (3 s, conf 0.85, Session `…172854_29c5bf`; Events `hint_triage`/`hint_suppressed`, Inbox markiert); fail-safe (nur explizites Verdikt ≥0.6, sonst bleibt der Hinweis)
- [x] **`same_problem_judge` verdrahtet**: fuzzy Incident-Belege bestätigt/verworfen (`evidence_judged`); Exact-Hits/Notes nie gejudged; fail-open
- [x] Skill-Mining + **Review-Flow**: `skills mine|list|validate|promote|demote|show|export` — Promotion nur nach Re-Verifikation (propose-first), Export schreibt valides SKILL.md; Drift-Demotion; live auf 10 Sessions durchgespielt
- [x] **Eval-Harness**: 6 deterministische Episoden inkl. Stille bei gesunder Arbeit (`godsvoice eval`, in CI via pytest)
- [x] Recurrence-Kill-Rate + Operations-Metriken (`metrics.py`, CLI `metrics`)
- [x] Live verifiziert: Delivery mit Beleg (`…172239_4b76bd`), Triage-Suppression (`…172854_29c5bf`) — **162 Tests, bandit 0**
- [ ] Offen (Richtung V1.5/V2): Skill-Konsumption (Promotion → Injection) · Mining-Vielfalt über mehr Sessions · echter Embedding-Provider · `hermes godsvoice`-Subcommand

## V1.5 — Tool-Suiten — 🚧 Kern steht (11.09.)

- [x] **Tool-Specs + Store** (`tools.py`): propose-first (candidate→scanned→approved→revoked), Provenienz, Tags, Kompositionen; CLI `tools list|show|propose|compose|approve|revoke`
- [x] **Compose before create**: `compose_candidates` (Rangliste überlappender scanned/approved Tools), Kompositions-Validierung + `tool_spec_draft`-Prompt (Required-ID; compose-vs-create explizit)
- [x] **Scan-Gate** (`toolscan.py`): eval/exec/`__import__`/Shell/ctypes blocken; Netzwerk/Sockets/pickle = medium; Secret-Scan integriert; CLI `tools scan`
- [x] **Gate-Runner** (`gates.py`): realer Subprozess offline mit Timeout — scan → import → Tests → Determinismus (2 Läufe); **bwrap-Sandbox** (Netz-NS getrennt, Root read-only) mit Fallback
- [x] **MCP-Export** (`mcpserver.py`, fastmcp): nur approved Python-Tools, Fehler als strukturierte Payloads; `godsvoice mcp serve` — **E2E live verifiziert** (echter stdio-Serverprozess, `shout_tool` → `{"shout": "HALLO"}`)
- [x] **201 Tests** grün · ruff clean · bandit 0
- [ ] Rest (V1.5+/V2): Input-Schemas pro Tool · Kompositions-Runner (Ausführung mehrstufiger Ketten) · Agent-getriebene Proposals (Plugin schlägt Tools während der Arbeit vor)

## V2 — Computer Use — 🚧 Kern steht (11.09.)

- [x] **Isolations-Pflicht im Code:** `CUPolicy.require_isolation` lehnt Runner ohne Isolation ab (Live-Desktop verboten); Runner: dry-run · **sandbox (bwrap)** · **container (rootless podman)** + GUI-Image `localhost/godsv-cu-x11` (Xvfb+xdotool, Netz aus, `--rm`)
- [x] **Kanal-Reihenfolge API > DOM > Vision** erzwungen (`order_attempts`, stabil) mit Fallback über die Versuche
- [x] **Verifikation nach jeder Aktion** erzwungen (Versuch ohne Erwartung = PolicyViolation); Audit-Events `cu_attempt|action|verified|failed|aborted|blocked`; Stop-Bedingungen (`max_actions`, `max_fallbacks`)
- [x] **Prompt** `cu_step_plan` (Required-ID) + `plan_cu_attempts` (LLM-Plan, geordnet, je Versuch Verifikation)
- [x] **Live verifiziert:** bwrap-CLI-Aktion · Container-CLI-Roundtrip · **GUI-Zyklus im Container** (Xvfb 800×600 → Fenster → Maus → per `observe` bestätigt → Executor „verified"); `godsvoice cu check` meldet alle 4 Isolationsstufen verfügbar
- [x] **224 Tests** grün · ruff clean · bandit 0
- [ ] Rest (V2+): Screenshot/vision-Ausführer (VLM) · VM-Image (qemu) für untrusted Apps · Desktop-Modus (später, nur mit explizitem Nutzer-OK)

## V2.5 / V3 — Treasury — 🚧 Kern steht (11.09.)

- [x] **Zone A — Vault + Broker:** Fernet-verschlüsselt at rest (Schlüssel aus Master-Secret via scrypt, nie gespeichert; `cryptography` als Extra); Handles `secret://service/name`; Agent sieht **nur Metadaten** (`list/describe`) — Live-Beweis: 0× Klartext-Secret auf Platte
- [x] **Injection statt Preisgabe:** `Broker.run_with_secret` führt Kommandos in der **bwrap-Sandbox** aus, Secret nur in der Kind-Env; Rückgabe wird **gescrubbt** (`[REDACTED:treasury]`); Rate-Limit pro Handle; Scopes erzwungen
- [x] **Grant-Flow:** Agent fragt an (`request_grant`) → Besitzer erteilt/lehnt ab; Einträge mit `grant:required` ohne aktiven Grant = verweigert (live getestet)
- [x] **Zone B — TOTP:** Seeds im Vault, Broker mintet kurzlebige Codes (**RFC 6238**, gegen Testvektoren verifiziert); Seed verlässt den Vault nie; Browserprofil/Device-Alerts als Betriebs-Doku
- [x] **Zone C — Gestuftes Geld:** Stufe 0 Default = **jede** Ausgabe `pending_human` (ohne Approval keine Ausführung — live bewiesen); Stufe 1 = Whitelist-Mikrokäufe mit Caps; Stufe 2 = Mandat (20 €/Tx, 50 €/Woche); **verifizierbarer Intent** (Betrag/Merchant/Expiry müssen matchen — AP2-Idee); alles als Audit-Events (`spend_*`, `treasury_*`)
- [x] **CLI:** `vault add|list|describe|revoke` (Secret-Eingabe per getpass, nie argv/Chat/History) · `spend request|list|approve|deny|complete`; Prompt `spend_intent` (Required-ID)
- [x] **22 Treasury-Tests** (Vault-Platte, Wrong-Key, Expiry, Injection-Scrub, Grants, Rate-Limit, TOTP-Vektoren, Stufen/Caps/Intents, CLI) 
- [ ] Rest (V3+): echte Broker-Verbindungen (nur mit Owner-Freigabe: dedizierter Account, kein Banking/Main-Mail) · AP2-VDC-Format · Egress-Proxy (versehentliche Ausgabe verhindern) · Plugin-Verdrahtung (Spend-Intents aus laufender Session)
