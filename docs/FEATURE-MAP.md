# Skaldir Agent (godsv) — Vollständige Feature-Landkarte

> **Revision 2 (13.09.2026 abends):** Fix-Etappe + Verifikations-Re-Mapping abgeschlossen — Statusbilanz in Kapitel 10.
> **Stand:** 13.09.2026 · Branch `godsv-supervisor-hardening` · Quelle: **ausschließlich der Code** (`src/godsv/`, `integrations/hermes/godsv_plugin/`, `tools/`, `app-overlay/`).
> Bestehende Dokumentation (`docs/`) wurde bewusst NICHT als Quelle verwendet; alle Angaben sind am Code verifiziert und mit `Datei:Zeile` belegt.
> **Methode:** 6 parallele Audit-Agenten (je ein Bereich, Code-only) + eigener Audit des Fokus-Teils „Voice ↔ Main-Agent-Kommunikation“. Brisante Befunde (Event-Kind-Brüche, nie geschriebener `verified`-Status, tote Produktionspfade) wurden stichprobenartig nachverifiziert (grep-belegt).
> **Format je Feature:** Name — Beschreibung — `Datei:Zeile` — Eintrittspunkt — Grenzen.

> **Runde 14.09.2026 (Live-Control-Plane):** elf neue Dokumente, Belege je Block in
> `IMPLEMENTATION-MATRIX.md` §6a–§6f. Offene Punkte: §40 Stufen 6–9, Schema-Platzhalter in `BENCHMARK-RUN-SCHEMA.json`, Dedupe-/Retention-Durchsetzung.

## 0. Architektur-Überblick in 15 Zeilen

Skaldir Agent ist ein **Supervisor über den Main Agent** auf Hermes-Basis: Der Hermes-Host ruft pro Session/Turn/Tool-Call Plugin-Hooks auf; das godsv-Plugin (`integrations/hermes/godsv_plugin/`) beobachtet, bewertet (Watcher → Signale), entscheidet (Pause-Controller mit Lease + Recherche), steuert (Hints, Block/Redirect, Abschluss-Guard) und protokolliert (Event-Store `events.db`). Die **Voice** ist dabei die Gesamtheit aus deterministischem Watcher (`monitor.py`), Pause-Auslöser (`pause.py` + `voice.py`-Kostenmodell), Recherche-/Investigator-Läufen (`research.py` + `context.py`), optionalem LLM-Judge (`guard.py`) und Hints (`inject.py`). Der **Completion-Stack** (`completion.py`) hält den Goal-Vertrag (DoD, Revisionen), führt Gates aus und entscheidet allein `VERIFIED_COMPLETE`/`INCOMPLETE`. Die **Capability-Schicht** (`capability.py`, `capability_mining.py`, `toolbuilder.py`, `mcpserver.py`) lernt Tools (Builder-Pipeline mit Gates + Sandbox), verwaltet Projekt-/Global-Scopes und exponiert geprüfte Tools (MCP, scope-gefiltert). **Memory** (Incidents/Notes/Skills, `memory.db`) und **Research** (Archiv) erden Entscheidungen; **Treasury** (`treasury.py`) ist die einzige Secret-Autorität (Vault + Grants + Staging-Spends). Die **Desktop-App** ist eine gebrandete Hermes-Instanz mit eingebackenem Cockpit-Panel (6 Views) und REST-API; die **CLI** (`godsvoice`) spiegelt alle Subsysteme für headless Betrieb. Nichts davon veröffentlicht automatisch; Sicherheitsregeln (Sandbox-fail-closed, Grants, Redaction) sind hart.

**Wichtigste durchgehende Fakten:**
- Default-Modus ist **`shadow`** (nichts wirkt steuernd); Steuerung erfordert `GODSV_MODE=whisper` (die GV-App setzt das selbst).
- Alle **steuernden** Ausgänge (Hint, Block, Continue, Footer) haben harte `mode() != "whisper"`-Frühausgänge im Plugin.
- Die Gesamtheit der Host-Schnittstelle sind **10 Hooks** (unten, Kapitel 1.1).

---

## 1. FOKUS: Voice ↔ Main-Agent-Kommunikation

*Selbst kartiert aus `integrations/hermes/godsv_plugin/__init__.py` (1767 Zeilen) + `pause.py`/`inject.py`/`guard.py` (Stand Commit `44f16e3`).*

### 1.1 Die 10 Host-Hooks (die komplette Schnittstelle)

Registriert in `integrations/hermes/godsv_plugin/__init__.py:1753-1767` (`register(ctx)`):

| Hook | Richtung | Wirkung | Zeile |
|---|---|---|---|
| `pre_llm_call` | Voice → Agent | 1×/User-Turn: Turn-Reset + **Contract-Kontextblock** an die User-Message (Ziel, offene Blocker, Honest-Stop-Konvention) | `:1655-1672` |
| `pre_tool_call` | Voice → Agent | **Block** eines Tool-Calls, wenn ein fertiger Redirect bereitliegt (genau EINMAL) | `:1587-1599` |
| `transform_tool_result` | Voice → Agent | **Whisper**: hängt wartende Hints an ein Tool-Ergebnis (append-only, cache-safe) | `:1718-1750` |
| `pre_verify` | Voice → Agent | **Abschluss-Guard**: unbelegter Abschluss → `{'action':'continue', message:<Redirect>}` (Agent macht weiter, ohne Nutzer) | `:1602-1618` |
| `transform_llm_output` | Voice → Agent | Final-Antwort-Anreicherung: Guard-Footer + `::godsv-live{…}`-Supervisor-Block | `:1621-1641` |
| `on_session_start` | Host → Voice | Session anlegen, Testing-Tools registrieren | `:1562-1577` |
| `post_tool_call` | Host → Voice | Beobachtung: `tool_call`/`tool_result`-Events, Datei-Edit-Spiegel, Monitor/Pause/Voice-Pipeline | `:1675-1706` |
| `post_llm_call` | Host → Voice | `thought`-Event + Claim-Extraktion aus der Antwort | `:1709-1715` |
| `on_interim_message` | Host → Voice | Zwischenmeldungen (vor Finalantwort) → Claims werden früher sichtbar | `:1644-1652` |
| `on_session_end` | Host → Voice | Hints flushen, Pause-Release, Watchdog-Stop | `:1580-1584` |

Kernschleife: `observe()` (`:1201-1241`) — Event → `monitor.ingest()` → Plugin-Signale (`context_drift`, `progress_stall`, `capability_gap`) → `_maybe_pause()` → Evidenz sammeln → `voice.consider()` → Hints (Queue oder Enrichment-Thread).

### 1.2 Die sechs Kommunikationskanäle (verifiziert)

**Kanal 1 — Contract-Kontext (`pre_llm_call` → `contract_context`, `:753-838`).** Einmal pro User-Turn hängt der Host den Rückgabewert an die User-Message. Der Main Agent sieht damit bei JEDEM Turn: das Live-Ziel, den Vertragsstand (Revision, Kriterien-/Blocker-Zähler), offene Requirements und die Honest-Stop-Konvention. Das ist der primäre „der Agent weiß vom Vertrag“-Kanal — kein separates Prompting des Agents nötig.

**Kanal 2 — Whisper-Hints an Tool-Ergebnisse (`transform_tool_result`, `:1718-1750`).** `voice.consider()` baut höchstens einen Hint pro Step (`voice.py:184-205`, Budget 5/Session, Mindestabstand 2 Steps) und legt ihn in `session.pending`. Beim nächsten Tool-Call hängt der Hook die wartenden Hints (max 5) als Textblock an das Tool-Ergebnis an (`take_pending` → `mark_delivered`). Host-Vertrag: erster String-Rückgabewert ersetzt das Ergebnis; `None` lässt es unberührt. **Reihenfolge-Fakt (im Code dokumentiert): der Transform feuert VOR `post_tool_call` desselben Calls — ein Hint von Call N reitet also auf Call N+1.**

**Kanal 3 — Block/Redirect (`pre_tool_call` → `before_tool_call`, `:621-633`).** Liegt ein fertiges Pause-Deliverable vor (`pause.deliverable()`, `pause.py:612-653`), liefert der Hook `{'action':'block','message':<redirect_md>}` — der Redirect ersetzt genau EINEN Tool-Call; danach läuft der Agent auf der neuen Richtung weiter. Stale-Ergebnisse (>15 Steps alt) werden als `stale` verworfen statt geliefert; TTL-Ablauf markiert `expired`; bei `mark_delivered`-Fehlschlag wird nicht geblockt.

**Kanal 4 — Abschluss-Guard + Auto-Continue (`pre_verify` → `guard_pre_verify`, `:1083-1151`).** Behauptet der Agent Abschluss, fährt der Strict-Pfad `_strict_completion()` (`:945-1011`): Request + Evidenz + `run_requirement_checks` → `GateRunner.verify()`. Nicht-terminal ⇒ `{'action':'continue','message':redirect_md()}` — **derselbe Main-Agent-Run arbeitet ohne Nutzer-Continue weiter** (genau der §4-Mechanismus). Nudge-Cap `_STRICT_NUDGE_CAP = 40` je Session (Deadlock-Schutz). Checker-Crash ⇒ `_strict_crashed` + **fail-closed** (kein stilles Grün). Nur in `whisper` steuernd; in `shadow` läuft alles nur als Event.

**Kanal 5 — Final-Antwort-Anreicherung (`transform_llm_output`, `:1153-1189` + `live_block_suffix`).** Hängt bei unbelegten Claims einen `[voice·guard]`-Footer an und, wenn die Stage wechselte oder Voice-Aktivität war, den kompakten `::godsv-live{stage=…,sid=…}`-Block — die Chat-Karte, die das Cockpit im Transkript spiegelt (Live-Block der GV-App rendert ihn).

**Kanal 6 — Beobachtung/Telemetrie (die Host→Voice-Hooks).** `post_tool_call`/`post_llm_call`/`on_interim_message` speisen `observe()`; Datei-Edits werden aus write/patch/Redirect-Aufrufen gespiegelt (`_maybe_file_edit`, `:318-351`, `/dev/*` gefiltert). Alles landet als Events in `events.db` — Grundlage für Panel, Metriken (`voice_quality`) und den Guard-Evidence-Index.

### 1.3 Modi und Pause-Lifecycle

- **Modi** (`mode()`, `:112-115`): `shadow` (Default) = alles nur Inbox/Events; `whisper` = steuernd; `off` = Adapter no-op. Die gebrandete App setzt `GODSV_MODE=whisper` (`desktop.py:113`).
- **Pause-Lease** (`pause.py:107-302`, SQLite `pauses.db`): `requested → researching → ready → delivered` (+ `expired|abandoned|stale`); nur `kind=voice` wird produktiv erzeugt.
- **Trigger-Schwellen** (`pause.py:92-104`): loop 2 · error_burst 2 · scope_drift 3 (klassenweise gezählt!) · known_incident_repeat 1 · strategy_repeat 2 · hallucinated_api 2 · capability_gap 1; Strictness-Delta +1/0/−1, Kosten-Gate `assess_intervention` (`voice.py:80-139`).
- **No-Intervention ist ein zulässiger Ausgang**: Research kann `no_intervention_needed` setzen → Lease `abandoned`, Agent läuft ungestört weiter (`pause.py:529-555`). *(Bruch: das dabei emittierte Event `pause_no_action` fehlt in `events.KINDS` — live nicht persistiert, aber der Ablauf selbst funktioniert.)*
- **Novel-Strategy-Eskalation**: ab der 2. Pause derselben Trigger-Klasse wird dem Problemtext „REPEATED INTERVENTION #N … NOVEL strategy" angehängt (`pause.py:428-448`).

### 1.4 Was real wirkt vs. was nicht (Kurzfassung)

| Wirkt live (whisper) | Existiert, aber ohne Produktionswirkung |
|---|---|
| Contract an jede User-Message | `main_agent_steering`-Prompt (kein Aufrufer) |
| Hints an Tool-Ergebnisse | `InjectionQueue.drain()` (Delivery nutzt `session.pending`) |
| Block + Redirect (einmalig) | `guard.find_contradiction()` (nur Eval; Plugin prüft inline) |
| Abschluss-Guard + Auto-Continue | Watcher-`ContextBuilder`-Profil (nur Investigator live) |
| Guard-Footer + Live-Block in der Antwort | `_pact_excerpt()` liefert konstant „(kein Pakt geladen …)" |

---

## 2. Die Voice an sich + ihre Subagenten (Voice-Infrastruktur)

### Voice-Infrastruktur (godsv) — Feature-Landkarte aus dem ECHTEN Code

**Stand**: Commit `44f16e3`, Repo `/home/willy/projects/gods-voice`. Quelle ist ausschließlich der gelesene Code; `docs/` wurde nicht als Beleg verwendet. Alle Referenzen sind `Datei:Zeile` dieses Stands.

### 0. Vorab-Befund: Die „Trigger-Policy OBSERVE/WHISPER/REDIRECT/PAUSE_AND_INVESTIGATE/BLOCK_ACTION" existiert im Code NICHT

`grep -rn "OBSERVE|WHISPER|PAUSE_AND_INVESTIGATE|BLOCK_ACTION"` über `src/`, `integrations/`, `tools/`, `app-overlay/` liefert **keinen einzigen Treffer** als Policy/Enum/Konstante. Nur zwei Zufallstreffer: `WHISPER` als UI-Label (`integrations/hermes/godsv_plugin/desktop/plugin.js:1659`, „shadow mode · observe only" / „whisper mode · steering") und `_OBSERVE_LOCK` (`integrations/hermes/godsv_plugin/__init__.py:97`). Was real existiert, sind **drei Delivery-Modi** plus eine **Eskalationsleiter**; die Tasksprache mappt darauf:

| Tasksprache | Realer Code-Mechanismus | Wo |
|---|---|---|
| OBSERVE | `Monitor.ingest()` → nur `Signal`-Objekte + Events/Inbox, keine Agent-Wirkung; `shadow`-Modus ist strikt passiv | `monitor.py:276`; `plugin/__init__.py:89`,`112-115` |
| WHISPER | Hint wird per `transform_tool_result` an ein Tool-Ergebnis angehängt (append-only, cache-safe) | `plugin/__init__.py:1718-1750` |
| REDIRECT | `ResearchResult.redirect_md()` als Block-Text (`[voice·pause] …`) | `research.py:83-104` → `plugin/__init__.py:628` |
| PAUSE_AND_INVESTIGATE | `VoicePause.consider()` → Lease in SQLite → Recherche-/ToolBuild-Thread | `pause.py:366-498`,`500-609` |
| BLOCK_ACTION | `pre_tool_call` liefert `{"action":"block","message":redirect}` — genau einmal | `plugin/__init__.py:621-633`; `pause.py:262-266` |

Delivery-Modi (`GODSV_MODE`, pro Aufruf gelesen, Default `shadow`): `shadow` = Hints nur in `inbox.jsonl` + Events, nie Steuerung; `whisper` = Hints/Block/Continue wirken; `off` = Adapter no-op (`plugin/__init__.py:88-89`, `112-115`). Alle steuernden Ausgänge haben harte `mode() != "whisper"`-Frühausgänge (`plugin/__init__.py:1114`, `1138`, `1157`).

---

### 1. `src/godsv/pause.py` — PauseController, Strictness, Thresholds, Trigger-Policy

**PauseStore (SQLite-Lease-Speicher)** — thread-sicherer Lease-Store (`pauses`-Tabelle: id, kind, session_id, base_step, trigger_kind/fp, problem, status, created/heartbeat/expires, redirect, research_id, delivered_at, released_reason) — `pause.py:107-302` — Eintrittspunkt: Konstruktor mit `pauses.db` (`plugin/__init__.py:274`); alle Controller-Zugriffe — Grenzen: Lease-Kinds `voice|user|safety` (`pause.py:29-32`), aber produktiv wird **nur `voice`** erzeugt (`create` ohne kind → Default); `user`/`safety` nur im Test (`tests/test_pause.py:99`). `problem` wird auf 500 Zeichen gekürzt (`pause.py:163`).

**Status-Modell & Terminalzustände** — `requested → researching → ready → delivered`, daneben `expired|abandoned|stale` — `pause.py:34-41` — Eintrittspunkt: `set_status()` (`pause.py:231-253`) — Grenzen: Übergänge sind konditional nur über `from_statuses`; terminale Leases tauchen in `current()` nicht mehr auf (`pause.py:221-229`).

**Trigger-Policy (real)** — `VoicePause.consider()` entscheidet, ob eine Pause beantragt wird: Schritt 0 aus, Signal-Fingerprints zählen, keine zweite offene Pause, Stundenfenster (max 4), Contradiction-Sonderpfad, Threshold-Vergleich, Cooldown, Kosten-Gate, dann Lease + Recherche — `pause.py:366-498` — Eintrittspunkt: `GodsvSession._maybe_pause()` (`plugin/__init__.py:519-555`), aufgerufen aus `observe()` (`plugin/__init__.py:1214`) — Grenzen: gesamte Methode läuft unter `RLock`; `_counts` ist **in-memory pro VoicePause-Instanz** (kein Persistenzbezug); Fehler im Feed werden im Plugin nur getract (`plugin/__init__.py:554-555`).

**TRIGGER_RULES (Schwellen)** — `loop:2`, `error_burst:2`, `scope_drift:3`, `known_incident_repeat:1`, `strategy_repeat:2`, `hallucinated_api:2`, `capability_gap:1` — `pause.py:92-104` — Zählung: `kind:fingerprint` über `note_signals()` (`pause.py:359-364`), Vergleich `count >= max(1, base + strictness_delta)` (`pause.py:399-424`) — Grenzen: `scope_drift` wird bewusst **klassenweise** gezählt (Summe über alle `scope_drift:*`-Keys, `pause.py:404-412`), weil der Fingerprint pro Pfad ist; alle anderen Signale pro Fingerprint. Nach Auslösen werden **alle** mitgegebenen Signalzähler genullt (`pause.py:472-474`), nicht nur der auslösende.

**Contradiction-Trigger** — direkter Kandidat (`severity=3`, `occurrences=2`), umgeht die Threshold-Tabelle, aber **nicht** Cooldown/Kosten/Rate-Limits — `pause.py:387-394` — Eintrittspunkt: Plugin-Erkennung „Claim, dann derselbe Fehler innerhalb 12 Steps" (`plugin/__init__.py:524-548`, `claim_lookback=12`) — Grenzen: Vergleich nur Fehler-Fingerprint gegen zuvor gesehene `_error_fps` der Session.

**Strictness-Level** — `relaxed|standard|strict` in `<state_dir>/settings.yaml`; liest live, Default `standard`; Schwellen-Delta `+1/0/-1` (Floor 1) und Kostenfaktor `0.6/1.0/1.7` — Datei-Logik `pause.py:51-89`, Anwendung `pause.py:396-398` (Thresholds) und `pause.py:456-458` + `voice.py:98`,`116` (Kosten) — Eintrittspunkt: Datei wird vom Panel/CLI geschrieben (`integrations/hermes/godsv_plugin/dashboard/plugin_api.py:326-332`,`1615-1618`) — Grenzen: `save_strictness` validiert hart (`ValueError`, `pause.py:76-77`); kaputte YAML fällt still auf `standard` (`pause.py:68-69`). Strictness ist **global** (ein `settings.yaml` im Session-State-Dir), nicht pro Session.

**Kosten-Gate (`assess_intervention`)** — deterministischer Sanity-Check vor jeder Pause: Fortsetzungskosten `2200 Token/Step × Severity-Faktor (1→0.6, 2→1.0, 3→2.2) × min(occurrences,6) × Strictness-Faktor` gegen `EST_PAUSE_COST=3500`; `intervene = continuation > 3500` — `voice.py:80-139` — Eintrittspunkt: `pause.py:452-471`; Ergebnis wird als Event `voice_intervention_assessment` (measured vs. estimated getrennt) emittiert — Grenzen: Werte sind explizit **Schätzungen** (`voice.py:79-81`); `steps_in_window` wird nie befüllt (Default 0, `voice.py:122`); bei Severity 3/occurrences 2 passiert jeder Contradiction das Gate auch in `relaxed` (2200×2.2×2×0.6 = 5808 > 3500).

**Novel-Strategy-Eskalation** — zählt dauerhaft aus dem Store, wie oft dieselbe Trigger-Klasse schon pausiert wurde; ab der 2. Wiederholung wird dem Problemtext ein „REPEATED INTERVENTION #N … NOVEL strategy"-Block angehängt — `pause.py:428-448` — Eintrittspunkt: `consider()` nach Threshold-Treffer — Grenzen: Zählung via `history_for_session()` (`pause.py:198-210`, durabel über Turns) und try/except → 0 bei Store-Fehler (`pause.py:432-439`).

**Rate-Limits** — Cooldown pro Fingerprint (`cooldown_seconds=60.0` Default) und `max_per_hour=4` — `pause.py:338-339`, `383-385`, `449-451` — Eintrittspunkt: `consider()` — Grenzen: `_pauses`-Fenster ist in-memory; Plugin setzt beide Werte nicht (nur `ttl`) → es gelten die Defaults (`plugin/__init__.py:273-293`).

**Recherche-Start & Heartbeat** — Lease → `researching`; Runner läuft in Daemon-Thread, paralleler Heartbeat-Thread alle 2 s — `pause.py:500-609` — Eintrittspunkt: `consider()` → `_start_research()` (`pause.py:497`) — Grenzen: ohne Runner → sofort `abandoned` mit Grund „kein Research-Runner" (`pause.py:503-507`); Ergebnis `status=="ok"` ohne `intervention_needed` → `abandoned` mit No-Intervention-Grund (§10-Pfad, `pause.py:529-555`); Fehlschlag/Exception → `abandoned` + `research_failed` (`pause.py:580-603`); Recherche-Ende wird auf `ready` gesetzt und der Redirect als `redirect`-Feld persistiert (`pause.py:556-563`).

**Genau-einmal-Zustellung (`deliverable`)** — wartet bis `GODSV_PAUSE_WAIT` (Default 6 s, hart auf `MAX_WAIT=20` geklemmt) auf `ready`, verwirft Ergebnisse jenseits des `stale_window` (15 Steps) als `stale`, sonst konditionaler `ready→delivered`-Übergang — `pause.py:612-653` — Eintrittspunkt: `before_tool_call()` (`plugin/__init__.py:621-633`) via `pre_tool_call`-Hook (`plugin/__init__.py:1587-1599`) — Grenzen: liefert `None` bei TTL-Ablauf (markiert `expired`), terminalem Lease oder zu spätem Ergebnis; bei `mark_delivered`-Fehlschlag kein Block.

**Release & Watchdog** — `release(reason)` schließt den offenen Lease normal; Watchdog-Thread (Intervall 2,5 s) expiriert TTL-Leases und abandoned Leases ohne Heartbeat (>30 s) — `pause.py:655-665`, `668-691` — Eintrittspunkt: `on_session_end` → `session.end()` (`plugin/__init__.py:309-316`,`1580-1584`); Watchdog startet mit der ersten Recherche (`pause.py:609`) — Grenzen: Watchdog betrifft **nur** `kind=voice` (`expire_stale`/`abandon_dead_research`, `pause.py:268-298`); `stop()` beendet nur die Loop-Bedingung, keine Thread-Joins.

---

### 2. `src/godsv/monitor.py` — Watcher (deterministische Step-Ebene, „cheap tier")

**Signal-Datentyp & Severities** — `Signal(kind, severity, fingerprint, message, refs)`, `SEV_INFO=1/WARN=2/HIGH=3` — `monitor.py:20-31` — Eintrittspunkt: `Monitor.ingest()` gibt `list[Signal]` zurück — Grenzen: reine Datenklasse (frozen), keine Priorisierung hier.

**Monitor-Treiber** — verarbeitet `tool_call`, `tool_result`, `file_edit`; alles andere nur über `_stall_check` — `monitor.py:276-288` — Eintrittspunkt: `GodsvSession.observe()` (`plugin/__init__.py:1207`) — Grenzen: nur diese drei Eventarten erzeugen Signalarten; `known_error_fingerprints` kommt aus `IncidentStore.signatures()` (`plugin/__init__.py:242`).

**Signale & Schwellen (15 Arten, Defaults)** — `Monitor.__init__`: `loop_repeat=3`, `loop_window=8`, `error_repeat=2`, `error_window=6`, `stall_after=12`, `churn_lines=400` (`monitor.py:245-255`):
- **loop** — gleiche Tool-Signatur ≥3× im 8-Step-Fenster (`monitor.py:233-235`,`332-344`); Fingerprint = SHA-256(name+kanonisierte args)[:12].
- **revert** — `git checkout|revert|reset|stash` in den Args (`monitor.py:181`,`346-358`).
- **error_without_details** — Fehlertext ist nur ein Exit-Status (`is_coarse_error_text`, `monitor.py:216-230`,`370-384`): bewusst **kein** Repeat-/Incident-Match.
- **strategy_repeat** — gleiche Fehlersignatur + gleiche vorangehende Tool-Signatur (Step-Abstand ≤2) ≥2× (`monitor.py:386-415`).
- **error_recurrence_after_progress** — gleiche Signatur ≥2× im Fenster, aber zwischenzeitlich echter Fortschritt (`monitor.py:425-443`).
- **known_incident_repeat** — gleiche Signatur ≥2× ohne Fortschritt und Fingerprint ist bekannte Incident-Signatur (`monitor.py:444-458`).
- **error_burst** — gleiche Signatur ≥2× ohne Fortschritt, unbekannt (`monitor.py:459-471`).
- **error_cluster** — ≥3 verschiedene Signaturen im Fenster, einmalig pro Clustergröße (`monitor.py:472-490`; Kommentar: „kein Loop-Beweis", keine Pause-Regel).
- **tool_error_\<klasse\>** — deterministische Fehlerklassifikation (`classify_tool_error`, 11 Klassen: auth, rate_limit, timeout, invalid_args, unsupported, permissions, provider_outage, schema_mismatch, mcp_unavailable, ui_failure, api_changed, `monitor.py:49-114`,`171-178`) inkl. Recovery-Satz aus `ERROR_RECOVERY` (`monitor.py:137-158`,`491-503`).
- **hallucinated_api** — API-förmige Fehler (attr/import/name/signature, `monitor.py:118-135`) ab der 2. gleichen Klasse (`monitor.py:504-522`).
- **stall** — ≥12 Steps seit letztem Fortschritt, einmalig (`monitor.py:299-317`).
- **diff_churn** — ≥400 hinzugefügte Zeilen seit letztem erfolgreichen Lauf, reset nur bei Erfolg (`monitor.py:294-297`,`527-547`).
- **dependency_change** — Edit an requirements/pyproject/package.json/Cargo.toml/Locks (`monitor.py:182-184`,`548-560`).
- **slop_\<regel\>** — 6 Regeln: `todo_placeholder`(WARN), `stub`/`NotImplementedError`(HIGH), `bare_except`(HIGH), `except_pass`(HIGH), `assert_true`(HIGH), `tbd_comment`(INFO) auf dem added-Text (`monitor.py:34-47`,`187-193`,`561-570`).
- **scope_drift** — Datei außerhalb `pact.scope_paths` (fnmatch) (`monitor.py:571-585`).
— Eintrittspunkt jeweils: `ingest()` (Plugin ruft es in `observe()`); `_maybe_file_edit()` spiegelt write/patch **und Terminal-Redirections** in `file_edit`-Events (`plugin/__init__.py:318-351`) — Grenzen: `/dev/*`-Ziele werden gefiltert (`plugin/__init__.py:334-339`); Slop/Scope/Dependency feuern nur, weil das Plugin diese Events erzeugt; `_errors`/`_tool_calls` sind auf 200 Einträge begrenzt (deque, `monitor.py:264`,`268`).

**Zusatzsignale des Plugins (nicht in monitor.py)** — `context_drift` (≥3 Edits in Folge ohne Goal-Bezug, persistenter Zähler in `<contract>.drift.json`) `plugin/__init__.py:382-447`; `progress_stall` (≥40 Tool-Events ohne Contract-Update) `plugin/__init__.py:448-471`; `capability_gap` (frisches `godsvoice tools request` erkannt) `plugin/__init__.py:475-517` — Eintrittspunkt: `observe()` hängt sie an die Monitor-Signale (`plugin/__init__.py:1208-1213`) — Grenzen: `context_drift`/`progress_stall` haben **keine** TRIGGER_RULES → nie Pause-Auslöser, nur Hint-Kandidaten.

---

### 3. `src/godsv/guard.py` — Verdikte, Evidence-Index, Abschluss-Guard

**Claim-/Assumption-Extraktion** — 6 Claim-Muster (tests ×2, ui, api, done, works) minus Negationsmarker, satzweise, max. 20 Claims/400 Zeichen pro Satz; Assumptions = Versions-/Env-Aussagen — `guard.py:30-95`,`98-119`,`122-133` — Eintrittspunkt: `record_claims()` im Plugin (interim + Turn-Ende, `plugin/__init__.py:557-577`,`1652`,`1715`), Claims landen als `claim`-Event — Grenzen: rein deterministisch; Claim nur, wenn **kein** Negativmarker im selben Satz (`guard.py:71-75`,`111-112`).

**EvidenceIndex** — indiziert pro Session: letzter Edit-Step, erfolgreiche Test-/Visual-/API-/sonstige Exec-Steps, Versionschecks, Fehler-Steps — `guard.py:136-205` — Eintrittspunkt: `build_evidence_index()` im Plugin (`plugin/__init__.py:688`) — Grenzen: nur **Ausführungs-Tools** zählen als Run-Evidenz (`_EXEC_TOOLS`, `guard.py:90-93`); `/dev/*`-Pseudo-Edits verschieben den Freshness-Checkpoint nicht (`guard.py:173-179`); Kategorisierung hängt an Textmustern in den Args (`TEST_HINT`/`VISUAL_HINT`/`API_HINT`, `guard.py:82-86`).

**Verdikt (`evaluate_completion`)** — Claims vs. Evidenz nach dem letzten Edit (Checkpoint): `tests` verlangt erfolgreichen Testlauf danach, `done`/`works` eine ausgeführte Prüfung, `ui` einen Visual-Beleg, `api` einen erfolgreichen API-Call; Zustand `ok` / `needs_verification` / `unsupported` (unsupported, sobald eine `done`/`tests`-Lücke ohne Kriteriums-ID vorliegt) — `guard.py:216-226`,`266-414` — Eintrittspunkt: `GodsvSession._completion_verdict()` (`plugin/__init__.py:680-730`, mit Cache + Event `guard_check`) — Grenzen: ohne Claim keine Findings; Acceptance-Kriterien werden nur zusätzlich geprüft, wenn bereits eine `done`-Lücke existiert (`guard.py:375-402`, „precision over recall"); UI-Prüfung nutzt `checkpoint-1` (`guard.py:364`).

**Verdikt-Text** — kompakter Ein-Zeiler („Unbacked completion claim: …") vs. volle Liste (max. 4 Findings) — `guard.py:228-247` — Eintrittspunkt: `guard_pre_verify` (Nudge) und `guard_final` (Footer) — Grenzen: Texte sind **englisch**, obwohl Monitor/Pause deutsch sind.

**LLM-Judge (fail-open)** — optionale Zweitmeinung über Prompt `completion_check`, nur zusätzliche Findings mit `confidence ≥ 0.6`, max. 5 — `guard.py:417-460` — Eintrittspunkt: `_completion_verdict(..., allow_judge=True)` nur auf dem Nudge-Pfad (`plugin/__init__.py:704-716`) — Grenzen: jeder Fehler → leere Liste (`guard.py:426-435`); Digest-Basis ist `evidence_digest()` (`guard.py:463-477`, letzte 40 Events).

**Contradiction-Finder** — Claim (done/tests/works) → derselbe Fehler-Fingerprint erneut innerhalb `claim_lookback=12` Steps ⇒ `{claim, fingerprint, claim_step, error_step}` — `guard.py:480-518` — Eintrittspunkt: **nur** `evalharness.py:324` und Tests; das Plugin nutzt eine eigene Inline-Variante (`plugin/__init__.py:524-548`) — Grenzen: Produktionscode nutzt diese Funktion nicht (nur Episoden-Check/Eval).

**Abschluss-Guard (Wiring)** — `pre_verify`-Hook → `GodsvSession.guard_pre_verify()`: (1) **Strict-Pfad** `_strict_completion()` → `GateRunner.verify()` (completion.py) mit DoD + Runner-Evidenz; nicht-terminal und `mode()=="whisper"` ⇒ `{"action":"continue","message":result.redirect_md()}`, gedeckelt durch `_STRICT_NUDGE_CAP=40`; (2) **Fallback**: Legacy-Claim-Guard (ein Nudge pro Turn, `attempt>0` → None) — `plugin/__init__.py:1083-1151`,`945-1011`,`1602-1618` — Grenzen: in `shadow` nie steuernd (`1114`,`1138`); Checker-Crash ⇒ `_strict_crashed` + fail-closed (`1098-1103`); BLOCKED-Marker (`BLOCKED_USER|BLOCKED_EXTERNAL|FAILED_UNRECOVERABLE`) beenden nur mit Claim bzw. bei FAILED immer (`plugin/__init__.py:736-743`,`963-970`).

**Abschluss-Footer** — `transform_llm_output` hängt `[voice·guard] …` an die finale Antwort, wenn Claims unbelegt blieben (compact message, strict-INCOMPLETE oder „checker FAILED — fail-closed") — `plugin/__init__.py:1153-1189`,`1621-1641` — Grenzen: nur `whisper`; sonst nur Event `guard_footer`.

---

### 4. `src/godsv/inject.py` — InjectionQueue, Hinweis-Format, Wortlimits

**Sanitisierung** — entfernt Unicode-Kategorie `Cf` (Zero-Width/Bidi) und `Cc` außer `\n\t`, kollabiert Leerzeilen, kappt auf `MAX_HINT_WORDS = 220` Wörter (Rest „[…]") — `inject.py:15-16`,`19-34` — Eintrittspunkt: `format_hint()` und direkt — Grenzen: Evidence-Items werden separat mit 60 Wörtern gekappt (`inject.py:46`); keine Längenbegrenzung pro Zeile.

**Hinweis-Format** — Header `[voice id=… sig=<8 Zeichen> typ=<kind> conf=<0.00> pos=<n>/<total>]`, Body, optional „Belege:"-Liste, immer `[Hinweis, keine Anweisung — bitte selbst verifizieren.]` — `inject.py:15`,`37-48` — Eintrittspunkt: `InjectionQueue.push()` in `observe()`/`_maybe_skill_hint()`/`_promote_held()` — Grenzen: `id` ist im Voice-Pfad der laufende Zähler (`voice.py:196`), damit nicht global eindeutig.

**InjectionQueue** — Append-only-Puffer mit `push()`/`drain()`/`pending_count()` — `inject.py:51-67` — Eintrittspunkt: `push()` aus dem Plugin (`plugin/__init__.py:661`,`1230`,`1451`) — Grenzen: **`drain()` und `pending_count()` haben im Produktivcode keinen Aufrufer** (nur `tests/test_voice_inject.py:79-81`); das Plugin hält die Auslieferung selbst in `session.pending` (max. `MAX_PENDING_HINTS=5`, `plugin/__init__.py:88`,`1499-1503`) — die Queue ist damit reiner Format-Puffer.

---

### 5. `src/godsv/synthesis.py` — `triage_step` / `synthesize_hint`

**Triage** — ruft Prompt `voice_triage` mit `pact_excerpt` + `event_digest`, verlangt **explizites boolesches** `stuck` (sonst `None`), klemmt Confidence auf [0,1], max. 3 Suchqueries, Rationale ≤300 Zeichen — `synthesis.py:19-25`,`36-68` — Eintrittspunkt: `_triage_verdict()` (`plugin/__init__.py:1316-1331`) aus `_enrich()` — Grenzen: aktiv nur mit `GODSV_TRIAGE=1` (`plugin/__init__.py:126-127`) und nur für `SUPPRESSIBLE_KINDS=("loop",)` (`plugin/__init__.py:90`); jede Ausnahme → `None` (Template bleibt).

**Synthese** — ruft Prompt `voice_synthesis` mit `trigger` + `evidence_items`, verlangt nicht-leeren `hint_md`, Confidence-Default 0.5, `verify` ≤200 Zeichen — `synthesis.py:28-33`,`71-105` — Eintrittspunkt: `_synthesize_into()` (`plugin/__init__.py:1394-1430`) — Grenzen: Exceptions/Decline → `None`, Template bleibt; Upgrade wird **in-place** gepatcht (`plugin/__init__.py:1421-1429`) und verfällt, wenn der Hint schon als Template raus ist („upgrade missed", 1424).

**Enrichment-Lebenszyklus (Plugin)** — Hint wird „gehalten" (Daemon-Thread: Triage → Evidence-Judge → Synthese), Deadlines `GODSV_LLM_GRACE` (Default 12 s) bestimmen Shadow-Promotion; `take_pending()` promoviert unfertige Hints als Template, `flush()` am Session-Ende — `plugin/__init__.py:1244-1266`,`1432-1466`,`1499-1503` — Grenzen: Enrichment ist **nie blockierend**, ein spätes Modell verliert nur das Upgrade; Triage-Suppression nur bei `stuck == False` **und** `confidence ≥ 0.6` (`TRIAGE_MIN_CONFIDENCE`, `plugin/__init__.py:91`,`1289-1306`).

---

### 6. `src/godsv/prompts.py` + `src/godsv/promptrun.py` — Systemprompts & Runtime-Versorgung

**PromptRegistry** — versionierte Prompt-Dateien `prompts/<id>.v<N>.md` mit YAML-Frontmatter; `load()` nimmt je ID die höchste Version; `get()`/`all()`/`ids()` — `prompts.py:18`,`54-83` — Eintrittspunkt: `_prompt_registry()` im Plugin (lazy, einmalig, Fehler werden gemerkt) `plugin/__init__.py:1468-1477` — Grenzen: Root aus `$GODSV_PROMPTS` oder Repo-`prompts/` (`prompts.py:46-51`); Dateien ohne Namensschema werden ignoriert (`63-66`); `get()` ruft `load()` bei **jedem** Aufruf (Verzeichnis-I/O pro Prompt-Lauf, kein Cache in der Klasse); Frontmatter muss ein Mapping sein, sonst `ValueError` (`95-96`).

**REQUIRED_IDS** — 13 Pflicht-IDs (`main_agent_steering`, `voice_triage`, `voice_synthesis`, `same_problem_judge`, `incident_extract`, `skill_draft`, `tool_spec_draft`, `cu_step_plan`, `spend_intent`, `completion_check`, `voice_redirect`, `slop_check`, `research_note`) — `prompts.py:20-34` — Eintrittspunkt: `godsvoice prompts`-Diagnose (`cli.py:138`,`1566-1568`) und Test `tests/test_prompts.py:27` — Grenzen: die Liste wird zur **Laufzeit nirgends erzwungen** (nur Diagnose/Test); im Verzeichnis liegen 16 Dateien / 13 IDs — mehrfach versioniert sind nur `same_problem_judge`, `voice_redirect` und `voice_triage` (je v1+v2).

**Prompt-Tier aus Daten** — `model_tier` im Frontmatter: `voice_triage` small, `voice_synthesis` mid, `voice_redirect` small, `completion_check` small, `main_agent_steering` main (alle `prompts/*.md:5`) — Eintrittspunkt: `run_prompt()` liest `prompt.meta["model_tier"]` — Grenzen: `main_agent_steering` ist registriert/required, hat aber **keinen Laufzeit-Aufrufer** (grep über `src/`, `integrations/`, `tools/`: nur `prompts.py:21` und die Datei selbst) — Versorgung existiert, Konsument fehlt.

**`run_prompt` (Runtime-Versorgung)** — lädt Prompt, hängt Inputs als separaten `# Input`-Block an (Prompt-Bodies werden **nie** getemplatet), ruft `client.complete(prompt_id, prompt, tier)`, optional Budget, parst JSON best-effort mit **einem** Repair-Versuch („Reply with ONLY the corrected JSON") — `promptrun.py:26-31`,`75-110` — Eintrittspunkt: `synthesis.triage_step`/`synthesize_hint`, `guard.judge_completion`, `research.py` — Grenzen: `extract_json` akzeptiert Code-Fences/freien Text/first-balanced-object (`34-64`); bei endgültigem Parse-Fehler fliegt `PromptError` (Aufrufer fangen → `None`/Fallback); `budget.spend` schätzt Tokens als chars/4 (`llm.py:66-68`).

---

### 7. `src/godsv/llm.py` + `src/godsv/modelroles.py` — LLM-Client, Rollen/Tiers, Routing

**Tiers & Client-Protocol** — `small|mid|main`; `LLMClient.complete(*, prompt_id, prompt, tier) -> str` als Protocol; `ScriptedLLM` (Prompt-ID→Antwort, test/demo) und `CallableLLM` (beliebiges Callable) — `llm.py:19-22`,`33-63` — Eintrittspunkt: alle Voice-Module — Grenzen: kein Provider-Code im Core; der echte Client kommt aus dem Plugin.

**BudgetTracker** — task-scoped Tokenbudget pro Tier, `spend()` wirft `BudgetExceeded`; `limits=None` = keine Prüfung — `llm.py:29`,`71-90` — Eintrittspunkt: nur im Plugin am `ResearchRunner` (`BudgetTracker(limits={"mid": 6000, "small": 4000})`, `plugin/__init__.py:286`) — Grenzen: Triage, Evidence-Judge, Synthese und `judge_completion` laufen **ohne** BudgetTracker (kein Limit); Schätzung chars/4 ist Guard, kein Accounting.

**ContextLLM (Hermes-Anbindung)** — übersetzt `complete()` in `ctx.llm.complete(messages, timeout, max_tokens=900, purpose=f"godsv:{prompt_id}", [provider, model])`; Timeout `GODSV_LLM_TIMEOUT` (Default 8 s) — `plugin/__init__.py:156-199` — Eintrittspunkt: `_llm_client()` nur bei `GODSV_LLM==1` (sonst Template-Modus) `plugin/__init__.py:215-222` — Grenzen: kein Provider-/Modell-Override → keine Hermes-Capability nötig; auf `trust`-Refusal des Overrides wird einmal ohne Override wiederholt (fail-open, getract, `189-198`); Fehler propagieren, die Aufrufer fallen auf Templates zurück.

**Rollen & Routing** — Rollen `main|watcher|investigator|judge|fallback`; Prompt-ID-Hinweise schlagen den Tier: `triage→watcher`, `investigate|research|voice_redirect→investigator`, `judge|verify→judge`, sonst `small→watcher`, `mid→investigator`, `main→main` — `modelroles.py:29`,`33-41`,`149-155` — Eintrittspunkt: `resolve_for_call(prompt_id, tier)` (`modelroles.py:158-165`) aus `ContextLLM._role_override()` (`plugin/__init__.py:201-212`) — Grenzen: `main` und unkonfigurierte Rollen liefern bewusst `None` (Hermes-Default); unbekannte Prompt-IDs fallen auf den Tier-Match zurück.

**Rollen-Datei & Override-Gate** — `~/.hermes/godsv/models.yaml` (nur `provider`/`model`/`reasoning`-Namen, Validierung per Regex, atomares Schreiben via tmp+`os.replace`) — `modelroles.py:47-141` — Eintrittspunkt: `save_role`/`clear_role` (CLI/Panel), `load_roles` beim Call; `overrides_enabled()`/`ensure_overrides_enabled()` setzen die Hermes-Flags `plugins.entries.godsv.llm.allow_model_override` + `allow_provider_override` per `hermes config set` — `modelroles.py:183-221` — Grenzen: fehlende/defekte Datei → `{}` → Hermes-Default (`82-88`); ist das Gate zu, läuft der Call ohne Override weiter (fail-open, `plugin/__init__.py:193-198`); `roles_payload()` liefert nur Namen, nie Secrets (`modelroles.py:168-175`).

---

### 8. `src/godsv/voice.py` — Hint-Budget, Dedup, Template-Synthese, Kostenmodell

**Voice (Hint-Budget & Dedup)** — pro Session max. `DEFAULT_BUDGET=5` Hints, Mindestabstand `DEFAULT_MIN_GAP=2` Steps, Dedup pro Fingerprint, Auswahl = höchste Severity (stabile Reihenfolge bei Gleichstand) — `voice.py:15-16`,`155-205` — Eintrittspunkt: `Voice.consider(signals, step, evidence)` in `observe()` (`plugin/__init__.py:1217`) — Grenzen: nach Budget-Erschöpfung **Schweigen** (177-178); Dedup-Set wächst unbegrenzt pro Session (161); es wird **höchstens ein** Hint pro Step gebaut (184-205).

**Confidence-Mapping & Templates** — `CONFIDENCE`: loop 0.70, error 0.75, scope 0.60, slop 0.85, budget 0.50; Prefix = Text vor dem ersten `_`; 12 Templates (loop, error_burst, scope_drift, 5× slop, hallucinated_api, known_incident_repeat, diff_churn), sonst Fallback `"Signal: {message}."` — `voice.py:18-24`,`26-71`,`192-194` — Eintrittspunkt: `consider()` — Grenzen: für `revert`, `error_without_details`, `error_recurrence_after_progress`, `error_cluster`, `stall`, `dependency_change`, `tool_error_*`, `context_drift`, `progress_stall`, `capability_gap` (und `skill`) fehlen Templates → generischer Text; `tool_error_<klasse>` bekommt Prefix `tool` → Default-Confidence 0.60, obwohl `CONFIDENCE["error"]` existiert (193).

**Interventions-Kostenmodell (`assess_intervention`)** — s. §1 (Kosten-Gate); Konstanten `EST_TOKENS_PER_STEP=2200`, `EST_PAUSE_COST=3500`, Severity-Faktoren und `_STRICTNESS_FACTOR={relaxed:0.6, standard:1.0, strict:1.7}` — `voice.py:79-139` — Eintrittspunkt: `pause.py:452-459`, Ergebnis-Event in `pause.py:460-469` — Grenzen: `measured` enthält nur occurrences/severity/steps_in_window (letzteres ungenutzt 0); Entscheidung fällt allein über geschätzte Tokenkosten.

**Hint-Datentyp** — `Hint(id, sig, kind, severity, confidence, text, evidence, pos, total)`; `pos/total` speist das `[voice … pos=n/5]`-Header-Format — `voice.py:142-152` — Eintrittspunkt: `Voice.consider` → `InjectionQueue.push` — Grenzen: `id` = laufende Nummer, `total` = Budget, beides nur informativ.

---

### 9. Eintrittspunkte (Hermes-Hooks, registriert in `plugin/__init__.py:1753-1767`)

| Hook | Wirkung im Voice-Pfad | Zeile |
|---|---|---|
| `on_session_start` | Session anlegen, `started=True`, Testing-Tools registrieren | `1562-1577` |
| `post_tool_call` | `tool_call`- + `tool_result`-Events, Datei-Edit-Spiegelung, Monitor/Pause/Voice | `1675-1706` |
| `post_llm_call` | `thought`-Event + Claims | `1709-1715` |
| `on_interim_message` | Zwischenmeldung als `thought` + Claims (frühe Claim-Erkennung) | `1644-1652` |
| `pre_tool_call` | BLOCK, wenn ein Redirect bereitliegt (genau einmal) | `1587-1599` |
| `pre_verify` | Strict-Abschluss-Guard: reject → continue mit Redirect | `1602-1618` |
| `transform_llm_output` | Guard-Footer + `::godsv-live{…}`-Block | `1621-1641` |
| `pre_llm_call` | Turn-Reset + Contract-Kontext (Ziel, Blocker, claim-check) an die User-Message | `1655-1672` |
| `transform_tool_result` | Hints an Tool-Ergebnis anhängen (nur `whisper`) | `1718-1750` |
| `on_session_end` | Flush gehaltener Hints, Pause-Release, Watchdog-Stop | `1580-1584` |

Kernschleife `observe()`: Event → `monitor.ingest` → Drift-/Capability-Signale → `_maybe_pause` → Evidence sammeln → `voice.consider` → Triage/Judge/Synthese (Thread) oder Queue (`plugin/__init__.py:1201-1241`).

### 10. Gesammelte Lücken/Grenzen (nur aus dem Code belegt)

1. **Policy-Namen aus dem Auftrag existieren nicht** als Code-Artefakte (kein Enum, keine Konstanten) — nur Modi + Leiter (§0). Wer sie erwartet, findet sie weder in `pause.py` noch sonst im Produktivcode.
2. `main_agent_steering` (Prompt, Tier `main`, Pflicht-ID) hat **keinen Aufrufer** — toter Versorgungsweg.
3. `InjectionQueue.drain()`/`pending_count()` ohne Produktionsaufrufer; die Delivery nutzt `session.pending` (max. 5).
4. `guard.find_contradiction()` wird produktiv nicht genutzt (Plugin prüft inline mit 12-Step-Fenster); nur Eval/Tests.
5. Budget-Limits existieren nur für den Research-Runner; Triage, Synthese, Evidence-Judge und Completion-Judge laufen ohne Limit.
6. 11 der ~18 Signalarten haben kein Template (generischer „Signal: …"-Text); `tool_error_*` bekommt Default-Confidence statt der `error`-Klasse.
7. `user`/`safety`-Leases sind im Store implementiert, werden aber nie erzeugt (nur Test) — die „no pause path grants permissions"-Zusage stützt sich darauf, dass nur `voice` existiert.
8. In `shadow` (Default!) wirkt **nichts** steuernd: Hints nur `inbox.jsonl`, kein Block, kein Continue, kein Footer — Steuerung setzt `GODSV_MODE=whisper` voraus.
9. Zähler (`_counts`, `_pauses`, `_last_pause`) und Hint-Budget sind in-memory; über Prozess-/Sessiongrenzen hinweg existiert nur die Lease-Historie in SQLite (Novel-Strategy-Zählung) und die Drift-Zählerdatei.
10. `_pact_excerpt()` liefert konstant „(kein Pakt geladen …)" (`plugin/__init__.py:1349-1350`) — die Triage sieht den Pakt-Text nie, obwohl der Parameter existiert.

---

## 3. Tool-Systeme

### Tool-Systeme (Tools, Builder, Gates, MCP, Ledger, Hub)

Alle Angaben aus dem Code (`src/godsv/…`, `integrations/hermes/godsv_plugin/…`, `pyproject.toml`); `docs/` wurde nicht verwendet.

**Achse im Code:** Kandidat/Proposal → statischer Scan → Gates (bwrap-sandboxed: Import / Tests / Determinismus) → explizite Approval → Ausführungsoberfläche (CLI-Wrapper bzw. MCP-Server) → Nutzungs-Ledger (`tool_usage.jsonl`) → Discovery/Hub-Manifest. Jede Stufe läuft offline; nichts wird automatisch veröffentlicht.

---

### 1. Tool-Registry & Propose-first — `src/godsv/tools.py`

- `ToolSpec` (`tools.py:41-71`): Datenmodell (name/title/description/tags/kind/module/entrypoint/steps/test_command/sample_input/rationale/provenance/status/version/scan_summary). `to_mcp_descriptor()` (`:61`) hat **keinen Aufrufer** (Totholz).
- Statusmodell (`:30-34`): `candidate → (scanned) → approved | revoked`, validiert in `ToolStore.set_status` (`:191-207`). **Lücke: `scanned` wird von keinem Codepfad gesetzt** — nur `compose_candidates` liest den Wert (`:240`).
- `ToolStore` (`:78-224`): SQLite (WAL), Tabelle `tool_specs`, name UNIQUE, RLock; `upsert` (`:113-181`, setzt created/updated, vergibt id), `get` (`:183`), `all` (`:209`), `by_status` (`:214`, unbenutzt), `count` (`:217`, unbenutzt), `close` (`:222`).
- `compose_candidates` (`:227-247`): deterministisches Wort-Overlap-Scoring (kein LLM) über name/title/description/tags, nur `approved`/`scanned`.
- `propose_tool` (`:250-262`): upsert + Kompositionskandidaten — der einzige Ort, an dem „compose before create“ tatsächlich greift.
- `validate_composition` (`:265-277`): prüft Schritte auf unbekannte/widerrufene Tools; führt nichts aus. Meldungen sind deutsch („Komposition ohne Schritte“).
- `draft_tool_spec` (`:280-317`): LLM-Entwurf über Prompt `tool_spec_draft` (Name-Regex `[a-z][a-z0-9_]{2,39}`, max. 10 Steps). **Lücke: kein einziger Aufrufer in `src/` oder `integrations/`** — nur die Prompt-ID ist als required registriert (`prompts.py:27`).
- Eintritt: CLI `godsvoice tools list|show|propose|approve|revoke|compose` (Parser `cli.py:1218-1314`, Handler `cli.py:583-713`); Python-API `ToolStore`/`ToolSpec`; kein MCP-Zugang.
- Grenzen: Kompositionen sind rein informativ (mcpserver filtert sie aus, `mcpserver.py:39-48`); `validate_composition` wird nur im und nur für `kind=composition` im CLI-Approve aufgerufen (`cli.py:661-670`).

### 2. Statischer Tool-Scan (Scan-Gate) — `src/godsv/toolscan.py`

- Regelkatalog `_PATTERNS` (`:25-61`): **high** = `eval(`, `exec(`, `__import__(`, `os.system|popen(`, `subprocess…shell=True`, ctypes-Import; **medium** = socket, requests/httpx/urllib/aiohttp, pickle; **low** = Datei-Write (`open(…"w"`).
- `scan_tool_source` (`:72-88`) hängt Secret-Funde aus `secretscan.scan` als Severity **high** an (`:78-87`); `scan_tool_file` (`:91-92`); `has_blocking` (`:95-96`) = mindestens ein high.
- Eintritt: Gate-Stufe „scan“ in `gates.run_gates` (`gates.py:259-268`) und CLI `godsvoice tools scan <path>` (`cli.py:1227-1229`, Handler `:609-616`); Python `scan_tool_file`.
- Grenzen: reine Regex-Muster (umgehbar via `getattr(__builtins__,…)`, `importlib`, subprocess ohne shell=True); die CLI liefert Exit 1 bei **jedem** Fund inkl. low/medium — keine Severity-Schwelle.

### 3. Secret-Scanner & Redactor — `src/godsv/secretscan.py`

- 8 Muster (`:14-26`): openai (`sk-…`), anthropic (`sk-ant-…`), github_pat (`ghp_…`), AWS (`AKIA…`), private_key, slack (`xox…`), jwt (`eyJ…`), generic_key (Kontext `api_key|secret|token[:=]…`, 20+ Zeichen).
- `scan` (`:43-54`): Findings mit kind/start/end + maskierter Preview (`_preview` `:37-40`), nach Position sortiert.
- `redact` (`:57-71`): ersetzt Treffer durch `[REDACTED:kind]`, überspringt überlappende Funde.
- Reale Verdrahtung: `events.append` serialisiert redigiert (`events.py:229`), Ledger-Fehlertexte (`toolusage.py:15-22`), Research-Payloads (`research.py:416`), Completion-Diff (`completion.py:1012`), generierter CLI-Wrapper (`toolbuilder.py:405-410`), toolscan (`toolscan.py:19`); CLI `godsvoice scan <path|->` (`cli.py:1126-1128`, Handler `:119-130`, Exit 1 bei Fund, JSON-Ausgabe mit Masken).
- Grenzen: V0-Regex ohne Entropie-Heuristik; generische Regel braucht key-artigen Kontext; `redact` ist best effort (unbekannte Formate bleiben stehen).

### 4. Gate-Runner (sandboxed) — `src/godsv/gates.py`

- `GateCheck`/`GateReport` (`:31-49`); `GateReport.passed` = alle Checks grün (`:42-44`), `render_lines` (`:46-49`).
- `_sandbox_available` (`:52-98`): einmalige bwrap-Probe (`--unshare-net`, ro-bind `/usr`,`/etc`, `python3 -c pass`); **nur positive Ergebnisse werden gecacht** (`:58-60`), Fehlproben werden wiederholt.
- `_run` (`:101-213`): workdir wird zu absolut aufgelöst (`:110`, bwrap braucht absolute Bind-Pfade); minimales Env (`PATH`, `PYTHONPATH=workdir`, `HOME=workdir`, `LC_ALL=C.UTF-8`, `:111-117`); bwrap-Args: ro-bind `/usr`,`/etc`,`/opt`, Symlinks für /bin,/sbin,/lib,/lib64, `--dev /dev --proc /proc`, tmpfs `/tmp`,`/home`,`/root`,`/var`, zusätzliche ro-binds für die Python-Executable-Kette inkl. Symlink-Hops (`:127-147`) und optionale `extra_binds` (`:149-154`), Workspace read-write (`:194-196`), Timeout (`:200-205`).
- **Fail-closed** (`:209-213`): ohne bwrap — oder wenn bwrap selbst scheitert — wird **nicht** ausgeführt: `(-127, "sandbox unavailable — execution blocked (fail-closed)", False)`. Der Modul-Docstring (`:6-8`) behauptet noch einen Fallback auf einen ungesandboxten Subprozess — **veraltet gegenüber dem Code**.
- `_run_checked` (`:216-226`): übersetzt Timeout in `(-9, "Timeout nach Xs")`.
- `run_gates` (`:249-314`): (a) `scan` über `_find_source` (`:234-246`: `workdir/<module>.py`, cwd-relativ, `workdir/<basename>.py`) + `toolscan` + Severity-Zählung; (b) `import <module>`; (c) `test` = `spec.test_command`; (d) `determinism` = zwei Läufe mit `sample_input`, identische Ausgabe (`:287-312`) — nur wenn `entrypoint` **und** `sample_input` gesetzt.
- Nutzer: CLI `godsvoice tools approve` (`cli.py:654-685`, `--force` überstimmt rote Gates), `ToolBuilder.build` (`toolbuilder.py:322`), Panel-Recheck (`plugin_api.py:1058-1070`), Testing-Tool-Runner am Completion-Gate (`capability.py:297-335` via `_run_checked`); außerdem fremde Module (`treasury.py:300,350`, `computer_use.py:35-36`).
- Grenzen: **bwrap ist harte Voraussetzung** — fehlt es, scheitern Import/Test/Determinismus ausnahmslos (kein Rückfall); ein Kommando mit Exitcode 2 **und** leerem Output wird als „sandbox unavailable“ gemeldet statt als Kommando-Fehler (`:206-211`); Netzwerk wird nur global per netns entzogen, `allowed_domains` werden hier nicht geprüft.

### 5. Autonomer Tool-Builder — `src/godsv/toolbuilder.py`

- `ToolBuildRequest` (`:78-104`): capability, reason, kind (`auto|mcp|cli|testing_tool`), source_code/source_path, tests_code/tests_path, module, entrypoint, sample_input, research_notes, `required_secrets` (nur `secret://`-Handles), allowed_domains, dependencies, risk_level, session_id, project_id, validation_cases.
- `choose_kind` (`:107-118`): CLI bei `wraps_existing_cli`/CLI-Hints/kleiner Beschreibung, MCP bei ≥2 API-Funktionen oder MCP-Hints.
- `inspect_dependencies` (`:126-143`): AST-Importprüfung gegen `sys.stdlib_module_names` + deklarierte Dependencies + `godsv`; Syntaxfehler → `ok=False`.
- `ToolBuilder` (`:146-450`): Runtime = übergebenes Verzeichnis, Ablage `generated_tools/`, Store (`tools.db`) wenn nicht injiziert (`:159-161`), Queue-Datei `tool_builds.jsonl` (`:163`); `_record` (`:166-182`) schreibt JSONL **und** spiegelt jedes Stadium als Event `tool_build_<stage>` (Fehler nur geloggt, nie build-brechend); `queue` (`:188-210`) aggregiert pro `build_id` newest-first (§ Panel).
- `build` (`:213-450`), Stadien `STAGES` (`:62-71`) = requested → researching → generating → manifest → dependency_inspection → gates → register → ready (plus `failed` außerhalb von STAGES):
  - Quelltext aus `source_code` oder `source_path`, sonst Abbruch „no source supplied (builder does not invent code)“ (`:238-249`); Modulname Default `godsv_tool_<name>`, Datei `generated_tools/<name>/<module>.py`; Tests nach `tests/` + `pytest -q tests/` als test_command (`:250-262`).
  - `manifest.yaml` (`:264-289`): scope **project**, project_id, type=kind, purpose, network.allowed_domains, filesystem, required_secrets, dependencies, tests, risk_level, status.
  - dependency_inspection (`:292-304`): undeclared Imports → `failed`.
  - gates (`:322-337`): `run_gates`; rot → `failed` + Manifest-Status failed.
  - Testing-Tool-Pflichtvalidierung (`:341-385`): ohne `validation_cases` → Status `needs_validation`, kein Register; mit Fällen `validate_testing_tool` (Broken→FAIL, Working→PASS) über `_run_testing_case` (`:44-59`: `python <module>.py <target>`, PASS auf stdout = bestanden); Abweichung → „promotion refused“.
  - register/ready (`:387-449`): `ToolSpec` (kind immer `python`, tags `[kind, generated, toolbuilder]`, provenance `toolbuilder:<build_id>`), Status **approved**, `scan_summary` aus den Gate-Ergebnissen; CLI-Wrapper nur für `kind in (cli, testing_tool)` (`:394-441`): JSON-argv → `**kwargs` der Entrypoint-Funktion (Default `main`), JSON-stdout, eigener Usage-Eintrag mit secretscan-Scrub nach `runtime/tool_usage.jsonl`.
- `redirect_payload` (`:453-483`): kleiner `tool_created`-Payload (capability, tool, kind, how_to_use, constraints inkl. Secret-Handles + Netzwerkfreigaben) — bewusst kein Build-Transkript.
- Eintritt: CLI `godsvoice tools build …` (`cli.py:1244-1268`, Handler `:498-565`) und `godsvoice tools builds` (`:1269-1271`, Handler `:568-580`); **Session-Gap**: Agent führt `godsvoice tools request` aus → Plugin erkennt das Kommando (`integrations/hermes/godsv_plugin/__init__.py:475-515`, Aufruf `:1211`), Signal `capability_gap` (`pause.py:103` = Schwelle 1) → Pause → `ToolBuildRunner.run` (`capability.py:144-255`) → `CapabilityAwareRunner` (`capability.py:258-280`) → Redirect an den Agenten; Sofortlauf ohne Session: `godsvoice tools fulfill` (`cli.py:1292-1297`, Handler `:458-495`); Mining-Weg `tools mine|candidates|promote` (`cli.py:1298-1308`, Handler `:844-885`) über `capability_mining`.
- Grenzen/Lücken:
  - **Projekt-Scope ohne Projekt-ID**: `build` schreibt unconditionally `scope: project` + `project_id` (`:270-271`), aber **kein Produktionspfad füllt project_id** (CLI/Plugin/Mining setzen es nie; nur `godsvoice mcp serve --project` berechnet eine ID zur Laufzeit, `cli.py:721-724`). `visible_tools` (`capability.py:544-565`) zeigt projekt-scoped nur bei exakt passender project_id → **Builder-Tools sind auf der scope-gefilterten MCP-Oberfläche unsichtbar**.
  - **Compose-before-create fehlt im Builder**: `build()` ruft weder `compose_candidates` noch `propose_tool` auf.
  - **testing_tool über CLI nicht baubar**: `--kind` choices nur `auto|mcp|cli` (`cli.py:1250`); `validation_cases` sind über keinen CLI-Schalter setzbar; `capability_mining.promote` setzt `kind=testing_tool` ohne Fälle (`capability_mining.py:311-345`) → Builder endet zwangsläufig in `needs_validation`.
  - **Moduldatei-Namensbruch**: Builder schreibt `{module}.py` (Default `godsv_tool_<name>`), `register_testing_tools` erwartet `<verzeichnisname>.py` (`capability.py:355-374`) → Testing-Tools werden am Completion-Gate nur registriert, wenn `module == name` übergeben wurde.
  - MCP-Kind ist nur Manifest-`type`: `spec.kind` wird immer `KIND_PYTHON` (`:311`); ohne `--entrypoint` bleibt der Spec damit für `approved_python_specs` unqualifiziert (module+entrypoint Pflicht) — der Redirect behauptet dann trotzdem „exposed by the godsv-tools MCP server“ (`:466`).

### 6. Nutzungs-Ledger — `src/godsv/toolusage.py`

- `usage_path` (`:25-26`) = `<runtime>/tool_usage.jsonl`; `record_usage` (`:29-46`): `{ts,name,ok,duration_ms?,error?}`, append-only, Fehlertext über `_redact` (`:15-22`) und auf 300 Zeichen gekürzt. Docstring verspricht „never raises“, intern fehlt jedoch try/except — die Aufrufer wrappen.
- `usage_summary` (`:64-79`): count/ok/failed/success_rate/avg_duration_ms/last_used/last_errors (max. 3, neueste zuerst).
- `usage_stats` (`:82-116`): pro Tool runs/passes/fails/last_ts/last_error — Basis der Panel-Reliability und der §26-Stats; `usage_counts` (`:119-126`): Aufrufe pro Tool.
- Schreiber: MCP-Wrapper (`mcpserver.py:86-101`), generierter CLI-Wrapper (inline, `toolbuilder.py:412-425`), Completion-Test-Runner (`capability.py:314-323`). Leser: Panel (`plugin_api.py:711-713, 1030-1032`), Hub (`toolhub.py:119-126`), `capability.testing_tool_stats` (`capability.py:378-425`).
- Grenzen: jedes `ok=false` zählt als Fail (auch Fehlbedienung); nur Metadaten, nie Payloads (by design); kein Locking/Rotation.

### 7. MCP-Export — `src/godsv/mcpserver.py`

- `approved_python_specs` (`:24-56`): nur `status=approved`, `kind=python`, `module` **und** `entrypoint` gesetzt; mit `runtime` zusätzlich Scope-Filter über `visible_tools` + `resolve_tool_names` (project beats global, dann Version; genau ein Spec pro Name).
- `_wrapper_for` (`:59-122`): MCP übergibt ein `arguments`-Objekt, das als `**kwargs` an die Entrypoint-Funktion geht; Import per importlib mit Fallback auf `base_dir/generated_tools/<name>/<module>.py` (`:72-84`); Import-/Call-Fehler werden zu `{"error": …}` statt Server-Crash (`:103-117`); Usage nur wenn `base_dir` bekannt (`:86-101`); `__name__/__doc__` werden auf den Spec gesetzt (`:119-121`).
- `build_server` (`:125-158`): FastMCP, ein registriertes Tool pro Spec; fehlt fastmcp → RuntimeError mit Hinweis auf Extra `godsv[mcp]` (`:140-145`; `pyproject.toml:24-26`). `run_serve` (`:161-178`): stdio, blockierend.
- Eintritt: CLI `godsvoice mcp serve [--db --name --project --all --runtime]` (`cli.py:1316-1331`, Handler `:716-749`); Python `build_server`/`run_serve`.
- Grenzen/Lücken: **kein Codepfad schreibt einen MCP-Server-Eintrag in die Hermes-Config** (in `integrations/` existiert nur die read-only Anzeige `mcp_servers_payload`, `plugin_api.py:661-692`) → Einbindung nur manuell; pro-Tool-JSON-Schemata fehlen (nur ein `arguments`-Objekt, `:64-65` sagt das selbst); Compositions bleiben außen vor; **Default-DB der CLI ist `.godsv/tools.db` (cwd-relativ, `cli.py:76` + `:1320`), der Builder/Panel-Speicher liegt aber unter `$HERMES_HOME/godsv/tools.db`** → out of the box leere Oberfläche.

### 8. Discovery & Hub-Manifest — `src/godsv/toolhub.py`

- `discover` (`:129-179`): read-only Gesamtbild — generierte Tools aus `generated_tools/*/manifest.yaml` (type/version/purpose/status, network+filesystem, Secret-Handles, verification aus `manifest.validation`, reliability aus dem Ledger `:119-126`), Skills aus SKILL.md-Frontmatter (`_skill_entries` `:56-88`), optional MCP-Server (`_mcp_entries` `:91-116`; liest `mcp_servers`/`mcpServers`/`mcp`). Keine LLM-Aufrufe, keine Netzwerkzugriffe.
- `search` (`:182-198`): Wort-Overlap über name/purpose/type via `research._terms`, Default-Limit 8.
- `build_hub_manifest` (`:210-261`): Eligibility `status == "ready"`, für `type=testing_tool` zusätzlich `validation.status == "validated"`, sonst ValueError; Plaintext-Secret-Verdacht über `_SECRETISH` (`:43`) → Abbruch („Treasury handles only“); Ergebnis: teilbares Manifest v1 mit provenance, permissions, `secret_handles`, verification (`trust: godsv-verified|local`), `checksum_sha256` über den Tool-Baum (`_tree_hash` `:201-207`) und `publish.automatic=False`. **Erzeugt nur JSON — veröffentlicht nie.**
- Eintritt: CLI `godsvoice tools-hub [--runtime --search --show --manifest --json]` (`cli.py:1169-1183`, Handler `:1665-1697`); Python `discover`/`search`/`build_hub_manifest`.
- Grenzen/Lücken: `config_path` wird vom CLI **nie** übergeben → MCP-Server erscheinen in der CLI-Discovery nie; Skills-Dir ist hart `~/.hermes/skills` und ignoriert `HERMES_HOME` (`:175-176`); `--show` filtert erst nach `--search`; Textausgabe zeigt nur die ersten 20 Einträge; keine Panel-Route für den Hub (Panel nutzt ihn nicht).

### 9. Querverbindung: Testing-Tools → Completion-Gate

- `register_testing_tools` (`capability.py:340-375`): registriert nur Tools mit `type=testing_tool`, `status ∈ {ready, approved}` und `validation.status=validated`; der Runner (`capability.py:289-337`) fährt `<module>.py <target>` sandboxed (`_run_checked`, Target als ro-bind), wertet Exit 0 + „PASS“ als bestanden, schreibt Usage und liefert `RunEvidence(source="runner")`.
- Completion-Seite: `_registered_check_names` (`completion.py:252-261`, lazy/fehlertolerant), `plan_verification(text, tool_names)` (`:221` ff.), `register_check`/`execute_check`/`run_requirement_checks` (`:1406-1490`); Panel `POST /completion/verify` (`plugin_api.py:1754-1762`) und `_verification_for` (`plugin_api.py:1097-1114`).
- Grenzen: nur „runner“-Evidenz zertifiziert; ungültige/gebrochene Tools werden still übersprungen; da der Builder validation_cases kaum befüllbar macht (s. §5), bleibt dieser Pfad praktisch ohne Zulauf.

### Gesamtbilanz der Lücken (nur Code-Fakten)

1. `draft_tool_spec` (`tools.py:280`) — LLM-Draft-Strecke unverdrahtet.
2. Status `scanned` wird nie gesetzt (`tools.py:31`).
3. `ToolSpec.to_mcp_descriptor` (`tools.py:61`), `ToolStore.by_status` (`:214`) ohne Aufrufer.
4. Compose-before-create fehlt in `ToolBuilder.build`.
5. `project_id` wird nie befüllt → §7-Scope-Filter blendet Builder-Tools aus der MCP-Surface.
6. `promote_tool` (`capability.py:664-717`) ohne Aufrufer (kein CLI, keine Route); nur `promotion_report` wird im Panel angezeigt.
7. MCP-Server nicht automatisch in der Hermes-Config registriert; keine per-Tool-Schemas; CLI-DB-Default `.godsv/tools.db` vs. Runtime `$HERMES_HOME/godsv`.
8. toolhub: `config_path`/MCP nie verdrahtet; Skills-Dir ignoriert `HERMES_HOME`; keine Panel-Einbindung.
9. testing_tool: CLI kann es nicht bauen, validation_cases nicht setzbar; Moduldatei-Namensbruch zu `register_testing_tools`.
10. Gates: bwrap Pflicht (fail-closed), Docstring veraltet, Exit-2-mit-Leerlauf wird als Sandboxfehler klassifiziert.
11. Panel-Aktion `test` (`plugin_api.py:1071-1091`) führt den Wrapper **unsandboxed** aus (subprocess.run ohne bwrap) — Inkonsistenz zur fail-closed-Policy.
12. Sprachmix: `validate_composition`- und toolscan-Meldungen erscheinen deutsch in CLI-Ausgaben (`cli.py:644-666`, `toolscan.py:26-60`), der Rest dieser Oberflächen ist englisch.

### Ein-Zeilen-Übersicht (Format: **Feature** — Was — `Datei:Zeile` — Eintritt — Grenzen)

- **ToolStore (SQLite-Registry)** — Specs speichern/lesen/mutieren, name UNIQUE, 4 Status — `src/godsv/tools.py:78-224` — Python-API, CLI `tools list|show|propose|approve|revoke` — `scanned` nie gesetzt; kein Delete; CLI-DB-Default `.godsv/tools.db` ≠ Runtime-DB.
- **ToolSpec/Propose-first** — Datenmodell + Upsert mit created/updated — `src/godsv/tools.py:41-181` — CLI `tools propose --spec <json>`, Python `ToolSpec`/`ToolStore.upsert` — Steps sind Daten ohne Ausführung.
- **Compose-before-create** — Wort-Overlap-Ranking bestehender Tools — `src/godsv/tools.py:227-262` — CLI `tools propose`, `tools compose <need>` — nur im Proposal-Pfad; Builder prüft nicht.
- **Kompositions-Validierung** — prüft Schritte/Status — `src/godsv/tools.py:265-277` — CLI `tools approve` (nur kind=composition) — führt nichts aus; DE-Meldungen.
- **LLM-Tool-Draft** — Prompt `tool_spec_draft` → ToolSpec — `src/godsv/tools.py:280-317` — **kein Aufrufer (Lücke)** — Prompt-ID existiert (`prompts.py:27`), Strecke tot.
- **Statischer Tool-Scan** — Regex-Audit + Secret-Funde, high blockiert — `src/godsv/toolscan.py:25-96` — Gate „scan“ (`gates.py:259-268`), CLI `tools scan <path>` — umgehbare Muster; CLI-Exit 1 schon bei low/medium.
- **Secret-Scan/Redaction** — 8 Muster, `[REDACTED:kind]` — `src/godsv/secretscan.py:14-71` — CLI `scan <path|->`, intern in Events/Ledger/Research/Wrapper — V0-Regex, kein Entropie-Check.
- **Gate-Runner (bwrap)** — Import/Test/Determinismus offline, fail-closed ohne Sandbox — `src/godsv/gates.py:101-314` — CLI `tools approve`, Builder, Panel-Recheck, Completion-Runner — bwrap Pflicht; Docstring veraltet; Exit-2-Leerlauf als Sandboxfehler.
- **Sandbox-Probe** — bwrap-Testlauf, Cache nur positiv — `src/godsv/gates.py:52-98` — intern (`gates`, `computer_use.py:36`) — negative Probe wird jedes Mal neu geprüft (gewollt).
- **Determinismus-Gate** — zwei Läufe, identische Ausgabe — `src/godsv/gates.py:287-312` — via `run_gates` — nur mit `entrypoint` + `sample_input`.
- **Tool-Builder-Pipeline** — 8 Stadien, Manifest, Deps-Prüfung, Gates, Register, CLI-Wrapper — `src/godsv/toolbuilder.py:213-450` — CLI `tools build`, Session-Gap via `tools request`→Pause→Redirect, `tools fulfill` — kein compose-check, project_id nie gesetzt, testing_tool praktisch nicht baubar.
- **Build-Kind-Wahl** — Heuristik CLI vs MCP — `src/godsv/toolbuilder.py:107-118` — intern (Build ohne `--kind`) — rein textbasiert.
- **Import-/Dependency-Inspektion** — AST-Check gegen stdlib+declared+godsv — `src/godsv/toolbuilder.py:126-143` — Stadium `dependency_inspection` — nur Top-Level-Modulnamen, kein Symbolcheck.
- **Build-Queue (JSONL)** — Stadien pro build_id, newest-first + Event-Spiegel — `src/godsv/toolbuilder.py:166-210` — CLI `tools builds`, Panel `tools_payload().queue` — Event-Spiegel best effort (Warnung).
- **Testing-Tool-Validierung** — Broken→FAIL/Working→PASS Pflicht — `src/godsv/toolbuilder.py:44-59, 341-385` + `capability_mining.py:350-382` — Build mit `kind=testing_tool`/validation_cases — CLI kann keine cases liefern → `needs_validation`.
- **CLI-Wrapper für generierte Tools** — argv-JSON → Entrypoint → JSON-stdout + Usage-Zeile — `src/godsv/toolbuilder.py:392-441` — `<generated_tools>/<name>/bin/<name> '{"…":"…"}'` — nur kind cli/testing_tool; Usage-Log eigens implementiert (nicht `record_usage`).
- **Redirect-Payload** — `tool_created`-Notiz für den Main Agent — `src/godsv/toolbuilder.py:453-483` — Session-Gap-Redirect, CLI-Build-Ausgabe — behauptet MCP-Exposure auch ohne entrypoint.
- **Capability-Request-Store** — append-only JSONL, open/fulfilled/failed — `capability.py:36-132` — CLI `tools request|fulfill`, Plugin-Signal — Nachrichten-Latenz bis zum nächsten Tool-Call.
- **Session-Gap-Tool-Build** — Signal `capability_gap` → Pause → Builder als „Researcher“ → Resume — `capability.py:139-280`, `godsv_plugin/__init__.py:475-515` — `godsvoice tools request` im Chat — nur bei erkanntem Kommando-Muster (`looks_like_request_command`).
- **Capability-Mining** — wiederkehrende Bedarfe → Kandidaten → `promote` in eine Request — `capability_mining.py:253-345` — CLI `tools mine|candidates|promote` — testing_tool-Promotion ohne cases landet in `needs_validation`.
- **Nutzungs-Ledger** — Aufrufe/Erfolge/Fehler/Dauer, Fehler redigiert — `src/godsv/toolusage.py:29-126` — implizit (Wrapper/MCP/Runner), gelesen von Panel/Hub/Stats — kein Locking; „never raises“ im Docstring nicht eingehalten.
- **MCP-Server (FastMCP)** — approved Python-Specs als MCP-Tools, Scope-gefiltert — `src/godsv/mcpserver.py:24-178` — CLI `mcp serve`, Python `build_server/run_serve` — manuelle Hermes-Config nötig, kein per-Tool-Schema, Default-DB-Falle.
- **MCP-Modul-Loader** — importlib + Fallback auf `generated_tools/<name>/<module>.py` — `src/godsv/mcpserver.py:72-84` — Teil von `build_server` — lädt nur, wenn Dateiname exakt dem gespeicherten Modul entspricht.
- **Tool-Registry-Discovery** — read-only Liste aus Manifesten/Skills/MCP-Config + Ledger-Reliability — `src/godsv/toolhub.py:129-179` — CLI `tools-hub [--search --show]` — MCP-Config wird vom CLI nie übergeben; Skills-Dir ignoriert HERMES_HOME.
- **Hub-Manifest** — teilbares Manifest mit Verification-State + Tree-Hash, Publish explizit aus — `src/godsv/toolhub.py:210-261` — CLI `tools-hub --manifest <name>` — erzeugt nur JSON, keine Veröffentlichung, keine Panel-Route.
- **Testing-Tool→Completion-Check** — validierte Tools als Runner-Checks (`source=runner`) — `capability.py:289-375`, `completion.py:221-261, 1406-1490` — Panel `POST /completion/verify`, Plugin-Gates — still übersprungen bei fehlender Validierung.
- **Panel-Tool-Verwaltung** — Liste/Detail/Enable/Disable/Recheck/Test + Grants — `plugin_api.py:695-800, 1013-1091, 1705-1724`, UI `plugin.js:927-1140` — Panel-Tab „Tools“ (token-guardete Plugin-API) — `test` läuft unsandboxed; Promotion nur anzeigend.

---

## 4. Capability-System, Scopes, Mining, Workflows & Computer Use

### Skaldir Agent — Feature-Landkarte: Capability-System, Scopes, Mining, Workflows, Computer Use

Basis: ausschließlich gelesener Quellcode (`src/godsv/…`, `integrations/…`, CLI-Parser). `docs/` ist bewusst ignoriert. Zeilenangaben beziehen sich auf den Stand vom 13.09.2026. Es wurde nichts ausgeführt oder geändert (Auftrag: NUR LESEN); Testdateien sind als Fundstellen genannt, nicht als Laufbeleg.

### 1. Capability-Request-Pipeline (Request → Build → Registry)

**CapabilityRequest** — Dataclass mit Build-Material: capability, reason, requested_by, session_id, project_id, kind, name, source_code/source_path, tests_code/tests_path, module, entrypoint, sample_input, required_secrets, allowed_domains, dependencies, research_notes, status (Default `open`), request_id, validation_cases, created, result_tool, error. — `src/godsv/capability.py:36-63` (Statuskonstanten `:28-30`) — Python-Dataclass — Grenzen: `validation_cases` (Liste `(target, expect_pass)`) und `project_id` werden von keinem Produktionspfad (CLI/Plugin/Panel) befüllt.

**CapabilityRequest.to_tool_request** — baut `ToolBuildRequest`; `kind` wird nur übernommen, wenn in ("mcp","cli","testing_tool"), sonst "auto". — `src/godsv/capability.py:65-86` (Filter `:70`) — Python — Grenzen: CLI-Anforderung erlaubt nur `auto|mcp|cli` (`src/godsv/cli.py:1279`), `testing_tool` ist per CLI nicht anforderbar (Kanal-Inkonsistenz).

**CapabilityStore** — Append-only JSONL `<runtime>/capability_requests.jsonl` (`:92-93`). `add()` vergibt `cap-<uuid10>` + `created` und hängt die Dataclass als JSON an (`:95-102`); `all()` liest neu und verwirft unbekannte Felder (`:104-115`); `newest_open(session_id=…)` bildet je request_id den letzten Stand und liefert den neuesten offenen (Session-Treffer bevorzugt, sonst global) (`:117-128`); `update()` = weiterer Append (`:130-132`). — `src/godsv/capability.py:89-132` — Python; im Plugin gegen `state_dir = HERMES_HOME/godsv` (`integrations/hermes/godsv_plugin/__init__.py:1540-1542`), in der CLI gegen `HERMES_HOME/godsv` (`src/godsv/cli.py:428-429`) — Grenzen: kein Locking/kein DB-Schutz, „letzter Stand gilt“ ist reine Konvention.

**looks_like_request_command** — Regex `_CMD_HINT = \b(godsvoice|godsv)\b.*\btools\b.*\brequest\b` als billiger Filter für Tool-Call-Kommandos. — `src/godsv/capability.py:139-141` (Regex `:32`) — Python, aufgerufen vom Plugin-Hook — Grenzen: matcht die Wortfolge auch in Kommentaren/Strings beliebiger Kommandos; prüft den Request-Erfolg nicht.

**ToolBuildRunner** — `research_runner`, der in der Pause einen Tool-Build fährt. `run()`: Request via `newest_open(session_id) or newest_open()` (`:176`); ohne Request → `ResearchResult(status="failed")` mit Anweisung (`:177-186`); sonst `ToolBuilder.build(request.to_tool_request())` (`:188-189`). Erfolg: Request `fulfilled` + `result_tool`, Registry-Lookup über `ToolStore(runtime/tools.db)` (`:197-207`), Redirect-Bau `_Builder.redirect_payload` (`:209-216`), `direction` nennt Tool/Kind/How-to-use/Constraints (`:218-223`), `avoid`, `uncertainty` („echter API-Smoke gegen den Live-Dienst offen“, `:225-228`), Status ok (`:229`); fehlt der Registry-Eintrag → failed „Registry state inconsistent“ (`:230-237`); Build-Fehler → Request `failed` + `error`, aber `result.status = "ok"` mit konkretem nächsten Schritt (`:238-251`); `tokens_est = len(blob)//4` (`:253-254`). — `src/godsv/capability.py:144-255` — Eintritte: Plugin-Pause (`integrations/hermes/godsv_plugin/__init__.py:289`) und CLI `tools fulfill` (`src/godsv/cli.py:480-487`) — Grenzen: `uncertainty` ist selbstdeklariert (nur Mock-/Gate-Tests, kein Live-API-Nachweis); Build-Fehler gilt als „ok“-Ergebnis.

**CapabilityAwareRunner** — Dispatch: `trigger_kind == "capability_gap"` → ToolBuildRunner, sonst Primär-Runner (Recherche); Rest per `__getattr__` delegiert. — `src/godsv/capability.py:258-283` — Konstruktion in der Plugin-Session hinter `_pause_enabled()` (Default `GODSV_PAUSE=1`, `integrations/hermes/godsv_plugin/__init__.py:138-139`, `:270-293`); Primär-Runner ist der `ResearchRunner` (`:278-287`) — Grenzen: nur dieser Signaltyp baut Tools, alles andere forscht.

**Plugin-Kante `capability_gap`** — `_capability_gap_signal(kind,payload)`: nur bei `tool_call` (`:482`), liest `args.command` (`:485`), `looks_like_request_command` (`:490`), holt `newest_open(session_id) or newest_open()` (`:493-494`), schreibt Event `capability_requested` (`:500-508`), liefert Signal `capability_gap` (severity 3, fingerprint `cap-<request_id>`) (`:511-517`); eingehängt im `observe()`-Pfad (`:1211-1213`). Schwelle im Pause-Controller: `capability_gap: 1` (`src/godsv/pause.py:103`). — `integrations/hermes/godsv_plugin/__init__.py:475-517`, `:1211` — Plugin-Hooks (`post_tool_call`/`observe`) — Grenzen: **setzt kein `project_id`**; setzt einen Request voraus, der bereits auf Platte liegt (CLI-Ausführung nötig).

**Redirect-Auslieferung (Pause)** — `pre_tool_call` → `before_tool_call`: wenn ein Deliverable fertig ist, `{"action":"block","message": <redirect>}` — genau ein blockierter Tool-Call. — `integrations/hermes/godsv_plugin/__init__.py:621-633`, Hook `:1587-1599` — Plugin — Grenzen: ohne fertiges Deliverable passiert nichts; Fehler werden nur getract.

**CLI `godsvoice tools request`** — schreibt `CapabilityRequest` (capability erforderlich; reason, requested-by, session, kind, name, source, tests, module, entrypoint, sample als JSON, research, secret/domain/dependency) und verweist auf den Pause-Pfad bzw. `tools fulfill <id>`. — `src/godsv/cli.py:417-455`, Parser `:1272-1291` — CLI — Grenzen: **kein `--project`**, keine validation-cases; Ausgabe nennt den Request-Status `open` (auch bei `--json`, `:453-454`).

**CLI `godsvoice tools fulfill [request_id]`** — Pause-loser Pfad: explizite ID (nur Status `open`, sonst Fehler; `:465-474`) oder `newest_open()` (`:476-479`), dann `ToolBuildRunner.run(..., trigger_kind="capability_gap", trigger_fp=f"cap-{request_id}")` (`:480-487`); Ausgabe `[status] direction`, `avoid`, `evidence`; Exitcode 0, wenn `result.status == "ok"` (`:488-495`). — `src/godsv/cli.py:458-495` — CLI — Grenzen: da Build-Fehler laut Vertrag „ok“ liefern, ist der Exitcode kein Erfolgssignal; `--json` gibt `result.__dict__` aus.

**CLI `godsvoice tools build`** — direkter Builder-Aufruf ohne Request-Store (capability erforderlich; kind auto|mcp|cli; source/tests/module/entrypoint/sample/research/research-file/secret/domain/dependency), Ergebnisdruck inklusive Registry-Checks. — `src/godsv/cli.py:498-565`, Parser `:1244-1268` — CLI — Grenzen: kein `--project`, keine validation-cases.

**ToolBuilder.build (Request→Build→Registry)** — Stages `requested → researching → generating → manifest → dependency_inspection → gates → register → ready` (`src/godsv/toolbuilder.py:62-71`). Ablauf: Quelle zwingend („builder does not invent code“, `:246-249`), Moduldefault `godsv_tool_<slug>` (`:250`), Manifest mit `scope: project`, `project_id: request.project_id`, `version: 0.1.0`, network/fs/secrets/deps, `status: built` (`:264-288`); AST-Import-Prüfung, undeclared → failed (`:292-304`); `run_gates(spec, workdir=tool_dir)` → Manifest/Checks (`:322-337`); Testing-Tool-Pflichtgate (`:339-385`); Registrierung `spec.status=approved` + `ToolStore.upsert` (`:387-391`), CLI-Wrapper `generated_tools/<name>/bin/<name>` für kind cli/testing_tool (`:392-441`), Manifest `status: ready` (`:442-443`). — `src/godsv/toolbuilder.py:213-450` — Python (Aufrufer: CLI `tools build`, `ToolBuildRunner`) — Grenzen: registriert in `tools.db`; `scope/project_id` existieren nur im Manifest; leeres `project_id` → unsichtbar für jede `visible_tools`-Sicht (siehe §3/§8).

**ToolBuilder.redirect_payload** — Rückmeldung an den Main-Agent: `{tool_created:{capability, tool, kind, how_to_use, important_constraints}}`; CLI-Pfad nennt Wrapper-Aufruf mit JSON-Args, sonst MCP-Server; Constraints: secret-Handles + erlaubte Domains. — `src/godsv/toolbuilder.py:453-483` — genutzt von `src/godsv/capability.py:211-216` — Grenzen: `kind == testing_tool` fällt in den MCP-Zweig und wird als „MCP: exposed by the godsv-tools MCP server“ beschrieben (`:463-467`) — falscher Nutzungstext für Testing-Tools.

**Panel: Tools-Payload** — `/api/plugins/godsv/tools` baut `requests[]` aus `CapabilityStore.all()` (letzter Stand je id), verknüpft Builds aus `ToolBuilder.queue` über `request.name`, liefert status/result_tool/error/required_secrets (nur Handles)/allowed_domains/build{id,stage,ok,stages} — `integrations/hermes/godsv_plugin/dashboard/plugin_api.py:716-756`; Scope-/Promotionsfakten `:757-765`; installierte Tools inkl. `scope/version/project_id/promoted_from` `:766-789`; Routen `GET /tools` `:1705-1707`, `GET /tools/{name}` `:1709-1714`. UI: Capability-Request-Karten und -Tabelle — `integrations/hermes/godsv_plugin/desktop/plugin.js:890`, `:939-960`, `:989` — Plugin/HTTP — Grenzen: rein lesend; keine Panel-Aktion für fulfill/promote.

### 2. Testing-Tools → Completion-Gate (P0-2/B4)

**`_testing_tool_runner`** — erzeugt den Gate-Runner je generiertem Testing-Tool: `python <module>.py <target>` über `gates._run_checked` (Sandbox, Timeout 60 s, `extra_binds=[target]`); `ok = rc==0 AND "PASS" in stdout.upper()`; jeder Crash ⇒ Fail; `toolusage.record_usage` best effort; Rückgabe `RunEvidence(name, check_id=name, source="runner", status passed/failed, cmd, detail)`. — `src/godsv/capability.py:289-337` (source="runner" `:329`, „nur runner-Evidenz zertifiziert“ `:327-328`) — Python, registriert via `completion.register_check` — Grenzen: Erfolgssemantik allein über „PASS“ im stdout; kein eigener Repo-Kontext außer Ziel-Arg.

**`register_testing_tools(runtime)`** — idempotenter Scan `runtime/generated_tools/*/`: verlangt `manifest.yaml` + `<dir>.py`, `type == "testing_tool"`, `status in (ready, approved)`, `validation.status == "validated"`; registriert `register_check(name, runner)` und gibt die Namen zurück; unlesbares Manifest/kaputtes Modul ⇒ still übersprungen. — `src/godsv/capability.py:340-375` — Eintritte: Plugin `on_session_start` (`integrations/hermes/godsv_plugin/__init__.py:1562-1577`), Panel `_verification_for` (`plugin_api.py:1097-1114`), `completion._registered_check_names` (`src/godsv/completion.py:254-261`), Eval-Seam `tools/gv_eval_longhorizon.py:118-139` — Grenzen: nur validierte Tools; nicht validierte Tools sind stumm nicht registriert (Absicht, aber kein Reporting außer Statistik).

**`testing_tool_stats(runtime)`** — §26-Statistik je generiertem Tool: capability, runs/passes/fails aus dem Usage-Ledger, `bugs_detected = fails`, validation-Status, last_error; Totals inkl. `false_pass_policy` („structurally excluded by validation“). — `src/godsv/capability.py:378-425` — Python, konsumiert von `src/godsv/metrics.py:116-118, 318` — Grenzen: zählt alle Verzeichnisse unter `generated_tools/`, auch nie registrierte.

**Completion-Seams** — `plan_verification(text, tool_names)` leitet die Check-ID ab: tests→pytest, ui→ui_e2e, api→api, restart/persist→persistence_restart, sonst Wortmatch gegen registrierte Testing-Tool-Namen (`src/godsv/completion.py:221-251`); `register_check` `:1406`, `execute_check` `:1431`, `run_requirement_checks` `:1479`; Panel `/completion/verify` läuft die DoD-Checks. — Python/Panel — Grenzen: Fallback „run“ bleibt für alles andere (nicht zertifizierbar); nur `source="runner"`-Evidenz zählt am Gate.

**`validate_testing_tool(run_case, cases)`** — Erwartungstreue: je `(target, expect_pass)` wird der echte Tool-Lauf geprüft; Abweichung oder Crash ⇒ `status: "invalid"`; Ergebnis enthält die Checks. — `src/godsv/capability_mining.py:350-382` — Python; im Builder verdrahtet (`src/godsv/toolbuilder.py:354-385`): keine Cases ⇒ Manifest `needs_validation` + Abbruch („before it may be registered“); invalid ⇒ Manifest `failed`, „promotion refused“ — Grenzen: Qualität hängt an den mitgegebenen Cases; **kein Produktionspfad erzeugt Cases** (Lücke §8).

### 3. Scope-System project/global/legacy (§6/§7)

**Scope-Konstanten + Vertragskommentar** — `SCOPE_PROJECT/GLOBAL/LEGACY`; Kommentar: Scope = nur Sichtbarkeit, nie Berechtigung; neue Tools projekt-scoped; Namenskonflikt-Auflösung (project > global, dann höhere Version) und „SURFACED when unresolvable — no silent overwrite“; Records ohne Scope behalten nachweisbaren Kontext und werden nie still promoted. — `src/godsv/capability.py:428-439` — Python.

**`_version_key(value)`** — monotone Ordnung: bool→0, Zahl→int, Semver-String → major*1e6 + minor*1e3 + patch (je Segment nur Ziffern, `% 1000`). — `src/godsv/capability.py:442-454` — Python (resolve_tool_names, load_tool_entries) — Grenzen: Nicht-Ziffern-Segmente werden 0; Werte ≥1000 je Segment werden abgeschnitten.

**`_normalize_remote(url)`** — entfernt `.git`, Schema, `user@`; wandelt scp-Form `host:pfad` in `host/pfad`; lowercased. — `src/godsv/capability.py:457-471` — Python (nur `project_id_for`) — Grenzen: rein stringbasiert (z. B. ssh-Aliasse vs. echte Hosts bleiben verschieden).

**`project_id_for(path)`** — stabile Projekt-ID in drei Stufen: (1) `git -C <pfad> remote get-url origin` → `remote:<normalisiert>` (`:485-491`); (2) `git -C <pfad> rev-parse --git-common-dir` → `gitdir:<abs>` (Worktree und Hauptcheckout teilen sie, `:492-501`); (3) Fallback `path:<resolved>`; alle Aufrufe mit 5 s Timeout und try/except. — `src/godsv/capability.py:474-507` — einziger Produktions-Eintritt: `godsvoice mcp serve --project <pfad>` (`src/godsv/cli.py:720-724`) — Grenzen: **wird im Request-/Build-/Mining-Pfad nirgends aufgerufen**; dadurch bleibt `project_id` überall leer (Lücke §8.1).

**`load_tool_entries(runtime)`** — liest jedes `generated_tools/<dir>/manifest.yaml` und liefert `name, type, status, version (key), version_raw, scope, project_id, path`; Scope-Normalisierung: nur `project`/`global` bleiben, alles andere (inkl. fehlend) ⇒ `legacy`. — `src/godsv/capability.py:510-541` — Python (mcpserver, Panel, Promotion) — Grenzen: konsultiert nur Manifeste, nicht die Registry-Konsistenz; defektes YAML ⇒ still übersprungen.

**`visible_tools(runtime, project_id, include_legacy=False)`** — `global` immer sichtbar; `project` nur bei nicht-leerer, exakt gleicher project_id; `legacy` nur im expliziten „alles“-View. — `src/godsv/capability.py:544-565` — Eintritte: `godsvoice mcp serve` (`src/godsv/cli.py:716-749`; Args `--project`/`--all`/`--runtime` `:1321-1334`), `mcpserver.approved_python_specs` (`src/godsv/mcpserver.py:49-56`) — Grenzen: **project-Tools mit leerem project_id sind in keiner Sicht sichtbar** (nicht global, Match braucht nicht-leere ID, nicht legacy).

**`resolve_tool_names(entries)`** — gruppiert je Name, Rang `(project?1:0, version)` absteigend; eindeutiger Sieger ⇒ Pick, Gleichstand ⇒ `conflicts` (Name erscheint in keinem Pick). — `src/godsv/capability.py:568-598` — Eintritt: MCP-Export (`src/godsv/mcpserver.py:54`, dort `[0]` genommen, Conflicts verworfen) — Grenzen: Konflikt lässt den Namen aus dem Export verschwinden, ohne Meldung an den Aufrufer.

**MCP-Scope-Filter** — `approved_python_specs(store, runtime, project_id, include_legacy)`: erst Status approved + kind python + module/entrypoint (`:30-48`), dann Schnittmenge mit `visible_tools`/`resolve_tool_names` (`:49-56`); Tests: `tests/test_mcp_scope.py` (6 Testfunktionen: global-only ohne Kontext, Projekt+global, kein Leak fremder Projekt-Tools, legacy nur mit `--all`, nicht-approved bleibt raus). — Grenzen: `runtime=None` ⇒ Altverhalten ohne Scope-Filter.

**Panel-Scope-Anzeige** — `toolsScopeCell(scope,row)` (Projekt/Global/Global↑ mit `promoted_from`-Details und fehlenden Blockern), Spalte „Scope“ im Tools-Tab. — `integrations/hermes/godsv_plugin/desktop/plugin.js:836-853`, `:970`; Daten `plugin_api.py:757-765`, `:786-789` — Plugin/HTTP — Grenzen: reine Anzeige, keine Aktion.

### 4. Promotion project → global (§7)

**`promotion_report(runtime)`** — listet nur `scope == project` mit `candidate = keine Blocker`; Blocker derzeit: „no project context recorded“ (project_id leer) und „negative tests missing (validation.status != validated)“. — `src/godsv/capability.py:638-661` — Panel (`plugin_api.py:760-765`), Python — Grenzen: die ≥2-Fremdprojekte-Bedingung wird hier nicht geprüft (erst in `promote_tool`); praktisch ist wegen des leeren project_id jeder gebaute Tool-Kandidat „no project context“.

**`promote_tool(runtime, name, evidence_projects=None, promoted_by="owner")`** — Voraussetzungen: Scope `project`, `validation.status == validated`, **≥2 unabhängige fremde** Projekt-Kontexte (eigene project_id wird gefiltert, Dubletten dedupliziert), alle `required_secrets` als `secret://…`. Bei Erfolg wird das Manifest in-place aktualisiert: `scope: global`, Version via `_bump_version` (MAJOR+1, Familie int/semver beibehalten), `promoted_from{project_id, evidence_projects, at, promoted_by}`; Rückgabe `{promoted, name, previous_version, version}` bzw. `{promoted: False, candidate: True, blockers}`. — `src/godsv/capability.py:664-717`; `_bump_version` `:613-626`; Tests `tests/test_tool_scopes.py:181-277` — **Eintritt: nur Python-API** — Grenzen: **kein CLI-Kommando, keine Panel-Aktion, kein MCP-Pfad**; `tools promote` (CLI) ist die Mining-Promotion, nicht die Scope-Promotion.

### 5. Mining (wiederkehrende Fähigkeiten → Kandidaten)

**`classify_capability(text, kind_hint)`** — explizite Hints gewinnen: `mcp`/`cli` (`:54-55`), `testing`/`testing_tool`/`test` → testing_tool (`:56-57`); dann `_TESTING_HINT` (`:58-59`), `_API_HINT` → mcp (`:60-61`), `_SKILL_HINT` → skill (`:62-63`), sonst cli. Regexe `:33-48`. — `src/godsv/capability_mining.py:51-64` — Python — Grenzen: reine Wortlisten-Heuristik, kein LLM-/Kontextbezug.

**`Candidate` / `CandidateStore`** — Candidate-Felder: capability, occurrences, sessions, current_workaround, suggested_type, expected_benefit, required_secrets, first_seen/last_seen, status, id (`:67-81`). Store: JSONL `capability_candidates.jsonl` (`:22`); `all()` merged je id (nicht-leere Felder überschreiben, occurrences = Maximum) (`:95-117`); `merge()` akkumuliert occurrences/sessions auf einem bestehenden **candidate** gleicher (capability, suggested_type) (`:125-144`); `by_id` (`:119-123`); `set_status(id, status, **extra)` (`:146-154`). Statuswerte candidate/promoted/dismissed (`:24-26`). — `src/godsv/capability_mining.py:67-154` — Python (mine/promote/CLI) — Grenzen: `dismissed` wird nirgends gesetzt; Merge filtert leere Werte (`:109`) — ein bewusst geleertes Feld verschwindet statt zu leeren.

**`mine_from_requests(requests, min_occurrences=2)`** — gruppiert nach normalisierter capability (lowercase, Whitespace kollabiert, 120 Zeichen; `_normalize` `:157-158`, Key `:166`), sammelt occurrences, Session-Set, kinds, reasons (200 Zeichen), nur `secret://`-Secrets, created; ab Schwelle: `suggested_type = classify_capability(" ".join(reasons), kind_hint)` (Hint nur mcp/cli, `:197`), `current_workaround = erste reason`, `expected_benefit = "removes Nx manual re-doing of the same capability"`, `sessions = max(1, |Sessions|)`, first/last_seen. — `src/godsv/capability_mining.py:161-214` — Python, aufgerufen von `mine()` — Grenzen: nur exakte Textgleichheit nach Normalisierung; reasons werden platt zum Klassifikationstext verbunden; „sessions“ zählt nur Sessions mit ID.

**`mine_from_commands(commands, min_occurrences=2)`** — gruppiert rohe Shell-Kommandos (>=6 Zeichen) nach normalisiertem Volltext (160 Zeichen), zählt occurrences/Sessions/created; `suggested = testing_tool`, wenn `_COMMAND_TESTING` (pytest|check_|_check|gv_ui_e2e|smoke|e2e|verify|bandit|ruff, `:45-47`) matcht, sonst cli; `capability = "recurring command: <key>"`. — `src/godsv/capability_mining.py:217-250` — Python, aufgerufen von `mine()` — Grenzen: jede Argument-/Pfadabweichung zerlegt die Gruppe; keine Fehler-/Erfolgssemantik der Kommandos.

**`mine(runtime, min_occurrences=2)`** — scannt `<runtime>/capability_requests.jsonl` und `<runtime>/events.db` (nur Events kind `tool_call`, `args.command` oder `args.argv` als Liste→Text), schreibt die vereinigten Kandidaten in den CandidateStore; Rückgabe `{scanned_requests, scanned_commands, candidates_recorded}`; Event-Lesefehler ⇒ still `commands=[]`. — `src/godsv/capability_mining.py:253-308` — einziger Eintritt: `godsvoice tools mine` (`src/godsv/cli.py:844-853`) — Grenzen: **kein Cron/Hook/Plugin** — Mining läuft nur manuell; `min_occurrences` ist per CLI nicht setzbar (Default 2).

**`promote(runtime, candidate_id, session_id="cli")`** — Kandidat → `CapabilityRequest` (requested_by `godsv-mining`, reason mit Häufigkeit/Sessions, required_secrets übernommen): `skill` wird abgelehnt („skill candidates are promoted through the skill-mining flow“), `testing_tool` bleibt testing_tool, `mcp`→mcp, sonst cli (`:319-329`); Request wird in den CapabilityStore geschrieben, Kandidat auf `promoted` + `request_id` gesetzt; Rückgabe `{candidate, request_id, kind}`. — `src/godsv/capability_mining.py:311-347` — Eintritt: `godsvoice tools promote <candidate_id>` (`src/godsv/cli.py:872-885`) — Grenzen: der genannte „skill-mining flow“ existiert in dieser Datei nicht; testing_tool-Kandidaten sind danach per CLI nicht fulfullbar (Lücke §8.5).

**CLI-Mining-Oberfläche** — `tools mine` (`cli.py:844-853`), `tools candidates` (listet id, status, suggested_type, capability, ×occurrences, Sessions; filtert `dismissed`; `:856-869`), `tools promote <id>` (`:872-885`); Parser `:1298-1308` — CLI — Grenzen: kein dismiss, kein Filter, kein `--json`, keine Anzeige der workaround/expected_benefit-Felder.

### 6. Workflows (§9) — `src/godsv/workflows.py`

**Ergebnisvertrag** — `_base_result`: `run_id, status("RUNNING"), summary, result{}, evidence_refs[], artifact_refs[], warnings[]` (`:35-44`); `_fail` ergänzt `failure_class, failed_stage, attempts(+1), recoverable, recommended_next_action, side_effect_state` und setzt FAILED (`:47-65`); `persist_run` schreibt `runtime/workflow_runs/<run_id>.json` best effort (`:232-241`), `_persist` hängt `run_ref` an (`:498-501`). — Python (alle Workflows) — Grenzen: Statuswerte SUCCEEDED/PARTIAL/FAILED sind Konvention, kein Schema; `run_ref` fehlt, wenn kein runtime übergeben wurde (Screenshot-Pfad fällt dann auf `/tmp` zurück, `:371`, `:457`).

**Substrat** — `cdp_targets(port)` (HTTP `127.0.0.1:<port>/json`, `:71-79`); `cdp_screenshot(port,path)` via websockets `Page.captureScreenshot` PNG base64 (`:82-113`); `_run` (subprocess, 127 bei not found, 124 bei Timeout, `:116-123`); `process_running(pattern)` = `pgrep -f` minus eigene Vorfahrenkette (`:126-146`); `x11_windows()` via `xprop -root _NET_CLIENT_LIST` + `_NET_WM_NAME/WM_NAME/WM_CLASS` (kein wmctrl/xdotool nötig, `:149-168`); `find_window(title_contains, wm_class_contains)` bevorzugt Klassen-Treffer (`:171-182`); `focus_window(xid)` xdotool → wmctrl → python-Xlib, sonst False (deklarierte Umgebungsgrenze, `:185-207`); `screenshot(path,xid)` via ImageMagick `import -window` (ohne Backend präziser Fehler, `:210-218`); `wait_for(predicate,timeout,interval)` (`:221-229`). — Python.

**`open_app_snapshot(app, project_path, start_cmd, cdp_port, ready_timeout=25, runtime)`** — §9-A: (a) bei gesetztem `start_cmd` ohne cdp_port wird die Binary per `shutil.which` geprüft; fehlt sie ⇒ `failure_class="environment"`, kein Auto-Install (`:268-282`); (b) läuft nichts und fehlt `start_cmd` ⇒ environment-Fail (`:284-295`); (c) sonst Start via `start_cmd.split()`, 15 s Timeout, `attempts=1`, Warning `start_cmd ausgeführt (rc=…)`, `side_effect_state="app-started"` (`:296-299`), rc ∉ {0,124} ⇒ application-Fail (`:300-311`); (d) mit `cdp_port`: Warten auf CDP-Ziel, window-Dict aus CDP (title/url/backend `cdp`, kein xid) (`:313-338`); ohne Port: `find_window(wm_class_contains=app | title_contains=app)` (`:339-343`); (e) kein Fenster ⇒ `timeout`-Fail `failed_stage="ready"` **plus** x11-Inventar (max. 6 Fenster) als Evidenz (`:344-357`); (f) Fokus: mit CDP automatisch true, sonst `focus_window(xid)`; ohne Fokus Warnung „deklarierte Umgebungsgrenze“ (`:364-369`); (g) Screenshot via `cdp_screenshot` oder `import`, sonst environment-Fail (`:371-388`); Ergebnis `{app, window, focused, screenshot_backend, project_path}`, Status SUCCEEDED nur bei `focused`, sonst PARTIAL (`:390-401`). — `src/godsv/workflows.py:247-403` — **nur Python-API; kein CLI-/MCP-/Panel-Aufrufer** — Grenzen: `project_path` wird ausschließlich ins Ergebnis kopiert, **es wird kein Projekt geöffnet**; `start_cmd.split()` ist keine Shell (Quotes/Pipes/Env brechen); Titeltrunkierung 120/200 Zeichen (`:358-362`).

**`open_app_settings_snapshot(app, section, start_cmd, settings_keys="ctrl+comma", ready_timeout=25, runtime)`** — §9-B, ändert nichts: Fensterzustand „vorher“ merken (`:423`), `open_app_snapshot` ohne cdp aufrufen und dessen Status prüfen (`:425-433`), Attempts/side_effect/evidence übernehmen (`:434-437`); Navigation nur bei vorhandenem `xdotool`, und dann `xdotool key --window <xid> <settings_keys.split("+")[-1]>` (`:441-443`) — von „ctrl+comma“ bleibt also nur „comma“; ohne xdotool nur Warning „Settings-Navigation nicht ausführbar — Zustand wird nur berichtet“ (`:444-447`); 6 s Warten auf ein Fenster mit Titel „settings“/„einstellungen“ und passender WM_CLASS (`:449-453`); `state_changed = Fenster existiert UND (kein Vorher ODER andere xid ODER anderer Titel)` (`:454-455`); Screenshot via `import` auf Ziel-xid (`:457-474`), sonst environment-Fail; Ergebnis `{app, section, window, state_changed, screenshot_backend}`; SUCCEEDED nur bei `state_changed`, sonst PARTIAL + Warning („Settings nicht als eigener Zustand verifiziert“, `:476-493`). — `src/godsv/workflows.py:406-496` — **nur Python-API** — Grenzen: Navigation faktisch defekt (Key-Token) und ohne xdotool gar nicht möglich; kein CDP/Electron-Pfad; Zustandsnachweis hängt an Titelmatches.

**`_restart_app(stop_pattern, start_cmd, cdp_port, ready_timeout)`** — SIGTERM an die gesammelten PIDs, nach 8 s SIGKILL, dann `Popen(start_cmd.split(), start_new_session=True)` mit stdout/stderr DEVNULL, Readiness über CDP-Target bzw. Prozesspräsenz (`:504-546`). — Python; einziger Nutzer: `verify_setting_persistence` — Grenzen: Muster per `pgrep -f` (breite Muster können fremde Prozesse treffen; ausgeschlossen wird nur die eigene Ahnenkette); kein Sanity-Check, dass `start_cmd` dieselbe App startet.

**`run_model_test(app, model, test_case, expect, cdp_port, session_dir, restore_model=True, answer_timeout=120, runtime)`** — §9-C: ohne `cdp_port` configuration-Fail (`:651-661`); keine CDP-Ziele ⇒ timeout-Fail (`:662-672`); Modellname vorher aus der UI lesen (`MODEL_READER_JS` `:580-586`, Aufruf `:674-675`); bei fehlendem Namen im UI wird nur der Dropdown-Trigger geklickt (kein Menüeintrag, `:676-692`), danach erneut lesen, `selection_verified = model in UI-Text` (`:693-697`); Prompt senden über `SEND_JS_TEMPLATE` (contentEditable `[data-slot=composer-rich-input]` + `document.execCommand('insertText')` + Enter-keydown, `:594-601`, Aufruf `:699-715`), Fehler ⇒ application-Fail; Antwort-Polling 2 s-Intervall via `ANSWER_READER_JS` (`:588-592`, `:717-719`); Ergebnis `{app, model_requested, answer(≤2000), duration_ms, selection_verified}` (`:721-727`); keine Antwort ⇒ timeout-Fail (`:728-737`); sonst Routing-Evidenz: neuester `request_dump_*.json` in `session_dir` (`_latest_routing_evidence` `:603-624`), `routing_verified` nur wenn dessen `model` den angeforderten Namen enthält; Warnungen bei fehlender bzw. abweichender Backend-Evidenz (`:739-752`); `expect` muss case-insensitiv in der Antwort vorkommen (`:753-755`); **SUCCEEDED nur wenn selection_verified UND routing_verified UND expectation_ok**, sonst PARTIAL (`:756-767`) — Vertrag wörtlich: „Dropdown allein beweist kein Routing“. `restore_model`-Zweig (`:768-776`): setzt nur `side_effect_state="model-selection-left"` + Warnung — der JS-Aufruf ist ein Platzhalter-String, **es wird nichts wiederhergestellt**. — `src/godsv/workflows.py:627-778` — **nur Python-API** — Grenzen: Auswahl nur per Trigger-Klick; `session_dir` muss auf Request-Dumps zeigen; kein Restore trotz Docstring-Vertrag.

**`verify_setting_persistence(setting, temporary_value, settings_file, reader, writer, stop_pattern, start_cmd, cdp_port, ready_timeout, runtime)`** — §9-D: YAML-Default-Reader/-Writer (`:808-822`); Original lesen, leer ⇒ Warning „war vorher nicht gesetzt“ (`:827-831`); Testwert schreiben, Fehler ⇒ application-Fail `failed_stage="write"` (`:833-846`), sonst `side_effect_state=setting-changed:<k>=<v>`; kontrollierter `_restart_app` (`:849-863`), Fehler ⇒ timeout-Fail `failed_stage="restart"`; Verify: `persisted = _read(setting) == temporary_value`, und `runtime_value = _read(setting)` stammt **aus derselben Datei** (`:865-873`); persisted ⇒ SUCCEEDED („Testwert überstand den Neustart“), sonst application-Fail `failed_stage="verify"` (`:874-889`); `finally`: Restore **immer** (bei vorher fehlendem Setting wird eine leere Zeichenkette geschrieben, sonst der Originalwert), `restored` nur wenn Re-Read == original (`:890-899`); nicht bestätigt ⇒ Warning mit verbleibendem Zustand, `side_effect_state=setting-left-changed:<k>`, und ein SUCCEEDED wird auf PARTIAL degradiert (`:900-909`). — `src/godsv/workflows.py:781-911` — **nur Python-API** — Grenzen: kein unabhängiger Runtime-Effekt-Nachweis trotz Docstring („persistierten Wert UND Runtime-Effekt prüfen“); `cdp_port` dient nur der Restart-Readiness; Restore über leere Zeichenkette lässt den Schlüssel bestehen.

**`workflow_contracts()`** — statische §6-Kompaktverträge für die vier Workflows (name/scope/purpose/execution/reasoning/inputs/success/side_effects/error_policy): open_app_snapshot global/work/deterministic (`:917-927`), open_app_settings_snapshot global/work/deterministic (`:928-938`), verify_setting_persistence **project**/verification/deterministic (`:939-949`), run_model_test global/verification/**bounded-agent** (`:950-963`). — Python — Grenzen: an keine Registry/keinen Prompt/kein Tool angebunden; einziger Leser: `tests/test_workflows.py:155-162`.

**Workflow-Eintritt insgesamt** — `grep workflow src/godsv/cli.py` = 0 Treffer; kein MCP-Tool, kein Plugin-Hook, keine Panel-Sektion (plugin.js enthält kein „workflow“); Aufrufer sind ausschließlich Tests (`tests/test_workflows.py`, 17 Testfunktionen). **Lücke:** die Workflow-Bibliothek ist fertig, aber an kein Produktsurface angeschlossen.

### 7. Computer Use (§5/§22) — `src/godsv/computer_use.py`

**Kanal-Ordnung und Typen** — `API/DOM/VISION`, `CHANNEL_ORDER = (api, dom, vision)` (`:41-44`); `GUI_KINDS = {mouse, key, type_text, dom_click, dom_fill}`, `ALL_KINDS = {cli, api_call} ∪ GUI_KINDS` (`:46-47`); `PolicyViolation` (`:50-51`); Dataclasses `Action(kind, argv, payload, description)` (`:55-59`), `Verification(expect_stdout_contains, expect_observe_contains)` mit `is_empty()` (`:63-69`), `Attempt(channel, action, verify)` (`:73-76`), `ActionResult(ok, stdout, detail)` (`:80-83`), `Observation(text, detail)` (`:87-89`), `CURunResult(step, status, channel, actions, fallbacks, detail)` (`:108-114`); Runner-Protokoll `isolation/is_isolated/act/observe/close` (`:118-128`), `BaseRunner` mit `isolation="none"` (`:131-145`). — Python.

**`order_attempts(attempts)`** — stabile Sortierung nach Kanalrang API(0) > DOM(1) > Vision(2); unbekannte Kanäle ans Ende. — `src/godsv/computer_use.py:92-95` — Python — Grenzen: sortiert nur, entfernt keine Duplikate.

**`CUPolicy`** — Defaults: `allowed_kinds` = alle 7 (sortiert), `max_actions=20`, `max_fallbacks=3`, `require_isolation=True`, `dry_run=False`. — `src/godsv/computer_use.py:99-104` — Python; Panel zeigt die Policy (`plugin_api.py:828-834`).

**`DryRunRunner`** — deterministisch, keine Seiteneffekte: protokolliert Aktionen, liefert vorbereitete `behaviors` (per `action.description` oder `action.kind`), sonst `ActionResult(ok=True, stdout="dry-run")`; Beobachtungen aus Skriptliste (erste bleibt nach Aufbrauchen stehen). — `src/godsv/computer_use.py:148-173` — Eintritte: CLI `cu demo` (`src/godsv/cli.py:895-932`), Panel `cu_demo_payload` (`plugin_api.py:839-892`), Tests — Grenzen: führt keinerlei echte Aktion aus.

**`LocalSandboxRunner`** — CLI-/API-Aktionen in bwrap über `gates._run` (Netz-NS aus, root read-only); nur kind `cli`/`api_call` mit argv; `observe()` listet das Workdir per `python -c`. — `src/godsv/computer_use.py:176-202` — **kein Produktionsaufrufer** (nur Tests) — Grenzen: GUI-Kinds explizit abgelehnt („nicht unterstützt“); `workdir` muss absolut sein (bwrap-Bind).

**`ContainerRunner`** — rootless podman, `--rm`, `--network none` (Default), `no-new-privileges`, Default-Image `localhost/godsv-cu-x11:latest` (Xvfb auf :90, 800x600; Start `:252-286`); `available()` prüft nur podman + Image (`:244-250`); `_default_argv` übersetzt mouse→`xdotool mousemove x y [click n]`, key→`xdotool key <name>`, type_text→`xdotool type -- <text>`, sonst argv zwingend (`:288-304`); `act` verweigert GUI-Kinds ohne `gui=True` und schickt den Rest per `podman exec` (DISPLAY env wenn gui) (`:306-320`); `observe` = Fensternamen per xdotool bzw. `ls -la` (`:322-346`); `close` = `podman rm -f` (`:348-351`). — `src/godsv/computer_use.py:215-351` — **kein Produktionsaufrufer**; `runner_availability()` probt nur die Verfügbarkeit — Grenzen: Image muss lokal vorhanden sein; GUI-Pfad wird außerhalb der Tests (`tests/test_computer_use.py:231-298`) nicht gefahren.

**`CUExecutor`** — `run(step, attempts, runner)`: erst `_check_policy` (`:375-394`) — isolierter Runner zwingend (`require_isolation`), bei `dry_run`-Policy nur der DryRunRunner, nicht erlaubte Kinds und **Versuche ohne Verifikation** werden mit Event `cu_blocked` abgelehnt; danach `order_attempts` (`:398`); `max_actions`-Überschreitung ⇒ Event `cu_aborted` + Abbruch (`:402-405`); je Versuch Events `cu_attempt` (`:406-414`) und `cu_action` (`:417-426`); `_verify_attempt` prüft `expect_stdout_contains` im stdout und/oder `expect_observe_contains` in einer frischen Beobachtung (`:450-458`); Erfolg ⇒ Event `cu_verified` + `CURunResult("verified", channel)` (`:427-433`); Misserfolg ⇒ `cu_failed`, Fallback-Zähler, Abbruch nach `max_fallbacks` (`:434-447`); alle Events via `EventStore.append(session_id, step, kind, payload)` (`:369-373`). Die Event-Kinds sind in `events.KINDS` registriert (`src/godsv/events.py:39-44`; `cu_blocked`/`cu_failed` zusätzlich im Chat-Feed `:134-135`). — `src/godsv/computer_use.py:354-458` — Python (CLI-Demo, Panel-Demo, Tests) — Grenzen: ohne übergebenen `EventStore` wird nichts auditiert; kein eigener Retry-/Timeout-Begriff außer max_actions/max_fallbacks.

**`runner_availability()`** — Probe-Report: dry-run „verfügbar“; `sandbox (bwrap)` via `gates._sandbox_available()`; `container (podman)` mit alpine-Image; `container GUI (godsv-cu-x11)` mit Default-Image; fehlt podman, endet der Report dort. — `src/godsv/computer_use.py:461-477` — Eintritte: CLI `godsvoice cu check` (`src/godsv/cli.py:888-892`), Panel `GET /cu` (`plugin_api.py:815-836`) — Grenzen: reine Verfügbarkeitsprobe.

**`plan_cu_attempts(client, registry, goal, observations, channels, budget)`** — LLM-Entwurf geordneter Versuche über den Prompt `cu_step_plan` (`run_prompt` mit goal/observations/available_channels), max. 4 Einträge (`:507`), unbekannte Kanäle verworfen (`:511-512`), Verifikationen aus `expect_stdout_contains`/`expect_observe_contains`, Ergebnis über `order_attempts`; jede Exception ⇒ None. — `src/godsv/computer_use.py:480-531` — **kein Produktionsaufrufer** (nur Tests `tests/test_computer_use.py:328-337`) — Grenzen: **der modellgetriebene CU-Loop existiert nur als Planfunktion**; Ausführung/Anbindung an den Executor fehlt.

**CU-Produktsurfaces** — CLI: `godsvoice cu check` und `godsvoice cu demo` (Parser `src/godsv/cli.py:1336-1341`; Imports `:18-30`); Panel: `GET /cu` (Runners + Policy + letzte Dry-Runs aus `cu_runs.jsonl`), `POST /cu/demo` (läuft den echten Executor mit DryRunRunner, schreibt `cu_runs.jsonl`; `plugin_api.py:815-892`, Routen `:1726-1736`); UI-Tab „Computer Use“ (`integrations/hermes/godsv_plugin/desktop/plugin.js:75`, `:1168-1177`), Event-Labels `:2123`, `:2157-2158`. — Grenzen: produktiv ist nur der Dry-Run-Demo-Pfad; keine echten Desktop-Aktionen, kein MCP-Export von CU-Aktionen.

### 8. Explizite Lücken (im Code nicht existent)

1. **project_id wird nie gesetzt**: `tools request` hat kein `--project` (`src/godsv/cli.py:1272-1291`), der Plugin-Gap-Signal-Bau setzt es ebenfalls nicht (`integrations/hermes/godsv_plugin/__init__.py:487-517`), Mining-Promotion setzt es nicht (`src/godsv/capability_mining.py:330-340`). Folge: gebaute Tools bekommen `scope: project` + `project_id: ""` (`src/godsv/toolbuilder.py:270-271`) und sind in `visible_tools` in **keiner** Sicht sichtbar — nicht global, Projekt-Match verlangt nicht-leere ID (`src/godsv/capability.py:558-562`), legacy nur bei fehlendem Scope (`:528-536`, `:563-564`). Sichtbar bleiben sie nur in `tools.db`/Panel.
2. **Kein Eintritt für Scope-Promotion**: `promote_tool` (`src/godsv/capability.py:664-717`) ist ausschließlich Python-API; Panel zeigt `promotion_report` nur lesend (`plugin_api.py:760-765`, `plugin.js:836-853`); `tools promote` (CLI) ist Mining-Promotion.
3. **Mining läuft nie automatisch**: nur manuell über `godsvoice tools mine` (`src/godsv/cli.py:844-853`); kein Cron, kein Plugin-Hook, kein Panel-Button; `min_occurrences` nicht parametrisierbar.
4. **`dismissed` ist toter Status**: definiert (`capability_mining.py:26`), gefiltert (`cli.py:859`), aber kein Aufruf von `set_status(..., "dismissed")` — kein dismiss-Kommando/Aktion vorhanden.
5. **Testing-Tool-Kandidaten sind per CLI nicht baubar**: `promote` erzeugt `kind=testing_tool` (`capability_mining.py:324-329`), aber weder `tools request` noch `tools build` noch `tools fulfill` können `validation_cases` liefern (Parser `cli.py:1244-1268`, `:1272-1291`); der Builder verlangt sie (`src/godsv/toolbuilder.py:341-353`, Manifest `needs_validation`) — ein `tools fulfill` solcher Requests endet deterministisch im Fehlschlag.
6. **Workflows ohne Surface**: kein CLI-Kommando, kein MCP-Tool, keine Panel-Sektion, kein Builder-Aufruf; `workflow_contracts()` wird von nichts außer Tests gelesen.
7. **`open_app_snapshot` öffnet kein Projekt**: `project_path` wird nur ins Ergebnis kopiert (`src/godsv/workflows.py:390-396`); kein Aufruf, kein Tastatur-/CDP-Weg dafür (Docstring spricht von „optional Projekt öffnen“).
8. **Settings-Navigation defekt**: `settings_keys.split("+")[-1]` sendet von „ctrl+comma“ nur „comma“ (`src/godsv/workflows.py:443`); ohne xdotool wird gar nicht navigiert (nur Warning).
9. **`verify_setting_persistence` prüft keinen Runtime-Effekt**: `runtime_value` und `persisted` stammen aus demselben Datei-Read (`src/godsv/workflows.py:865-866`); `cdp_port` dient nur der Restart-Readiness; Restore schreibt bei vorher fehlendem Setting `""` statt den Schlüssel zu entfernen (`:893-896`).
10. **`run_model_test` stellt das Ausgangsmodell nicht wieder her**: `restore_model`-Block setzt nur `side_effect_state="model-selection-left"` + Warning (`src/godsv/workflows.py:768-776`); der Vertragstext „Temporäre Änderungen werden wiederhergestellt“ ist nicht implementiert.
11. **CU ohne echten Ausführungspfad**: `LocalSandboxRunner`, `ContainerRunner` (inkl. GUI-Image) und `plan_cu_attempts` haben keine Produktionsaufrufer; produktiv nur Dry-Run-Demos (CLI `cu demo`, Panel `/cu/demo`) und `cu check`; kein MCP-Export von CU-Aktionen.
12. **`tools list` zeigt keinen Scope**: nur Status/Name/Kind/Titel (`src/godsv/cli.py:583-593`); Scope/Version/project_id nur im Panel bzw. als Notiz bei `mcp serve` (`:735-741`).
13. **MCP-Export verwirft Konflikte**: `resolve_tool_names(...)[0]` (`src/godsv/mcpserver.py:54`) — gleichrangige Namensdubletten lassen den Namen stillschweigend aus dem Export verschwinden.
14. **Mining-Gruppierung ist stringexakt**: Kommandos (`capability_mining.py:224`) und Capability-Texte (`:166`) matchen nur nach Normalisierung auf Zeichenebene; ein Event-DB-Lesefehler degradiert still auf 0 Kommandos (`:289-290`).
15. **Falscher Nutzungstext im Redirect**: `testing_tool`-Tools werden als MCP-Server-Nutzung beschrieben (`src/godsv/toolbuilder.py:463-467`).
16. **Kanal-Inkonsistenz**: `CapabilityRequest`/`to_tool_request` kennen `testing_tool` (`src/godsv/capability.py:70`), die CLI-Anforderung nur `auto|mcp|cli` (`src/godsv/cli.py:1279`).
17. **Kein Skill-Zweig im Mining-Promote**: `promote` verweist auf einen „skill-mining flow“ (`capability_mining.py:320-323`), der in dieser Datei nicht existiert (Skills liegen in `src/godsv/skills.py` mit eigenem CLI-Zweig `skills mine/promote`).

### 9. Fundstellen der Tests (nur gelesen, nicht ausgeführt)

`tests/test_capability_flow.py` 9 Testfunktionen · `tests/test_capability_mining.py` 10 · `tests/test_tool_scopes.py` 23 · `tests/test_testing_tool_gate.py` 4 · `tests/test_testing_tool_stats.py` 3 · `tests/test_workflows.py` 17 · `tests/test_computer_use.py` 23 · `tests/test_mcp_scope.py` 6.

---

## 5. Completion / Verifikation / Pact / Metriken / Eval

### Completion / Verifikation / Pact / Metriken / Eval — code-verifizierte Landkarte (`/home/willy/projects/gods-voice`)

Quelle: ausschließlich gelesener Code (docs/ ist unverbindlich). Pfade relativ zum Repo-Root. Format: **Name** — Beschreibung — `Datei:Zeile` — Eintrittspunkt — Grenzen/Lücken.

### A. `src/godsv/completion.py` (1499 Z.) — Supervisor: Vertrag, Store, Verdikt, Gates

### A1. Datenmodell

**Zustandskonstanten + `STATES`** — elf Zustände `WORKING, COMPLETION_REQUESTED, VERIFYING, INCOMPLETE, REDIRECT, VERIFIED_COMPLETE, BLOCKED_USER, BLOCKED_EXTERNAL, FAILED_UNRECOVERABLE, USER_CANCELLED, SHIPPED_WITH_USER_OVERRIDE`; `transition()` validiert den Zielzustand dagegen. — `src/godsv/completion.py:32-56` — Eintritt: `CompletionStore.transition` (:346), persistiert in `<runtime>/completion/<sid>.json`. — Grenzen: `COMPLETION_REQUESTED` wird nur in Tests gesetzt, `VERIFYING`/`REDIRECT` von keinem Produktivpfad — `verify()` schreibt nur `INCOMPLETE` oder einen Terminalstatus (:1201-1205); das Zustandsbild im Modulkopf ist idealisiert.

**`TERMINAL_STATES`** — `(VERIFIED_COMPLETE, BLOCKED_USER, BLOCKED_EXTERNAL, FAILED_UNRECOVERABLE, USER_CANCELLED, SHIPPED_WITH_USER_OVERRIDE)`; `INCOMPLETE` ist ausdrücklich nicht terminal (Schleife läuft weiter); `VerificationResult.terminal` als Property. — `:58-65`, `:172-174` — Eintritt: `transition` verweigert jeden Nicht-`WORKING`-Wechsel aus einem Terminalzustand (:350-353); zusätzlich prüfen `set_goal` (:452), `add_criteria` (:484), `_criterion_edit` (:545), `record_trajectory` (:419) selbst. — Grenzen: nur ein neues `WORKING` (neues Ziel) öffnet einen terminalen Vertrag; kein Timeout, kein Übergang `INCOMPLETE → WORKING` außer neuem Ziel.

**`DodItem`** — Definition-of-Done-Kriterium: `id, text, blocking=True, verification=''` (kanonische Check-ID), `status='open'`. — `:75-83` — Eintritt: `begin_goal` (:363, `asdict`-Serialisierung), Plugin `_dod_from_store` (Plugin :857) / `_dod_from_pact` (:872). — Grenzen: `status` wird nur als `'open'` geschrieben (:459, :510); `gate_dod` liest `'verified'` (:1074) — nichts im Repo schreibt es je (grep-verifiziert) → Kriterien-Status kann nie hochzählen.

**`CompletionRequest`** — Claim-Container (§13): `user_goal, claimed_completed, remaining, implementation_summary, files_changed, tests_run, test_results, build_results, runtime_checks, ui_checks, persistence_checks, regression_checks, security_checks, known_limitations, unresolved_questions, attempt_id, created`. — `:86-106` — Eintritt: Plugin `_strict_completion` (Plugin :978-984), Panel `completion_verify_payload` (plugin_api.py:1294-1300). — Grenzen: außer `claimed_completed`/`attempt_id` liest kein Gate ein Feld (grep: nur :836, :842, :845, :851, :1170-1171) — `tests_run/files_changed/build_results` sind reine Historie.

**`RunEvidence`** — tatsächlich ausgeführter Check: `name, cmd, ok, detail, ts, status` (passed|failed|unknown), `check_id`, `source` (`runner`|`events`), `tool_call_id`, `revision`; `passed`-Property = nur `status==passed` (Legacy-Fallback `ok`), `fresh(after)`. — `:109-143` — Eintritt: `run_evidence_from_events` (:1381-1393), `execute_check` (:1466), `run_requirement_checks` (:1494). — Grenzen: `revision` (:133) wird von keinem Produzenten gesetzt und von keinem Gate gelesen → Evidenz ist nicht an die Vertragsrevision gebunden.

**`Finding`** — `gate, severity (BLOCKER|MAJOR|MINOR|INFO), detail, requirement, evidence`; Sortierung über `_SEVERITY_ORDER`. — `:72`, `:146-152`.

**`VerificationResult` + `redirect_md()`** — §28-Verdikt: `status, verified_requirements, blocking_failures, major_failures, minor_findings, evidence, next_actions, fingerprint, repeat_count, escalate, created`; `redirect_md()` erzeugt den Redirect (max. 5 Blocker / 3 Majors / 5 Aktionen, plus Hinweis auf Statuszeile `BLOCKED_USER:` und Live-Block-Marker `::godsv-live{stage=working}`). — `:155-212` — Eintritt: `GateRunner.verify` (:1168-1184); Plugin gibt das als `{'action':'continue','message':redirect_md()}` zurück (Plugin :1132-1135). — Grenzen: `verified_requirements` sind die Claim-Sätze, nicht die DoD-Kriterien; `evidence` ist Textarray `name: ok|failed (cmd)` ohne Zuordnung Kriterium→Beleg.

**`fingerprint_for(failures)`** — SHA256[:16] über sortierte Blocker-/Major-Texte = stabile Ablehnungsidentität (§31). — `:215-218` — Eintritt: `verify` (:1182), `rejection_attempts` (:681-689). — Grenzen: nur Blocker+Majors; kleine Textänderung ändert den Fingerprint.

**`plan_verification(text, tool_names)`** — leitet die Check-ID ab: via `guard.criterion_categories` (Regex-Tabelle `_CRITERION_CATEGORY`, guard.py:250-263) `tests`→`pytest`, `ui`→`ui_e2e`, `api`→`api`; Substring `restart|persist`→`persistence_restart`; sonst erster registrierter Testing-Tool-Name als Wort im Text; `''` = Plan fehlt (wird gemeldet statt still bestanden). — `:221-251` — Eintritt: `add_criteria` (:507-509), `update_criterion` (:592-594), Panel `_verification_for` (plugin_api.py:1095-1114, Fallback `run`). — Grenzen: reine Regex-/Substring-Heuristik; die IDs `ui_e2e/api/build/persistence_restart/run` haben keinen eingebauten Runner (s. A4) → strukturell unzertifizierbar.

**`_registered_check_names(runtime)`** — lazy, fehlertolerant: `capability.register_testing_tools(runtime)` → Namen validierter generierter Testing-Tools (Exception → `[]`). — `:254-261` — Eintritt: `add_criteria`, `update_criterion`.

**`CriterionConflict`** — `ValueError`-Subklasse mit `current_revision` = stale `expected_revision`. — `:264-269` — Eintritt: `_criterion_edit` (:552); Panel-Route `/completion/criteria` mappt auf HTTP 409 (plugin_api.py:1788-1797).

### A2. Store `CompletionStore` (:272-715)

**Session-Scoping/Pfade** — Mit `session_id`: `<runtime>/completion/<safe>.json` + `.jsonl`; ids mit Präfix `prodtest-` landen in `<runtime>/completion/_tests/` (E2E-Isolation); ohne id: Legacy `<runtime>/completion_state.json` / `completion_history.jsonl`. — `:275-298` — Eintritt: Plugin `_completion_store` (Plugin :746-749), Panel `_completion_store` (plugin_api.py:1168-1180), CLI (cli.py:760/778/799/825/838 — ohne session_id). — Grenzen: CLI und Produktivpfad schreiben verschiedene Dateien; die CLI kennt keinen Session-Schalter (Parser :1343-1364) → zeigt typisch nicht den Live-Chat-Vertrag.

**Legacy-Migration** — adoptiert `completion_state.json` nur, wenn deren `session_id` exakt passt (kein Cross-Session-Leak). — `:302-324` — Eintritt: `state()` (:328).

**`state()`** — liest die Zustandsdatei; fehlend/kaputt → Default `WORKING`/leeres Ziel. — `:326-344` — Eintritt: alle Leser. — Grenzen: 'kaputt' und 'kein Vertrag' sind ununterscheidbar.

**`transition(state, **extra)`** — Zustandsvalidierung, Terminal-Sperre (:350-353), Merge über das Alt-Record (Ziel/DoD/Session überleben), Write + `{'kind':'state', …}` ins JSONL. — `:346-361` — Eintritt: `begin_goal`, `set_goal`, `add_criteria`, `_criterion_edit`, `verify`, CLI `override`, Plugin-Marker. — Grenzen: kein Lock — parallele Panel-/Plugin-Schreiber überschreiben sich (last write wins).

**`begin_goal(session_id, goal, dod, task_base, non_goals, constraints)`** — frischer Vertrag: `revision=1`, `started`, `task_base` (Diff-Basis), DoD serialisiert, optionale Non-Goals/Constraints mit Zeitstempel; Session-Mismatch → ValueError. — `:363-397` — Eintritt: Plugin `_contract` (Plugin :852-854), Plugin-Chat-`@goal` (Plugin :775, mit leerer DoD), Panel `completion_goal_payload` (plugin_api.py:1346-1352), CLI `completion begin` (cli.py:826). — Grenzen: die CLI-Variante erzeugt `DodItem` ohne Verifikationsplan (cli.py:823) → sofort 'no verification plan'; ein neues Ziel überschreibt ein laufendes `WORKING`-Record kommentarlos.

**`record_trajectory(kind, text, reason)`** — trägt decision|assumption|non_goal am Vertrag nach, ohne Revisions-Bump; terminaler Vertrag verweigert. — `:399-441` — Eintritt: Panel `completion_trajectory_payload` (plugin_api.py:1448-1455), Route `POST /completion/trajectory`; Intake-UI `!decision: …`/`!assumption: …`/`!non-goal: …` im Composer (desktop/plugin.js:2040). — Grenzen: kein Gate liest die Einträge; nur drei kinds; keine Inhaltsprüfung.

**`set_goal(goal)`** — ersetzt den Zieltext; setzt alle DoD-Positionen auf `status='open'` und erhöht `revision` um 1 — alte Nachweise verlieren formal die Gültigkeit. — `:443-472` — Eintritt: Panel `goal_set_payload` (plugin_api.py:1508-1515), Route `POST /goal`. — Grenzen: löscht/entwertet keine Evidenz; Gates prüfen keinen Revisionsbezug.

**`add_criteria(criteria)`** — `@goal`-Kriterien: case-insensitive dedupliziert, `c<n>`-IDs fortlaufend, je Kriterium `plan_verification` (leer = explizit), `blocking=True`, Revision++ + History. — `:474-531` — Eintritt: Panel `criteria_op_payload` op=add (plugin_api.py:1476), Route `/completion/criteria`. — Grenzen: alles blockierend; keine Obergrenze; keine Änderung ohne neue Kriterien (kein Bump).

**`_criterion_edit(…)`** — gemeinsamer Pfad: `request_id`-Idempotenz (`replayed: True` ohne Mutation, rollende 50er-Liste `applied_requests`), Terminal-Sperre, Revisionszaun (`expected_revision != current` → `CriterionConflict`), sonst Mutation + Revision++. — `:533-567` — Eintritt: `update_criterion`/`remove_criterion` ← Panel `criteria_op_payload` (plugin_api.py:1460-1497). — Grenzen: Zaun nur hier; 50er-Fenster für Idempotenz.

**`update_criterion(cid, text=, blocking=)`** — editiert ein Kriterium; `text` leitet den Verifikationsplan neu ab; `status` ist bewusst nicht editierbar (gehört der Evidenz). — `:569-600` — Eintritt: Panel-Route + Cockpit-Editor. — Grenzen: kein Reorder, keine Teil-Historie.

**`remove_criterion(cid)`** — entfernt ein Kriterium (Evidenz bleibt im Event-Store). — `:602-616` — Eintritt: Panel-Route. — Grenzen: kein Soft-Delete/Undo.

**`sessions()` / `latest()`** — alle Session-Verträge (id, state, goal, updated, revision) neueste zuerst / Store der neuesten. — `:618-654` — Eintritt: Panel-Übersicht, Default-Session für den Event-Feed (plugin_api.py:1418-1426). — Grenzen: `_tests/` bewusst ausgeblendet; gelöschte Sessions bleiben als Dateien liegen.

**History: `_append`, `record_request`, `record_result`, `timeline(limit)`** — Append-only JSONL; `timeline` liest die ganze Datei und liefert die letzten N Zeilen. — `:658-679` — Eintritt: CLI/Plugin/Panel-Historien. — Grenzen: keine Rotation, Datei wächst unbegrenzt; jedes `timeline` parst alles.

**`rejection_attempts(fingerprint)`** — zählt `verification`-Rows mit `status==INCOMPLETE` und gleichem Fingerprint in den letzten 500 Zeilen. — `:681-689` — Eintritt: `verify` (:1189). — Grenzen: 500er-Fenster.

**`CompletionStore.metrics()`** — `completion_requests`, `completion_rejections`, `verified_completions`, `false_completion_prevented` (= Anzahl Ablehnungen), `attempts_until_verified` (rejections+verified, sonst None), `requirements_verified` (Summe `verified_requirements` der grünen Rows). — `:693-715` — Eintritt: CLI `completion status` (cli.py:786-792), Panel `completion_payload['metrics']` (plugin_api.py:1248). — Grenzen: `false_completion_prevented` ist identisch mit den Rejections; liest max. 1000 Zeilen.

### A3. Repo-Fakten & Gates (`GateRunner` :815-1206)

**`RepoFacts.collect(root, base)`** — echter Git-Zustand: `diff --name-only <base|HEAD>`, voller Diff (bei `base` zusätzlich `diff HEAD` für committete Task-Änderungen), `status --porcelain` (staged-new `A `/`AM`), `ls-files --others` für untracked (Verzeichnis-Kollaps umgangen); Untracked zählt als geändert; Exception → `unreadable=True`, leere Listen. — `:730-812` — Eintritt: `verify` (:1132, `base=task_base`). — Grenzen: `unreadable` liest kein Gate (grep-verifiziert) → ein kaputter Git-Read wirkt wie 'keine Änderungen'; nur Dateiendungen, nur git.

**`gate_evidence`** — Claim ohne Evidenz → BLOCKER; nur fehlgeschlagene Checks → BLOCKER. — `:831-854`. — Grenzen: keine Zuordnung Claim→Beleg.

**`gate_tests`** — Code-Datei (`.py .js .mjs .cjs .ts .tsx`) geändert und kein `source==runner`-Testlauf → BLOCKER; ein Fehllauf bleibt Blocker, bis ein späterer grüner Lauf (gleiche check_id) ihn schlägt. — `:856-899`. — Grenzen: Runner-Pflicht (Event-Evidenz genügt nicht); check_ids nur `pytest|tests|test`.

**`gate_build`** — Build-relevante Dateien (`package.json`, `pyproject.toml`, `electron-builder.yml`, Pfad mit `desktop/`) ohne Build-Lauf → MAJOR; fehlgeschlagener Build → BLOCKER. — `:901-933` — Grenzen: Suffix-Heuristik.

**`gate_integration`** — jede neue `src/godsv/*.py`-Datei muss in `src`/`tests` per `git grep -l` vorkommen, sonst BLOCKER 'dead component'; `__init__`/`__main__` ausgenommen. — `:935-964` — Grenzen: nur `src/godsv/`-Präfix; grep-Fehler → Gate fällt aus (Pfad zählt als Treffer).

**`gate_fake_completion`** — Scan hinzugefügter Diff-Zeilen: `TODO|FIXME|XXX` → MAJOR; `placeholder|fake data|hardcoded demo|dummy data` → BLOCKER; pytest-Skip/xfail-Marker → MAJOR. — `:966-1004`, Regexe `:720-727`. — Grenzen: nur `+`-Zeilen und diese Muster.

**`gate_security`** — `secretscan.redact` über den Diff, Treffer (max. 5) → BLOCKER. — `:1006-1024`. — Grenzen: `except: pass` → Scanner-Fehler lässt das Gate still bestehen (fail-open).

**`gate_cleanup`** — in geänderten `src/`-Zeilen: Debug-`print(` oder hartkodierter `/tmp/`-Pfad → MINOR. — `:1026-1057`. — Grenzen: MINOR blockiert nie; `_SILENT_EXCEPT` (:725) ist definiert, aber von keinem Gate des Moduls verwendet.

**`gate_dod`** — pro Kriterium: `status=='verified'` → skip (nie erreichbar, s. DodItem); leeres `verification` → Finding 'no verification plan' (BLOCKER bei `blocking=True`, sonst MINOR); sonst müssen Runner-Evidenzen der Check-ID existieren und dürfen nicht fehlschlagen; event-only Evidenz → Blocker 'needs a runner-executed check'. — `:1059-1121`. — Grenzen: kein Verfall/Revisionsbezug; Plan-IDs ohne Runner sind eingebaut unerfüllbar.

**`verify(request, evidence, store=)`** — Pipeline: Fakten → 8 Gates → Hollow-Green-Regel (keine blockierenden Kriterien UND kein bestandener Check → BLOCKER 'nothing was verified', :1147-1160) → Severity-Sortierung → `VERIFIED_COMPLETE` nur ohne Blocker und Majors, sonst `INCOMPLETE` → mit Store: Request/Result protokollieren, Repeat-Zähler, `escalate` (repeat>=1 → 'STOP repeating the same fix'), Zustandsübergang (`INCOMPLETE` oder Terminalstatus). — `:1125-1206` — Eintritt: Plugin :993, Panel plugin_api.py:1313, `tools/gv_eval_longhorizon.py:122-132` (nur `execute_check`). — Grenzen: kein Revisionsabgleich; MINOR/INFO blockieren nie; `verified_requirements` sind Claims.

**`_next_actions(…)`** — Gate-Name → Handlungsanweisung (Tests/Build/Integration/Marker/Secret/DoD/Evidenz; sonst Standard). — `:1209-1228` — Grenzen: statisch, eine Zeile je Gate-Gruppe.

**`_result_status(payload)`** — einzige Ausgangslogik: `error` → failed; `exit_code` → passed/failed; Status-Whitelists (`ok|passed|success|succeeded|completed|done` bzw. `error|failed|failure|cancelled|canceled|aborted|timeout`); `ok`-Flag; sonst `unknown`. — `:1231-1257` — Eintritt: `run_evidence_from_events`. — Grenzen: Feld-Heuristik; `unknown` zählt nie als bestanden.

**`run_evidence_from_events(events)`** — Evidenz aus echten Kommando-Ausführungen: nur `tool_call`s mit `command|argv|cmd`; Check-Vorschlag (build-Regex→`build`, `TEST_HINT`→`pytest`, `VISUAL_HINT`→`ui_e2e`, `API_HINT`→`api`, sonst `run`); Pairing Call↔Result zuerst über `tool_call_id`, sonst FIFO je Tool-Name; Freshness: nur Results mit `step > letzter file_edit`; Status via `_result_status`; `source='events'`. — `:1260-1394` — Eintritt: Plugin `_run_evidence` (Plugin :936-943), Panel (plugin_api.py:1284). — Grenzen: kann kein Kriterium zertifizieren (by design); die Freshness-Kopplung an `file_edit` ist die bekannte Phantom-Edit-Falle; Check-IDs entstehen aus dem Kommandotext, nicht aus dem Vertrag.

### A4. Runner-Schicht

**`register_check(check_id, runner)` + `_CUSTOM_RUNNERS`** — Registry für Custom-Runner (`callable(repo_root) -> RunEvidence`). — `:1403-1408` — Eintritt: `capability.register_testing_tools` (capability.py:341-372; Runner-Fabrik `_testing_tool_runner` capability.py:289).

**`_run_command(argv, cwd, timeout)`** — subprocess ohne Shell, Output-Tail 600 Z.; Timeout/`OSError` → `(None, Meldung)`. — `:1411-1428`.

**`execute_check(check_id, repo_root, timeout=600)`** — Custom-Runner (Exception → `status=failed`, 'runner error', source=runner) oder eingebaut nur `pytest|tests|test` (Projekt-Venv `.venv/bin/python`, sonst `sys.executable`; `-m pytest -q`); alles andere → None. — `:1431-1476` — Eintritt: Plugin :988-991 (timeout=300), Panel plugin_api.py:1291-1293, `tools/gv_eval_longhorizon.py:125-132`. — Grenzen: kein `ui_e2e|api|build|persistence_restart|run|app_build`-Runner; Custom-Runner laufen ungesandboxt im Aufruferprozess.

**`run_requirement_checks(dod, repo_root, timeout=600)`** — führt jede im DoD deklarierte Check-ID genau einmal aus (Dicts oder `DodItem`), überspringt nicht-ausführbare still. — `:1479-1499` — Eintritt: Plugin/Panel wie oben. — Grenzen: keine Rückmeldung, welches Kriterium übersprungen wurde (nur `gate_dod` via leerem Plan).

### A5. Externe Eintrittspunkte (Kurzreferenz)

**Plugin `_strict_completion`** — baut Request + Evidenz (Events + `run_requirement_checks(…, timeout=300)`), ruft `GateRunner.verify` (Plugin :945-1010, Request :978, verify :993); BLOCKED-Marker nur zusammen mit Claims. — Eintritt: Hook `pre_verify` (Plugin :1602-1618) → `guard_pre_verify` (:1083-1151): Nicht-Terminal → `{'action':'continue','message':redirect_md()}`, Shadow steuert nie, Nudge-Cap 40 (:744), Checker-Crash → fail-closed. — Grenzen: steerend nur bei `GODSV_MODE=whisper`; Cap 40 = Deadlock-Schutz, kein 'verified'.

**Plugin `guard_final`** — transform_llm_output-Footer bei `INCOMPLETE`/Crash ('not independently verified'). — Plugin :1153-1189, Registrierung :1764-1765.

**Plugin `contract_context` (pre_llm_call)** — `@goal <text>` → `begin_goal(self.session_id, goal, [])` (Plugin :766-775; leere DoD — Kriterien kommen nur über Pact-Import oder Panel-Route); liefert den Vertragsblock (`godsv·contract`, `godsv·dod: x/y`, Blocker, Trajektorie, Live-Block-Hinweis; Plugin :779-836); die DoD-Zeile zählt `status != 'open'` und steht damit dauerhaft auf 0/N (Plugin :786-788). Live-Block-Stage mappt Zustand→`working|verifying|rejected|verified` (Plugin :1046-1076). — Plugin :753-836, :1655-1671.

**Panel-Routen** — `GET /completion` (dod, revision, trajectory, last_verdict, gates, metrics; plugin_api.py:1183-1249), `POST /completion/verify` (echte Checks + GateRunner + Verdikt, :1255-1315), `POST /completion/override` (nur mit `reason` → `SHIPPED_WITH_USER_OVERRIDE`, :1317-1325), `POST /completion/goal` (:1328-1352), `POST /completion/trajectory` (:1448-1456), `POST /completion/criteria` (409 bei Conflict, :1460-1504/:1788-1797), `POST /goal` (:1508-1515); Routentabelle :1750-1806.

**CLI** — `godsvoice completion status|history|begin|override` (cli.py:775-841, Parser :1343-1364) — arbeiten auf dem session-losen Legacy-Store (Lücke, s. E).

### B. `src/godsv/pact.py` (123 Z.) — Nutzerintention/Kriterien

**`Pact`** — Aufgabenvertrag: `goal, acceptance[AcceptanceCriterion(id,text)], non_goals, constraints, budget: dict, scope_paths, version=1`. — `:29-47`.

**`validate()`** — Ziel nicht leer; mindestens ein Kriterium; IDs nicht leer/eindeutig; Text nicht leer; sonst ValueError. — `:49-62` — Eintritt: nur `godsvoice init` (cli.py:87-88). — Grenzen: `Pact.from_yaml`/Plugin `_load_pact` rufen `validate()` nie auf → ein Pact ohne Kriterien passiert still; `_dod_from_pact` liefert 0 Items, `_contract` startet dann keinen Vertrag.

**`to_dict`/`from_dict`/`to_yaml`/`from_yaml`** — YAML-Persistenz; `from_dict` akzeptiert Kriterien als String (id `ac<n>`) oder Mapping; `_criterion_text` nimmt bei verschachtelten Dicts das innere `text` (verhindert Dict-Repr-Leak ins Kriterium). — `:16-26, 64-108` — Eintritt: `godsvoice init` (cli.py:87-89), `godsvoice pact-show` (cli.py:96-99, Parser :1111-1113), Plugin `_load_pact` (liest `<state_dir>/pact.yaml`, mtime-gecacht, Fehler → None + Trace; Plugin :666-678). — Grenzen: keine Typprüfung/Coercion unbekannter Felder; `version` dekorativ (keine Migration); YAML-`non_goals/constraints` sind nur Anzeige-Strings.

**`Pact.example()`** — Beispielvertrag (Ziel, 2 Kriterien, Non-Goals, Constraints, Budget, scope_paths) für `init`/Demo. — `:110-123` — Eintritt: cli.py:87, demo.py:54, evalharness :298-302.

**Konsumenten** — Monitor-Scope-Drift über `pact.scope_paths` (monitor.py:573-575; Konstruktor :247); Ledger `from_pact` (ledger.py:58); Plugin `_dod_from_pact` (Plugin :872-893): Akzeptanz → blockierendes `DodItem`, Mapping tests→`pytest`, ui→`ui_e2e`, api→`api`, sonst `run`; Plugin `_contract` (Plugin :838-854) adoptiert den Pact einmalig, nur wenn noch kein Ziel im Vertrag steht. — Lücken: `Pact.budget` wird nirgends gelesen/erzwungen (grep); Pact-`non_goals`/`constraints` werden bei der Übernahme nicht in den Completion-Vertrag übernommen (kein `begin_goal(non_goals=…)`-Aufruf); keine Persistenz des Pact in den Store; `scope_paths` wirkt nur im Monitor, nicht in den Completion-Gates.

### C. `src/godsv/evalharness.py` (366 Z.) — Evaluations-Szenarien

**Charakter** — deterministisch, ohne LLM/Netz/Zufall: jede Episode speist eine gescriptete Event-Sequenz in einen frischen `EventStore` (Temp-DB) und treibt Monitor + Voice; geprüft werden Signalarten (`expect_signals`), Hint-Anzahl (`min_hints`/`silent`/`exact_signals`), Guard-Verdikt (`completion_text`+`expect_guard`), `expect_contradiction`, `expect_pause_delivered`, `context_budget`. — `:1-10, 293-355` — Eintritt: `run_episode` (:293), `run_eval` (:358), `render_report` (:362).

**`Episode` / `EpisodeResult`** — Erwartungs-Dataclass bzw. Ergebnis mit `render()` (PASS/FAIL + Notizen). — `:46-74`.

**15 Episoden (`episodes()`)** — `healthy_work` (still trotz grüner Läufe + Edit, :77-83); `busy_loop` (4× pytest → `loop`, :86-91); `error_burst` (3× CUDA-OOM → `error_burst`, :94-99); `slop_edits` (TODO/bare except/assert True → 3 Signale, :102-113); `scope_drift` (Edit außerhalb `src/*`/`tests/*`, Pact-`scope_paths` angepasst, :116-127); `demo_script` (fährt `demo.SCRIPT`, 4 Signalarten, exakt 10 Treffer, :130-138); `false_completion` (Claim ohne Evidenz → Guard `unsupported`, :141-148); `unsupported_claim_no_actions` (:151-157); `contradiction` (Claim 'works' vs. wiederholter ImportError, :160-169); `tool_error_classes` (HTTP 401 → `tool_error_auth`, :172-179); `progress_stall` (14 Fehlschläge → `stall`, :182-189); `dependency_drift` (pyproject-Edit → `dependency_change`, :192-196); `revert_ops` (`git checkout --` → `revert`, :199-204); `pause_flow` (4× AssertionError + echter `PauseStore` im Temp-Ordner + `_InstantResearch`-Double; erwartet genau-eine Delivery, 'voice·pause' im Redirect; :207-212, :224-270); `long_task_bounded_context` (40 Schritte, `ContextBuilder(total_budget=1500)` ±120 Zeichen, :215-221). — Eintritt: `episodes()` :273-290; CLI `godsvoice eval` (cli.py:113-115, Parser :1123-1124; Exit 1 bei FAIL); Panel `GET /evals` → `evals_payload` (plugin_api.py:945-971) und `POST /evals/run` → `evals_run_payload` (:974-999, schreibt `eval_runs.jsonl`); pytest `tests/test_eval_harness.py`, `tests/test_demo_cli.py`.

**Grenzen/Lücken** — kein einziger Completion-Gate-Fall: `completion.py`/`GateRunner`/`plan_verification`/`run_requirement_checks` kommen im Harness nicht vor (grep-verifiziert); `exact_signals` ist brittel (neue Signale verschieben feste Zahlen in Harness + `test_eval_harness.py` + `test_demo_cli.py`); Erwartungen handgescriptet, kein Baseline-/Zeitvergleich; keine Persistenz der Läufe außer dem Panel-Pfad.

### D. `src/godsv/metrics.py` (330 Z.) — real berechnete Metriken

**`Metrics`-Dataclass** — Träger aller Kennzahlen (sessions, events, problem_classes, recurring_classes, recurrence_kill_rate, hints_delivered, incidents, skills_promoted, pause_*, research_*, `main_agent_tokens_measured`, `voice_quality`, `testing_tools`, recovery_ms_avg, guard_*/claim/contradiction/skill_consumed, tool_error_classes, interventions_held/recurred); `to_dict`/`to_json`. — `:33-72` — Eintritt: `compute_metrics` (:293-330).

**`compute_metrics(events, incidents=None, skills=None, session_db=None, session_id=None, pause_db=None, testing_runtime=None)`** — reale, deterministische Berechnung:
- Problemklassen / recurrence_kill_rate: für jedes `tool_result` `error_text`+`error_fingerprint` (monitor) → Sessions je Fingerprint; `recurring` = Klassen in >=2 Sessions; `kill_rate = 1 − recurring/classes` (1.0 bei 0 Klassen). — `:225-265, 298`.
- Zähler aus Event-Kinds: `hint_delivered`, `pause_requested/delivered/expired/stale`, `guard_check/nudge/footer`, `claim`, `contradiction`, `skill_consumed`. — `:229-243`, Rückgabe :302-326.
- Research: `research_done` → `duration_ms`/`tokens_est` → `research_runs`, `research_ms_avg`, `research_tokens_est` (Schätzung, keine Abrechnung). — `:250-255, 306-308`.
- Recovery: `recovery_ms_avg` aus `pause_delivered.recovery_ms` (Schreiber pause.py:649). — `:244-249, 320`.
- Interventions-Schätzung: je Delivery Suche nach `tool_result` mit gleichem Fehler-Fingerprint innerhalb `RECURRENCE_WINDOW = 12` Schritte (`:30`) → `interventions_recurred`, sonst `held`; Delivery ohne Fingerprint zählt automatisch als `held`. — `:270-291, 328-329`.
- `skills_promoted` aus `SkillStore`; `tool_error_classes` = klassifizierte Tool-Fehler (monitor) sortiert; `incidents.count()` wenn Store übergeben. — `:260-268, 300, 327`.

**`measured_session_usage(session_db, session_id)`** — host-gemessene Werte read-only aus `state.db` (`sessions`: input/output/cache read+write/reasoning/estimated_cost_usd/actual_cost_usd); None ohne DB/Tabelle/Row ('nie geraten'). — `:75-113` — Eintritt: `compute_metrics(main_agent_tokens_measured=…)` nur wenn `session_db` UND `session_id` (:309-313); CLI `godsvoice metrics --session-id/--session-db` (cli.py:241-256, Parser :1155-1170). — Grenzen: genau eine Session/Zeile, keine Aggregation; inkompatible DB-Schemata enden still als None.

**`testing_tool_stats` (Re-Export)** — delegiert an `capability.testing_tool_stats` (capability.py:378-…): Läufe/Pässe/Fehlschläge generierter Testing-Tools aus dem Usage-Ledger. — `:116-120` — Eintritt: `compute_metrics(testing_tools=…)` bei `testing_runtime` (:317-319); CLI setzt das Runtime-Dir (:257).

**`voice_quality_from_db(pause_db)` / `voice_quality(pauses)`** — §34-Interventionsqualität aus der durabilen `pauses`-Tabelle (letzte 500 Zeilen): `interventions`, `delivered`, `no_action` (Status `abandoned` + `released_reason` enthält 'no intervention needed', geschrieben von pause.py:529-545), `abandoned`, `expired`, `stale`, `steps_before_first_intervention` (min `base_step` je Session), `recovery_success` vs. `recurrence_after_intervention` (je gelieferter Pause: gibt es in derselben Session eine spätere Pause mit gleichem `trigger_fp` und höherem `base_step`?), `recovery_success_rate = success/delivered` (None ohne Deliveries), `no_action_rate = no_action/interventions`. — `:123-203` — Eintritt: `compute_metrics(voice_quality=…)` nur wenn `pause_db` eine Datei ist (:314-316); CLI `<memory-dir>/pauses.db` (cli.py:249-252). — Grenzen: Rekurrenz ist status-agnostisch (jede spätere Zeile gleichen `trigger_fp`, auch requested/expired/stale) und ohne Zeitfenster — anders als die Event-Sicht mit `RECURRENCE_WINDOW`; `steps_before_first_intervention` nutzt alle Pausen, nicht nur gelieferte; nur 500 DB-Zeilen; `recovery_success` ist eine Überlebens-Heuristik, keine gemessene Erholung.

**Explizit NICHT berechnet** — keine LLM-basierte Formulierungsqualität der Voice, keine Erfolgsrate je Trigger-Klasse, keine session-übergreifende Recovery-Zeit, keine Kosten-Aggregation über Sessions, keine Perzentile/Streuung (nur Mittelwerte), keine Messung 'durch Evidenz vs. durch Zeitablauf erledigt'. `voice_quality`/`testing_tools` sind standardmäßig None (JSON null) — nur auf ausdrückliche Parameter.

### E. Systemische Lücken (Nicht-Existierendes, explizit)

1. DoD-Status-Lifecycle fehlt — `DodItem.status` wird nie `verified`/`failed` geschrieben (nur `open`, completion.py:459/510), `gate_dod` liest `verified` (:1074) → Skip nie erreichbar; 'x/y verified' in Panel/CLI ist eine Schätzung aus den Failure-Texten des letzten Verdikts (plugin_api.py:1197-1204, cli.py:756-773), kein Kriterien-Status.
2. Revisionszaun nur halb — Zaun existiert für Editor-Ops (`expected_revision`/`CriterionConflict`, :550-552) und Revisionen bumpen bei set_goal/add/update/remove (:460, :517, :565) — aber Evidenz/Verifikation ist an keine Revision gebunden (`RunEvidence.revision` :133 nie gesetzt/gelesen), `verify` vergleicht keine Revision, ein Ziel-Edit invalidiert keine gespeicherten Läufe.
3. Kein Runner für `ui_e2e`, `api`, `build`, `persistence_restart`, `run` — `execute_check` (:1431-1476) kann eingebaut nur `pytest|tests|test`; sonst None. Nur über registrierte generierte Testing-Tools zertifizierbar; der Panel-Fallback `run` (plugin_api.py:1114) und `run` aus `_dod_from_pact` (Plugin :889) erzeugen den bekannten Deadlock.
4. `RepoFacts.unreadable` unsichtbar (:811) — kein Gate/Verdikt wertet es aus; ein gebrochener Git-Read liest sich als 'keine Änderungen'.
5. Eval deckt die Completion-Schleife nicht ab — 15 Episoden für Monitor/Voice/Guard/Pause/Kontext, null Szenarien für `GateRunner`/`plan_verification`/`run_requirement_checks`.
6. Pact-Budget/Non-Goals werden nicht erzwungen — `Pact.budget` ohne Leser; `non_goals`/`constraints` gehen beim Import ins Completion-Ziel verloren; `Pact.validate()` nur in `godsvoice init`.
7. CLI-Vertrag != Chat-Vertrag — `godsvoice completion *` (cli.py:775-841) liest/schreibt `<runtime>/completion_state.json`, das produktiv niemand schreibt (Live: `<runtime>/completion/<sid>.json`); kein `--session`-Schalter im Parser (:1343-1364).
8. Kein Lock/keine Atomarität — `transition` (write_text) und `_criterion_edit` konkurrieren mit Panel-Requests und Plugin-Hooks (last write wins).
9. Historie wächst unbegrenzt — JSONL ohne Rotation; `timeline` parst immer die ganze Datei (:670-679).
10. `_SILENT_EXCEPT` definiert (:725), nirgends genutzt — stille `except: pass`-Muster erkennt kein Completion-Gate (nur der Monitor in der slop-Episode).

---

## 6. Memory / Research / Incidents / Ledger / Skills / Notes / Retrieval

### Feature-Landkarte godsv: Memory / Incidents / Ledger / Research / Retrieval / Skills / Notes / Embeddings / Context

Grundlage: ausschließlich der echte Code unter `/home/willy/projects/gods-voice/src/godsv/` und `integrations/hermes/godsv_plugin/` (docs/ bewusst ignoriert). Zeilenangaben `Datei:Zeile` beziehen sich auf diesen Stand.

### 0. Wie die acht Module live verdrahtet sind

Alles läuft über die Plugin-Session `GodsvSession` in `integrations/hermes/godsv_plugin/__init__.py`:

- `IncidentStore(state_dir/"memory.db")` — `__init__.py:235`
- `NoteStore(state_dir/"memory.db")` — `__init__.py:236` (gleiche SQLite-Datei wie Incidents, WAL)
- `SkillStore(state_dir/"memory.db")` — `__init__.py:255`
- `Monitor(known_error_fingerprints=self.incidents.signatures())` — `__init__.py:242` (Snapshot beim Sessionstart)
- `VoicePause(... research_runner=CapabilityAwareRunner(primary=ResearchRunner(...)))` — `__init__.py:270-293`, nur bei `_pause_enabled()`
- Hooks: `pre_tool_call` → `before_tool_call` (`:1587-1599`, `:621-633`), Event-Pipeline `observe` (`:1201-1241`), `on_session_end` (`:1580-1584`) macht nur `flush()` + Pause-Release — **keine** Memory-Konsolidierung am Sessionende.
- Registrierte Event-Kinds für diese Features: `evidence_judged` (`events.py:38`), `research_started/done/failed` (`:64-66`), `skill_consumed` (`:67`), `voice_context_built` (`:70`); `pause_no_action` fehlt (siehe unten).

---

### 1. **Incident-Memory** — dauerhafte Problem-/Fix-Records mit Signatur, FTS-Suche, Matching gegen Live-Fehler

**Was konkret**
- `Incident`-Dataclass: `signature` (Fingerprint aus `monitor.error_fingerprint`), `title`, `kind`, `root_cause`, `resolution_steps`, `verification`, `evidence_refs`, `status` (candidate|active|archived), `confidence`, `occurrences`, `first_seen`/`last_seen`, `repo_scope`, optional `embedding`; `to_evidence_text()` rendert die kompakte Zeile „Titel — Ursache — Lösung (max 3) — Beleg“ — `incidents.py:31-57`.
- `IncidentStore`: SQLite WAL, `check_same_thread=False` + RLock (Hooks laufen auf Worker-Threads), Tabellen `incidents` (`signature UNIQUE`) + FTS5 `incidents_fts(title, root_cause, resolution_text)` — `incidents.py:70-104`.
- Schreiben: `upsert()` fügt ein oder merget per Signatur (`occurrences += 1`, `last_seen` neu, nicht-leere Felder überschreiben, Embedding optional, danach FTS-Resync + commit) — `incidents.py:107-184`, `_sync_fts_locked :186-191`. `record_sighting()` zählt nur bekannte Signaturen hoch — `:199-209`. `promote()` setzt candidate/active/archived (validiert) — `:211-219`.
- Lesen: `signatures()` (Cross-Session-Check) — `:193-197`; `search_text()` FTS5-OR-Suche über `_fts_query()` (nur alnum-Tokens ≥2 Zeichen, lowercase, dedupe) ohne `archived`, Score = −rank — `:60-67`, `:239-252`; `search_similar()` Kosinus über gespeicherte Embeddings (>0, absteigend) — `:254-267`; `get/get_by_id/all/count/close` — `:222-285`.
- Matching live: Der Monitor kennt die Signatur-Menge (`known_error_fingerprints`) und stuft dieselbe Fehlersignatur mehrfach im Fenster **ohne Fortschritt** als `known_incident_repeat` (SEV_HIGH) ein — `monitor.py:424-457`; „coarse errors“ ohne Details (`exit != 0` ohne Text) werden bewusst nie gegen Incidents gematcht (`error_without_details`) — `monitor.py:368-384`. Die Pause eskaliert `known_incident_repeat` sofort (Schwelle 1) — `pause.py:92-104`.

**Eintrittspunkt**
- Sessionstart: `plugin __init__.py:235` + `:242`.
- Live: `observe()` → `monitor.ingest()` → Signal (`plugin :1201-1216`); Evidence-Anreicherung in Hints über `retrieval.find_evidence` (`plugin :1479-1497`); Research-Memory-Zeilen (`research.py:290-294`); Investigator-Kontext (`context.py:242-249`).
- Schreibpfad produktiv nur CLI: `godsvoice nightshift --events … --memory … --session <id> [--apply]` → `consolidate()` / `propose_solved_incidents()` → `incidents.upsert()` (`cli.py:193-230`, `nightshift.py:107-155`, `:215-266`); `godsvoice memory-stats` / `memory-search` (`cli.py:145-190`); `godsvoice metrics` (`cli.py:233-265`).
- Panel (read-only): `GET /api/plugins/godsv/memory` → `memory_payload()` (`plugin_api.py:165-187`, Route `:1581-1583`).

**Grenzen/Lücken**
- `promote()` und `record_sighting()` haben **keinen Produktionsaufrufer** (nur Definitionsstelle + Tests) — Incidents bleiben `candidate`; es gibt keinen Promote-/Review-Pfad in CLI oder Panel. `occurrences` steigt produktiv nur durch erneutes `upsert` (nightshift).
- `search_similar()` + die Spalte `embedding` sind toter Produktionscode: **kein** Code erzeugt Embeddings, niemand ruft `search_similar()` (nur `tests/test_incidents.py:81,94`). Die im Docstring versprochene Kette „exact → FTS → cosine“ ist real nur exact+FTS.
- `repo_scope` wird gespeichert, aber bei keiner Suche gefiltert.
- Das Monitor-Set ist ein Session-Snapshot: Incidents, die während der Session entstehen (nightshift), matchen erst in der Folge-Session.
- Ernte nur nachgelagert per CLI (Dry-Run-Default), nicht am Sessionende (`on_session_end` macht nur flush/end).
- Exakter Signatur-Hit nur bei `error_burst` (`plugin :1486`), Loop-Signale gehen nur als Freitext-Query rein.

---

### 2. **Working Ledger** — Fortschritts-/Kontinuitätszustand pro Ziel mit Beleg-Invariante und YAML-Persistenz

**Was konkret**
- `Ledger`-Dataclass: `goal`, `criteria` (id → `CriterionState{status, evidence}`), `criterion_texts`, `decisions`, `open_questions`, `artifacts`, `test_status`, `next_steps`, `updated_at` — `ledger.py:39-55`. Status: `open|in_progress|evidenced|at_risk` (`:19-23`), Icons fürs Rendering (`:27-32`).
- Invariante: `set_status(…, "evidenced")` verlangt eine nicht-leere Evidenz-Notiz („kein ‚done‘ ohne Beleg“) — `ledger.py:67-79`; weitere Mutationen `add_decision/add_question/add_artifact/set_test_status/set_next_steps` aktualisieren `updated_at` — `:81-99`.
- `from_pact()` erzeugt das Gerüst aus Pakt-Kriterien (`:57-64`); `completion()`/`is_complete()` zählen nur `evidenced` (`:102-109`); `render_markdown()` deutsches Layout (Ziel/Kriterien/Fortschritt/Entscheidungen/Offene Fragen/Artefakte/Tests/Nächste Schritte) — `:115-146`; `to_dict/from_dict` (`:148-186`); `save()/load()` als YAML-UTF-8, `load` verlangt Mapping (`:188-197`). `TOKEN_GUARD = 2000` als „softes“ Budget deklariert (`:25`), `size_tokens_estimate()` = Länge Markdown/4 (`:111-112`).

**Eintrittspunkt**
- `godsvoice init` schreibt `.godsv/pact.yaml` + `.godsv/ledger.yaml` (aus Pakt) — `cli.py:79-93`; Parser `:1106-1109`.
- `godsvoice ledger-show` lädt `ledger.yaml` und druckt das Markdown — `cli.py:102-105`, Parser `:1115-1117`.
- `demo.py:55-81`: baut ein Ledger im Speicher, setzt Status/Test/Nächste Schritte, druckt Markdown (kein Save).

**Grenzen/Lücken**
- **Kein Live-Pfad:** `integrations/` importiert `Ledger` nirgends — der Ledger wird weder vom Plugin geladen noch fortgeschrieben (das Plugin lädt nur `pact.yaml`, `plugin :666-678`).
- `TOKEN_GUARD` ist toter Code (kein Aufrufer), `size_tokens_estimate()` ebenfalls (nur Definitionsstelle); das Budget wird bei `save()` nicht geprüft.
- Kein Schreibkommando außer `init`: Kriterien/Entscheidungen/Fragen/Artefakte lassen sich nur programmatisch setzen; keine Event-getriebenen Updates, keine Session-Zusammenfassung.
- `save()` schreibt die ganze Datei ohne Lock/Atomik; parallele Schreiber könnten sich überschreiben (praktisch ungenutzt).
- Pro Kriterium gibt es keinen Zeitstempel; `decisions`/`open_questions`/`artifacts` wachsen unbegrenzt.

---

### 3. **Research-Läufe** — begrenzte, deterministisch-fallende Untersuchungen im Pause-Pfad, deren Ergebnis nur als kondensierter Redirect zurückfließt

**Was konkret**
- Trigger-Templates (loop/error_burst/contradiction/drift/other) mit deterministischer `direction`+`avoid` — `research.py:31-56`.
- `ResearchResult`: problem/evidence/direction/avoid/uncertainty, status ok|failed, duration_ms, tokens_est (Schätzung), archive_path, used_llm, `intervention_needed`/`no_intervention_reason`; `redirect_md()` baut den kurzen Redirect (Problem max 300 Zeichen, Evidenz max 3×220) — `research.py:62-99`.
- `ResearchRunner.run()` — `research.py:211-372`:
  1. **Erst Archiv-Wiederverwendung**: `find_prior_research(state_dir, problem)` — `:234`;
  2. Investigator-Kontext über `ContextBuilder` mit `role="investigator"` — `:256-266` (+ `voice_context_built`-Event, best-effort, `:270-285`);
  3. Memory-Zeilen: Incidents FTS (limit 4) + Notes FTS (limit 4) + Prior-Findings mit Alter/Konfidenz/STALE-Marker — `:289-308`;
  4. Repo-Snippets (`_repo_snippets :377-406`: Term-Match ≥5 Zeichen, erste Trefferzeile je Datei, Suffixe `py/md/toml/yaml/yml`, Skip-Dirs `.git/.venv/…`, max 200 Dateien gelesen, max 10 Snippets);
  5. Evidenz = Memory[:3] + Snippets[:2] — `:317`;
  6. Optional LLM über Prompt `voice_redirect` (nur mit Client+Registry und >2 s Restbudget): kann `no_intervention` setzen oder direction/avoid/uncertainty/evidence überschreiben — `:319-361`;
  7. Archivierung als JSON `state_dir/research/research-<hex10>.json` inkl. `prior_findings`/`reused_from`, `confidence` immer `"unverified"`, `stale_after`; Payload durch `redact()` (Secret-Scrub) — `:408-420`.
- **Wiederverwendung** `find_prior_research()` — `research.py:122-177`: liest `research-*.json`, Term-Overlap der Problemtexte (Tokens ≥5 Zeichen, Stopwortliste `:102-105`, Set-Schnitt ≥ **2** gemeinsame Terme), liefert kompakte Einträge (id, problem[:200], conclusion[:400], confidence, age_hours, `stale` bei >14 Tagen `_STALE_AFTER_SECONDS :119`) sortiert nach Overlap/Alter, max 3; ausdrücklich als „re-check applicability“-Hinweis gedacht.
- Fehlertoleranz: nie raisen — äußere Exception → `status="failed"` + Fehlernotiz im Archiv — `:364-366`; LLM-Fehler → Template bleibt — `:362-363`.

**Eintrittspunkt**
- Pause-Controller `VoicePause._start_research()` startet den Lauf in einem Daemon-Thread, Heartbeat alle 2 s — `pause.py:500-609`; Runner kommt aus dem Plugin (`CapabilityAwareRunner(primary=ResearchRunner(...))`, `plugin :277-290`).
- Ergebnisse: `research_done`-Event + Lease auf `ready` mit `redirect_md()` (`pause.py:556-579`); `no_intervention` → Lease abgebrochen, `pause_no_action`-Event, Agent läuft ungestört weiter (`pause.py:529-555`); Fehler → `research_failed`, Lease abandoned (`:580-600`).
- Auslieferung des Redirects genau einmal: `before_tool_call` blockt den nächsten Tool-Call — `plugin :621-633` + `pause.deliverable()` (`pause.py:613-652`).
- Capability-Lücke (`capability_gap`) routet am ResearchRunner vorbei in den Tool-Builder — `capability.py:258-283`.

**Grenzen/Lücken**
- **Bei `status="failed"` gibt es live KEINEN Redirect**: `pause.py:580-590` bricht die Lease ab („Recherche fehlgeschlagen“); der im Docstring versprochene Template-Redirect bleibt ungenutzt.
- Wiederverwendung ist rein **lexikalisch** (Overlap ≥2) und nur im Runner selbst verdrahtet; es gibt kein CLI/Kommando zum Suchen/Prüfen des Archivs, kein Aufräumen, kein Updaten von `confidence` (immer „unverified“) und keine Dedupe der Archivdateien.
- `stale` (14 Tage) wird nur **markiert**, nie verworfen; Prior-Findings landen max. auf den Memory-Zeilen des Redirects, nicht als harte Quelle.
- `skills_text` ist im Live-Pfad **immer leer**: `CapabilityAwareRunner.run` (capability.py:265-280) und `pause._start_research` (pause.py:522-528) übergeben es nicht — die `skills`-Sektion des Investigator-Kontexts bleibt leer.
- Snippet-Suche ist heuristisch (nur Namen/Suffixe/erste Trefferzeile, `repo_root=Path.cwd()` beim Plugin-Init, `plugin :283`), limitiert auf 200 Dateien/10 Snippets; `tokens_est` ist Schätzung chars/4.
- Der Redirect kürzt hart (Problem 300, Evidenz 3×220, direction 400, avoid 300, uncertainty 300); das Archiv behält den Volltext, ist aber nur auf Platte.

---

### 4. **Research-Notizen** — atomare Behauptungen mit Provenienz, Trust, Ablaufdatum und Widerspruchs-Links

**Was konkret**
- `ResearchNote`: `topic`, `claim`, `source_type`/`source_url`/`retrieved_at`, `valid_until` (Version-/Zeitbezug), `trust` 0..1, `repo_scope`, `status` (active|stale|contradicted|archived), `contradicts`; `is_stale(today)` vergleicht `valid_until < today`; `to_evidence_text()` rendert mit Quelle — `notes.py:34-60`, Statuswerte `:27-31`.
- `NoteStore`: SQLite WAL, Tabelle `notes` + FTS5 `notes_fts(topic, claim)` — `notes.py:63-93`; `add()` schreibt Row + FTS — `:96-127`; `set_status()` — `:129-137`; `link_contradiction()` hängt eine Referenz an **und setzt die Notiz auf `contradicted`** — `:139-155`; `search_text()` nutzt den `_fts_query`-Builder aus `incidents.py`, schließt `archived` aus, Score = −rank — `:163-177`; `all/count/close` — `:179-194`.
- `curate_finding()` verdrahtet den Prompt `research_note` (raw finding + Quell-Meta → validierter `ResearchNote`-Vorschlag, trust geklemmt, `valid_until` normalisiert) — `notes.py:198-233`; propose-first, Speichern entscheidet der Aufrufer.

**Eintrittspunkt**
- Live: nur lesend — `NoteStore(state_dir/"memory.db")` (`plugin :236`), Notes-FTS in Research-Memory (`research.py:295-297`), Notes in der Investigator-Evidenz (`context.py:250-258`), `find_evidence` markiert Notes als `untrusted` (`retrieval.py:69-78`).
- Schreiben produktiv nur per CLI: `godsvoice nightshift --session <id> --apply` → `consolidate_research_notes()` verwandelt `research_done`-Funde deterministisch in Notizen (topic=Problem[:120], claim=direction[:500], `source_type="research"`, `source_url`=Archivpfad, trust 0.6), dedupliziert per Claim, verlinkt gleiches Thema + abweichender Claim als Widerspruch (`nightshift.py:158-212`, Aufruf `cli.py:207-209`).
- Panel (read-only): `GET /api/plugins/godsv/memory` zeigt `id, topic, claim, source_type, source_url, trust, status, created` (`plugin_api.py:179-184`).

**Grenzen/Lücken**
- **Live-Sessions schreiben nie Notizen**; der einzige produktive Writer ist die CLI (`nightshift`), und die ist Dry-Run-Default.
- `curate_finding` (LLM-Kuration `research_note`) ist produktiv **nicht verdrahtet** (nur Tests) — der Live-Pfad erzeugt Notizen deterministisch ohne LLM.
- `is_stale()` hat keinen Produktionsaufrufer; Status `stale` wird nie automatisch gesetzt, `valid_until` wird beim Retrieval **nicht durchgesetzt** (einziges Filterkriterium überall: `status != archived`). Abgelaufene Claims können also weiter als Evidenz auftauchen.
- `trust` wird gespeichert und in Evidence/Kontext angezeigt, aber nicht zum Reranking benutzt.
- Widerspruchserkennung ist heuristisch: Vergleich nur über `topic[:120].lower()` + Claim-Text, Link-Format `note#id`, einseitiges `link_contradiction`; keine Auflösung von Widersprüchen.
- Notes werden nie gelöscht/kompaktiert; `all()` wächst unbegrenzt.

---

### 5. **Evidence-Retrieval** — gerankte Evidence-Objekte (Exact → FTS) inklusive Untrusted-Spotlight für Notes und optionalem LLM-Urteil

**Was konkret**
- `Evidence`-Dataclass: `ref`, `text`, `trust`, `untrusted`, `exact`; `render()` wickelt untrusted Inhalte in `spotlight(text, source=ref)` (Prompt-Injection-Schutz, SECURITY T2) — `retrieval.py:25-36`.
- `find_evidence()` — `retrieval.py:39-85`: (1) exakter Signatur-Hit aus dem IncidentStore (nur wenn nicht `archived`, `exact=True`, trust=confidence); (2) Incidents-FTS (limit); (3) Notes-FTS (limit, `untrusted=True`, trust=note.trust); dann Dedupe per `ref` und Kürzung auf `limit` (Default 3). Reihenfolge im Ergebnis: exakt zuerst, dann Incidents, dann Notes.
- `judge_same_problem()` — `retrieval.py:88-118`: befragt den LLM-Prompt `same_problem_judge`, ob das aktuelle Problem zu einem der Kandidaten passt; liefert die bestätigte `incident#id`-Referenz oder `None`; ohne Client/Registry → `None` (Aufrufer verlässt sich dann auf die deterministischen Hits).

**Eintrittspunkt**
- Plugin `_gather_evidence()` (`plugin :1479-1497`): nur für Signal-Kinds `error_burst` und `loop`; Query = Fehlertext bzw. Signal-Message; Signatur nur bei `error_burst`; dedupe, max 3. Ergebnis geht in `voice.consider(..., evidence=…)` (`plugin :1215-1217`).
- `_judge_evidence()` (`plugin :1352-1392`): im Enrichment-Thread; nur „fuzzy“ Kandidaten (nicht exact, nicht untrusted) werden gejudgt; bei `None` werden **alle** fuzzy-Treffer verworfen, bei Fehler alle behalten (fail-open); Ergebnis-Event `evidence_judged` (`events.KINDS :38`).
- CLI: `godsvoice memory-search [query] [--signature …] [--limit …]` (`cli.py:169-190`) — gleiche Funktion, Ausgabe mit trust/exact/untrusted-Flags.

**Grenzen/Lücken**
- Kein Kosinus/Embedding im Retrieval — nur Exact+FTS; `trust` beeinflusst die Reihenfolge nicht.
- Judge-Semantik ist grob: „same=true, aber unbekannte id“ → `None` → alle fuzzy Treffer weg (`retrieval.py:114-118` + `plugin :1379-1382`); ohne LLM-Client bleiben fuzzy Treffer ungeprüft im Hint.
- Notes sind per Konstruktion immer untrusted und werden nie gejudgt (gewollt), Exakt-Hits auch nicht.
- Evidenz wird nur für zwei Signal-Kinds gebaut; `repo_scope`/`valid_until`/`stale` werden nicht gefiltert; Dedupe nur per `ref`.
- Panel und Chat zeigen Evidence nicht direkt (nur über Hints/Events).

---

### 6. **Skills** — Skill-Mining aus Tool-Ketten, propose-first-Lifecycle und deterministische Wiederverwendung im Live-Loop

**Was konkret**
- `SkillCandidate`: name/title/tool_chain/occurrences/sessions/evidence/body/status (candidate|shadow|promoted|demoted)/version/applied/succeeded/created/updated; `success_rate()` = succeeded/applied oder None — `skills.py:31-58`.
- `SkillStore`: SQLite WAL, Tabelle `skill_candidates` (name UNIQUE) — `:61-90`; `upsert()` merged Sessions + Evidenz, behält die größere `occurrences`, füllt `body` nur wenn leer — `:92-152`; `set_status()` — `:164-173`; `record_use(name, success)` zählt applied/succeeded — `:175-183`; `all/count/close`.
- **Mining** `mine_tool_patterns()` — `:202-250`: liest `tool_call`-Namen aller Sessions aus dem EventStore, bildet Ketten der Länge 2..3, Schwelle ≥3 Vorkommen in ≥2 Sessions, verwirft kürzere Ketten, die in einer längeren enthalten sind (`_is_subchain :253-255`), Name `workflow-<kette>`.
- **Draft** `draft_skill()` — `:258-284`: mit Client/Registry LLM-Prompt `skill_draft` (expect_json=False); Body wird nur übernommen, wenn er mit `---` beginnt, sonst deterministisches SKELETT (`_skeleton_body :287-305`). `parse_skill_header()/is_valid_skill_body()` prüfen YAML-Frontmatter (name+description) — `:308-324`.
- **Shadow-Validierung** `validate_candidate()` — `:408-444`: (1) gültiger SKILL.md-Body; (2) Muster in den Live-Events re-verifiziert (≥ Schwellen über ≥ Sessions, `_count_chain :447-463`); ohne Event-Store wird die Promotion verweigert; Fehler-Sessions nur informativ (`_sessions_with_errors :466-475`). `ValidationReport` mit ✓/✗-Zeilen — `:396-405`.
- **Drift-Demotion** `demote_on_drift()` — `:327-339`: promoted + applied ≥5 + Rate <0.5 → `demoted`.
- **Konsumption** `relevant_skills()` — `:342-365`: **nur promoted**, deterministisch: die Tool-Chain muss als zusammenhängende Teilfolge im Tail der letzten `window=8` Tool-Namen liegen (Plugin übergibt die letzten 16, gematcht wird das Tail-Fenster), Sortierung längste Kette zuerst, `limit=1`. `consumption_hint()` baut den Kurztext — `:368-374`.

**Eintrittspunkt**
- Live-Konsumption: `before_tool_call` → `_maybe_skill_hint()` (`plugin :621-633`, `:635-664`): sammelt die letzten 16 Tool-Namen, matcht `relevant_skills`, überspringt bereits konsumierte Skills (`_consumed_skills` :257), emittiert `skill_consumed` (`events.KINDS :67`) und legt einen Hint (kind „skill“, severity 2, confidence 0.9) in Queue+Inbox.
- Lifecycle nur per CLI: `godsvoice skills list|mine|validate|promote|demote|show|export` (`cli.py:268-414`, Parser `:1185-1216`); `mine` ruft `draft_skill` **ohne** Client/Registry → immer Skelett; `promote` validiert und setzt `promoted` (mit `--force`-Override trotz fehlgeschlagener Checks) — `cli.py:340-363`.
- Panel (read-only): `GET /api/plugins/godsv/skills` → Name/Status/occurrences/sessions/applied/succeeded (`plugin_api.py:190-208`, Route `:1589-1591`).

**Grenzen/Lücken**
- Mining/Validierung/Promotion/Demotion sind **CLI-only** — kein Auto-Promote (propose-first by design), aber auch kein Panel-Button.
- `record_use()` hat **keinen Produktionsaufrufer** → `applied`/`succeeded` bleiben im Live-Betrieb 0; `success_rate()` ist immer `None`; damit ist `demote_on_drift()` im Produkt wirkungslos (auch seine einzigen Aufrufer sind Tests). Der Panel-Hinweis „applied/succeeded counts consumption“ ist durch Code nicht gedeckt — es gibt nur `skill_consumed`-Events.
- Der LLM-Draft-Pfad (`skill_draft`) ist produktiv unerreichbar (CLI übergibt keinen Client).
- Ketten-Mining nutzt nur Tool-Namen (keine Args/Erfolg), nur exakt benachbarte `tool_call`-Events, Schwellen fest 3/2, max. Kettenlänge 3; `sessions` wächst unbegrenzt.
- `export` schreibt eine SKILL.md in ein Verzeichnis (`cli.py:397-414`) — keine automatische Installation/Verteilung in Hermes-Skills.
- Konsumption feuert nur in `before_tool_call`; ein Skill mit Kettenanfang, der nie als Tool-Aufruf auftaucht (z. B. Chat-Turn), wird nie vorgeschlagen; pro Skill genau ein Hinweis je Session.

---

### 7. **Embeddings** — deterministische Trigram-Hash-Vektoren als dependenzfreier Fallback; Kosinus-Fassade

**Was konkret**
- `HashingEmbeddings(dim=256)`: lowercased/whitespace-normalisiert, Padding, SHA-256 je Zeichen-Trigramm → Bucket (erste 4 Bytes mod dim) + Vorzeichen (5. Byte), danach L2-Normalisierung. Ausdrücklich **nur lexikalische Überlappung, keine Semantik** — `embedding.py:16,23-44`.
- Protokoll `EmbeddingProvider.embed()` für einen echten Provider — `embedding.py:19-20`; `cosine(a,b)` mit Längen- und Null-Guards — `:47-55`.

**Eintrittspunkt**
- Einziger Konsument: `IncidentStore.search_similar()` (`incidents.py:23,254-267`). Tests: `tests/test_incidents.py:81,94`.

**Grenzen/Lücken**
- **Keine Produktionsverdrahtung**: kein `HashingEmbeddings()`-Aufruf außerhalb der Tests, kein Provider-Auswahl-/Config-Layer, kein Code schreibt `incidents.embedding`; `search_similar()` wird nie produktiv gerufen. Die Embedding-Spalte bleibt NULL, Vektor-Suche existiert nur im Test.
- Lexikalisch statt semantisch (Docstring sagt es selbst); Trigramm-Hashing verpasst Synonyme/Paraphrasen.

---

### 8. **Decision-Context** — frische, zeilenkürzbare Kontexte pro Voice-Entscheidung mit harten Zeichen-Quotas und Rollenprofilen

**Was konkret**
- Budgets: `DEFAULT_TOTAL_BUDGET=6000`; Quotas pact 700 / acceptance 500 / recent 2200 / errors 700 / evidence 900 / skills 800 / state 300 — `context.py:21-31`. Rollenprofile: **watcher** (2500 Budget, recent 10, eigene kleinere Quotas) und **investigator** (4000, recent 24, Default-Quotas) — `:35-54`.
- `_clip()` kürzt je Sektion und markiert explizit `…[gekürzt: N Zeichen]` — `:57-60`; `_enforce_budget()` schrumpft bei Überschreitung die größte nicht-geschützte Sektion zuerst, `pact`/`acceptance` sind geschützt — `:63-91`.
- `DecisionContext`: `total_chars`, `truncated_sections()`, `stats()` (getrennt **measured** chars vs. **estimated** tokens=chars/4) — `:102-127`; `render()` mit `## <sektion>`-Headern — `:129-135`.
- `_event_line()` rendert kompakte, quellenfeste Zeilen für `tool_call`, `tool_result` (ERR/exit), `file_edit`, `claim`, `pause_*`/`research_done`, `thought`; alles andere → leer und fällt raus — `:138-160`.
- `ContextBuilder.build()` — `:177-274`: (1) `pact` (Ziel/Constraints/Non-Goals) + `acceptance` ([id] Text); (2) `recent` = letzte `recent_limit` Event-Zeilen; (3) `errors` = Top-3 Fehler-Fingerprints mit Zähler (via `monitor.error_fingerprint/error_text`); (4) `evidence` = Incidents-FTS + Notes-FTS (je limit 4, nur bei `query`; Notes mit „untrusted, trust=…“-Präfix); (5) `skills` = `skills_text` wie übergeben; (6) `state` = session/step/kinds-Zählung/Anzahl Fehler-Fingerprints. Danach Budget-Enforcement.

**Eintrittspunkt**
- **Produktiv nur** `ResearchRunner.run()` mit `role="investigator"` (`research.py:256-266`; Builder instanziiert mit `total_budget=4000`, `:209`).
- Evalharness baut Kontexte je Episode mit `ContextBuilder(total_budget=episode.context_budget)` und prüft die Einhaltung (`evalharness.py:331-335`) — ohne Rolle.
- Instrumentierung: `voice_context_built`-Event mit Budget/used/truncated/tokens_estimated (`research.py:270-285`).

**Grenzen/Lücken**
- **Der Watcher-Kontext wird produktiv nirgends gebaut** — das Profil `watcher` (`context.py:36-47`) hat außer Tests keinen Aufrufer; der Plugin-Watcher-Pfad (Voice-Hints) arbeitet ohne `ContextBuilder`. Rollen-Quotas kommen also live nur als Investigator-Profil zur Geltung.
- Live fließt in den Investigator-Kontext: Pakt (+Akzeptanzkriterien), die letzten 24 Event-Zeilen, Top-3-Fehlergruppen, maximal 4 Incident- + 4 Notes-FTS-Treffer (nur wenn das Problem als Query durchgereicht wird — im Runner ja), `skills_text` (im Live-Pfad leer) und die State-Zeile. Der Watcher-Pfad bekommt davon nichts über dieses Modul.
- `_event_line` blendet alle nicht gelisteten Kinds aus (z. B. `voice_context_built`, `evidence_judged`, Treasury- und Tool-Build-Events) — der „recent“-Block ist eine kuratierte Auswahl, kein Rohprotokoll.
- Kürzung ist hart (Zeichenbudget), Trefferlisten sind auf 3/4 Einträge begrenzt; `skills` ist ein blind durchgereichter String (kein eigenes Rendering).
- Evidenz-Treffer sind rein FTS; valid_until/stale/trust fließen nicht in die Auswahl ein.

---

### 9. Belegte Bruchstellen quer über die Features

- `pause_no_action` (no-intervention-Ausgang) wird von `pause.py:547-554` emittiert, ist aber **nicht in `events.KINDS` registriert** (`events.py:25-92`) → `EventStore.append` wirft `unknown event kind` (`events.py:217-218`); das Plugin fängt den Fehler in `_emit` nur ab und tracet ihn (`plugin :295-300`). Der Ausgang ist damit live **nicht persistiert**; der Test (`tests/test_no_intervention.py:57`) nutzt eine Fake-Emit-Liste und sieht das nicht.
- Tote Produktionspfade der Memory-Schicht (jeweils grep-verifiziert): `IncidentStore.promote`/`record_sighting`, `IncidentStore.search_similar` + Embeddings, `NoteStore.is_stale`, `curate_finding`, `SkillStore.record_use`, `demote_on_drift`, Ledger gesamt (außer CLI init/show), `TOKEN_GUARD`/`size_tokens_estimate`.
- Schreibpfade der Memory-Schicht im Live-Betrieb: nur die CLI (`nightshift --apply`, `skills *`) — der Plugin-Pfad schreibt Incidents/Notes/Skills nie selbst.

---

## 7. Desktop-App / Cockpit / CLI / Events / Betriebsfeatures

### Feature-Landkarte Skaldir Agent — Desktop-App / Cockpit / CLI / Events / Betriebsfeatures

Quelle: nur Code, `docs/` ignoriert. Alle Zeilenangaben aus dem gelesenen Stand (Repo `/home/willy/projects/gods-voice`, Version 1.5.0 laut `pyproject.toml:8`).

---

### A. Native Desktop-App (Build · Deployment · Start)

**Native App-Build (Overlay-Pipeline)** — Baut aus dem Hermes-Desktop-Quellbaum eine eigene App `gods-voice`: hardlinked Spiegel (`rsync --link-dest`) unter `$HERMES_HOME/godsv/app-src`, dann Overlay-Patches, dann `npm run pack`, dann Deployment nach `$HERMES_HOME/godsv/app/linux-unpacked`. Der Hermes-Checkout wird nie editiert. — `src/godsv/appbuild.py:109-181` (build), `:78-92` (sync_source), `:95-99` (_run_overlay), `:31` (SKIP_DIRS), `:53-64` (packaged_binary). — Eintrittspunkt: `godsvoice desktop build-app [--force]` (`src/godsv/cli.py:1446-1453` → `_cmd_desktop_build_app` `cli.py:1460-1474`); Modul-CLI `python -m godsv.appbuild` (`appbuild.py:184-195`). — Grenzen: braucht `npm` + `rsync` (`appbuild.py:102-106` wirft sonst); `--force` verhält sich identisch zum Normalpfad (`appbuild.py:85-91` — beide Zweige machen dasselbe inkl. Stempel-Neuschreiben, Unterscheidung ist faktisch wirkungslos); vor `staging.rename(target)` wird das alte Ziel gelöscht (`appbuild.py:171-172`) → kurzes Fenster ohne App; Default-Timeout 3600 s (`:109`).

**App-Overlay (Branding/Theme/SDK-Fixes)** — Exakte String-Swaps in bekannten Hermes-Dateien (jeder Patch asserted „genau 1 Treffer“, sonst lauter Fehler statt halb-gebrandeter App): GV-Theme + Default-Skin `gods-voice` in `src/themes/presets.ts` / `use-skin-command.ts`, F15-Owner-Token für das Session-Open-Swap-Overlay in `src/sdk/index.ts`, Kopien: `plugin.js` → `apps/desktop/src/plugins/godsv/plugin.js`, Logo-Mark + Icon, `REMOVE: apps/desktop/public/nous-girl.jpg`. — `app-overlay/patch_app.py:114-359` (PATCHES), `:361-372` (FILES), `:374-376` (REMOVE), `:399-430` (apply_patches), `:385-397` (atomare Writes, brechen Hardlinks); zweite Patchliste `app-overlay/patch_app_f15b.py:8-51` (`src/store/profile.ts` 90-s-Watchdog gegen hängendes Gateway-Swap-Overlay). — Eintrittspunkt: nur über `appbuild._run_overlay` (`appbuild.py:95-99`); direkt `python3 patch_app.py <target-tree> <repo-root>`. — Grenzen: nicht eigenständig aufrufbar über CLI; nur für Linux-Pack (`icon.png`; icns/ico bleiben Stock, `patch_app.py:365-372`-Kommentar); jeder Hermes-Update, der einen Anker verschiebt, bricht den Build.

**User-lokale Launcher-Installation** — Schreibt `.desktop`-Eintrag (`~/.local/share/applications/gods-voice.desktop`), skaliert `assets/icon.png` in hicolor 64/128/256 (ImageMagick `convert`, sonst Copy), verlinkt die CLI nach `~/.local/bin/godsvoice`, installiert den Skin (ohne Aktivierung) und legt `$HERMES_HOME/plugins/godsv` als Symlink auf `integrations/hermes/godsv_plugin`. — `src/godsv/desktop.py:397-413` (install), `:303-320` (desktop_entry), `:323-348` (render_icons), `:351-368` (_link_cli), `:371-394` (_link_panel/panel_source), `:249-278` (install_skin), `:43-60` (Pfade). — Eintrittspunkt: `godsvoice desktop install [--no-link]` (`cli.py:1426-1432` → `_cmd_desktop_install` `cli.py:1477-1497`); Shell-Setup `install.sh:1-45` (venv via uv, editable Install, `desktop install --no-link`, `doctor`). — Grenzen: `uninstall` (`desktop.py:416-436`) entfernt `.desktop`, Icons, Skin und nur den CLI-Symlink — **der Plugin-Symlink (`~/.hermes/plugins/godsv`) bleibt liegen** (kein Gegenstück zu `_link_panel`), Runtime-/App-Build-Verzeichnisse werden nie entfernt; `_link_cli` überspringt Modul-Fallback-Pfade (`:354`); ein fremdes existierendes `~/.local/bin/godsvoice` wird nie überschrieben (`:363-364`).

**Instanz-Trennung Skaldir Agent ↔ normaler Hermes** — Eigene Electron-userData (`$HERMES_HOME/godsv/desktop-userdata`), App-Name + Password-Store-Env, Default-`GODSV_MODE=whisper` (die echte App steuert, kein Shadow), Deep-Link `hermes://open/gods-voice`. Prozess-Erkennung läuft über `pgrep` + Marker-Prüfung in `/proc/<pid>/environ`, damit nur die GV-Instanz als „läuft“ gilt. — `src/godsv/desktop.py:96-119` (godsvoice_user_data/gods_voice_env), `:122-174` (Marker-/PID-Erkennung), `:93` (PANEL_ROUTE), `:211-232` (open_panel: argv `[binary, --class=SkaldirAgent, hermes://open/gods-voice]`). — Eintrittspunkt: `godsvoice desktop start [--check-only]` (`cli.py:1519-1536` → `desktop.launch` `desktop.py:461-514`); GNOME-Start via `.desktop`-Exec `godsvoice desktop start`. — Grenzen: Rückgabecodes nur 0/2/3 (`desktop.py:461-478`); Kaltstart ohne GV-Build ruft `hermes desktop --build-only` (Timeout 900 s) und startet danach die Stock-App (`:484-511`) — Fehler landen nur im Log `~/.hermes/godsv/desktop-launch.log`; `_wait_and_open_panel` (`:235-242`) ist Produktions-Totcode (nur Tests nutzen es, `tests/test_desktop.py:135`).

**Deployment-Status / Health-Check** — Prüft Hermes-CLI, Plugin-Verzeichnis, Panel-Datei, Desktop-Eintrag, Icons, Skin, GV-App-Build, Instanz-Status. — `src/godsv/desktop.py:439-458` (deployment_status), `:517-532` (format_status); `src/godsv/cli.py:1539-1586` (`_cmd_doctor`: Python ≥3.11, Paket, Prompt-Registry, Deployment, Runtime-Events-DB, optionale Tools bwrap/podman/convert). — Eintrittspunkt: `godsvoice desktop status` (`cli.py:1435-1438`, Handler 1512-1517; Exit 1 wenn hermes/Plugin fehlen), `godsvoice doctor` (`cli.py:1455-1456`). — Grenzen: `status` prüft nicht, ob Icons/Skin/Build vorhanden sind (nur Anzeige); `doctor` gibt Exit 1 bei jedem ✗.

**Skin (opt-in)** — Kopiert `assets/skin-godsvoice.yaml` nach `$HERMES_HOME/skins/gods-voice.yaml`; Aktivierung nur explizit per `--activate`/`hermes config set display.skin gods-voice`. — `src/godsv/desktop.py:245-278` (install_skin, modul-Docstring 249-257), `:281-300` (uninstall_skin setzt auf `default`). — Eintrittspunkt: `desktop.install()` (`desktop.py:404`) bzw. `install_skin(activate=True)`; keine eigene CLI-Flags. — Grenzen: `install_skin(activate=True)` wird von der CLI nie mit `activate=True` aufgerufen (`cli.py:1480` → `desktop.install(link_cli=…)` ohne activate) → Skin ist immer „ready, opt-in“; Assets fehlen still (Rückgabe ohne Fehler, `desktop.py:260-261`).

---

### B. Cockpit-Panel im Desktop-Client (`integrations/hermes/godsv_plugin/desktop/plugin.js`, 3051 Zeilen)

**Registrierung & Views** — Panel ist eine Plugin-ESM ohne Build-Schritt (nur `@hermes/plugin-sdk`, React, `jsx()`); Backend ist `/api/plugins/godsv/`. Ansichten: Cockpit (`/gods-voice`), Verification (`/gods-verification`), Activity (`/gods-activity`), Tools (`/gods-tools`), Settings (`/gods-settings`), Treasury (`/gods-treasury`). Die Sidebar registriert im Code nur vier Ziele mit eindeutiger Funktion — Verification, Tools, Settings, Treasury (Orders 53/55/56/57, `plugin.js:3794`); Cockpit und Activity haben die Sidebar verlassen (V2-01/V2-11) und bleiben als Panel-Heimroute sowie ueber Palette/Strip/Drawer-Aktionen erreichbar. Im normalen Hermes bleibt ein einzelner Eintrag „Skaldir Agent“ (Order 55). — Views/Icon-Map `plugin.js:31-46`, Hash→View `:48-54`, NAV-Gruppen `:57-68`, Settings-Subtabs `:72-80`, `SettingsView` `:82-107`, Seiten-Router/Queries `Page` `:1839-1952`, Registrierung `:2925-3051` (page-Route 2935, Sidebar 2936-2971, ⌘K-Palette 2972-2981, Empty-Chat 2982-2986, Transcript-Direktive `godsv-live` 2991-3005, Composer-Erweiterungen 3006-3049). — Eintrittspunkt: Desktop-App mit geladenem Plugin (Symlink `$HERMES_HOME/plugins/godsv`), Deep-Link `hermes://open/gods-voice`; Fallback-Navigation im Panel selbst als `aside` (`:1877-1897`). — Grenzen: `sdk`-Felder werden defensiv gelesen (`:109-114`) — ohne `COMPOSER_AREAS` entfallen Statusstrip/Approval-Pill/@goal-Middleware still; ohne `host.state.focusedSessionId` fällt die Session-Bindung auf „latest“ zurück (`:123-147`).

| View/Tab | Funktion | Datei:Zeile |
|---|---|---|
| **CockpitView** (Start) — eigenes Ziel, Fortschritt (verified/total), Blocker, Requirements-Liste, „Latest evidence“, Voice-Aktivität, „Recent signal“; Button „Begin a goal →“ springt zu Verification | | `plugin.js:1685-1837` |
| **StatusStrip** (persistent oben) — „SKALDIR AGENT“, State-Chip (STATE_LINES 1603-1614, Tones 1596-1601), Modus shadow/whisper, Blocker-/Pause-Zähler, Goal-Ausschnitt, Refresh | | `plugin.js:1636-1675` |
| **Verification** = `CompletionTab` + `EvalsTab` — Gate-Grid, Requirements, Verdict/History, „verify now“ (/completion/verify), Override mit Begründung, neues Goal + Kriterien; Evals: Episodenkatalog, Lauf starten, Token-Metriken | | `:1913-1917`, `CompletionTab :1253-1450`, `EvalsTab :1450-1564` |
| **Activity** = `LiveTab` + `PausenTab` — KPIs (Pauses active/Delivered/Guard-Checks/Claims), Event-Typ-Zählungen, Testgeld-Allowance, Recent Events; Pausen: Pause→Research→Redirect→Resume-Tabelle + Pause-/Guard-Events | | `:1918-1922`, `LiveTab :320-344`, `PausenTab :346-360`, `PauseRows :252-285`, `EventRows :285-304`, `AllowanceBlock :304-318` |
| **Tools** = `ToolsTab` (`/tools`) — installierte Tools, Build-Queue, Capability-Requests (`CapabilityCard`), MCP-Server, Health/Scope/Usage; Drilldown `ToolDetail` (Spec, Manifest, Grants, Builds, Usage, Wrapper-Pfad; Aktionen enable/disable/recheck/test) | | `:1923-1924`, `ToolsTab :927-1017`, `ToolDetail :1017-1159`, `CapabilityCard :870-927`, `GrantTable :809-837` |
| **Settings** = `SettingsView` mit Sub-Tabs Models/Tools/Computer Use/Memory/Skills/System — Models: Rolle→Provider/Modell (`ModelSelect`, `RoleRow`), Strictness (`StrictnessRow`), Live-Test; CU: Runner-Isolation, Policy, Dry-Run, letzte Läufe; Memory: Incidents + Research-Notes; Skills: Kandidaten; System: Runtime/Deployment-Kennzahlen | | `:1925-1926`, `SettingsView :82-107`, `ModelsTab :654-703`(+478-654), `ComputerUseTab :1159-1251`, `MemoryTab :362-404`, `SkillsTab :406-432`, `SystemTab :1564-1592` |
| **Treasury** = `TreasuryTab` — Spend-Requests (Testgeld), Grants-Tabelle, `CredentialsPanel` (Handles hinzufügen/testen/revoken, Grants setzen/entfernen mit TTL; Werte kommen nie zurück) | | `:1927`, `TreasuryTab :434-474`, `CredentialsPanel :705-809` |

**Chat-Integration (Composer/Transcript)** — `@goal`-Autocomplete (`goalCompletionRows :2008-2031`), Submit-Middleware, die `@goal …`-Zeilen in Kriterien (`POST /completion/criteria`) und Trajektorien-Einträge (`POST /completion/trajectory`) umsetzt (`:3014-3042`), stiller Statusstrip über dem Composer (`SessionStatusStrip :2577-2753`, Inline-Goal-Edit via `POST /goal`), Approval-Mode-Pille (`ApprovalModePill :2071-2112`, Zyklus off→manual→smart→off, `GET/POST /approvals`), „Live-Block“ als Transcript-Direktive `::godsv-live{stage,sid}` (`GvLiveBlock :2236-2490`, `GvEventDetail :2490-2577`, Drawer `:2215-2236`), Kriterien-Editor im Block mit Revision-Fence (`expected_revision`) + idempotenter `request_id`, ohne Status-Pfad (`:2267-2290`), Empty-Chat-Branding `SkaldirAgentEmpty` (`:1956-1985`, Logo als Base64 `:1963`), Chrome-/Composer-Styles (`installChromeStyles :2764-2800`, Direktiven-Highlight `:2800-2935`). — Eintrittspunkt: Session-Chat der Desktop-App (Plugin-Hooks `transform_llm_output` erzeugt den Block serverseitig, `integrations/hermes/godsv_plugin/__init__.py` `transform_llm_output`/`live_block_suffix`). — Grenzen: Feed zeigt bewusst nur `CHAT_KINDS` und max. 3-8 Zeilen; ohne fokussierte Session (`sid`) sind Live-Block und Strip deaktiviert (`:2247`, `:2280`); Kriterien sind bei `VERIFIED_COMPLETE` gesperrt (`:2280`).

---

### C. Panel-Backend REST (`integrations/hermes/godsv_plugin/dashboard/plugin_api.py`, 1818 Zeilen)

Basis: `APIRouter` (defensiv importiert, `:19-24`), montiert unter **`/api/plugins/godsv/`** (Modul-Docstring `:1`, `plugin.js:11`), Version `PANEL_VERSION = "1.0.0"` (`:26`), Runtime = `$HERMES_HOME/godsv` (`:29-31`), alle SQLite-Zugriffe read-only-URI (`:34-42`). Mutationen sind origin-geprüft (`_guard_origin` `:1599-1606`, Allowlist `origin_allowed` `:1140-1165`) und werden auditiert (`_audit` `:236-247`, Events „panel“). **Modul-Docstring behauptet „read-only“ (`:1-7`) — das gilt heute nur für die Lese-Routen; 20 der 32 Routen sind POSTs.**

| Methode + Pfad | Zweck | Datei:Zeile (Route / Payload) |
|---|---|---|
| GET `/health` | ok + Panel-Version | `:1573-1575` |
| GET `/summary?limit=` | Aggregat: `kinds`, `recent`, `pauses`, `allowance`, `voice_mode` (aus `GODSV_MODE`, Default shadow), `runtime` | Route `:1577-1579`, Payload `:107-155` |
| GET `/memory?limit=` | Incidents + Research-Notes (Limit 5…200) | `:1581-1583`, `:165-187` |
| GET `/events?since=&limit=&session_id=` | Chat-Feed, nur `CHAT_KINDS`, Default-Session = neuester Vertrag | `:1585-1587`, `:1400-1445`, `_chat_event_rows :1355-1397` |
| GET `/skills?limit=` | Skill-Kandidaten mit Lifecycle/Counts | `:1589-1591`, `:190-208` |
| GET `/treasury?limit=` | Spends + Grants (nie Vault-Inhalt) | `:1593-1595`, `:211-233` |
| GET `/models` | Provider-Katalog (Hermes-Cache), Config-Modelle, Rollen, Strictness | `:1608-1610`, `:335-348` (+`255-332`) |
| POST `/models/strictness` | Pausen-Strictness speichern | `:1612-1621` |
| POST `/models/roles` | Rolle→Provider/Modell validieren + persistieren (unbekanntes Provider = Fehler; unbekanntes Modell wird geflaggt, nicht blockiert) | `:1623-1634`, `:351-375` |
| POST `/models/test` | Mini-LLM-Smoke-Test (Antwort + Tool-Call-Probe, Latenz, Auth-Verdikt), auditierbar | `:1636-1643`, `:378-458` |
| GET `/treasury/credentials` | Handles + Grants + `used_by`; ohne `GODSV_VAULT_KEY` `available:false` | `:1645-1647`, `:523-545` (`_open_treasury :507-521`) |
| POST `/treasury/credentials` | Handle anlegen (service/name-Regex, Secret ≤8192, scopes fix `grant:required,use`), Antwort nur Handle + Maske | `:1649-1662`, `:548-573` |
| POST `/treasury/credentials/revoke` | Handle löschen | `:1664-1670`, `:600-607` |
| POST `/treasury/credentials/test` | Owner-Selbsttest (decrypt + isolierte Injektion, keine Netzwerk-Calls) | `:1672-1680`, `:576-597` |
| POST `/treasury/grants` | Grant an **ein** Tool (Panel-Klick = menschliche Freigabe), ttl 60…2592000 s | `:1682-1695`, `:610-633` |
| POST `/treasury/grants/revoke` | Genehmigten Grant entziehen | `:1697-1703`, `:636-643` |
| GET `/tools` | Registry (installed/generated/queue/requests/mcp_servers) inkl. Scope, Promotion, Usage, Manifest-Daten | `:1705-1707`, `:695-799` (+`661-692` MCP, `:651-658` Manifest) |
| GET `/tools/{name}` | Detail: Spec, Manifest, tool-spezifische Grants, Builds, Usage, Wrapper-Pfad | `:1709-1714`, `:1013-1042` |
| POST `/tools/{name}/action` | `enable`/`disable`/`recheck` (gates) /`test` (Wrapper mit Sample-Input, argv-fest, 30 s) | `:1716-1724`, `:1045-1091` |
| GET `/cu` | Runner-Isolationsprobe, Policy (API>DOM>Vision, max actions/fallbacks, require_isolation), letzte 8 Dry-Runs aus `cu_runs.jsonl` | `:1726-1728`, `:815-836`, `_read_jsonl_tail :802-812` |
| POST `/cu/demo` | Deterministischer CU-Lauf über den echten Executor mit `DryRunRunner`; schreibt `cu_runs.jsonl` | `:1730-1736`, `:839-892` |
| GET `/evals` | Episodenkatalog + Token-Metriken (`_token_metrics_payload :895-942`: gemessene Zählungen aus dem Event-Store, Token nur geschätzt chars/4) + letzter Lauf | `:1738-1740`, `:945-971` |
| POST `/evals/run` | Deterministische Eval-Suite real ausführen, Ergebnis an `eval_runs.jsonl` | `:1742-1748`, `:974-997` |
| GET `/completion?session_id=` | Goal-Vertrag: State/terminal, Goal, Requirements (verified), `dod` + `revision`, Trajektorie, Gate-Grid (`:1117-1137`), letzter Verdict, Timeline, Metriken; ohne sid = „latest“ (explizit, `:1168-1180`) | `:1750-1752`, `:1183-1252` |
| POST `/completion/verify` | Führt die echte Gate-Pipeline jetzt aus (Event-Evidenz + `run_requirement_checks` + `GateRunner.verify`) und liefert den neuen Vertrag; verweigert in Terminalzuständen | `:1754-1762`, `:1255-1314` |
| POST `/completion/override` | `SHIPPED_WITH_USER_OVERRIDE` nur mit expliziter Begründung — nie „VERIFIED“ | `:1764-1770`, `:1317-1325` |
| POST `/completion/goal` | Neues Goal setzen (re-armiert den Supervisor), Kriterien → `DodItem` mit abgeleiteter Verification (`_verification_for :1097-1114`) | `:1772-1778`, `:1328-1352` |
| POST `/completion/trajectory` | Entscheidung/Annahme/Non-Goal am aktiven Vertrag erfassen | `:1780-1786`, `:1448-1457` |
| POST `/completion/criteria` | Kriterien add/update/remove, Revision-Fence + Idempotenz; `status` wird nie vom Client akzeptiert (CriterionConflict → 409) | `:1788-1798`, `:1460-1505` |
| POST `/goal` | Goal-Text ändern (Inline-Edit im Chat) | `:1800-1806`, `:1508-1516` |
| GET `/approvals` | Echter Hermes-Approval-Modus (`manual|smart|off`) direkt aus `config.yaml`, API-Fallback, `available:false` bei Fremd-Runtime | `:1808-1810`, `:1527-1552` |
| POST `/approvals` | Modus setzen (validiert) | `:1812-1818`, `:1555-1566` |

— Eintrittspunkt: Hermes-Gateway lädt `dashboard/manifest.json` (`api: plugin_api.py`, Label „Skaldir Agent“) → Router unter `/api/plugins/godsv/`; das Plugin ruft über `ctx.rest` (`plugin.js:2932`). — Grenzen: **keine Spend-Routen** (Treuhand-Ausgaben sind nur per CLI/Bibliothek entscheidbar), **keine TOTP-Route**, **kein Pause-Release/Redirect-Trigger**, **kein Grant-Listing ohne Vault-Key**, Credentials immer mit Scope `grant:required` (kein „use-only“-Handle über das Panel); `/completion/verify` läuft synchron ohne Timeout; `origin_allowed` akzeptiert fehlenden Origin-Header (Same-Origin-Fetch) — dokumentierter Trade-off (`:1140-1148`).

---

### D. CLI `godsvoice` (`src/godsv/cli.py`, 1707 Zeilen)

Parser: `build_parser()` `cli.py:1096-1457`; Dispatch `main()` `:1700-1707` (ohne Argument → Welcome-Screen `_print_welcome :1610-1662`, Banner aus `assets/logo.ansi` `:1589-1603`). Entry-Point `godsvoice = godsv.cli:main` (`pyproject.toml:31-32`); `DEFAULT_DIR = ".godsv"` (`cli.py:76`); `--version` (`:1103`).

| Subkommando | Was es tut | Handler | Parser |
|---|---|---|---|
| `init [--dir --force]` | Beispiel-Pakt + Ledger in `.godsv/` anlegen | `:79-93` | `:1106-1109` |
| `pact-show [--dir]` | Pakt als YAML drucken | `:96-99` | `:1111-1113` |
| `ledger-show [--dir]` | Ledger als Markdown | `:102-105` | `:1115-1117` |
| `demo [--db]` | Deterministischer V0-Durchlauf (Event-Store→Monitor→Voice→InjectionQueue→Ledger) | `:108-110` | `:1119-1121` |
| `eval` | Deterministische Eval-Episoden; Exit 1 bei Fehlschlag | `:113-116` | `:1123-1124` |
| `scan <pfad|->` | Secret-Scan, JSON-Ausgabe, Exit 1 bei Fund | `:119-130` | `:1126-1128` |
| `prompts list` | Prompt-Registry + Pflicht-IDs prüfen | `:133-142` | `:1130-1133` |
| `memory-stats [--db]` | Incident-/Note-Zähler aus `memory.db` | `:145-166` | `:1135-1137` |
| `memory-search [query --db --signature --limit]` | Evidenzsuche (Trust/Exact/Untrusted-Flags) | `:169-190` | `:1139-1144` |
| `nightshift --session [--events --memory --apply]` | Episode → Incident-/Note-Vorschläge (Dry-Run Default) | `:193-230` | `:1146-1153` |
| `metrics [--events --memory --session-id --session-db]` | Recurrence-Kill-Rate + Betriebszähler (+ optional Host-Token aus `state.db`) | `:233-265` | `:1155-1168` |
| `tools-hub [--runtime --search --show --manifest --json]` | Lokale Tool-Registry: discover/search, Hub-Manifest bauen | `:1665-1697` | `:1169-1183` |
| `skills list` | Kandidatenliste mit Success-Rate | `:268-283` | `:1185-1189` |
| `skills mine [--min-occurrences --min-sessions --apply]` | Wiederholte Tool-Muster → SKILL.md-Kandidaten | `:286-317` | `:1190-1198` |
| `skills validate <name>` | Kandidat gegen Live-Events re-verifizieren | `:320-337` | `:1200-1211` |
| `skills promote <name> [--force]` | Nach Validierung promoten (Refuse ohne `--force`) | `:340-363` | `:1200-1216` |
| `skills demote <name>` | Kandidat zur Review degradieren | `:366-376` | `:1200-1216` |
| `skills show <name>` | SKILL.md-Draft drucken | `:379-394` | `:1200-1216` |
| `skills export <name> [--out]` | SKILL.md schreiben (verweigert ungültigen Body) | `:397-414` | `:1200-1216` |
| `tools list` / `tools show <name>` | Tool-Specs listen / einzeln als JSON | `:583-593` / `:596-606` | `:1220-1226` |
| `tools scan <pfad>` | Statischer Security-Scan einer Tooldatei, Exit 1 bei Fund | `:609-616` | `:1227-1229` |
| `tools propose --spec` | ToolSpec vorschlagen + Compose-Kandidaten zeigen | `:619-651` | `:1230-1233` |
| `tools approve <name> [--workdir --force]` | Gates laufen lassen, dann freigeben (Composition-Sonderpfad) | `:654-685` | `:1234-1239` |
| `tools revoke <name>` | Tool entziehen | `:688-698` | `:1240-1243` |
| `tools build --capability … [--kind --source --tests --sample --research/--research-file --secret --domain --dependency --json]` | Vollständige gated Build-Pipeline (Capability → Tool → registriert) + Redirect-Payload | `:498-565` | `:1244-1268` |
| `tools builds [--limit]` | Build-Queue mit Stages | `:568-580` | `:1269-1271` |
| `tools request --capability …` | Capability-Lücke als Request ablegen (Session baut sie im nächsten Gap) | `:417-455` | `:1272-1291` |
| `tools fulfill [request_id] [--json]` | Capability-Pipeline sofort laufen lassen (ohne Session) | `:458-495` | `:1292-1297` |
| `tools mine` / `tools candidates` / `tools promote <candidate>` | Capability-Mining, Kandidatenliste, Kandidat→Build-Request | `:844-853` / `:856-869` / `:872-885` | `:1298-1308` |
| `tools compose <need> [--tag]` | Composition-Kandidaten für einen Bedarf | `:701-713` | `:1310-1314` |
| `mcp serve [--db --name --project --all --runtime]` | Stdio-MCP-Server über freigegebene Python-Tools (Scope Projekt/global/legacy) | `:716-749` | `:1316-1334` |
| `cu check` / `cu demo` | Runner-Isolationen sondieren / deterministische Kanal-Reihenfolge API>DOM>Vision | `:888-892` / `:895-932` | `:1336-1341` |
| `completion status` / `history [--limit]` / `begin --goal [--criterion]` / `override --reason` | Goal-Zustand + Metriken / Timeline / neues Goal / Nutzer-Override (nie VERIFIED) | `:775-793` / `:796-817` / `:820-828` / `:831-841` | `:1343-1364` |
| `vault list` / `describe <handle>` / `add --service --name [--scopes --description]` / `revoke <handle>` | Credential-Vault (Handles only; Secret per `getpass`, nie in argv) | `:954-964` / `:967-976` / `:979-996` / `:999-1007` | `:1366-1387` |
| `spend request <betrag> --merchant [--purpose --currency --tier]` / `list` / `approve <id>` / `deny <id> [--reason]` / `complete <id>` / `allowance [--tier]` | Gestuftes Ausgeben (Testgeld) inkl. Allowance-Anzeige | `:1010-1028` / `:1031-1044` / `:1047-1055` / `:1058-1066` / `:1069-1078` / `:935-943` | `:1389-1420` |
| `desktop install [--no-link]` / `uninstall` / `status` / `start [--check-only]` / `build-app [--force]` | Launcher/Start/Build (siehe A) | `:1477-1497` / `:1500-1509` / `:1512-1517` / `:1519-1536` / `:1460-1474` | `:1422-1453` |
| `doctor` | Release-Health-Check (Deployment, Prompts, Runtime, optionale Tools) | `:1539-1586` | `:1455-1456` |

— Eintrittspunkt: `godsvoice …` (Symlink `~/.local/bin/godsvoice` aus `desktop._link_cli` bzw. `install.sh:30-32`), alternativ `python -m godsv`. — Grenzen: **kein CLI für Grants** (`Broker.request_grant/approve_grant/deny_grant`, `treasury.py:397-415`, sind nicht verdrahtet) und **kein TOTP-Weg** (`Broker.totp_code :367-394`, `Vault.add(totp_seed=…)` hat kein Flag); `spend request` übergibt nie ein `SpendIntent` → Stufe-1/2-Auto-Freigabe ist über die CLI unerreichbar (landet immer `pending_human` mit „mandate without a verifiable intent“, `treasury.py:658-662`); `spend approve/deny/complete` prüfen/verwenden hart `PRESETS["stufe0"]` (`cli.py:1050,1061,1072`) — Tier-Caps des Requests werden bei der Entscheidung nicht erneut angewandt; `tools-hub` fehlt in Modul-Docstring (`cli.py:3-5`) und im Hilfe-Epilog (`:1081-1093`); `_cmd_metrics` importiert `os` lokal (`:244`).

---

### E. Event-Store (`src/godsv/events.py`, 300 Zeilen)

**Append-only Event-Store (SQLite/WAL)** — Tabelle `events(seq AUTOINCREMENT, ts, session_id, step, kind, payload)`; ein Verbindungsobjekt mit `check_same_thread=False` und `RLock`-Serialisierung für alle Zugriffe. Reads: `iter_session`, `stream(after_seq)`, `count`, `session_ids`. — Store `:163-300`, Schema `:166-184`, `append` `:186-195`, `append_redacted` `:197-206`, Kern `_append` `:208-254`, Reads `:257-285`. — Eintrittspunkt: `EventStore($HERMES_HOME/godsv/events.db)` aus Plugin-Session, CLI, Broker, Panel-Audit; DB-Write ist das einzige Persistenz-Fundament von Panel/Metriken. — Grenzen: `iter_session`/`stream` laden unbegrenzt in den Speicher (kein Limit/Paging), keine Retention/Kompaktierung, kein Löschen/Redaktions-API (bewusst append-only), kein Index außer PK.

**Event-Kind-Whitelist** — Exakt 59 erlaubte Kinds (`KINDS :25-92`, verifiziert per Import: 59); unbekannter Kind → `ValueError`. Abdeckung: Kern (thought/tool_call/tool_result/file_edit/test_run/user_signal/budget_tick/hint_*), Guard/Completion, Pause/Research/Skills (`pause_*`, `research_*`, `skill_consumed`, `tool_error_class`), Capability/Tool-Builder-Stages, Computer-Use (`cu_*`), Treasury (`treasury_*`, `spend_*`), v1.3.1 (`voice_context_built`, `capability_requested`), `system`. — `events.py:25-92`, Validierung `:217-218`. — Eintrittspunkt: jeder `EventStore.append` (Plugin `_record`, Broker `_record`, CLI-Handler, Panel `_audit`). — Grenzen: **`treasury_selftest` ist NICHT in `KINDS`** (verifiziert) — ein `Broker` mit angeschlossenem EventStore würde beim Selbsttest `ValueError: unknown event kind` werfen (`treasury.py:361-364`; aktuell nur entschärft, weil CLI/Panel den Broker ohne Events bauen).

**Chat-Filter (`CHAT_KINDS`)** — 30 Kinds, die im Chat-Feed landen dürfen (Interventionen, Goal-Vertrag, Completion/Guard, Tool-Builder, Sicherheits-/Geldgrenzen, CU-Fehler); alles andere bleibt im Panel. — `events.py:97-137`; konsumiert von `plugin_api._chat_event_rows :1355-1397` / `events_since_payload :1400-1445` und `plugin.js`-Feed (`:2119-2215`). — Eintrittspunkt: `GET /api/plugins/godsv/events`. — Grenzen: Liste ist statisch; neue Kinds erscheinen erst nach Code-Änderung im Chat.

**Secret-Leak-Gate** — Jeder Payload wird vor dem Persistieren gescannt: `append` wirft `SecretLeakError` (mit Findings), `append_redacted` ersetzt Treffer und markiert `_godsv_redacted: n`; Größenlimit `MAX_PAYLOAD_CHARS = 100_000`. — `events.py:139-150`, `:219-231`. — Eintrittspunkt: alle Schreibpfade; Plugin/Broker fangen `SecretLeakError` und schreiben redigiert (`integrations/hermes/godsv_plugin/__init__.py` `_record`; `treasury.py:247-255, 776-784`). — Grenzen: Scan läuft auf der JSON-Repräsentation (serialisierte Escape-Sequenzen können Treffer verändern), kein Feld für nachträgliche Löschung, Redaktion ist best-effort-Regex-Scan (`secretscan`).

---

### F. Betriebs-/Analyse-Features: `demo`, `nightshift`, `spotlight`

**Demo-Lauf (`godsv demo`)** — Skriptete Session mit 8 Events (3× fehlschlagender `pytest`, Slop-Edits mit TODO/`except: pass`/`TBD`), gespeist durch echten `EventStore`, `Monitor`, `Voice(budget=5, min_gap=1)`, `InjectionQueue`, `Ledger`; druckt Hinweise + Ledger-Markdown; ohne `--db` temporäre Datei (wird nicht aufgeräumt). — `src/godsv/demo.py:20-43` (SCRIPT), `:46-92` (run_demo, Summary-Dict auch von Tests genutzt). — Eintrittspunkt: `godsvoice demo [--db]` (`cli.py:108-110, 1119-1121`); `run_demo()` direkt. — Grenzen: rein Bibliotheksebene — kein Plugin-/Pause-/Completion-Pfad, kein LLM; Temp-DB bleibt liegen; „verbose“ ist immer True über CLI.

**Nightshift-Konsolidierung** — Macht aus einer fertigen Session dauerhafte Erinnerung, alles deterministisch und **propose-first**: (1) `build_episode_summary` (Events, Kinds, Fehler-Fingerprints nach Häufigkeit, Signale, berührte Dateien, letzter Gedanke — ohne LLM); (2) `consolidate` erzeugt **einen** Incident-Vorschlag aus dem Top-Fehler (Status `candidate`, Confidence 0.3; optional per LLM-Prompt `incident_extract` verfeinert: Titel, Root Cause, Resolution-Schritte, Verification, Confidence); (3) `consolidate_research_notes` wandelt `research_done`-Findings in atomare Research-Notes (trust 0.6, repo_scope=Session, Dedupe per Claim, gleicher Topic + anderer Claim → Contradiction-Link); (4) `propose_solved_incidents` schlägt Fehlerklassen vor, deren letzter Auftritt von einem Erfolg desselben Tools gefolgt wurde. — `src/godsv/nightshift.py:22-50` (EpisodeSummary), `:61-104`, `:107-155`, `:158-212`, `:215-266`. — Eintrittspunkt: `godsvoice nightshift --session <id> [--events --memory --apply]` (`cli.py:193-230`, Parser `:1146-1153`); `--apply` schreibt Incidents/Notes (`:206-213`). — Grenzen: kein Auto-Trigger/Scheduler (weder Plugin noch Panel rufen das auf — die Nightshift-Pipeline existiert nur als CLI/Bibliothek); `--session` ist Pflicht (kein „letzte Session“); Obergrenzen limit=5 Notes / 3 Incidents; Lauf ohne `--apply` schreibt nichts, gibt aber auch keine persistierte Vorschlagsliste aus.

**Spotlighting (`spotlight.py`)** — Injection-Abwehr für Fremdtexte: `sanitize` entfernt Format- und Steuerzeichen (Kategorien `Cf`/`Cc`, außer `\n`/`\t`) und kürzt auf 20 000 Zeichen mit `[…truncated…]`; `spotlight` klammert Text in `<<<UNTRUSTED source=… retrieved=…>>> … <<<END UNTRUSTED>>>`. — `src/godsv/spotlight.py:13-14`, `:17-30`, `:33-39`. — Eintrittspunkt: Bibliotheksfunktion (Aufrufer z. B. Research/Retrieval-Pfade). — Grenzen: Truncation passiert VOR dem Wrappen, das Marker-Band selbst wird nicht escaped — Text, der die Markerliterale enthält, kann die Klammerung optisch schließen; kein Längen-/Encoding-Fehlerpfad.

---

### G. Treasury: Secrets, Grants, Geld (`src/godsv/treasury.py`, 924 Zeilen)

**Backends (real im Code):** (1) **Vault** — JSON-Datei, Fernet-verschlüsselt, Schlüssel per `scrypt(n=2^14,r=8,p=1)` aus `GODSV_VAULT_KEY` (nie gespeichert), atomare Writes + `0600`, Cross-Prozess-`flock` (`treasury.py:78-81`, `:99-219`); (2) **TreasuryState** — SQLite/WAL mit Tabellen `usage`, `grants`, `spends` + In-place-Migration `tool_id` (`:497-527`); (3) **Broker** — Injektion am Nutzungspunkt (`:230-432`); (4) **SpendLedger** — gestuftes Ausgeben (`:759-884`); (5) **PRESETS** — Stufen 0/1/2 (`:458-462`). Kein externes Secret-Backend (kein libsecret/Keyring, kein Cloud-KMS) — „gnome-libsecret“ wird nur als Electron-Password-Store der Desktop-App gesetzt (`desktop.py:114`).

**Vault-Handles (`secret://service/name`)** — `add` (kind, scopes, description, `expires_at`, optional `totp_seed`), `revoke`, `list_handles`, `describe`; Agent sieht nur Metadaten, `_decrypt` ist „broker side“. — `treasury.py:128-160` (add), `:162-169` (revoke), `:172-182` (list/describe), `:197-209` (_decrypt). — Eintrittspunkt: `godsvoice vault add|list|describe|revoke` (`cli.py:946-1007`; Secret-Eingabe via `getpass`, `cli.py:983`) bzw. Panel `POST /treasury/credentials` (`plugin_api.py:548-573`). — Grenzen: kein CLI-Flag für `expires_at`/`totp_seed`; kein Re-Key/Rotation; Vault-Verzeichnis wird nur beim Anlegen auf `0700` gesetzt (`treasury.py:107-110`).

**Broker-Injektion (`run_with_secret`)** — Prüfkette: Handle existiert → `grant:required` verlangt aktiven Grant (tool-scoped oder unscoped) → geforderte Scopes → Stunden-Rate-Limit (30/h, `bump_usage`) → Decrypt → Ausführung über `gates._run` (Sandbox) mit Secret **nur** im Kind-Env → **fail-closed**, wenn nicht sandboxed → Output- und Purpose-Scrubbing (`[REDACTED:treasury]`, URL-encoded Variante ab 8 Zeichen). Events: `treasury_use` / `treasury_denied`. — `treasury.py:266-327`, `_scrub :417-432`, `_deny :257-259`, `_grant_ok :261-264`; Kinds `events.py:45-49`. — Eintrittspunkt: Agent-Tool-Aufrufe über den Broker (Bibliothek); Panel/CLI nur über `selftest`. — Grenzen: ohne Sandbox (kein bwrap/podman) ist Secret-Nutzung komplett blockiert (Feature-Grenze, `:308-313`); Rate-Limit pro Handle nur in-process-DB (kein globaler Zähler über Maschinen).

**Credential-Selbsttest** — Beweist die ganze Kette ohne Netzwerkkontakt: Handle da, Decrypt ok, Secret erreicht ein isoliertes Subprocess-Env (`sh -c 'test -n "$GODSV_TEST_VALUE" && echo injection-ok'`), Output gescrubbt; kein Grant nötig, Owner-Aktion, auditiert als `treasury_selftest` — letzteres **kein registrierter Kind** (siehe E). — `treasury.py:329-365`. — Eintrittspunkt: `POST /treasury/credentials/test` (`plugin_api.py:576-597`). — Grenze: kein CLI-Kommando (nur Panel).

**TOTP (RFC 6238)** — `totp_now` (HMAC-SHA1, 6 Stellen, 30 s, Seed base32 normalisiert) und `Broker.totp_code` (Grant-/Rate-Prüfung, Seed verlässt den Vault nie, Event `treasury_use` mit `totp:true`). — `treasury.py:435-446`, `:367-394`. — Eintrittspunkt: Bibliothek/Broker. — Grenzen: **kein CLI- und kein Panel-Weg** (kein Route, kein Subkommando) und kein Weg, einen Seed über die CLI in den Vault zu legen → Funktion praktisch unerreichbar ohne eigenes Skript.

**Grants** — `add_grant_request` (ttl, optional `tool_id`), `decide_grant`/`approve_grant`/`deny_grant`/`revoke_grant`, `has_active_grant(_for_tool)` (TTL-Fenster, jüngste 20 Genehmigungen); Broker-Events `treasury_grant_requested/approved/denied`. — `treasury.py:553-631`, `Broker.request_grant/approve_grant/deny_grant :397-415`. — Eintrittspunkt: Panel `POST /treasury/grants` (Panel-Klick = Freigabe, „owner-panel“, `plugin_api.py:610-633`) und `/treasury/grants/revoke` (`:636-643`); Broker-API. — Grenzen: **kein CLI-Subkommando für Grants**, kein Ablauf-/Cleanup-Job, `list_grants` limitiert (Default 100).

**Staged Spending (Testgeld)** — `SpendIntent` (amount/currency/merchant/expires_at, AP2-Idee) + `SpendRequest` (Status `pending_human|approved|denied|completed`); `TreasuryState.add_spend` prüft per-tx-/Wochen-/Monats-Caps, Tier 0 → immer menschlich, Tier 1 → Whitelist, Tier 2 → nur mit verifiable Intent Auto-Approval; `SpendLedger.request/approve/deny/complete/allowance`; Events `spend_requested/approved/denied/completed`; `draft_spend_intent` (LLM-Prompt `spend_intent`) existiert. — `treasury.py:449-494` (Dataclasses), `:634-686` (add_spend inkl. Caps), `:688-694` (_spent_since), `:786-832` (request inkl. Intent-Abgleich + finiter/positiver Betrag), `:834-884`, `:846-871` (allowance, „mode: testgeld“), `:887-924` (draft_spend_intent). — Eintrittspunkt: `godsvoice spend request|list|approve|deny|complete|allowance` (`cli.py:935-1078`, Parser `:1389-1420`); Panel liest nur (`GET /treasury`, `plugin_api.py:211-233`). — Grenzen: `complete` verweigert bei abgelaufenem Intent und markiert `denied` (`:873-884`), aber Intents werden nur programmatisch erzeugt — `draft_spend_intent` hat **keinen Aufrufer** in CLI/Panel; Panel hat **keine** Approve/Deny/Complete-Routen; keine echten Zahlungswege (nur Buchhaltung, `:847-852`).

---

### H. Querschnitt: Betriebs-Env-Variablen (aus dem Code, sortiert)

`GODSV_MODE` (shadow Default im Plugin, `whisper` als Default der App-Instanz: `desktop.py:113`; `off` killt alle Hooks), `GODSV_VAULT_KEY` (`cli.py:947`, `plugin_api.py:513`), `GODSV_HERMES_SOURCE` (`appbuild.py:35`), `GODSV_HOME` (`cli.py:1673`), `GODSV_PROMPTS`, `GODSV_LLM`/`GODSV_TRIAGE`/`GODSV_LLM_GRACE`/`GODSV_LLM_TIMEOUT`, `GODSV_PAUSE`/`GODSV_PAUSE_TTL`/`GODSV_PAUSE_WAIT`, `GODSV_GUARD`/`GODSV_GUARD_FORCE`, `GODSV_DEBUG_TRACE`/`GODSV_TRACE_FILE`, `GODSV_TEST_VALUE` (nur intern beim Vault-Selbsttest), `HERMES_HOME`, `HERMES_DESKTOP_USER_DATA_DIR`/`HERMES_DESKTOP_APP_NAME`/`HERMES_DESKTOP_PASSWORD_STORE`/`HERMES_DESKTOP_CWD`/`HERMES_DESKTOP_HERMES_ROOT`/`HERMES_DESKTOP_APP_NAME`.

### I. Belegte Lücken/Pannenliste (kompakt)

1. `plugin_api.py:1-7` behauptet „read-only“ — real 20 mutierende, origin-geprüfte Routen.
2. `treasury_selftest` fehlt in `events.KINDS` (`treasury.py:362` vs. `events.py:25-92`) → latenter `ValueError` bei Broker-Selbsttest mit EventStore.
3. Kein CLI/ Panel-Pfad für Grants (nur Panel bzw. Bibliothek), keiner für TOTP, keiner für Intent-basierte Stufe-1/2-Freigaben.
4. `desktop.uninstall` lässt Plugin-Symlink, Runtime und App-Build zurück.
5. `appbuild.sync_source` behandelt `force` und Normalpfad identisch.
6. `desktop._wait_and_open_panel` ist Produktions-Totcode (nur Tests).
7. Nightshift hat keinen Trigger/Scheduler — nur manueller CLI-Aufruf mit expliziter Session-ID.
8. Panel: keine Spend-Entscheidungen, kein Pause-Release, kein TOTP, Credentials nur mit `grant:required`-Scope.
9. `EventStore.iter_session`/`stream` ohne Paging; keine Retention.
10. `spotlight` escaped die eigenen UNTRUSTED-Marker nicht.

---

## 8. Lücken-Register (konsolidiert, stichprobenverifiziert)

Aus allen Sektionen zusammengeführt; die mit ⚠ markierten wurden im Rahmen dieses Audits direkt am Code nachverifiziert.

**Kommunikation/Lifecycle**
1. ⚠ `pause_no_action` fehlt in `events.KINDS` (`pause.py:548` vs. `events.py:25-92`) → No-Intervention-Ausgang wird live nicht persistiert.
2. ⚠ `treasury_selftest` fehlt in `events.KINDS` (`treasury.py:362` vs. `events.py`) → latenter `ValueError` beim Broker-Selbsttest mit EventStore.
3. `main_agent_steering`-Prompt ohne Aufrufer; `ContextBuilder`-Profil `watcher` produktiv ungenutzt; `_pact_excerpt()` konstant leer.
4. Zähler/Hint-Budget in-memory; nur Lease-Historie und Drift-Zähler überleben Prozessgrenzen.

**Completion/Verifikation**
5. ⚠ `DodItem.status='verified'` wird nie geschrieben (nur `gate_dod` liest es, `completion.py:1074`) → Kriterien-Status kann nie hochzählen; „x/y verified" ist eine Schätzung aus Verdikt-Failures.
6. Kein Runner für `ui_e2e|api|build|persistence_restart|run` — nur `pytest` und registrierte Testing-Tools; Panel-Fallback `run` ⇒ bekannter Deadlock.
7. Evidenz ist an keine Revision gebunden (`RunEvidence.revision` nie gesetzt/gelesen); `set_goal` entwertet keine Läufe.
8. `RepoFacts.unreadable` wertet kein Gate aus; kein Lock (last write wins) im Completion-Store; Historie wächst unbegrenzt.

**Tools/Capabilities**
9. ⚠ `project_id` wird von keinem Produktionspfad befüllt (CLI-Request, Plugin-Gap, Mining alle ohne) → Builder-Tools (`scope: project`, leere ID) sind in `visible_tools` in KEINER Sicht sichtbar (nicht global, kein Match, kein Legacy).
10. Scope-Promotion `promote_tool` ohne Eintritt (kein CLI, keine Panel-Aktion); `promote` im Panel nur anzeigend.
11. ⚠ `workflow_contracts()`/`draft_tool_spec()` ohne Produktionsaufrufer; Status `scanned` wird nie gesetzt; `to_mcp_descriptor`/`by_status` unbenutzt.
12. Compose-before-create fehlt im Builder; `testing_tool` per CLI nicht baubar (validation_cases fehlen überall) → jede solche Promotion endet in `needs_validation`.
13. MCP-Server wird nirgends automatisch in die Hermes-Config geschrieben; CLI-DB-Default (`.godsv/tools.db`) ≠ Runtime-DB (`$HERMES_HOME/godsv/tools.db`).
14. Moduldatei-Namensbruch Builder vs. `register_testing_tools` (module == name nötig); Redirect-Text nennt Testing-Tools fälschlich „MCP-exposed".

**Mining/Lernen/Memory**
15. ⚠ `IncidentStore.promote`/`record_sighting`/`search_similar` (+ Embeddings) ohne Produktionsaufrufer; Embedding-Spalte bleibt NULL; „Semantik" ist Trigram-Hashing (nur Test).
16. Ledger: kein Live-Pfad (nur `init`/`ledger-show`); `TOKEN_GUARD`/`size_tokens_estimate` tot.
17. `curate_finding` (LLM) unverdrahtet; `is_stale`/`valid_until` werden nie durchgesetzt; Notes/Incidents nur per CLI `nightshift --apply` (Dry-Run-Default) geschrieben.
18. Mining nur manuell (`tools mine`); `dismissed`-Status ohne Schreiber; Skill-Draft-Pfad (`skill_draft`) produktiv unerreichbar.
19. `skills_text` im Live-Investigator-Kontext immer leer.

**Desktop/Cockpit/CLI/Betrieb**
20. Panel-Docstring „read-only", real 20 mutierende Routen (origin-geprüft); keine Spend-/TOTP-/Pause-Release-Routen.
21. `desktop.uninstall` lässt Plugin-Symlink + Runtime + App-Build zurück; `appbuild` `--force` wirkungslos; `_wait_and_open_panel` toter Code.
22. Kein Scheduler/Trigger für `nightshift`; Skins nur opt-in (nie aktiviert); `EventStore` ohne Retention/Paging.
23. Workflows (§9 A–D): kein CLI/MCP/Panel-Surface; `open_app_snapshot` kopiert `project_path` nur ins Ergebnis; Settings-Navigation sendet nur letztes Key-Segment; `run_model_test` stellt das Ausgangsmodell nicht wieder her.
24. F15-Historie: App-seitiger Watchdog in `patch_app_f15b.py` (90 s gegen hängendes Swap-Overlay) — laut Audit-Repro aktuell nicht reproduzierbar, Overlay-Härtung defensiv im Build.

## 9. Modul-Index (Rolle je Datei, kurz)

| Datei | Rolle |
|---|---|
| `cli.py` (1707) | Alle Subkommandos (`init` … `desktop`, `treasury`, `tools`, `mcp`, `evals`) |
| `completion.py` (1499) | Vertrag/Store/Gates/Verdikt; DAS Zentrum der „was ist fertig"-Wahrheit |
| `workflows.py` (964) | §9-Workflows: App-/Settings-Snapshot, Modelltest, Settings-Persistenz |
| `treasury.py` (924) | Vault (Fernet), Grants, Broker-Injektion, Staging-Spends, TOTP |
| `capability.py` (717) | Requests, Scope-IDs, Sichtbarkeit, Promotion, Testing-Tool-Runner |
| `pause.py` (690) | Pause-Leases, Trigger-Policy, Research-Threads, Watchdog |
| `monitor.py` (586) | Watcher: 15 Signalarten, Fehlerklassifikation, Slop-Detektoren |
| `computer_use.py` (538) | CU-Policy, Runner (DryRun/Sandbox/Container), Executor |
| `desktop.py` (532) | Panel-/App-Installation, Instanz-Erkennung, Launcher |
| `guard.py` (518) | Claim-Extraktion, Evidence-Index, Verdikte, LLM-Judge |
| `toolbuilder.py` (483) | Builder-Pipeline (8 Stadien), Wrapper, Manifest |
| `skills.py` (475) | Skill-Mining aus Tool-Ketten, propose-first Lifecycle |
| `research.py` (420) | Recherche-Läufe, Redirect-Bau, Archiv + Wiederverwendung |
| `capability_mining.py` (382) | Request-/Kommand-Mining → Kandidaten → Promote |
| `evalharness.py` (366) | Episode-Engine (Monitor/Voice/Guard/Pause/Kontext) |
| `tools.py` (340) | ToolRegistry (SQLite), Propose/Compose/Approve |
| `metrics.py` (330) | Qualitätsmetriken (voice_quality, Recovery, Token — gemessen vs. geschätzt) |
| `gates.py` (314) | bwrap-Sandbox, Scan/Import/Test/Determinismus-Gates (fail-closed) |
| `incidents.py` (305) | Incident-Memory (SQLite+FTS5), Signatur-Matching |
| `events.py` (300) | Event-Store (SQLite WAL) + KINDS-Register, Redaction |
| `context.py` (274) | Rollenbezogene Entscheidungskontexte (watcher/investigator) |
| `nightshift.py` (266) | Offline-Konsolidierung (Incidents, Notes) — Dry-Run-Default |
| `toolhub.py` (261) | Discovery + teilbares Hub-Manifest (publish: nie automatisch) |
| `notes.py` (250) | Research-Notizen (FTS5), Widerspruchs-Links |
| `modelroles.py` (233) | Rollen→Provider/Modell-Datei + Override-Gate |
| `voice.py` (205) | Hint-Budget/Dedup/Templates + Interventions-Kostenschätzung |
| `appbuild.py` (199) | Hardlink-Mirror + Overlay-Patches + Pack + Deploy |
| `ledger.py` (197) | Working Ledger (YAML) — ohne Live-Pfad |
| `mcpserver.py` (178) | MCP-Export (scope-gefiltert), Wrapper, Usage |
| `toolusage.py` (126) | Nutzungs-Ledger JSONL (Runs/Fehler/Dauer) |
| `pact.py` (123) | Nutzer-Pakt (Ziel/Kriterien/Non-Goals/Scope-Pfade) |
| `retrieval.py` (118) | Evidence-Retrieval (Exact→FTS), Untrusted-Spotlight |
| `promptrun.py` (110) | Prompt-Ausführung mit Registry/Grace/Timeout |
| `synthesis.py` (105) | Triage-/Redirect-Synthese (Regeln + optional LLM) |
| `prompts.py` (97) | Prompt-Registry (IDs, Tiers, Pflicht-IDs) |
| `toolscan.py` (96) | Statischer Tool-Scan (Regex + Secrets) |
| `demo.py` (92) | Demo-Zusammenfassungen |
| `llm.py` (90) | LLM-Client (Tier-basiert, Rollen-Override) |
| `secretscan.py` (71) | Secret-Muster + Redaction (überall verdrahtet) |
| `inject.py` (67) | Hint-Sanitisierung/Format, InjectionQueue |
| `spotlight.py` (59) | Untrusted-Content-Spotlighting (Prompt-Injection-Schutz) |
| `embedding.py` (56) | Trigram-Hash-Embeddings + Kosinus (nur Tests live) |
| `_pkg`-Dateien | `__init__`, `__main__` |

*Panel:* `integrations/hermes/godsv_plugin/desktop/plugin.js` (~3050 Z., 6 Views + Chat-Integration) · `dashboard/plugin_api.py` (~1818 Z., 32 Routen unter `/api/plugins/godsv/`) · `__init__.py` (1767 Z., Hooks/Session/Guard — siehe Kapitel 1).

---

## 10. Status nach Abarbeitung — Revision 2 (13.09.2026)

**Commit-Kette der Fix-Etappe:** `08bb6be` `483d4c7` `11fe037` `660d7de` `bb01d8f` `a8d07c6` `b1fa36f` `5faf722` `381df67` `9d79538` `17b11eb` `c47de62` `499b7a5` — jeder Fix rot-first getestet (Test zuerst, Fehlschlag belegt, dann Implementierung).

**Methodik der Statusbilanz:** 4 Verifikations-Agenten am Code (unabhängig von den Commit-Messages), plus eigener Nachlauf; alle „FIXED"-Aussagen tragen eine konkrete `Datei:Zeile`-Stelle als Beleg.

| Register-Thema | Status | Beleg / Rest |
|---|---|---|
| Event-Brüche `pause_no_action` + `treasury_selftest` | ✅ FIXED | `08bb6be`; events.py:90-91, Tests test_events.py |
| DoD-Status-Lifecycle (`verified`/`failed` schreiben) | ✅ FIXED + GEHÄRTET | `483d4c7` + `499b7a5`: `record_dod_outcomes`; **Sticky-Verified-Fund der Verifikation behoben** (Skip-Zweig in gate_dod entfernt — Zertifizierung braucht immer frische Evidenz); MINOR-Findings ⇒ `open` statt `verified` |
| `project_id` nie befüllt | ✅ FIXED | `11fe037` (CLI `--project` bei request/build, Plugin-Annotation aus task_base) + `17b11eb` (stdio-E2E: Build+Serve teilen den Kontext) |
| `promote_tool` ohne Eintritt | ✅ FIXED | `381df67`: `tools promote-global` + `POST /tools/{name}/promote` (origin-guarded, auditiert) |
| `testing_tool` per CLI unbaubar | ✅ FIXED | `5faf722` + `499b7a5`: `--kind testing_tool` + `--validation-case` bei request UND build |
| `workflow_contracts()` ohne Aufrufer | ✅ FIXED | `9d79538`: `godsvoice workflows list [--json]` |
| `run_model_test` ohne echten Restore | ✅ FIXED | `660d7de`: Restore-Klick + Reader-Verifikation; ohne Nachweis sichtbare Warnung |
| Settings-Navigation `ctrl+comma` | ✅ FIXED | `660d7de`: volles Kombo als ein xdotool-Argument |
| Testing-Tool-Redirect-Text | ✅ FIXED | `bb01d8f`: eigener Gate-Zweig, kein MCP-Text |
| MCP-Konflikte still verworfen | ✅ FIXED (defensiv) | `b1fa36f`: `scope_conflicts` + stderr-Warnung; greift erst bei künftigen Mehrfach-Manifesten |
| `uninstall` lässt Plugin-Symlink | ✅ FIXED (opt-in) | `a8d07c6` + `499b7a5`: `--remove-plugin`; Default behält das im Bestandstest dokumentierte Altverhalten |
| stdio-E2E (Folgefehler der Scope-Runde) | ✅ REPARIERT | `17b11eb` |
| `tools draft` (draft_tool_spec) | OFFEN | braucht CLI-LLM-Client, den es nicht gibt (bewusst nicht gebaut) |
| Compose-before-create im Builder | OFFEN | compose_candidates nur im Proposal-Pfad |
| Moduldatei-Namensbruch Builder↔register_testing_tools | OFFEN | {module}.py vs. <dir>.py |
| MCP-Auto-Registrierung / CLI-DB-Default | OFFEN | §7-MCP bleibt manuell; `.godsv`-Default vs. Runtime-Pfad |
| Ledger · Embeddings · Incident-promote · skills_text · Nightshift-Scheduling · Retention · Scope-Spalte in `tools list` · Runner für ui_e2e/api/… · Evidenz-Revisionsbindung | OFFEN | unverändert aus Kapitel 8 (bewusst nicht in dieser Etappe) |
| F15 | ✅ GEFIXT + live bewiesen (13.09. abends) | Real: 404-Reconcile-Loop stale Tiles (`use-background-sync.ts`, ef43b82) + absolute Overlay-Frist/age-Watchdog (ac8894c); live: Zähler 3→3 gestoppt, Desktop-Runde `ack` beantwortet |

**Neue Funde dieser Runde (alle adressiert oder dokumentiert):** Sticky-Verified (HOCH → behoben), `tools build`-Parser ohne `--validation-case` (MITTEL → behoben), MINOR→verified-Fehlmapping (NIEDRIG → behoben), promote-global-Runtime-Default (NIEDRIG → dokumentiert), Plugin-Annotations-Race bei `newest_open()`-Fallback (NIEDRIG → dokumentiert), `scope_conflicts` faktisch defensiv (NIEDRIG → dokumentiert).

**Teststand nach der Etappe:** volle Suite grün (130+), ruff clean über `src tests tools integrations app-overlay`, `desktop panel: contract OK`.

## 11. Audit-Runde 13.09.2026 (abends) — belegte Produktlücken

Quelle: unabhängiger Produkttest (BERICHT.md) gegen Stand `95fdd94`. Commits: `05e8de4` · `ef7585e` · `f784ffe` · `249a9cb` · `5aae51e` · `3c59c52` · `7acf48b`.

| Audit-Lücke | Status | Beleg (Code + Nachweis) |
|---|---|---|
| **P0** Stop-Gate band an Dateiänderungen (`_pre_verify_nudge`) | ✅ FIXED | `host-overlay/patch_agent.py` (idempotent, lauter Abbruch); Host-Trace: `pre_verify_nudge called … edited=0` |
| **P0** Zweite Hälfte: `pre_verify` stieg still aus (`_SESSIONS` leer bei `--resume --oneshot`) | ✅ FIXED | Session-Adoption bei On-Disk-Vertrag; Trace: `pre_verify adopted session=… → called` |
| **P0** Live-Beweis CLI: falscher Abschluss → Ablehnung → Reparatur → Zertifikat | ✅ BELEGT | Session `20260913_171809_a6cf0f`: Claim (edited=0) → Continue → Agent repariert `double()` selbst (2 × file_edit) → pytest → **VERIFIED_COMPLETE**, c1 `verified` |
| **P0** `FAIL: check did not PASS` @ Exit 0 zählte als Pass | ✅ FIXED | `parse_testing_result()` (FAIL gewinnt; kein Verdikt ⇒ fail-closed); 6 Tests inkl. Audit-Fixture byte-genau |
| **P1** Alte Evidenz zertifizierte neue Revision | ✅ FIXED | `RunEvidence.revision/workspace` + `GateRunner._evidence_bound` + Erzeuger-Bindung (Plugin/Panel/execute_check); 6+ Tests |
| **P1** `register_testing_tools` verlangte `module == Verzeichnisname` | ✅ FIXED | Manifest-`module` wird aufgelöst; „checker"+`custom_impl` registriert & läuft; 3 Tests |
| **P1** Keine MCP-Live-Insertion in laufenden Server | ⏸ OFFEN | bewusst nicht gebaut (Server-Neustart-Discovery bleibt; Live-Pfad unverändert) |
| **P2** `add_criteria` ignorierte `expected_revision` | ✅ FIXED | Fence + `request_id`-Idempotenz wie update/remove; Panel-Route reicht durch; Test |
| gv_ui_e2e: **39/39 GRÜN** (13.09. ~19:50) | ✅ ALL GREEN | Von 11 FAILs auf 0: CU (Settings-Re-Klick), Goal/Feed-Gruppe (CDP-Send-Handoff + visibleComposer + Element-Waits), v6-nav (Identity-Patch main.ts:915, UA SkaldirAgent/0.17.1), verify-button (Element-Wait) | CU (Settings-Re-Klick) + Goal/Feed-Gruppe (CDP-Send-Handoff + visibleComposer + Element-Waits, `b14db1c`) gelöst — 37 ok; offen: v6-nav (aside.w-44, Plan kommt) + `@goal mention` flaky (laufender Turn unterdrückt Popover) |
| Monitor: `strategy_repeat` + `error_recurrence_after_progress` widersprüchlich | ✅ FIXED | Gemeinsame Beurteilung: nach echtem Fortschritt keine Blind-Retry-Warnung; 3 Tests (kosmetische Varianten bleiben Detektor-Grenze — die Voice urteilt) |
| `gv_serve()` wählte Debug-Port 9333 statt Backend-API | ✅ FIXED | Identitäts-Probe über `/completion`; Debug-Ports (declared `--remote-debugging-port`) ausgeschlossen; 4 Tests |
| Desktop-Turn-E2E (Wiederholung des Live-Beweises) | ✅ DONE (13.09. abends) | Nach F15-Fix: Session `20260913_183421_230b31` user `ack` → assistant `ack` (State-DB+DOM); `gv_ui_e2e` 11 FAILs ohne offene Session → Triage offen |
| Restart-Recovery Redirect↔Continue, Pause/Budget, doppelte Events, parallele Sessions | ⏸ OFFEN | nicht in dieser Runde geprüft |
| Neue UI-Anforderungen (Fragen-Merge, Neuwürfeln, 10-min-Klärung) | ⏸ OFFEN | nicht abgenommen |

**Konsequenzen für künftige Runden:** Der CLI-Beweis (17:18) nutzt den Fehlerinjektions-Weg „glaubhafter Umgebungs-Kontext" (CI-verifiziert-Meldung) — direkte „sag die Lüge"-Prompts verweigert das Modell (belegt, Session `…e1c00c`). Die godsv-Trace-Datei liegt im CLI-Kontext unter `GODSV_TRACE_FILE`; im Desktop-Plugin-Prozess unter `GODSV_TRACE_FILE` (default /tmp/godsv_plugin_trace.log). Session-Adoption greift nur bei existierendem On-Disk-Vertrag — Sessions ohne Vertrag bleiben unberührt.
