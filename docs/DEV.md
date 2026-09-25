# DEV — Development Guide

## Setup

```bash
cd ~/projects/gods-voice
uv venv .venv --python 3.11 && source .venv/bin/activate
pip install -e ".[dev]"
# Node nur fuer die Desktop-Panel-Pruefungen + das Vendoring der Orb-Bibliothek:
npm install
```

## Kommandos

```bash
pytest                          # Test-Suite (deterministisch, kein Netzwerk)
ruff check src tests            # Lint (line-length 100, py311)
bandit -q -r src -ll            # Security-Scan (medium+; CI-Gate)
node tools/check_desktop_plugin.mjs      # Desktop-Panel: Load-Smoke + UI-Kontrakt
node tools/vendor_thinking_orbs.mjs --check   # vendored thinking-orbs aktuell?
godsvoice init                  # .godsv/ mit Beispiel-Pact + Ledger
godsvoice demo                  # deterministische V0-Pipeline (8 Events → 4 Hints)
godsvoice prompts list          # Prompt-Inventar; Fehler, wenn REQUIRED_IDS fehlen
godsvoice scan FILE|-           # Secret-Scan (Exit 1 bei Fund)
python -m godsv demo            # ohne installiertes Script
```

## Layout

```
src/godsv/      pact, ledger, events, monitor, voice, inject, prompts, secretscan, spotlight, demo, cli
prompts/        versionierte System-Prompts (siehe docs/PROMPTS.md)
tests/          pytest; deterministisch, feste ts-Werte, keine Netz-/LLM-Calls
docs/           CORE-PLAN.md (Master), DESIGN*.md, SECURITY.md, PROMPTS.md, STATUS.md, DEV.md
integrations/   Host-Integrationen (Hermes-Plugin-Adapter)
tools/          Recherche-Helfer (bing/arxiv-Fallbacks) + Panel-Smoke + Orb-Vendoring
node_modules/   nur Build-Zeit (thinking-orbs); nichts davon ist Laufzeit-Abhaengigkeit
```

## Konventionen

- **Code & Prompts:** Englisch. **Dokumentation & Nutzertexte:** Deutsch.
- **Tests deterministisch:** keine echten Zeitstempel/Zufall/Netz; Fehlerfingerprints nie als
  Literale testen (Kontrakte zwischen Inputs testen, nicht Snapshots).
- **Prompt-Änderungen:** neue Version statt Edit (docs/PROMPTS.md).
- **Security:** Secrets nie in Logs/Tests/Trajektorien; Scanner-Regeln in `secretscan.py`
  erweitern statt ad-hoc grep.
- **Commits:** Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).

## Hermes-Host (Basis)

- Basis ist Hermes Agent (MIT). Lokaler Dev-Checkout: `~/.hermes/hermes-agent`
  (gepinnt auf Commit `c8aa5608c2`, Sep 2026). Upstream-Fork für PRs:
  `bertus37987/hermes-agent` → siehe `integrations/hermes/README.md`.
- Skaldir Agent wird als **Plugin + Daemon** installiert (`~/.hermes/plugins/godsv/`), nie als
  Hard-Fork. Einzelne Upstream-Patches gehen als PRs an den Fork.
