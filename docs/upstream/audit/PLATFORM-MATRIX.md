# PLATFORM-MATRIX — Skaldir Agent (Kapitel 05 §1/§8, ABNAHME §74/§114)

Stand: 16.09.2026 · HEAD der Evidenz: siehe `audit/REQUIREMENTS.json` `observed_project_head`
(zum Zeitpunkt dieses Schreibens `b7a287a`; der Registerkopf wird von
`tools/build_audit_register.py` gesetzt).

Lege jede Zeile als belegten Zustand fest (Kapitel 06 §3: Implementierung · Evidenz · Abnahme).
ABNAHME §4.3 erlaubt neben `blocked` ausdrücklich `external_dependency` für eine fehlende
**fremde** Voraussetzung. Diese Matrix nutzt beides zusammen: Abnahme = `blocked`,
Feinmarker = `external_dependency`. `blocked`, `not_run` und `unknown` zählen **nie** als
`satisfied` (§4). Das Kriterienregister führt für die Plattformkriterien (PX-01…PX-08)
weiterhin `acceptance_state = open` — diese Matrix behauptet keine Abnahme, sie benennt den
Grund.

## 1. Zielplattformen (§74)

| Ziel | Installer | Build-Artefakt | Erststart ohne Devtools | Install/Reinstall/Uninstall | Native Host-Bedienung (PX-04) | Signierung (PX-07) | Zustand | Fehlende Voraussetzung (exakt) |
|---|---|---|---|---|---|---|---|---|
| **Linux x64** | AppImage/DEB/RPM (noch keine gebaut — bestehend: user-lokale `~/.hermes/godsv/app/linux-unpacked/gods-voice`-Build, Launcher + hicolor-Icons nachweisbar per `desktop status`) | ✅ nativer Build vorhanden (203 MB ELF, gebündelte Chromium-Runtime, eigener userData) | ✅ Build läuft (`--version` rc=0); voller Erststart-Chat am Geräte-Pfad offen (braucht Gerätefenster) | ⬜ Zyklen laufen noch (Pfad: `desktop install/status/uninstall --remove-plugin`) | ⬜ CU-Runner existieren (bwrap/Container; echter Host-GUI-Pfad = Engine-Bereich; Live-Beweis offen) | n/a (nicht Distributionsziel) | **partial** — einziger nativer Kandidat, Abnahme offen | keine (Linux ist baubar); offen ist Gerätezeit + Distributionsentscheidung |
| **Windows x64** | NSIS pro Nutzer | — kein Build-Host in dieser Umgebung | ⬜ | ⬜ | ⬜ | ⬜ Codesign-Identität fehlt | Abnahme **blocked**, Marker `external_dependency` | **kein Windows-Build-Host** und **keine Windows-Codesign-Identität** (Zertifikat + vertrauenswürdiger Ablagepfad) |
| **macOS arm64** | DMG + ZIP für Update | — kein Build-Host | ⬜ | ⬜ | ⬜ (Screen/Accessibility-Rechte) | ⬜ Developer-ID + Notarisierung fehlt | Abnahme **blocked**, Marker `external_dependency` | **kein macOS-Build-Host** (arm64) und **keine Apple Developer-ID + Notarization-Profil** |
| **macOS x64** | DMG + ZIP für Update | — kein Build-Host | ⬜ | ⬜ | ⬜ | ⬜ | Abnahme **blocked**, Marker `external_dependency` | wie macOS arm64 — zusätzlich ist x64-Hardware/CI älter als der Zweig |

Blocks (Kapitel 05 §6/§7): keine Signierungs-Identitäten; keine Windows/macOS-Runner.
Cross-Build ist **kein** nativer Runtimebeweis (§78) — deshalb wird für die drei fehlenden
Ziele kein Artefakt simuliert und kein PASS behauptet.
Benötigt: Build-Hosts für die drei fehlenden Ziele oder CI-Matrix; Signier-Identitäten
(Windows-Codesign, macOS Developer-ID + Notarization-Profil) über den geschützten Weg.

## 2. JEV-/TypeSafe-Zugang als Plattform-ähnliche Außenabhängigkeit (§63, §117.1)

Jev/TypeSafe ist keine Zielplattform, sondern eine **externe Providerzugangsvoraussetzung** —
sie wird hier mitgeführt, weil sie denselben Gate-Charakter hat: ohne sie bleibt eine
Abnahmeaussage unmöglich und darf niemals in PASS umgedeutet werden.

| Gegenstand | Was real existiert | Zustand | Fehlende Voraussetzung (exakt) |
|---|---|---|---|
| TypeSafe/Jev Live (`JEV-07`) | öffentlich dokumentierte API (`POST /v1/systemone`, Modell `jev-latest`, Status **Early Access**); **kein** Credential im Treasury, kein Connection-Eintrag, kein autorisierter Aufruf | Abnahme **blocked**, Marker `external_dependency` | **kein TypeSafe-Account/Credential und keine Waitlist-Freigabe** (Early Access); zusätzlich verlangt §63 einen Treasury-Eintrag als `ConnectionRef` mit `scope = system_one.evaluate` |
| Jev Shadow/aktiv (`JEV-10`/`JEV-11`, E2E-13/§21, §45–§46) | Shadow-Harness und Decision-Policy-Entwurf entstehen in einer parallelen Welle (`src/godsv/system_one_policy.py`, uncommitted) | Abnahme **not_run** | braucht Live-Zugang **und** Kalibrierungs-/Holdoutdaten (§105/§106) |
| Computer Use unter Jev (E2E-14/§22) | es existiert **kein** wiederverwendbares visuelles Subtool und **kein** GUIState-Vertrag — siehe `docs/audit/COMPUTER_USE_SUBTOOLS.md` | Abnahme **not_run** | erst GUIState + Subtool-Vertrag (§52/§55), dann Jev-Fragen (§54); zusätzlich fehlt auf diesem Host das Container-GUI-Image `localhost/godsv-cu-x11` |

**§117.1-Aussage heute:** Jev-Claim-Level = `not_started`. `JEV_READY` ist erst zulässig, wenn
Providerabstraktion, Contracts, Questions, Sanitizer, Decision Policy, LLM-Adapter,
Shadowharness, Computer-Use-GUIState und Tests vorbereitet sind — heute ist davon nur ein
uncommitteter Teil vorhanden. `JEV_LIVE_TESTED`/`JEV_ACTIVE_VERIFIED` sind ohne Credential
ausgeschlossen (§80–§85, §117.1).

## 3. Belegkette für Linux x64 (bislang real)

- Build: `godsvoice desktop build-app` → `~/.hermes/godsv/app/linux-unpacked/gods-voice`
  (Beweismittel: `desktop status` "✓ God's-Voice-App-Build (nativ)", ELF-64-bit + chrome-sandbox.)
- Launcher: Desktop-Eintrag + hicolor-Icons 64/128/256 deployt, CLI-Symlink existiert.
- Identität: `HERMES_DESKTOP_APP_NAME` → `Skaldir Agent`, `WM_CLASS = SkaldirAgent`
  (`src/godsv/desktop.py:24`), UA `SkaldirAgent/<ver>`.
- Nicht beansprucht: öffentliche Distribution, ARM64, Cross-Platform-Pfade.
- Offener Befund (UX-Wave, P2 `UX16-02`): die **deployte** App-Kopie trägt noch den alten
  Plugin-Stand (Cockpit/Activity in der NAV), der Repo-Kandidat nicht — der Auslieferungsstand
  hinkt dem Kandidaten nach; ein Release-Checklist-Schritt „Plugin neu deployen" fehlt.
