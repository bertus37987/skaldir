# Abnahmebeleg: VAL-TOOL-005 (Tool-Reuse in neuem Agent-Run)

**Datum:** 2026-10-14  
**Befund / Nachweis:** Erfolgreich  
**Artefakt:** `logs/tool-reuse-live/20261014-215425.json`  
**Testskript:** `tools/tool_reuse_live_proof.py`  
**Docker-Image:** `skaldir-tool-market-research:v1@sha256:2dfd7fdaf745eb45992b173fc1f8e99700f5aa14cf7d23201ab6264e5f490811`  

---

## 1. Gegenstand und Prüfschritte

VAL-TOOL-005 verlangt:
Ein **neuer** Agent-Run entdeckt über `tool_search` das zuvor registrierte Docker-Tool `market_research_snapshot` (Registry-ID 24), ruft es auf und erhält valide strukturierte Daten aus dem Container.

### Ausgeführte Schritte

1. **Health- und Selektierbarkeitsprüfung:**
   ```bash
   skaldir tools health market_research_snapshot --json
   ```
   - Exit-Code: `0`
   - Health-Status: `HEALTHY` (selectable: `True`, digest_match: `True`)

2. **Direkter CLI-Aufruf über Loop-Call-Pfad:**
   ```bash
   skaldir tools call market_research_snapshot --json-args '{"symbol":"AMD","detail":"brief"}' --json
   ```
   - Exit-Code: `0`
   - Wrapper: `/home/willy/.hermes/godsv/generated_tools/market_research_snapshot/bin/market_research_snapshot`
   - Ergebnis: Valides JSON mit AMD-Marktdaten (`sector: Semiconductors`, `symbol: AMD`, `market_cap_band: large`).

3. **Neuer Agent-Run mit Tool-Search & Call:**
   ```bash
   skaldir agent run "Find market research capability via tool_search and get snapshot for AMD" \
       --tools both --agent-tools --json
   ```
   - Run-ID: `run-20261014T195427Z-14fd78`
   - Status: `answered` (Exit-Code: `0`)
   - Steps: `3`, Tool-Calls: `2`, Ausgeführt: `2`
   - Schritt 1: `tool_search(need="market research")` → findet `market_research_snapshot` (Score: 0.949, Status: ready, selectable: True).
   - Schritt 2: `market_research_snapshot(symbol="AMD", detail="brief")` → führt Container-Wrapper aus, liefert strukturierte Marktdaten.
   - Schritt 3: Modell formuliert Abschlussantwort basierend auf den abgerufenen Daten.

4. **Transkriptprüfung über Produkt-CLI:**
   ```bash
   skaldir agent transcript run-20261014T195427Z-14fd78 --json
   ```
   - Exit-Code: `0`
   - Aufgezeichnete Tool-Calls: `["tool_search", "market_research_snapshot"]`
   - Beide Aufrufe mit `decision: ALLOW`, `executed: True`, `ok: True`.
   - Transkript-Pfad: `/home/willy/.hermes/godsv/agent/runs/run-20261014T195427Z-14fd78.jsonl`

---

## 2. Fazit

VAL-TOOL-005 ist im produktiven Pfad vollständig erfüllt und belegt.
