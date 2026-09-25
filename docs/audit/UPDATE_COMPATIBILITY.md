# UPDATE_COMPATIBILITY.md — Update-Isolation (S18/PX-07, Kapitel 04 §7, 05 §6)

Stand: 16.09.2026. Verträge, Fragestellung und belegter Zustand; die Abnahme-Läufe sind offen.

## Verankerter Zustand (belegbar)

- Skaldir besitzt Quell-/Build-/Datenverantwortung in `~/projects/gods-voice`; die
  Engine (`~/.hermes/hermes-agent`) ist **separat versioniert**:
  Basispin `c8aa5608c2` dokumentiert in `integrations/hermes/README.md`;
  Live-Engine ist derzeit `fb56a7e06d` (neuer — genau diese Drift ist der Testfall).
- Host-Overlay-Mechanik: `host-overlay/*.py` sind verankerte, idempotente, MARKER-geprüfte
  Module (`.gv-backup`, lauter Abbruch bei Anker-Drift, `--check`). Sie sind **Artefakte**:
  sie werden erst mit expliziter Anwendung aktiv — der Live-Baum bleibt in dieser Runde
  unverändert (bewusste Entscheidung, autorisiertes Fenster offen).
- App-/Web-Assets: die GV-App baut aus dem App-Spiegel über `app-overlay/`; ein normaler
  Hermes-Updatepfad sollte die Skaldir-Assets nicht berühren — genau das testet der
  ausstehende Isolationslauf.

## Kompatibilitäts-Schichten (ausstehender Beweis)

| Ebene | Pfad | Lauf |
|---|---|---|
| Discovery/Host-Bridge | `tests/test_discovery.py::TestHostBridge` läuft gegen deklarierte Revision `c8aa5608c2` mit Arbeitsbaum-Fallback | ✅ läuft (grün; dokumentierte Drift gegen `fb56a7e`) |
| Engine-Update-Isolation | eigener Kompatibilitäts-Lauf mit inkompatibler Kopie + Fallback | ⬜ offen |
| App-Asset-Isolation | normaler Hermes-Updatepfad darf Skaldir-App-/Web-Assets und Daten nicht unkontrolliert ändern | ⬜ (Kapitel 06 §4 Plattform/Update-Positiv-/Gegenfälle) |
| Migration/Rollback | Migrationsschritte versioniert, Snapshot vorher, atomare Aktivierung; Rollbackgrenzen benannt | ⬜ |
| Overlay-Trockenlauf | `patch_rf03*.py --check` auf der Live-Engine entscheidet Anker-Gültigkeit | ✅ Anker gegen `fb56a7e` geprüft (Probe-Log in RF-STATUS §1) |

## Rollback-Regel (Kapitel 04 §7 fix)

- Engine-Seite: `.gv-backup`-Rückspiel je Datei oder `git -C <engine> checkout -- agent/`.
- Daten: kein Rückschreiben alter Prozesse in neue Schemata; Migration vorab Snapshot.
- Kontrolliertes Aktualisieren nur nach isoliertem Kandidatenlauf mit Contract-,
  Migrations-, Tool-, Voice- und UI-Tests; abgebrochene Updates lassen die letzte
  kompatible App startbar.
