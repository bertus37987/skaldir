# Live verifizierte Befunde (eigene Prüfung, 13.09. 21:49)
Quelle: eigene Code-Prüfung + reproduzierbare Probe, unabhängig von den laufenden Agenten.

## 1. `gate_security` fail-open — BESTÄTIGT (Ort: `src/godsv/completion.py:1078-1096`)
```python
try:
    from .secretscan import redact
    _cleaned, scan_findings = redact(facts.diff_text)
    ...
except Exception:
    pass          # Z. 1094-1095
```
Ein Scanner-Fehler ⇒ `findings = []` ⇒ Secret-Diff passiert still. Belegt durch
(a) Live-Probe im Agenten-Transkript (`scanner raises -> findings = [] => secret diff
silently passes: True`) und (b) eigene Codeprüfung. **Severity: wie im Audit (SCHWACH/fail-open).**

## 2. KEIN Fehlalarm auf sauberem Text — gegenprüft
Eigene Probe mit `godsv.secretscan.redact` (5 Stichproben):
| Eingabe | Findings |
|---|---|
| `secret://openrouter-main` (Handle-Notation) | 0 |
| Prosa: „the dashboard token must be sent as a bearer header" | 0 |
| `api_key = "sk-…"` (echter Schlüssel) | 2 |
| `export …_TOKEN=<32 Zeichen>` | 1 |
| `diff --git a/docs/token-notes.md b/…` | 0 |

⇒ Der Scanner blockt **kein** sauberes Repository (Handles/Prosa sind safe). Der
Befund aus dem Agenten-Probe `happy: [('security','BLOCKER'),('security','BLOCKER')]`
ist damit **kein Überblocken**, sondern:

## 3. NEUER Befund (nicht im Audit, eigene Entdeckung) — doppelte Funde, MINOR
`api_key = "sk-…"` matcht **zwei** Muster gleichzeitig: `openai` (`sk-…`) und
`generic_key` (`api_key=…`). `scan()` gibt beide zurück ⇒ `gate_security` erzeugt
**zwei BLOCKER-Findings für ein und dasselbe Secret**. `redact()` dedupliziert
überlappende Treffer (`if finding.start < cursor: continue`), `scan()`/Gate **nicht**.
Wirkung: Rauschen/Doppelmeldung, keine Fehlblockade. **Severity: MINOR** → in
`docs/IMPLEMENTATION-MATRIX.md` als Nebenbefund führen.

## 4. Ebenfalls reconfirmed (aus demselben Probe-Lauf)
- `origin_allowed('')` → `True`
- `_audit` enthält nacktes `pass`
- `HERMES_DASHBOARD_SESSION_TOKEN` kommt in `plugin_api.py` nicht vor

## Datei-Eigentümerschaft (Race-Schutz)
`src/godsv/completion.py`, `gates.py`, `mcpserver.py`, `plugin_api.py` sind von
`deleg_ada3f0f5` (Block 1) beansprucht; `monitor.py`/`pause.py`/`events.py`/Plugin-Hooks
von Block 4+5 ⇒ **keine eigenen In-Tree-Edits dort**, nur Lesen und Verifizieren.
