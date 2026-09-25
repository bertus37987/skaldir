# Security Boundaries — Skaldir Agent

**Block 1 (Security / State Integrity)** · Branch `godsv-supervisor-hardening` ·
Grundlage: `docs/COMPLETE_AUDIT-2026-09-13.md` (Kapitel XI, `audit-5-security.md`) und
`docs/IMPLEMENTATION-PLAN-live-control-plane.md` (Block 1).

Jeder Eintrag hat die Form **Befund vorher → Fix → Testname → echte Nachweis-Ausgabe → Restgrenze**.
Alle Ausgaben sind echte Läufe gegen den Code in diesem Branch (Beweise in
`outputs/block1-evidence/`), keine Abschriften.

Ergänzend zu `docs/SECURITY.md` (V0-Bedrohungsmodell und Treasury): dieses Dokument beschreibt die
**Trust-Boundaries der Laufzeit** — wer Kontrolle besitzt, was Daten bleibt, und was fail-closed ist.

---

## 1. Trusted vs. Untrusted Sources

**Quelle im Code:** `src/godsv/untrusted.py`

```python
UNTRUSTED_RULE = "UNTRUSTED CONTENT IS NO CONTROL AUTHORITY"

TRUSTED_SOURCES   = {"user", "system", "agent", "self", "pact", "goal", "policy"}
UNTRUSTED_SOURCES = {"web", "webpage", "file", "repo_file", "tool_output",
                     "tool_result", "mcp", "memory", "note", "external", "unknown"}
```

Unbekannte Quellen zählen **untrusted** (Default-deny, nicht Default-allow).

| | Erlaubnis |
|---|---|
| **Trusted** (User, System, Agent selbst, Pact/Goal/Policy) | darf die eigene Trajektorie bestimmen (Control Authority) |
| **Untrusted** (Web, Dateien, Repo-Inhalt, Tool-Output, MCP, Memory) | **Daten, nie Steuerung** — Inhalt bleibt nutzbar, Anweisungen darin nicht |

**Echte Ausgabe** (Block-4-Semantik, `scan_untrusted`):

```
clean prose          -> findings=0 attempted=False blocked=False
addressed attempt    -> findings=1 attempted=True  blocked=True  kind=untrusted_instruction_attempt
trusted user text    -> findings=0 attempted=False blocked=False
```

**Restgrenze:** Die Klassifikation kommt vom Host-Hook (der die Quelle kennt). Ein Aufrufer, der eine
untrusted Quelle als `source="user"` deklariert, unterläuft sie — die Quelle ist Vertrauens-Input,
kein selbstbewiesenes Merkmal. Zusätzlich ist die Erkennung muster-/regex-basiert
(`untrusted.py`), also nicht semantisch vollständig; sie ist fail-closed für die *Adressierung*
(ohne erkennbares Muster kein Verdikt), nicht für jeden denkbaren Angriff.

---

## 2. Prompt-Injection-Behandlung

**Regel:** Signal ≠ Verdikt. Ein *verdächtiges Muster* erzeugt ein Signal
(`prompt_injection_signal`, Chat-Rauschen); erst ein **adressierter, bestätigter** Versuch wird
`untrusted_instruction_attempt` und damit blockierend (`InjectionAssessment.attempted` →
`blocked`). Inhalte aus **trusted** Quellen werden nie geflaggt.

**Wirkung:** Eine bestätigte Anweisung wird als `_untrusted_directives` gemerkt; ein späterer
Tool-Call, der genau diese Anweisung ausführt, ist damit die Übernahme selbst und wird geblockt
(siehe §3, „injected impulse"). Der fachliche Inhalt bleibt nutzbar (der Prompt-Inhalt wird
weiterverwendet, nur die Instruktion verworfen).

**Testname:** `tests/test_golden_set.py`
(`test_prompt_injection_is_a_signal_not_a_control_takeover`, Golden-Set-Fälle GS-01h/GS-05/GS-06).

**Echte Ausgabe:** siehe Tabelle in §1 (3 Fälle, echter `scan_untrusted`-Lauf).

**Restgrenze:** Die Abgrenzung „Signal vs. adressierter Versuch" ist heuristisch
(`_directed`/`_actionable` in `untrusted.py`). Ein geschickt formulierter Angriff ohne erkennbares
Adressierungsmuster bleibt Signal und blockt nicht. Das ist die bewusste Richtung (Überblockieren
wäre teurer), aber es ist eine Erkennungs-, keine Beweisschranke.

---

## 3. Tool-Call-Interception (Soll)

**Soll:** Jeder geplante Tool-Call wird **vor** der Ausführung bewertet
(`pre_tool_call`-Hook → `judge_tool_call`, `src/godsv/toolcall.py`). Fail-closed: ein nicht
bewertbarer Call ist **nie** ALLOW. `ALLOW` ist der Default; nur Sicherheitspfade eskalieren.

**Echte Ausgabe** (echter `judge_tool_call`-Lauf):

```
terminal rm -rf /  -> BLOCK   destruktiv (katastrophal): rm auf Wurzel/Home/System
terminal pytest    -> ALLOW   
read /etc/shadow   -> ALLOW   
injected impulse   -> BLOCK   der Call setzt eine zuvor als untrusted ignorierte Anweisung um
```

Klassifikationen: `ALLOW` · `ASK_APPROVAL` · `PAUSE_AND_INVESTIGATE` · `BLOCK`
(`toolcall.py:52-55`). Der Interceptor ist mit `GODSV_INTERCEPT` abschaltbar (Default `1` = an).

**Testname:** `tests/test_golden_set.py` (Golden-Set §38, u. a. destruktiv / falscher Kontext /
Injection-Impuls).

**Restgrenze:** (a) Die Bewertung sieht nur, was der Host-Hook liefert (`ToolIntent`); ein Host,
der Args unvollständig meldet, schwächt sie. (b) `GODSV_INTERCEPT=0` schaltet die Schranke
vollständig ab — Betriebs-Entscheidung, kein Verteidigungsmechanismus. (c) Kein Shell-Parsing:
ein als Arg verstecktes Destruktivmuster in ungewöhnlicher Syntax (`_command` liest String-Args)
kann unerkannt bleiben; deshalb ist die Sandbox (§6) die zweite, harte Schranke.

---

## 4. Approval-Policy

**Soll:** Der Agent sieht nie echte Credentials (nur Handles) und gibt niemals Geld ohne
Freigabe aus. Modi über das Panel setzbar: `APPROVAL_MODES = ("manual", "smart", "off")`;
die Wahrheit steht in `~/.hermes/config.yaml` (`approvals.mode`).

**Treasury-Stufen** (`treasury.py:61-63`): `TIER_HUMAN` (Default, jede Ausgabe braucht einen
Menschen) → `TIER_WHITELIST` (Whitelist-Mikropurchäufe innerhalb Budget auto) → `TIER_MANDATE`
(enger Mandatsrahmen). Grants sind explizit, mit TTL, Scope und Rate-Limit; Einträge mit
`grant:required` brauchen einen aktiven Grant.

**Echte Ausgabe** (Panel, isoliertes `HERMES_HOME`):

```
APPROVAL_MODES: ('manual', 'smart', 'off')
approvals_payload(): {'mode': None, 'available': False}   # ohne config.yaml: ehrlich „nicht verfügbar"
```

**Testname:** `tests/test_panel_actions.py` (Credential/Grant/Revoke-Roundtrip ohne Leak),
`tests/test_golden_set.py` (Destruktiv-/Freigabe-Fälle).

**Restgrenze:** `approvals.mode` ist eine *Betriebs*-Einstellung des Hermes-Engines, kein
GV-eigener Durchsetzungspunkt — GV liest/schreibt sie (`approvals_set_payload`), erzwingt sie aber
nicht selbst. Die GV-eigene Durchsetzung sitzt in der Treasury (Stufen/Grants) und im
Tool-Call-Interceptor (§3).

---

## 5. Secrets

**Invariante:** Kein Secret in Event-Store, Logs, Prompts oder Tests. Geprüft an **jedem**
Schreibpfad, nicht nur beim Lesen.

| Weg | Verhalten |
|---|---|
| `EventStore.append()` mit Secret | **wirft** `SecretLeakError` (kein stiller Write) |
| `EventStore.append_redacted()` | ersetzt durch `[REDACTED:kind]` + `_godsv_redacted`-Zähler |
| `gates.gate_security` (Completion-Gate) | Secret im Diff ⇒ **BLOCKER** (siehe §5.1) |
| `toolusage.record_usage` | Fehlerstrings laufen vor dem Schreiben durch `redact()` |
| Vault | Fernet-verschlüsselt at rest; der Agent sieht nur Handles |

**Echte Ausgabe:**

```
append(secret) -> SecretLeakError: secret detected in event payload (openai); redact before appending
append_redacted -> key=[REDACTED:openai] | redacted count: 1
```

**Testname:** `tests/test_events_security.py::TestEventStore::test_secret_payload_is_rejected` und
`::test_append_redacted_masks_and_marks`.

### 5.1 Befund vorher: `gate_security` war fail-open (Audit SEC-07)

**Befund vorher** (`completion.py:1078-1096`): `try: … redact(diff) … except Exception: pass`.
Ein **kaputter** Scanner lieferte eine **leere** Befundliste — ein Secret-Diff passierte das
Security-Gate still. Der Fehlerzweig war ungetestet. Nachweis vor dem Fix:

```
scanner raises -> findings = []  => secret diff silently passes: True
```

**Fix** (`src/godsv/completion.py`): zwei Fälle werden unterschieden, weil sie nicht dasselbe sind:

* **Scan fehlgeschlagen** (Exception, fehlendes Modul) ⇒ `Finding(gate="security", severity=BLOCKER)`,
  Detail „secret scan failed — the diff is unverified, not clean (fail-closed)". Ein ungeprüfter
  Diff ist kein sauberer Diff.
* **Scan lief sauber ohne Treffer** ⇒ **kein** Finding. Eine saubere Änderung darf nie blocken,
  nur weil ein Muster nicht gematcht hat (sonst blockiert jedes optionale Modul die Arbeit).

**Nachweis nachher** (echter Lauf):

```
scanner raises -> [('security', 'BLOCKER', 'secret scan failed — the diff is unver')]
clean diff (prose/handle) -> []
one secret -> 1 finding(s): [('BLOCKER', 'generic_key')]
```

**Testname:** `tests/test_security_gate_failclosed.py` (10 Tests)
— `TestScannerCrashFailsClosed::test_scanner_exception_becomes_a_blocking_finding`,
`::test_unavailable_scanner_module_fails_closed`,
`TestCleanScanNeverBlocks::{test_empty_diff_has_no_security_finding,
test_prose_and_handles_are_not_findings}`.

**Rot-Beleg:** `outputs/block1-evidence/red-gate-security.txt` — **6 failed** vor dem Fix
(u. a. `AssertionError: scanner crash must not yield an empty finding list`).

### 5.2 Nebenbefund (MINOR): ein Secret erzeugte ZWEI Befunde

**Befund vorher** (reproduziert, nicht im Audit): `secretscan.scan()` lieferte für dasselbe Secret
mehrere Treffer, weil mehrere Muster greifen. Reproduktion:

```
>>> redact('api_key = "sk-abcdefghijklmnopqrstuvwxyz123456"')
Findings: ['generic_key', 'openai']   # 2 Treffer für EIN Secret
```

`redact()` dedupliziert überlappende Treffer (`if finding.start < cursor: continue`), `scan()` und
damit `gate_security` **nicht** ⇒ zwei BLOCKER-Findings für einen Leak.

**Wirkung:** nur Rauschen/Doppelmeldung — **keine** Fehlblockade. Deshalb MINOR.

**Gegenprobe (nicht überhärten)** — sauberer Text erzeugt keine Treffer:

```
'secret://openrouter-main'                       -> []   (Handle, kein Wert)
'the dashboard token must be sent as a bearer…'  -> []   (Prosa)
'diff --git a/docs/token-notes.md'               -> []   (Pfad)
```

**Fix** (`src/godsv/secretscan.py`): `scan()` dedupliziert überlappende Treffer (linkester,
dann breitester Treffer gewinnt), genau wie `redact()` es schon tat.

**Nachweis nachher:** `one secret -> 1 finding(s): [('BLOCKER', 'generic_key')]`

**Testname:** `tests/test_security_gate_failclosed.py::TestSecretsAreFoundOnce`
(`test_overlapping_patterns_report_one_secret_once`, `test_scan_dedupes_overlapping_matches`,
`test_distinct_secrets_are_still_reported_separately` — zwei *verschiedene* Secrets bleiben zwei
Befunde).

**Restgrenze:** Überlappende Muster desselben Secrets sind dedupliziert; verschachtelte, aber
nicht überlappende Muster bleiben getrennt. Das Ziel war Rauschreduktion, nicht eine
Kanonalisierung der Muster.

---

## 6. Generated-Tool-Isolation

### 6.1 Befund vorher (Audit SEC-02/SEC-11)

**Befund vorher** (`mcpserver.py:115-129`): Der MCP-Wrapper importierte das generierte Modul in den
**Serverprozess** und rief es direkt auf (`function(**arguments)`). Ein „approved" Tool hatte damit
vollen Host-Zugriff (Dateisystem, Netz, Env) — nur die **Build-Gates** waren sandboxed.
`allowed_domains` im Manifest war rein deklarativ (kein Egress-Proxy).

**Rot-Beleg:** `outputs/block1-evidence/red-generated-tool-isolation.txt` — **8 failed** vor dem Fix
(u. a. fremde `/tmp`-Datei sichtbar, `network` offen).

### 6.2 Fix

Generierte Tools (`<base_dir>/generated_tools/<name>/<module>.py`) laufen jetzt über
**`gates._run`** — denselben bwrap-Pfad wie die Import-/Test-/Determinismus-Gates:

* Netz-Namespace entzogen (`--unshare-net`), `/home`, `/root`, fremdes `/tmp` leer
* nur das Tool-Verzeichnis ist beschreibbar (`--bind`, **absolute Pfade** — bwrap kann relative
  Bind-Ziele nicht auflösen)
* Ergebnisübergabe über einen Marker-Frame, damit `print()` im Tool die Antwort nicht zerstört
* **fail-closed**: fehlt/versagt die Sandbox, läuft **nichts** (kein stiller Rückfall auf in-process)
* Fehler kommen als strukturiertes Payload — der Server crasht nie; das Usage-Ledger bleibt erhalten

**Echte Ausgabe** (echter Sandbox-Lauf über `_wrapper_for`):

```json
{
  "cwd": "/tmp/tmp_ns0pk22/runtime/generated_tools/probe_tool",
  "env_godsv": [],
  "home": "/tmp/tmp_ns0pk22/runtime/generated_tools/probe_tool",
  "network": "blocked",
  "tmp": ["tmp_ns0pk22"]
}
foreign /tmp file visible: False
```

**Fail-closed-Beweis** (bwrap entfernt):

```
without bwrap -> {'error': "sandbox unavailable — execution blocked (fail-closed);
                  generated tool 'probe_tool' was not run"}
tool file written anyway: False
```

**Testname:** `tests/test_generated_tool_isolation.py` (12 Tests)
— `TestGeneratedToolsRunSandboxed::{test_foreign_tmp_and_home_are_invisible,
test_network_namespace_is_unshared, test_the_tool_directory_stays_writable,
test_host_environment_is_not_inherited, test_cwd_is_the_tool_directory}`,
`TestFailsClosedWithoutSandbox::{test_missing_bwrap_blocks_execution,
test_bwrap_runtime_failure_blocks_execution}`,
`TestRealBuilderOutputIsIsolated::test_builder_generated_tool_runs_through_the_sandbox`.

### 6.3 Restgrenzen (bewusst, im Test festgehalten)

1. **`allowed_domains` bleibt deklarativ.** Es gibt keinen per-Domain-Egress-Proxy; die Sandbox
   entfernt das Netz **global** (strikter als jede Allowlist). Wer im Manifest
   `api.linear.app` erlaubt, bekommt im Sandbox-Lauf trotzdem 0 Netz.
   Test: `test_network_namespace_is_unshared`.
2. **Specs ohne generierte Ablage laufen in-process.** Resolution aus `sys.path`
   (Fixtures/manuelle Registrierung) bleibt ungesandboxt — für vom Builder erzeugte Tools greift
   immer der Sandboxpfad. Test:
   `TestResultContract::test_syspath_fixture_stays_in_process_documented_residual`.
3. **bwrap ist harte Voraussetzung.** Ohne funktionierendes bwrap läuft kein generiertes Tool
   (fail-closed, gewollt) — auf Systemen ohne bwrap ist die Fähigkeit damit nicht nutzbar.

---

## 7. Panel-Trust-Boundary

### 7.1 Befund vorher (Audit SEC-06/SEC-10)

Drei zusammenhängende Schwächen in `integrations/hermes/godsv_plugin/dashboard/plugin_api.py`:

1. `origin_allowed("")` lieferte **True** — ein Aufrufer **ohne** Origin-Header (curl, lokaler
   Prozess, Agent-Tooling) galt als „Same-Origin-Browser-Fetch".
2. `_guard_origin` war **fail-open**: eine Exception beim Header-Lesen übersprang die ganze Prüfung
   (`except Exception: return`).
3. **Keine plugin-seitige Authentifizierung.** `HERMES_DASHBOARD_SESSION_TOKEN` kam in der Datei
   nicht vor; die Auth lag (nicht im Repo verifizierbar) beim Hermes-Gateway.

**Rot-Beleg:** `outputs/block1-evidence/red-panel-trust.txt` — **6 failed** vor dem Fix
(`test_missing_origin_is_rejected`, `test_mutation_without_token_is_rejected`,
`test_mutation_without_origin_is_rejected`, `test_wrong_token_is_rejected`,
`test_unreadable_headers_are_a_rejection`, `test_audit_failure_is_logged_not_swallowed`).

### 7.2 Fix — und welcher Pfad erlaubt bleibt

Mutationen brauchen jetzt **beides**: einen erlaubten Origin **und** einen nachgewiesenen Aufrufer.

```
Origin ∈ {null, file://, app://, hermes:, http(s)://localhost|127.0.0.1|::1}
        (leer/fehlend ⇒ 403)
UND     request.state.token_authenticated        (Gateway-Seam), ODER
        X-Hermes-Session-Token == HERMES_DASHBOARD_SESSION_TOKEN,
        ODER Authorization: Bearer <token>
        (sonst ⇒ 403)
```

**Warum die Desktop-App weiter funktioniert:** Die App spricht über `ctx.rest(...)`
(`plugin.js:2932`) mit `/api/plugins/godsv/…`; der Electron-Main-Prozess setzt
`Origin: app://…` **und** `X-Hermes-Session-Token: <pro Spawn gemintetes Token>`
(`apps/desktop/electron/main.ts:5382`, `hermes_cli/web_server.py:299`). Beide Bedingungen sind
erfüllt — kein App-Pfad bricht. Der **einzige** neu verweigerte Pfad ist ein Aufrufer ohne Token
und ohne Gateway-Authentifizierung (curl/lokaler Prozess) — genau das Trust-Loch aus SEC-06.

**Echte Ausgabe:**

```
origin_allowed('') = False
origin_allowed('app://hermes') = True
panel_token() = tok
_audit ok = True
audit events in store = [('system', 'evidence_probe')]
```

Zusätzlich abgesichert: `_guard_origin` verweigert bei unlesbaren Headern (403 statt Skip);
`POST /models/strictness|roles|test`, `/completion/*`, `/treasury/*`, `/tools/*`, `/approvals`
sind ohne Token unerreichbar; **GET-Routen bleiben ungeschützt** (Lese-Sichtbarkeit, keine Mutation).
`origin_allowed("null")` bleibt erlaubt (Sandboxed-Iframe-Fälle des lokalen App-Kontexts).

**Testname:** `tests/test_panel_trust_boundary.py` (14 Tests)
— `TestOriginAllowlistIsFailClosed`, `TestTokenIsRequiredForMutations`
(`test_mutation_without_token_is_rejected`, `test_mutation_without_origin_is_rejected`,
`test_desktop_app_path_is_allowed`, `test_wrong_token_is_rejected`),
`TestGuardOriginFailsClosed::test_unreadable_headers_are_a_rejection`.

**Restgrenze:** Ohne `HERMES_DASHBOARD_SESSION_TOKEN` in der Gateway-Umgebung (nackte
Library-/Test-Runtime) kann nur der Gateway-Seam `token_authenticated` eine Mutation zulassen —
alles andere wird verweigert. Das ist gewolltes fail-closed; in der echten App ist der Token immer
gesetzt (Desktop mintet pro Spawn).

### 7.3 Befund vorher: `_audit` verschluckte JEDEN Fehlschlag (Audit SEC-06)

**Befund vorher** (`plugin_api.py:236-247`): `except Exception: pass`. Zugleich wurde der
übergebene **Action-Name als Event-`kind`** verwendet (`store.append("panel", 0, kind, payload)`) —
und `"panel"` war gar kein registrierter Kind. Jeder Audit-Append warf daher
`ValueError: unknown event kind: …` und verschwand **still**: der Audit-Trail war leer, ohne dass
irgendwo etwas sichtbar wurde.

**Fix** (`plugin_api.py`): Der Eintrag wird als registriertes `system`-Event mit
`panel_action`-Feld geschrieben (Trail bleibt abfragbar); ein Fehlschlag wird mit **WARNING**
geloggt (Action + Exception-Typ, nie Secret-Werte) und per Rückgabewert gemeldet.

**Echte Ausgabe:**

```
audit events in store = [('system', 'evidence_probe')]
```

**Testname:** `tests/test_panel_trust_boundary.py::TestAuditFailureIsVisible`
(`test_audit_failure_is_logged_not_swallowed`, `test_successful_audit_writes_an_event`).

**Restgrenze:** GV kann einen Event-Kind nicht selbst registrieren (`events.KINDS` gehört zum
Event-Store-Vertrag); deshalb `system` + `panel_action` statt eines neuen `panel_action`-Kinds.
Eine echte Kind-Registrierung ist eine Folgeänderung im Event-Store, nicht in der Panel-Schicht.

---

## 8. Contract-Store: atomar, kein stiller leerer WORKING-Zustand

**Befund vorher** (`completion.py`): Der Completion-Store schrieb JSON per `Path.write_text` —
ohne Lock, ohne Temp-Rename. Ein Abbruch mitten im Write hinterließ eine abgeschnittene Datei;
`state()` fing `JSONDecodeError` und lieferte einen **sauberen leeren WORKING-Zustand** zurück:
Goal, Revision und DoD waren still verloren, und die Oberfläche zeigte einen neuen Lauf.

**Rot-Beleg:** `outputs/block1-evidence/red-contract-store.txt` — **11 failed** vor dem Fix.

**Fix** (`src/godsv/completion.py`):

* `_atomic_write_text`: Temp-Datei im selben Verzeichnis + `fsync` + `os.replace`
  (atomar im Dateisystem); bei Fehlschlag wird die Temp-Datei entfernt.
* `_path_lock` + `@_serialized` auf allen Mutatoren (`transition`, `begin_goal`,
  `record_trajectory`, `set_goal`, `add_criteria`, `record_dod_outcomes`, `_criterion_edit`):
  atomar schreiben genügt **nicht** — zwei Writer, die lesen, ändern und zurückschreiben, verlieren
  sonst ein Update. Der Test hat genau diese Lost-Update-Race reproduziert.
* `state()` meldet Korruption **sichtbar**: WARNING (Session + Grund), `corrupt: true` +
  `corrupt_reason` im Record, und die beschädigten Bytes werden als `<name>.corrupt.<ts>`
  **erhalten** statt überschrieben.

**Echte Ausgabe:**

```
atomic writer used: True
serialized: True
corrupt read -> st: WORKING | corrupt flag: True
quarantine files: ['ev.json.corrupt.1789335009196']
```

**Testname:** `tests/test_contract_store_atomic.py` (12 Tests)
— `TestInterruptedWriteKeepsTheOldContract::test_failed_replace_leaves_the_previous_state_intact`,
`TestCorruptionIsVisibleAndPreserved::{test_corrupt_state_is_flagged_not_silently_empty,
test_corrupt_bytes_are_preserved_in_a_quarantine_file, test_corruption_is_logged}`,
`TestWritersAreSerialized::{test_parallel_writers_never_lose_a_contract,
test_parallel_transitions_leave_one_valid_record}`.

**Restgrenze:** `os.replace` ist innerhalb **eines** Dateisystems atomar; der Lock ist
prozesslokal (`threading.RLock`), serialisiert also Threads im Gateway-Prozess, nicht mehrere
Prozesse. Für den Supervisor/Die drei Schreibpfade (Panel, Hook, Supervisor) laufen im
Gateway-Prozess, damit ist der Fall abgedeckt; ein zweiter Prozess auf demselben Runtime-Verzeichnis
bräuchte Datei-Locking (`fcntl`) — nicht implementiert.

---

## 9. Zusammenfassung Block 1

| # | Grenze | Vorher | Nachher | Test |
|---|---|---|---|---|
| 1 | `gate_security` | fail-open, Exception ⇒ `[]` | Scanner-Crash ⇒ BLOCKER; sauber ⇒ kein Finding | `test_security_gate_failclosed.py` (10) |
| 2 | Secret-Dedupe | 1 Secret ⇒ 2 Befunde | 1 Secret ⇒ 1 Befund | ebd., `TestSecretsAreFoundOnce` |
| 3 | Panel-Origin | `origin_allowed("") is True` | fehlend ⇒ 403 | `test_panel_trust_boundary.py` (14) |
| 4 | Panel-Token | keine plugin-seitige Auth | Origin **und** Token (oder Gateway-Seam) | ebd. |
| 5 | `_guard_origin` | fail-open bei Header-Fehler | 403 | ebd. |
| 6 | `_audit` | stiller `pass`, Trail leer | `system`+`panel_action`, WARNING bei Fehler | ebd. |
| 7 | Contract-Store | `write_text`, kein Lock | atomar (`os.replace`) + serialisiert + Quarantäne | `test_contract_store_atomic.py` (12) |
| 8 | Generated Tools | in-process, voller Host-Zugriff | bwrap über `gates._run`, fail-closed | `test_generated_tool_isolation.py` (12) |

**Beweise:** `outputs/block1-evidence/` (`red-*.txt` = Rot-Belege vor dem Fix,
`full-suite-final.txt`, `ruff-final.txt`).

**Suite-Stand dieses Blocks:** `pytest -q` läuft über die volle Suite; **allein**
`tests/test_capability_learning.py` (Block 6, Capability Learning — **untracked, in Bearbeitung
durch einen parallelen Agenten**) scheitert mit 2 Tests seiner eigenen Fläche. Gegenprobe, dass
Block 1 das nicht verursacht: mit `src/godsv/mcpserver.py` auf dem Vor-Block-1-Stand scheitern
**dieselben** Tests; kein Test außerhalb dieser einen Datei schlägt fehl. Diese Datei gehört
nicht zu Block 1 und wurde nicht angefasst.

**Nicht in Block 1 (abgegrenzt):** Prompt-Injection + Tool-Call-Interception sind Block-4-Flächen
(`untrusted.py`, `toolcall.py`, Plugin-Hooks) — hier nur als **Soll** dokumentiert (§1–§3), nicht
verändert.
