# SECURITY — Skaldir Agent

Scope dieses Dokuments: V0-Kern. Basis-Audit durchgeführt am 11.09.2026.
Voll-Audits folgen je Meilenstein (V1.5 Tool-Sandbox, V2.5 Treasury) — siehe STATUS.md.

## Bedrohungsmodell (Kurzfassung)

| # | Bedrohung | Vektor | Gegenmaßnahme (V0) | Status |
|---|---|---|---|---|
| T1 | Secret-Leak in Trajektorie/Store | Tool-Output, Keys in Args | `secretscan`-Gate am EventStore: `append()` wirft `SecretLeakError`; `append_redacted()` maskiert (`[REDACTED:kind]` + Marker); `godsvoice scan` als CLI | ✅ implementiert + Tests |
| T2 | Prompt-Injection über externe Inhalte (Web/Docs/Tool-Output) | Retrieval → Prompt | `spotlight()`-Wrapper (UNTRUSTED-Marker), `sanitize()` entfernt Zero-Width-/Control-Zeichen; Policy: externe Inhalte sind Daten, nie Instruktionen | ✅ Basis |
| T3 | Missbrauch des Voice-Kanals selbst | kompromittierter Wissensspeicher → `[voice]`-Text | Hinweis-Sanitizing (Control-Strip, Word-Cap 220), Advisory-Footer („keine Anweisung“), Belege als Pflichtfeld, Budget max. 5/Task, Dedup pro Signatur | ✅ Basis |
| T4 | Persistierte Instruktions-Payloads | Tool-Args/-Ergebnisse | Payload-Größenlimit (100 k chars), Events als JSON (kein `eval`/Code-Exec), Fremdtext wird nie ungeprüft in Prompts re-promotet | ✅ Basis |
| T5 | Command-/Code-Injection durch generierte Tools | Tool-Synthese | Spec-first + Scan-Gate (high blockt) + Gate-Runner (Import/Tests/Determinismus, Timeout) + bwrap-Sandbox; MCP nur für `approved` Tools | ✅ Kern V1.5 |
| T6 | Credential-Abfluss durch den Agenten | Treasury-Zugriffe | Broker/Injection (Agent sieht nur Handles), Scopes, Rate-Limit, Grants, Audit, Revoke; Scrub der Broker-Ausgaben (`[REDACTED:treasury]`); „Mail + Recovery = Identität“-Regel | ✅ Kern V2.5 |
| T7 | Lieferketten-Risiko | Dependencies | minimale Deps (PyYAML; dev: pytest/ruff/bandit), CI-Gate; `pip-audit` geplant | ⏳ |

## Harte Invarianten (aus CORE-PLAN)

1. **Kein Secret** in Store, Logs, Prompts oder Tests — Scanner + Gate an jedem Schreibpfad.
2. **Append-only:** vergangene Konversationsinhalte werden nie mutiert; Hinweise sind additiv (cache-safe).
3. **Treasury-Regel:** Der Agent sieht nie echte Karten-/Bankdaten oder Passwörter — nur Handles.
4. **Kein „done“ ohne Beleg:** das Ledger verweigert `evidenced` ohne Evidenz-Notiz (erzwungen im Code).
5. **Fremdtext = untrusted:** externe Inhalte erreichen Prompts nur über `spotlight()`.
6. **Computer Use ist isoliert:** Aktionen laufen nie auf dem Live-Desktop — die
   Isolationspflicht ist im Code erzwungen (PolicyViolation statt Konvention).
7. **Geld ist gestuft:** keine Ausführung ohne explizite Owner-Approval oder erfüllte
   Policy (Whitelist+Caps) **und** gültigen, passenden Spend-Intent; Stufe 0 ist Default.

## Audit-Log

### 2026-09-11 — V0 Baseline-Audit (Self-run, vor Publish)

- `ruff check src tests` → clean.
- `bandit -r src` → **0 Findings** (Low/Med/High: 0/0/0; ein dokumentierter B105-False-Positive
  per `# nosec` markiert und begründet).
- `pytest` → **54 passed**; darunter die Security-Suite: EventStore-Gate (2), SecretScanner (4),
  Spotlight (3), Injection-Sanitizing (1).
- Threat-Coverage: T1–T4 implementiert und getestet; T5–T7 bewusst auf spätere Meilensteine
  terminiert (nicht „vergessen“ — Terminierung ist Teil des Plans).

**Offene Punkte aus dem Audit:**
- `transform_tool_result`-Delivery erst nach Review + Shadow-Mode aktivieren (Injection-Pfad!).
- Fuzzing der Parser (`secretscan`, `spotlight`) und `pip-audit` in CI nachrüsten.
- Re-Scan-Regeln: bei jedem neuen Codepfad, der Fremdtext persistiert, T2/T4-Checkliste anwenden.

### 2026-09-11 — Live-Härtung (Plugin-Betrieb, nach Erst-Deployment)

- **Befund:** Hermes ruft Hook-Callbacks auf Worker-Threads auf; die SQLite-Connection war
  thread-gebunden → Events fielen je nach Lauf **still** aus (Root-Cause per Debug-Trace gefunden:
  `SQLite objects created in a thread can only be used in that same thread`).
- **Fix:** `EventStore` mit `check_same_thread=False` + `RLock` (alle Zugriffe serialisiert);
  Plugin serialisiert `observe()` + Session-Erzeugung; 2 Regressionstests (Cross-Thread-Write,
  4 Threads × 10 Writes).
- **Delivery-Pfad verifiziert:** `transform_tool_result` live getestet — Hint wird nur **additiv**
  ans Tool-Ergebnis gehängt (kein Rollen-/History-Umbau, cache-safe); `hint_delivered`-Events
  sind im Store auditierbar. Beobachtete Reihenfolge: transform feuert *vor* `post_tool_call`
  derselben Tool-Nutzung → Hint reitet auf dem nächsten Tool-Ergebnis.
- Bandit erneut 0 Findings (B108 bereinigt: `tempfile`-Pfad statt hartem `/tmp`).

### 2026-09-11 — Memory-Layer (V0.5)

- **Propose-first:** Incidents starten als `candidate`; nichts promotet automatisch (`promote()` explizit).
  Nacht-Konsolidierung ist Dry-Run per Default (`--apply` opt-in). **Archived ist unsichtbar** für
  Exact- und FTS-Retrieval — Lücke im Live-Test gefunden und gefixt (archivierte Einträge tauchten
  noch in Belegen auf).
- **Untrusted-Evidence:** Notes (extern hergeleitet) werden beim Rendern durch `spotlight()` geführt;
  Incident-Belege sind internes Wissen. Belege sind additiv und advisory, nie Weisungen.
- **Budgets:** `BudgetTracker` erzwingt harte Tiers-Limits; LLM-Aufrufe laufen ausschließlich über
  die `LLMClient`-Schnittstelle (Provider-kapselbar, testbar ohne Netz).
- Verifiziert live: Exact-Match-Beleg im Hint (Fingerprint `67e0831…`), Delivery in Session
  `…165731_1fe245`; **116 Tests grün**, ruff clean, bandit 0.

### 2026-09-11 — Voice v2 / LLM-Synthese + Skill-Mining (V1)

- **LLM-Zugriff nur über den Host:** `ContextLLM` ruft ausschließlich `ctx.llm` (host-owned
  side-LLM) **ohne Provider-/Modell-Overrides** → keine Plugin-Capability nötig (sonst
  fail-closed); jeder Call wird host-seitig auditiert (`purpose="godsv:voice_synthesis"`).
  Keine direkten Provider-Calls im Kern.
- **Hook blockiert nie:** Synthese läuft in einem Daemon-Thread. Livedaten: Ein Host-Call dauerte
  ~25 s (der `timeout`-Kwarg ist in v0.21.1 advisory) — synchron hätte das den Tool-Loop innerhalb
  des 30-s-Hook-Timeouts gestallt. Policy: Upgrade nur, wenn rechtzeitig fertig; sonst geht das
  Template sofort raus — **nichts geht verloren, nichts verzögert den Loop** (live: Hinweis ~1 s
  später ausgeliefert statt 25 s Stall).
- **Skill-Vorschläge sind Daten, keine Ausführung:** Kandidaten werden gespeichert und **nie
  automatisch in Prompts injiziert**; Promotion nur explizit (propose-first); Drift-Demotion
  schützt vor still veralteten Skills; Bodies werden gegen SKILL.md-Frontmatter validiert.
- Verifiziert live: `held-for-synthesis` + Delivery mit Beleg (Session `…172239_4b76bd`);
  **144 Tests grün**, ruff clean, bandit 0.

### 2026-09-11 — Triage/Judge + Skill-Review + Eval-Harness (V1-Rest)

- **Triage-Unterdrückung ist fail-safe:** unterdrückt wird nur bei **explizitem** `stuck=false`
  (ein fehlendes Feld zählt nie als „safe"), Konfidenz ≥ 0.6, und nur für `loop`-Signale. Bei
  Parse-/Client-Fehlern bleibt der Hinweis. Jede Entscheidung ist als
  `hint_triage`/`hint_suppressed`-Event + Inbox-Eintrag auditierbar — live belegt
  (Session `…172854_29c5bf`: Fehlalarm in 3 s unterdrückt, conf 0.85).
- **Evidence-Judge:** nur fuzzy Incident-Treffer (nie Exact-Hits, nie Notes); Judge-Fehler =
  fail-open (Belege bleiben); Ergebnis als `evidence_judged`-Event.
- **Skill-Review:** Promotion nur nach deterministischer Re-Verifikation (Muster gegen den
  Event-Store + gültiges SKILL.md) oder explizitem `--force`; `export` verweigert ungültige
  Bodies. Promotete Skills werden weiterhin **nie automatisch** in Prompts injiziert.
- **Eval-Harness in CI:** 6 deterministische Episoden prüfen Recall UND Stille bei gesunder
  Arbeit (Präzision) — läuft über pytest in jeder CI-Runde.
- **162 Tests grün**, ruff clean, bandit 0.

### 2026-09-11 — Tool-Suiten (V1.5-Kern)

- **Spec-first + propose-first:** Tools sind Spezifikationen; die MCP-Registrierung umfasst
  ausschließlich `approved` Tools, Approval ist ein expliziter Schritt **nach** den Gates.
- **Gate-Runner offline + sandboxed:** Subprozesse mit Minimal-Env und hartem Timeout; mit
  `bwrap`: Netzwerk-Namespace getrennt, Root read-only, Workspace rw (Fallback ohne bwrap
  wird im Gate-Detail ausgewiesen — Gates hängen nie und skippen nie still). Scan-Gate blockt
  `eval`/`exec`/`__import__`/Shell/ctypes und eingebettete Secrets (live: `eval`-Datei blockt).
- **MCP-Oberfläche minimal:** nur approved Python-Tools; Aufruf-/Import-Fehler werden als
  strukturierte Payloads zurückgegeben (kein Server-Crash); Secret-Scan umfasst auch Tool-Quellen.
- **201 Tests grün**, ruff clean, bandit 0; E2E-Smoke: stdio-Server → Tool-Call roundtrip ok.

### 2026-09-11 — Computer Use (V2-Kern)

- **Isolationspflicht:** `CUPolicy.require_isolation` verweigert Runner mit `isolation='none'`
  (Test: LiveRunner wird **ohne Ausführung** abgelehnt). Container laufen `--network none`,
  `--rm`, `no-new-privileges`; bwrap zusätzlich mit netns-off und read-only Root.
- **Verifikationspflicht:** Versuche ohne Erwartung werden geblockt (`cu_blocked`-Event);
  jede Aktion, jede Verifikation, jeder Fallback und jeder Abbruch ist als Event auditierbar;
  Stop-Bedingungen (`max_actions`, `max_fallbacks`) begrenzen Läufe.
- **GUI nur im Container:** der vollständige Maus/Tastatur-Zyklus läuft in
  `localhost/godsv-cu-x11` (Xvfb) — nie auf dem Host-Display; eine Steuerung der echten
  Session ist per Policy ausgeschlossen.
- **224 Tests grün**, ruff clean, bandit 0; live: `godsvoice cu check` = alle Isolationsstufen
  verfügbar (dry-run, bwrap, podman, GUI-Image).

### 2026-09-11 — Treasury (V2.5/V3-Kern)

- **Vault verschlüsselt at rest:** Fernet; Schlüssel deterministisch aus einem Master-Secret
  (scrypt, Nie-gespeichert-Regel) — Live-Beweis: das Test-Secret kommt im Vault-File **0×**
  im Klartext vor; falscher Schlüssel ⇒ `VaultError`; Expiry/Revoke wirksam.
- **Agent sieht nie Secrets:** `list_handles()`/`describe()` liefern ausschließlich Metadaten
  (Handle, Scopes, Beschreibung). Nutzung ist **Injection**: der Broker führt Kommandos in der
  bwrap-Sandbox aus, das Secret lebt nur in der Kind-Env — und sogar wenn das Kind es auf
  stdout schreibt, wird die Ausgabe **gescrubbt** (`[REDACTED:treasury]`), der Nutzen bleibt
  (Längen/Ergebnis), das Secret nicht. Scopes und Rate-Limits werden vor jedem Lauf erzwungen;
  `grant:required`-Handles verlangen einen aktiven, vom Besitzer erteilten Grant.
- **Geld getstaged:** Default **Stufe 0** — jede Ausgabe startet `pending_human`; ein `complete`
  ohne Approval ist live nachweislich wirkungslos (`nicht möglich…`). Caps (per-Tx/Woche/Monat),
  Whitelist (Stufe 1) und Mandat-Presets (Stufe 2: 20/50) werden im Code geprüft; ein
  **verifizierbarer Intent** (Betrag, Merchant, Währung, Expiry) muss zur Anfrage passen, sonst
  verweigert das Ledger — auch bei bereits erteilter Approval blockt ein abgelaufener Intent.
- **Secret-Eingabe ohne Leak-Pfade:** `godsvoice vault add` liest per `getpass` (nie argv, nie
  History, nie Chat); Audit-Events für jede Nutzung/Ablehnung (`treasury_*`, `spend_*`).
- **22 Treasury-Tests** grün (inkl. RFC-6238-Testvektoren und CLI-E2E); ruff clean, bandit 0.

### 2026-09-11 — Release-Härtung (Completion-Guards + Pause/Resume)

- **Gegen den Agenten gerichtete Kanäle sind bounded und trennscharf:** `pre_verify`-Nudges sind one-shot pro Turn (attempt-gated) und durch `agent.max_verify_nudges` gedeckelt; der `transform_llm_output`-Footer ist rein additiv (finaler Text); im Shadow-Modus wird **nichts** gesteuert (nur beobachtet + auditiert).
- **Pausen-Leases:** TTL + Heartbeat + Watchdog; nur `kind='voice'` wird je automatisch freigegeben — `user`/`safety` sind getrennte Namespaces, und kein Pause-Pfad erteilt Berechtigungen. Delivery ist exactly-once (bedingter DB-Übergang); verspätete Ergebnisse werden gegen die Basis-Step verworfen (stale).
- **Recherche-Isolation:** Vollmaterial landet redigiert im Archiv, in den Loop geht nur der verdichtete Redirect; harte Zeit-/Token-Budgets; keine Netzwerk-/Credential-Zugriffe im Runner.
- **Deterministischer Kern entscheidet:** Die LLM-Zweitmeinung (`completion_check`) kann nur **zusätzliche** Befunde liefern (fail-open) — nie Befunde wegnehmen; Beleg-Regel: nur Ausführungs-Tools zählen (Schreib-Tools nie).
- Verifiziert: volle Suite grün, 15/15 Eval-Episoden, ruff clean, bandit 0; Live-E2E in echten Hermes-Sessions (Pause-Delivery `…183209_ec14e0`, Guard-Nudge + Footer — Events + Session-Export + Trace).

### 2026-09-12 — Treasury-Re-Audit (Release)

**Geprüfte Grenze:** Angreifer = der Agent selbst (bzw. sein Terminal-Zugriff); Ziel =
Secrets, Berechtigungen, (Test-)Geld. **Modell:** gleicher Unix-User ⇒ keine echte
Isolation — Schutz kommt aus Injection statt Offenlegung, Env-Hygiene und Audit.

Findings → Status (alle mit Tests in `tests/test_treasury_hardening.py`):

| # | Finding | Härtung |
|---|---|---|
| T-A1 | `totp_code` umging `grant:required` | Grant-Pflicht wie bei `run_with_secret` |
| T-A2 | Auto-Approve (Stufe 1/2) ohne Intent | Auto-Approve **nur mit verifizierbarem Intent** (Betrag/Merchant/Währung gepinnt), sonst `pending_human` |
| T-A3 | Beträge `NaN/inf/≤0` möglich | Validierung vor jedem Schreibpfad → `denied` |
| T-A4 | Vault/State-Dateien erbten umask (0644) | Datei 0600, Eltern-Ordner 0700 (Vault, SQLite, WAL/SHM, Lock) |
| T-A5 | Vault-RMW nicht cross-Prozess-sicher | `flock`-Lock für `add`/`revoke` (best effort, POSIX) |
| T-A6 | Scrub ließ URL-encoded/gekürzte Varianten durch | Varianten-Scrub (URL-encoded, getrimmt) bei Länge ≥ 8 |
| T-A7 | Secret im `purpose` hätte Event-Append **crasht** | Broker scrubbt `purpose` vor dem Record; `_record` fällt auf `append_redacted` zurück (fail-safe, nie Klartext) |
| T-A8 | Master-Key könnte per Env ins Sandbox-Kind wandern | **Kein Fund** — `gates._run` baut ein hartes Minimal-Env (kein Parent-Erbe); Canary-Regressionstest (`GODSV_VAULT_KEY` nie im Kind) |
| T-A9 | Vault im Agenten-Kontext? | **Kein Fund** — Plugin/MCP importieren Treasury nicht; Vault öffnet nur in Owner-Prozessen (CLI). Key-Quelle bleibt `GODSV_VAULT_KEY` (Owner-Shell), Warnung: nie in der Shell exportieren, aus der der Agent-Host startet |
| T-A10 | Statisches KDF-Salt | **Bewusst so belassen** (Domain-Trennung, kein Geheimnis; Migration hätte Re-Encrypt-Risiko kurz vor Release). Upgrade-Pfad (per-Vault-Salt + Re-Encrypt) dokumentiert in `treasury.py` |

**Rest-Risiken (benannt, akzeptiert):** lokaler Brute-Force gegen den Vault ist möglich
(verschlüsselt at rest); Same-User-Zugriff auf Owner-Dateien ist nicht abwehrbar — deshalb
gilt „Agent sieht nur Metadaten/Handles" plus Injection-only-Nutzung als Primärschutz.

### 2026-09-12 — Desktop-Launcher (Release)

- **Angriffsfläche klein gehalten:** `.desktop` startet einen **absoluten** CLI-Pfad ohne
  Shell (`Exec=<absoluter godsvoice> desktop start`), `Terminal=false`; keine
  benutzerdefinierten Schema-Handler, keine Daemons.
- **Install nur user-lokal** (`~/.local/share`, `XDG_DATA_HOME`), kein sudo; `uninstall`
  entfernt nur eigene Artefakte; der `~/.local/bin`-Symlink wird nur ersetzt, wenn er auf
  uns zeigt (fremde Dateien werden nie überschrieben).
- **Deployment-Check vor jedem Start:** `godsvoice desktop start` verifiziert Hermes-CLI,
  Plugin-Link und Entry — Fehler enden als klare Meldung, nie als stiller Fehlstart.
- **UI bleibt logo-frei**; das Icon ist ausschließlich App-Menü-/Launcher-Icon, das
  ANSI-Logo ausschließlich CLI-Banner.
