# Tool Hub — Architektur (DoD §12)

Zwei **streng getrennte** Hälften. Der lokale Teil ist real gebaut
(`src/godsv/toolhub.py`, `godsvoice tools-hub`); der öffentliche Teil ist die
spezifizierte Ziel-Architektur, deren Manifest-Format hier bereits existiert —
veröffentlicht wird **nie automatisch**.

## 1. Local Tool Registry — gebaut

`discover(runtime)` liefert eine read-only Gesamtsicht aller installierten
Fähigkeiten aus echten Quellen:

| Quelle | Fundort | Metadaten |
|---|---|---|
| Generated Tools | `generated_tools/*/manifest.yaml` | name, version, type, purpose, status, permissions (network/fs), `secret://`-Handles, dependencies, risk, provenance (created_by/at) |
| Testing Tools | dieselben Manifeste (`type: testing_tool`) | + `validation.status`/`checks` (BROKEN/WORKING-Läufe) |
| Skills | `~/.hermes/skills/**/SKILL.md` (Frontmatter) | name, description — Skills sind Workflows, kein Proof (`state: workflow`) |
| MCP | `~/.hermes/config.yaml` (mcp-Sektion) | Server-Namen/Command — Health gehört dem Host (`state: external`) |

**Reliability:** Nutzungszähler aus dem echten Usage-Store (`tool_usage.json`).
**Suche:** deterministische Wort-Überlappung (dieselbe Heuristik wie die
Research-Retrieval, kein LLM) — `godsvoice tools-hub --search "…"`.
**Prinzip:** Vor jedem Neubau einer Fähigkeit erst hier suchen.

## 2. Public Tool Hub — Architektur (V1: kein Hosted-Backend)

**Registry = GitHub-Repo** mit `manifests/<name>.json`; **Verifikation = GitHub
Actions** (installiert das Tool, führt die definierten Tests aus, erst dann
entsteht der `godsv-verified`-Status); **Frontend = GitHub Pages** (einfache
durchsuchbare Liste über die Manifest-JSONs).

**`build_hub_manifest(runtime, name)`** erzeugt das teilbare Manifest und
**verweigert** ohne Voraussetzungen:

- `status: ready` Pflicht; Testing-Tools zusätzlich `validation: validated` —
  **„verified" heißt: installiert und gegen definierte Tests ausgeführt**,
  nie bloß Code-Review.
- Klartext-Secrets werden hart abgelehnt; erlaubt sind nur `secret://`-Handles
  (Treasury bleibt die einzige Secret-Autorität).
- Enthält: provenance, permissions (network/fs), secret_handles, verification
  (state + checks + last_tested_at + trust `godsv-verified|local`),
  compatibility, **sha256-Checksumme des Tool-Verzeichnisses**, und
  `publish: {automatic: false, requires: "explicit owner approval"}`.

**Publish-Flow (verbindlich):**
```
lokal generieren → Voice verifiziert (BROKEN→FAIL / WORKING→PASS)
→ Security-Checks (Secret-Scan, Domains, FS) → realistischer Smoke-Test
→ Nutzer gibt explizit frei → Registry-Validierung (Actions) → publish
```
**Trust-Level:** `local` → `godsv-verified` (erst nach nachgewiesener
Ausführung). Hub-Tools erhalten **nie** stillschweigend Secrets/Netz/FS —
Grants bleiben in der Treasury; das Manifest deklariert nur Ansprüche, die
die Installation sichtbar gegen die Grants prüft.

## Live-Beleg (13.09.2026)

```
$ godsvoice tools-hub --search "verify the readme of a target"
check_readme  generated  testing_tool  validated  Prove a target ships a README …
$ godsvoice tools-hub --manifest check_readme
hub OK: check_readme | godsv-verified | auto: False
```
Discovery live: 105 Einträge (4 generated, 101 Skills, 0 MCP konfiguriert).
Tests: `tests/test_toolhub.py` (6) — Discovery, Suche, Eligibility-Verweigerung
(draft/unvalidiert), Klartext-Secret-Verweigerung, Read-only/Empty-Safe.
