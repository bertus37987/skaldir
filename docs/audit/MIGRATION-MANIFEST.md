# Migrationsmanifest — bereinigter Projekttransfer

Stand: 25. September 2026. Quelle: `/home/willy/projects/gods-voice` (read-only, unverändert). Ziel: `/home/willy/Agentic_Projects/Skaldir_Agent`.

| Feld | Wert |
|---|---|
| Quell-Branch | `godsv-supervisor-hardening` |
| Quell-HEAD | `a8fcaa1beb2cd3da81262e46b6378d8ecd66cc0a` (2026-09-19 11:23:05 +0200) |
| Quell-Arbeitsbaum | dirty: 166 modifizierte, 192 untracked Pfade (vor der Migration erfasst) |
| Inventur | `git ls-files` (1296 tracked) + `git ls-files --others --exclude-standard` (192 untracked) |
| Übernommen | 1094 Dateien. 24.938.580 Bytes |
| Ausgeschlossen | 1649 Dateien (vollständige Liste: `logs/migration-excluded.txt`) |
| Kollisionen | 1 (`.gitignore`) — nicht überschrieben, stattdessen zusammengeführt |
| Hash-Prüfung | SHA-256 Quelle == Ziel für alle 1094 kopierten Dateien; erneute Kontrolle nach dem Kopieren: 0 Abweichungen, 0 Quelldrift |

## 1. Auswahlregeln

**Übernommen:** aktiver Quellcode (`src/skaldir`, `src/godsv` als Deprecated-Alias), Tests inkl. Fixtures, produktive Skripte und Probe-Tools (`tools/`, `scripts/`), Prompts, Skills, Docker-/Tool-Definitionen, CI-Konfiguration, `integrations/`, `host-overlay/`, `app-overlay/`, Paketdateien (`pyproject.toml`, `uv.lock`, `package.json`, `package-lock.json`, `install.sh`), Lizenzen/Attribution (`LICENSE`, `THIRD_PARTY_NOTICES.md`), Produktdokumentation und Audit-Register.

**Nicht übernommen:** `.git`/alte Remotes, `.venv`, `node_modules`, Python-/Test-/Build-Caches, Runtime-Datenbanken (`events.db`, `cu_runs.jsonl`), generierte Testoutputs (`outputs/`, 216 MB), UI-Audit-Screenshots (`ui-artifacts/`, 8,4 MB), rohe Probe-Evidence-Logs (`audit/evidence/`, 1,6 MB), Browserprofile. Nicht übernommene Dateien wurden **nicht gelöscht** — sie liegen unverändert in der Quelle.

**Verschoben statt überschrieben** (Ziel enthielt bereits eigenes Planpaket):

- `.hermes/plans/2026-09-13_161102-feature-map-luecken-fix-und-remap.md` → `docs/upstream/plans/plans/2026-09-13_161102-feature-map-luecken-fix-und-remap.md`
- `.hermes/plans/2026-09-13_1750-verifikation-und-release.md` → `docs/upstream/plans/plans/2026-09-13_1750-verifikation-und-release.md`
- `AGENTS.md` → `docs/upstream/AGENTS-UPSTREAM.md`
- `README.md` → `docs/upstream/README-UPSTREAM.md`
- `docs/94-token-economy-worked-example.py` → `docs/upstream/94-token-economy-worked-example.py`
- `docs/ABNAHME-STATUS.md` → `docs/upstream/ABNAHME-STATUS.md`
- `docs/ACCEPTANCE.md` → `docs/upstream/ACCEPTANCE.md`
- `docs/APP-BUILD.md` → `docs/upstream/APP-BUILD.md`
- `docs/ARCHITECTURE.md` → `docs/upstream/ARCHITECTURE.md`
- `docs/BENCHMARK-MATRIX.md` → `docs/upstream/BENCHMARK-MATRIX.md`
- `docs/BENCHMARK-RUN-SCHEMA.json` → `docs/upstream/BENCHMARK-RUN-SCHEMA.json`
- `docs/CAPABILITY-GAPS.md` → `docs/upstream/CAPABILITY-GAPS.md`
- `docs/CAPABILITY-LEARNING-E2E.md` → `docs/upstream/CAPABILITY-LEARNING-E2E.md`
- `docs/CHAT-INTEGRATION.md` → `docs/upstream/CHAT-INTEGRATION.md`
- `docs/CHAT-UI-V6-PLAN.md` → `docs/upstream/CHAT-UI-V6-PLAN.md`
- `docs/CHATTING.md` → `docs/upstream/CHATTING.md`
- `docs/CLI-REFERENCE.md` → `docs/upstream/CLI-REFERENCE.md`
- `docs/CLI-STYLE.md` → `docs/upstream/CLI-STYLE.md`
- `docs/CLI-VISUAL-AUDIT.md` → `docs/upstream/CLI-VISUAL-AUDIT.md`
- `docs/COMPLETE_AUDIT-2026-09-13.md` → `docs/upstream/COMPLETE_AUDIT-2026-09-13.md`
- `docs/CORE-PLAN.md` → `docs/upstream/CORE-PLAN.md`
- `docs/DESIGN-2-GODS-VOICE.md` → `docs/upstream/DESIGN-2-GODS-VOICE.md`
- `docs/DESIGN.md` → `docs/upstream/DESIGN.md`
- `docs/DEV.md` → `docs/upstream/DEV.md`
- `docs/DISCOVERY-TESTSET.md` → `docs/upstream/DISCOVERY-TESTSET.md`
- `docs/E2E-LIVE-PROOF-40.md` → `docs/upstream/E2E-LIVE-PROOF-40.md`
- `docs/EVAL-LONGHORIZON.md` → `docs/upstream/EVAL-LONGHORIZON.md`
- `docs/EXPERIENCE-V6-PLAN.md` → `docs/upstream/EXPERIENCE-V6-PLAN.md`
- `docs/FEATURE-MAP.md` → `docs/upstream/FEATURE-MAP.md`
- `docs/FEATURES.md` → `docs/upstream/FEATURES.md`
- `docs/FONTS.md` → `docs/upstream/FONTS.md`
- `docs/GODSV-COMPLETE-OVERVIEW.md` → `docs/upstream/GODSV-COMPLETE-OVERVIEW.md`
- `docs/GV-README-DRAFT.md` → `docs/upstream/GV-README-DRAFT.md`
- `docs/HANDOVER.md` → `docs/upstream/HANDOVER.md`
- `docs/HARDENING-MATRIX.md` → `docs/upstream/HARDENING-MATRIX.md`
- `docs/IMPLEMENTATION-MATRIX.md` → `docs/upstream/IMPLEMENTATION-MATRIX.md`
- `docs/IMPLEMENTATION-PLAN-docker-tool-builder.md` → `docs/upstream/IMPLEMENTATION-PLAN-docker-tool-builder.md`
- `docs/IMPLEMENTATION-PLAN-live-control-plane.md` → `docs/upstream/IMPLEMENTATION-PLAN-live-control-plane.md`
- `docs/IMPLEMENTATION-PLAN-recovery-skaldir.md` → `docs/upstream/IMPLEMENTATION-PLAN-recovery-skaldir.md`
- `docs/IMPLEMENTATION.md` → `docs/upstream/IMPLEMENTATION.md`
- `docs/INSTALL.md` → `docs/upstream/INSTALL.md`
- `docs/JEV-IMPLEMENTATION-PLAN.md` → `docs/upstream/JEV-IMPLEMENTATION-PLAN.md`
- `docs/LIVE-STREAM-SPEC.md` → `docs/upstream/LIVE-STREAM-SPEC.md`
- `docs/MEMORY-LEARNING.md` → `docs/upstream/MEMORY-LEARNING.md`
- `docs/PLATFORM.md` → `docs/upstream/PLATFORM.md`
- `docs/PRODUCT-RUN-6.2.md` → `docs/upstream/PRODUCT-RUN-6.2.md`
- `docs/PROJECT-STATE.md` → `docs/upstream/PROJECT-STATE.md`
- `docs/PROMPTS.md` → `docs/upstream/PROMPTS.md`
- `docs/QUICKSTART.md` → `docs/upstream/QUICKSTART.md`
- `docs/RENAME-MIGRATION.md` → `docs/upstream/RENAME-MIGRATION.md`
- `docs/RENAME.md` → `docs/upstream/RENAME.md`
- `docs/REPOS.md` → `docs/upstream/REPOS.md`
- `docs/RESEARCH-V6-INPUTS.md` → `docs/upstream/RESEARCH-V6-INPUTS.md`
- `docs/RF-LONGRUN-FREIGABE.md` → `docs/upstream/RF-LONGRUN-FREIGABE.md`
- `docs/RF-STATUS.md` → `docs/upstream/RF-STATUS.md`
- `docs/RUN-LIFECYCLE.md` → `docs/upstream/RUN-LIFECYCLE.md`
- `docs/RUNBOOK.md` → `docs/upstream/RUNBOOK.md`
- `docs/SECURITY-BOUNDARIES.md` → `docs/upstream/SECURITY-BOUNDARIES.md`
- `docs/SECURITY.md` → `docs/upstream/SECURITY.md`
- `docs/SNIPPET-93-subagent-roles.md` → `docs/upstream/SNIPPET-93-subagent-roles.md`
- `docs/SOURCES.md` → `docs/upstream/SOURCES.md`
- `docs/STATUS.md` → `docs/upstream/STATUS.md`
- `docs/SUPERVISOR-AUDIT.md` → `docs/upstream/SUPERVISOR-AUDIT.md`
- `docs/SURFACE.md` → `docs/upstream/SURFACE.md`
- `docs/TOOL-HUB.md` → `docs/upstream/TOOL-HUB.md`
- `docs/TOOLS-V2-BUILDER-GAPS.md` → `docs/upstream/TOOLS-V2-BUILDER-GAPS.md`
- `docs/TOOLS-V2-BUILDFLOW.md` → `docs/upstream/TOOLS-V2-BUILDFLOW.md`
- `docs/TOOLS-V2-DISCOVERY.md` → `docs/upstream/TOOLS-V2-DISCOVERY.md`
- `docs/TOOLS-V2-EXECUTION-BOUNDARY.md` → `docs/upstream/TOOLS-V2-EXECUTION-BOUNDARY.md`
- `docs/TOOLS-V2-HOST-WIRING.md` → `docs/upstream/TOOLS-V2-HOST-WIRING.md`
- `docs/TOOLS-V2-MINING-HEALTH.md` → `docs/upstream/TOOLS-V2-MINING-HEALTH.md`
- `docs/TOOLS-V2-REGISTRY.md` → `docs/upstream/TOOLS-V2-REGISTRY.md`
- `docs/UI-AUDIT-CHECKPOINTS.md` → `docs/upstream/UI-AUDIT-CHECKPOINTS.md`
- `docs/UI-DESIGN.md` → `docs/upstream/UI-DESIGN.md`
- `docs/UI.md` → `docs/upstream/UI.md`
- `docs/VERIFICATION-1.3.md` → `docs/upstream/VERIFICATION-1.3.md`
- `docs/VERIFICATION-1.4.md` → `docs/upstream/VERIFICATION-1.4.md`
- `docs/VERIFICATION-CLOSURE.md` → `docs/upstream/VERIFICATION-CLOSURE.md`
- `docs/VERIFIED-FINDINGS-live-2026-09-13.md` → `docs/upstream/VERIFIED-FINDINGS-live-2026-09-13.md`
- `docs/VOICE-CALIBRATION.md` → `docs/upstream/VOICE-CALIBRATION.md`
- `docs/artifacts-94-token-economy/archived-tool-result.json` → `docs/upstream/artifacts-94-token-economy/archived-tool-result.json`
- `docs/artifacts-94-token-economy/f1-lease-cas.txt` → `docs/upstream/artifacts-94-token-economy/f1-lease-cas.txt`
- `docs/artifacts-94-token-economy/hot-form.json` → `docs/upstream/artifacts-94-token-economy/hot-form.json`
- `docs/artifacts-94-token-economy/progress-decision.json` → `docs/upstream/artifacts-94-token-economy/progress-decision.json`
- `docs/artifacts-94-token-economy/retry-gate.json` → `docs/upstream/artifacts-94-token-economy/retry-gate.json`
- `docs/artifacts-94-token-economy/run/state/runs/run-20260919T135346Z-1cd1ec.jsonl` → `docs/upstream/artifacts-94-token-economy/run/state/runs/run-20260919T135346Z-1cd1ec.jsonl`
- `docs/artifacts-94-token-economy/run/state/tool_archive/dd59b60a2a5e55e42d3f2b940f999c7c5e195de203c855d07ede6f0935d3f479.json` → `docs/upstream/artifacts-94-token-economy/run/state/tool_archive/dd59b60a2a5e55e42d3f2b940f999c7c5e195de203c855d07ede6f0935d3f479.json`
- `docs/artifacts-94-token-economy/token-report.json` → `docs/upstream/artifacts-94-token-economy/token-report.json`
- `docs/artifacts-94-token-economy/token-report.txt` → `docs/upstream/artifacts-94-token-economy/token-report.txt`
- `docs/artifacts-94-token-economy/worked-example.json` → `docs/upstream/artifacts-94-token-economy/worked-example.json`
- `docs/audit/ARTIFACT-MANIFEST.json` → `docs/upstream/audit/ARTIFACT-MANIFEST.json`
- `docs/audit/AUDIT-FINDINGS.md` → `docs/upstream/audit/AUDIT-FINDINGS.md`
- `docs/audit/BENCHMARK-RAW-README.md` → `docs/upstream/audit/BENCHMARK-RAW-README.md`
- `docs/audit/BENCHMARK_MANIFEST.json` → `docs/upstream/audit/BENCHMARK_MANIFEST.json`
- `docs/audit/CLI-CONTRACT-PROBES.md` → `docs/upstream/audit/CLI-CONTRACT-PROBES.md`
- `docs/audit/COMPLETION_PRECHECK.md` → `docs/upstream/audit/COMPLETION_PRECHECK.md`
- `docs/audit/COMPUTER-USE-GUI-E2E.md` → `docs/upstream/audit/COMPUTER-USE-GUI-E2E.md`
- `docs/audit/COMPUTER_USE_SUBTOOLS.md` → `docs/upstream/audit/COMPUTER_USE_SUBTOOLS.md`
- `docs/audit/DOCKER-BUILDER-EVIDENCE.md` → `docs/upstream/audit/DOCKER-BUILDER-EVIDENCE.md`
- `docs/audit/JEV-WORKER-A-CODE-MAP.md` → `docs/upstream/audit/JEV-WORKER-A-CODE-MAP.md`
- `docs/audit/JEV-WORKER-A-REPORT.md` → `docs/upstream/audit/JEV-WORKER-A-REPORT.md`
- `docs/audit/JEV_CU_PACK.md` → `docs/upstream/audit/JEV_CU_PACK.md`
- `docs/audit/JEV_ID_MAP.md` → `docs/upstream/audit/JEV_ID_MAP.md`
- `docs/audit/JEV_PROVIDER_COMPATIBILITY.md` → `docs/upstream/audit/JEV_PROVIDER_COMPATIBILITY.md`
- `docs/audit/PERFORMANCE.md` → `docs/upstream/audit/PERFORMANCE.md`
- `docs/audit/PLATFORM-MATRIX.md` → `docs/upstream/audit/PLATFORM-MATRIX.md`
- `docs/audit/RELEASE_NOTES.md` → `docs/upstream/audit/RELEASE_NOTES.md`
- `docs/audit/RF09-CRASH-WINDOW-INVENTORY.md` → `docs/upstream/audit/RF09-CRASH-WINDOW-INVENTORY.md`
- `docs/audit/SKILLS_USED.md` → `docs/upstream/audit/SKILLS_USED.md`
- `docs/audit/SYSTEM_ONE_GUISTATE.md` → `docs/upstream/audit/SYSTEM_ONE_GUISTATE.md`
- `docs/audit/SYSTEM_ONE_QUESTIONS.json` → `docs/upstream/audit/SYSTEM_ONE_QUESTIONS.json`
- `docs/audit/SYSTEM_ONE_SHADOW.md` → `docs/upstream/audit/SYSTEM_ONE_SHADOW.md`
- `docs/audit/SYSTEM_ONE_TRIAGE_PACK.md` → `docs/upstream/audit/SYSTEM_ONE_TRIAGE_PACK.md`
- `docs/audit/TEST_REPORT.md` → `docs/upstream/audit/TEST_REPORT.md`
- `docs/audit/UI-FEATURE-MAP.md` → `docs/upstream/audit/UI-FEATURE-MAP.md`
- `docs/audit/UPDATE_COMPATIBILITY.md` → `docs/upstream/audit/UPDATE_COMPATIBILITY.md`
- `docs/audit/probes-2026-09-12.json` → `docs/upstream/audit/probes-2026-09-12.json`
- `docs/audit/probes-after-etappe1.json` → `docs/upstream/audit/probes-after-etappe1.json`
- `docs/audit/probes-after-etappe2.json` → `docs/upstream/audit/probes-after-etappe2.json`
- `docs/audit/review_probes.py` → `docs/upstream/audit/review_probes.py`
- `docs/skaldir-upgrade/decisions.md` → `docs/upstream/skaldir-upgrade/decisions.md`
- `docs/skaldir-upgrade/implementation-audit.md` → `docs/upstream/skaldir-upgrade/implementation-audit.md`
- `docs/skaldir-upgrade/ist-audit-2026-09-25.md` → `docs/upstream/skaldir-upgrade/ist-audit-2026-09-25.md`
- `docs/skaldir-upgrade/validation-report.md` → `docs/upstream/skaldir-upgrade/validation-report.md`

Der alte Ziel-`README.md` (Vorbereitungshinweis des Planpakets) ist als `docs/upstream/README-PLANPAKET.md` erhalten; die Produkt-`README.md` stammt jetzt aus der Quelle und ist um einen Abschnitt zum neuen Arbeitsbereich ergänzt.

## 2. Aufgeschoben (deferred)

| Pfad | Grund | Ersatz / Rückweg |
|---|---|---|
| `audit/evidence/` (199 rohe Probe-Logs) | lange Originalausgaben; laut Auftrag nur redigierte Zusammenfassungen versionieren | vollständig in der Quelle erhalten; Zusammenfassungen in `docs/audit/` |
| `outputs/` (1315 Dateien) | generierte Test-/UI-Audit-Outputs, ohne aktive Laufzeitabhängigkeit | nur als Dokstrings referenziert (Tests laufen ohne sie); Regenerierbar |
| `ui-artifacts/` (42 Screenshots) | reine Bildschirmfotos alter UI-Stände | nur von `tools/build_artifact_manifest.py` referenziert |
| `~/.hermes/godsv/app-src`, Live-Runtime | explizit geschützt laut AGENTS.md | nicht angefasst |

## 3. Übernommene Dateien mit SHA-256

`Herkunft` ist der Pfad relativ zur Quelle, `Ziel` relativ zum neuen Repository.

| Herkunft | Ziel | Bytes | SHA-256 (Quelle = Ziel) |
|---|---|---|---|
| `.github/workflows/ci.yml` | `.github/workflows/ci.yml` | 474 | `7df4801b82946bba9555311144a7362cd8317101530d83e338f534acab20c76e` |
| `.hermes/plans/2026-09-13_161102-feature-map-luecken-fix-und-remap.md` | `docs/upstream/plans/plans/2026-09-13_161102-feature-map-luecken-fix-und-remap.md` | 38364 | `e2f945a183c95e24420331d73c0fe863df9e5dc4c8ccc894dbf03cdd468b8302` |
| `.hermes/plans/2026-09-13_1750-verifikation-und-release.md` | `docs/upstream/plans/plans/2026-09-13_1750-verifikation-und-release.md` | 13567 | `70edfa71a40a9c8175d7bbe1843ff0c4e67f03705d3f88c3f86d0a1124c2c485` |
| `AGENTS.md` | `docs/upstream/AGENTS-UPSTREAM.md` | 13535 | `cba57dad0a931fc8dfbe17b89c1304577d66299e7e027ccb07c3fcd79db80675` |
| `CHANGELOG.md` | `CHANGELOG.md` | 35239 | `11a881ae13f9633ee36dae6053ad016ee00e8fa4ed5c797d2d9b91c1f5eacc17` |
| `LICENSE` | `LICENSE` | 11358 | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |
| `README-CLI.md` | `README-CLI.md` | 28478 | `906d9f05160fea63e7db357791c7af84161458d3b4e3d5913d85ce57265c14d2` |
| `README.md` | `docs/upstream/README-UPSTREAM.md` | 18097 | `5a2c03a6f6dfdf11cb0a02764dfcdeb869ffeedc3d3f15f65a0ec2c19c78c2b6` |
| `THIRD_PARTY_NOTICES.md` | `THIRD_PARTY_NOTICES.md` | 52941 | `4913937229691cdffcdb4eb777fbafec5b32dddd3509039c4ab236fba6fe9884` |
| `app-overlay/gv/gv-goal-bar.tsx` | `app-overlay/gv/gv-goal-bar.tsx` | 11138 | `5aab7be1355e30d10ffcaeb7b1fd57013f77c339745a54ccf834ae268c666525` |
| `app-overlay/gv/gv-orb.tsx` | `app-overlay/gv/gv-orb.tsx` | 2126 | `67067e6e0ce4f1cb344ef94e00e137eb5e811f3ee68b1b99ef9e6905bfe7d5d7` |
| `app-overlay/gv/gv-subagent-zone.tsx` | `app-overlay/gv/gv-subagent-zone.tsx` | 11364 | `38105b1442f3767905995142596ea980cc9ab29456402a8565ade2bf3a55a90c` |
| `app-overlay/patch_app.py` | `app-overlay/patch_app.py` | 19869 | `a71f67f740b9064df8c584e5bd9a57d04a3ac5a53136d05108f4d4f019548a82` |
| `app-overlay/patch_app_f15b.py` | `app-overlay/patch_app_f15b.py` | 2357 | `fc84b590a8709dfbc229b3a73dd8726c21c86734c5d4d4eaefbd3b6f0abef1ff` |
| `app-overlay/patch_app_ui.py` | `app-overlay/patch_app_ui.py` | 51653 | `3de1f1d2b316e588029a5c69e38abed66090064862586604225f292bb18d524b` |
| `assets/cli-logo.txt` | `assets/cli-logo.txt` | 1940 | `f15e97984dd4eadf1dd92b3a990f8b5c7f554007370cdf5a0808b09f67ba61f2` |
| `audit/CODE_MAP.md` | `audit/CODE_MAP.md` | 59510 | `2579fdf82623b763cc658eac5c5600759567ffad864601dcebed0813ab1e55c9` |
| `audit/REQUIREMENTS.json` | `audit/REQUIREMENTS.json` | 310837 | `82294fd93a44e383f206029972953f92045cdfdabf0c3e910893fdafb7bd6840` |
| `audit/RESUME.md` | `audit/RESUME.md` | 9534 | `1b74fe664ea625c1935e42eb05cd8e93647b47218c4973731a7cb482cc4ecd8e` |
| `audit/findings.json` | `audit/findings.json` | 58196 | `fb3d464e75123ea70177693b1d34376ce42e179bf4c8b7faaa06d3c8597c3ea1` |
| `audit/verification-mapping.json` | `audit/verification-mapping.json` | 114997 | `6deb78cdf1c9661dc48a7d88c57044a74403a1f7ff05486b7c132803cc86f2c9` |
| `docker/cu-x11.Containerfile` | `docker/cu-x11.Containerfile` | 4108 | `605b0a96ec9f8c289c97d27caf24f4aa811551cdf243c6c311774f3ae68efe23` |
| `docs/94-token-economy-worked-example.py` | `docs/upstream/94-token-economy-worked-example.py` | 11944 | `f800306252c83d091984457a3e01c5638d30f45af59db990145ee3940f772616` |
| `docs/ABNAHME-STATUS.md` | `docs/upstream/ABNAHME-STATUS.md` | 44573 | `04810be59de3ffa73949b481b6b993da99341b8c6b5cdc30b92026d565634e19` |
| `docs/ACCEPTANCE.md` | `docs/upstream/ACCEPTANCE.md` | 4836 | `1452050549be179835fa26a97ddb4addb13ee8b149593233f0f29ca9ea64416e` |
| `docs/APP-BUILD.md` | `docs/upstream/APP-BUILD.md` | 3988 | `3928d1ea102412572aae7187dd8c6b1a3b5339861d9190295c95a1c3c6a79fd2` |
| `docs/ARCHITECTURE.md` | `docs/upstream/ARCHITECTURE.md` | 19717 | `7d65321fbdf8a4f6ac3d53e5fb12570b065f64df2195ce8e29b2dd2af1288d58` |
| `docs/BENCHMARK-MATRIX.md` | `docs/upstream/BENCHMARK-MATRIX.md` | 11138 | `f3636430ec3351531f2ccfb84c57c13727e7ae74c6a20ed3a34c56a298548c10` |
| `docs/BENCHMARK-RUN-SCHEMA.json` | `docs/upstream/BENCHMARK-RUN-SCHEMA.json` | 6806 | `3532be6f3d02a51cea105c4fb2314df1193624cdc235fe1f409a31c42bcb68d3` |
| `docs/CAPABILITY-GAPS.md` | `docs/upstream/CAPABILITY-GAPS.md` | 8641 | `46fcd53f0808c7e0c36ad4a25a037d3f072633b54d9c682099b2bf035d72d073` |
| `docs/CAPABILITY-LEARNING-E2E.md` | `docs/upstream/CAPABILITY-LEARNING-E2E.md` | 17466 | `81c0d9108805661d74f93cc9ff45768e06ef58fa7d02145d1ff1b7c0dfb5daba` |
| `docs/CHAT-INTEGRATION.md` | `docs/upstream/CHAT-INTEGRATION.md` | 4538 | `9072457c0757e17a2411f3d86e79489f3475e4025980f8efe3cd778a6225e4f0` |
| `docs/CHAT-UI-V6-PLAN.md` | `docs/upstream/CHAT-UI-V6-PLAN.md` | 6660 | `ac8c346896413c53693d2db06a99b060fc41dfdd2e7385c2621cdf15f1049c87` |
| `docs/CHATTING.md` | `docs/upstream/CHATTING.md` | 65190 | `c77dc6760a8526f4f3608a58a0dd2e632d8e25a495de4f91411f97e9476d3c4c` |
| `docs/CLI-REFERENCE.md` | `docs/upstream/CLI-REFERENCE.md` | 687890 | `829ec807b9e2a1ae163301a239f9d3410d1833e9a5e20a708f73b05e8bf0e467` |
| `docs/CLI-STYLE.md` | `docs/upstream/CLI-STYLE.md` | 14596 | `e9d3d859a210f92a18ad1d1a692b18d54ecfa8e4bd69e6dc51cac8dd9f8cead4` |
| `docs/CLI-VISUAL-AUDIT.md` | `docs/upstream/CLI-VISUAL-AUDIT.md` | 17533 | `964af95fa6a5126635f48d10aee3761bb880f2ddc5985aea9a2ccc213d424286` |
| `docs/COMPLETE_AUDIT-2026-09-13.md` | `docs/upstream/COMPLETE_AUDIT-2026-09-13.md` | 562033 | `b43191dcc3fe1b2bf2d1efce9a5c750592cd3e258e12911d16d090f4bf4c2dce` |
| `docs/CORE-PLAN.md` | `docs/upstream/CORE-PLAN.md` | 27983 | `b0e06ed1b08ee52a08de544557bdd1c37820b69ca0e85033e267e8f8ee6f5d79` |
| `docs/DESIGN-2-GODS-VOICE.md` | `docs/upstream/DESIGN-2-GODS-VOICE.md` | 24994 | `c27c56a157ffb0e9f8298bbac0df9fba6841548f6893554d79b6f5413df58e52` |
| `docs/DESIGN.md` | `docs/upstream/DESIGN.md` | 35999 | `7bf2d00f928a45bbe17271cfb0f77bc51c4a78ae6154d445dc923be6edf601fd` |
| `docs/DEV.md` | `docs/upstream/DEV.md` | 2548 | `b9cf575170a58e897e178e479b5f42992a616c76f7130ebe04bb026f3ed9bebf` |
| `docs/DISCOVERY-TESTSET.md` | `docs/upstream/DISCOVERY-TESTSET.md` | 1484 | `94a26dd0bd924d0c4e45c2346cf09be4f06b8b2416672e2011973035f2bc6fd7` |
| `docs/E2E-LIVE-PROOF-40.md` | `docs/upstream/E2E-LIVE-PROOF-40.md` | 10097 | `1d74d0a867ead3d19cc7d9fddc715fad27d4a3195670645939184cf0aedc31d7` |
| `docs/EVAL-LONGHORIZON.md` | `docs/upstream/EVAL-LONGHORIZON.md` | 2790 | `b8fbc31c8c262d4b7b6acc2d98a7542c643388dad7f7513ac746a59c2ab338a6` |
| `docs/EXPERIENCE-V6-PLAN.md` | `docs/upstream/EXPERIENCE-V6-PLAN.md` | 4069 | `9a6762049bbe5bdf1c7159f120717c6b9c211b14176425ea71b75ed7ee53526a` |
| `docs/FEATURE-MAP.md` | `docs/upstream/FEATURE-MAP.md` | 217574 | `9cdc740abf0b1bd50e95e3fd630776a426a1685cea3bde460a1a7c2b19c370bc` |
| `docs/FEATURES.md` | `docs/upstream/FEATURES.md` | 21687 | `fc50f989647792bd3f463863e59cfa57cdf06b7f03cac7cc8de7043f330b7ca2` |
| `docs/FONTS.md` | `docs/upstream/FONTS.md` | 2026 | `4d3be7dcefa514ebc38645da709c1d6784778f9c3033d452e4428dde74645858` |
| `docs/GODSV-COMPLETE-OVERVIEW.md` | `docs/upstream/GODSV-COMPLETE-OVERVIEW.md` | 10720 | `2294d6dfe968e1803a2919e8016ba0e9d5925ec8da56885cef2f1166d428ad51` |
| `docs/GV-README-DRAFT.md` | `docs/upstream/GV-README-DRAFT.md` | 31532 | `0fbb1beac1c259fddec9e718a4c7b24b1d8275e917c2607166ca2d404a74cf74` |
| `docs/HANDOVER.md` | `docs/upstream/HANDOVER.md` | 22887 | `3075e329e663255608436c7d487fae5a9897466cd33ab93a59a3bae4d3d8f28d` |
| `docs/HARDENING-MATRIX.md` | `docs/upstream/HARDENING-MATRIX.md` | 10482 | `987f80c0b0e5dbe69d8e864584e5eeb26c0241f3a0498d4fdc59bf3ede8d0c39` |
| `docs/IMPLEMENTATION-MATRIX.md` | `docs/upstream/IMPLEMENTATION-MATRIX.md` | 25716 | `ed7b7a46794b3bbb86091897b9c7a502d78f57552e6c0656ea61418d66850803` |
| `docs/IMPLEMENTATION-PLAN-docker-tool-builder.md` | `docs/upstream/IMPLEMENTATION-PLAN-docker-tool-builder.md` | 11426 | `0aad4f94ecd8620798578c43c53878ca8da746fd1d187269813b7194f9cedfb6` |
| `docs/IMPLEMENTATION-PLAN-live-control-plane.md` | `docs/upstream/IMPLEMENTATION-PLAN-live-control-plane.md` | 6798 | `c1b6fe932cad4dbfc5a89c8c0b133fcb0feb12f6d98c1f3c93f5264509bf2c54` |
| `docs/IMPLEMENTATION-PLAN-recovery-skaldir.md` | `docs/upstream/IMPLEMENTATION-PLAN-recovery-skaldir.md` | 16132 | `e2c47f8bcebe5659c735782458b6f15ded8a0c613c4374b2168d0a11e820c483` |
| `docs/IMPLEMENTATION.md` | `docs/upstream/IMPLEMENTATION.md` | 19779 | `345a382e5b0d4a6f5bf290a13ebde7a0881d7d08fe303a7405df918ba8690587` |
| `docs/INSTALL.md` | `docs/upstream/INSTALL.md` | 10321 | `205da4b364d63bf308bb0510bf7e505655ffd4582f5a669b00b87d2581b83ea5` |
| `docs/JEV-IMPLEMENTATION-PLAN.md` | `docs/upstream/JEV-IMPLEMENTATION-PLAN.md` | 8839 | `7084480d0c9452108c2f7c77c00126c893fc398c52f8b4f510ff1e7d28e03f94` |
| `docs/LIVE-STREAM-SPEC.md` | `docs/upstream/LIVE-STREAM-SPEC.md` | 3462 | `7f729de387c56144becf2d10bc5dc12bc8bb68baa3fba2b1b524e3a6b8085561` |
| `docs/MEMORY-LEARNING.md` | `docs/upstream/MEMORY-LEARNING.md` | 21525 | `adf050ab87cf2002d11f7eb5dd1fd203cc6d6fc924e9e87fb0948b53feca4042` |
| `docs/PLATFORM.md` | `docs/upstream/PLATFORM.md` | 23483 | `496f9e7d9e547377ad6b345833265fd86b5fbdd3420abfbf4aa4c27c3936e291` |
| `docs/PRODUCT-RUN-6.2.md` | `docs/upstream/PRODUCT-RUN-6.2.md` | 2823 | `c226f9bf2b751b9ea42a96219b67d4943fa97f0941dd63f7b964e944f183fe6b` |
| `docs/PROJECT-STATE.md` | `docs/upstream/PROJECT-STATE.md` | 53262 | `41df80117afe314f94772a603094f629052fabfb2ea51c7fd5589d49311fc5b8` |
| `docs/PROMPTS.md` | `docs/upstream/PROMPTS.md` | 3383 | `d402b2143197fc66afee8a567a141d91b4874eed4c042398989bbad123b3d71d` |
| `docs/QUICKSTART.md` | `docs/upstream/QUICKSTART.md` | 18495 | `97fa685c9765dbf0f5c4ba74857667a860d752becbf312c2feb088144dcde27b` |
| `docs/RENAME-MIGRATION.md` | `docs/upstream/RENAME-MIGRATION.md` | 6080 | `b2ae7c02c02d33d2c16f00b8b5c96c73e36a71cc83751de22bfbbd8fb683798a` |
| `docs/RENAME.md` | `docs/upstream/RENAME.md` | 23015 | `1adb20a647b26bbfaf591f32d3d611fae19fd7005f42837c3445cd358df1e19c` |
| `docs/REPOS.md` | `docs/upstream/REPOS.md` | 7732 | `46ca5adf50ec9b2582a77ab65de43d3f6c5245aafad5c5510b1e7f508fefaa28` |
| `docs/RESEARCH-V6-INPUTS.md` | `docs/upstream/RESEARCH-V6-INPUTS.md` | 6346 | `9a88b3a92af2022173bc6add85605b65c1d4abff796ebe6abeb4232167776f50` |
| `docs/RF-LONGRUN-FREIGABE.md` | `docs/upstream/RF-LONGRUN-FREIGABE.md` | 6626 | `9b1eb38ec33601013a4e58d8804e2815b80a1f51b5f6cff42fa036f55714e758` |
| `docs/RF-STATUS.md` | `docs/upstream/RF-STATUS.md` | 18740 | `98d1c78568d384630cb1805894cd4ef92dfc2bdbdb657e36aff689e80852b729` |
| `docs/RUN-LIFECYCLE.md` | `docs/upstream/RUN-LIFECYCLE.md` | 13055 | `0ff2f6389464dc04a9eb5bc418fe76efc45458a5bd6d9ecaf08df9ca1430faeb` |
| `docs/RUNBOOK.md` | `docs/upstream/RUNBOOK.md` | 28955 | `d2b099790d31b132df56079cf4783c20a1360ccfe93fb7fec2a5935204b9beaa` |
| `docs/SECURITY-BOUNDARIES.md` | `docs/upstream/SECURITY-BOUNDARIES.md` | 22401 | `d9be238e056f4157a74ebf754304aaec624af4c42ba7092c17916a8d5b7d359a` |
| `docs/SECURITY.md` | `docs/upstream/SECURITY.md` | 15767 | `65bd105bcfc6137d8b0aadbf5b635b4a59c8f6c6f2ff234184fbbc0740a088af` |
| `docs/SNIPPET-93-subagent-roles.md` | `docs/upstream/SNIPPET-93-subagent-roles.md` | 5041 | `be0b7dfe74c937d6afc3c0567cffe28bfd88df89022c098a6cd4700517b1a53e` |
| `docs/SOURCES.md` | `docs/upstream/SOURCES.md` | 9306 | `e132ae89ab3838195737e5e8651c93ab15f983a8637822aa758d1f1a3c275c0c` |
| `docs/STATUS.md` | `docs/upstream/STATUS.md` | 14359 | `b17b064bf2b1dfdcd947737bbaa809206e12e023f4be60684355393977461ebc` |
| `docs/SUPERVISOR-AUDIT.md` | `docs/upstream/SUPERVISOR-AUDIT.md` | 12851 | `2f2482a9559780a0f599fe4402cd58d64f830c61e392f54645210cada9c81def` |
| `docs/SURFACE.md` | `docs/upstream/SURFACE.md` | 13352 | `11bdba69eb0e012ba177264f46a2912d4f7884b4580bd94d96d45e132d34dfc9` |
| `docs/TOOL-HUB.md` | `docs/upstream/TOOL-HUB.md` | 3478 | `e32a5195c1761bbffaf5e32023ed3fc5b7c543331256dd4744a396f1c0aa80ef` |
| `docs/TOOLS-V2-BUILDER-GAPS.md` | `docs/upstream/TOOLS-V2-BUILDER-GAPS.md` | 13381 | `7f80a90f230ca42b0b0052cd4e8772adfbf1424d0b4b2e53e47c4be4bd30cec2` |
| `docs/TOOLS-V2-BUILDFLOW.md` | `docs/upstream/TOOLS-V2-BUILDFLOW.md` | 1229 | `c796fa0f2037f16b09f294881021c1d475939395612de7152d8feef5d53ae97d` |
| `docs/TOOLS-V2-DISCOVERY.md` | `docs/upstream/TOOLS-V2-DISCOVERY.md` | 27653 | `65c72997db9ecdb950ba17e304d23ed0b53c065228c5671757dffadcba99aa38` |
| `docs/TOOLS-V2-EXECUTION-BOUNDARY.md` | `docs/upstream/TOOLS-V2-EXECUTION-BOUNDARY.md` | 13725 | `2a331eef5e6b913935d8a38d1d73f68be588180d1c06273f19ae8510c787d503` |
| `docs/TOOLS-V2-HOST-WIRING.md` | `docs/upstream/TOOLS-V2-HOST-WIRING.md` | 17908 | `793d251b40bc1f7ce3d0a63813b525ed2e2078d2286c9ea6f9718058305c5b80` |
| `docs/TOOLS-V2-MINING-HEALTH.md` | `docs/upstream/TOOLS-V2-MINING-HEALTH.md` | 20280 | `a8fffae1bddfba74a1954b2b9b9281abdc6e55605e6e646edd949a406d857d3e` |
| `docs/TOOLS-V2-REGISTRY.md` | `docs/upstream/TOOLS-V2-REGISTRY.md` | 12933 | `1c54909e19bf0a1dc5280e2632f99f045f356ce07f0dc7e6cec5ea0907e30ecc` |
| `docs/UI-AUDIT-CHECKPOINTS.md` | `docs/upstream/UI-AUDIT-CHECKPOINTS.md` | 6668 | `b10d0942f95279b2b4657b2df7791bc2d74f41bfe6666706c88c24c98609621a` |
| `docs/UI-DESIGN.md` | `docs/upstream/UI-DESIGN.md` | 4401 | `12caff1ec8b17ce2aa64535237d50602d8a6abb1a8566b725a9f8f5a38bcf35f` |
| `docs/UI.md` | `docs/upstream/UI.md` | 9132 | `d499ae050b08b77645437eb2da3540974f2d0f549dc7f2a504cb220deb860c46` |
| `docs/VERIFICATION-1.3.md` | `docs/upstream/VERIFICATION-1.3.md` | 8196 | `02801d8dd20d77acc753c2c33bc1c3397b06bd519631397f1f39cf73f91e347e` |
| `docs/VERIFICATION-1.4.md` | `docs/upstream/VERIFICATION-1.4.md` | 9157 | `0ad023667dbf2e2a3071dc6758a3134a166bf2b80b010281cfd0ab8687450fe6` |
| `docs/VERIFICATION-CLOSURE.md` | `docs/upstream/VERIFICATION-CLOSURE.md` | 21521 | `68e202e319d3d3cbaf951bd1e4d3ab05f054928000a0dc9ebf66769288a09437` |
| `docs/VERIFIED-FINDINGS-live-2026-09-13.md` | `docs/upstream/VERIFIED-FINDINGS-live-2026-09-13.md` | 2351 | `afb5a8412b7107774a0c59baa6eff2634b96da9ad2923d969569957ed0de9241` |
| `docs/VOICE-CALIBRATION.md` | `docs/upstream/VOICE-CALIBRATION.md` | 18575 | `6d1b124b607bdc4220f9770d2f9954040447ba557ea42dac555a9fac58db6864` |
| `docs/artifacts-94-token-economy/archived-tool-result.json` | `docs/upstream/artifacts-94-token-economy/archived-tool-result.json` | 1076 | `579e33069f831dd7354c54ad0db21f1894875613d4eacbc7f74ea648a7c57178` |
| `docs/artifacts-94-token-economy/f1-lease-cas.txt` | `docs/upstream/artifacts-94-token-economy/f1-lease-cas.txt` | 1700 | `d40240d67b468fddef8c46a570088490ff4aa89832fccaeb4978bb34f6fe25f6` |
| `docs/artifacts-94-token-economy/hot-form.json` | `docs/upstream/artifacts-94-token-economy/hot-form.json` | 1137 | `10d3e393967430a043187b49eb1cf2e58929744809d559ebcc5700554347cffd` |
| `docs/artifacts-94-token-economy/progress-decision.json` | `docs/upstream/artifacts-94-token-economy/progress-decision.json` | 3282 | `f3bbb7ae37d544f35d8e781d2918a824638d44cb078196480a3dc282827cb56b` |
| `docs/artifacts-94-token-economy/retry-gate.json` | `docs/upstream/artifacts-94-token-economy/retry-gate.json` | 1910 | `d898de2feedc966536e4473093ade3ad6cbc0f1fd3d689e7e083287a3e99442d` |
| `docs/artifacts-94-token-economy/run/state/runs/run-20260919T135346Z-1cd1ec.jsonl` | `docs/upstream/artifacts-94-token-economy/run/state/runs/run-20260919T135346Z-1cd1ec.jsonl` | 11337 | `896d2fe610f09b7126a5c64377d3db321db8b73b8246d2bb73e69694c4473ce5` |
| `docs/artifacts-94-token-economy/run/state/tool_archive/dd59b60a2a5e55e42d3f2b940f999c7c5e195de203c855d07ede6f0935d3f479.json` | `docs/upstream/artifacts-94-token-economy/run/state/tool_archive/dd59b60a2a5e55e42d3f2b940f999c7c5e195de203c855d07ede6f0935d3f479.json` | 137200 | `dd59b60a2a5e55e42d3f2b940f999c7c5e195de203c855d07ede6f0935d3f479` |
| `docs/artifacts-94-token-economy/token-report.json` | `docs/upstream/artifacts-94-token-economy/token-report.json` | 7350 | `2cb7a7a16bad1f17c3910557e0f731184bc37ce4a7e0271f542a665e354d33e6` |
| `docs/artifacts-94-token-economy/token-report.txt` | `docs/upstream/artifacts-94-token-economy/token-report.txt` | 2006 | `8106d1b413d9ff77f5916ae0fe07fd6e5ba07ad9b7289771801ee50a35379f1f` |
| `docs/artifacts-94-token-economy/worked-example.json` | `docs/upstream/artifacts-94-token-economy/worked-example.json` | 15271 | `b0a624eab5703b3643eb002105a2c7ad7ad5320b02c3319c3f117454c8309a14` |
| `docs/audit/ARTIFACT-MANIFEST.json` | `docs/upstream/audit/ARTIFACT-MANIFEST.json` | 23074 | `ad5c3397adad25526119691946a0797d843f0f61d1a91a415256d65d94c66670` |
| `docs/audit/AUDIT-FINDINGS.md` | `docs/upstream/audit/AUDIT-FINDINGS.md` | 25780 | `e883a4d28b43cc5737281d51b637851c32d3ce0ac620228fcf5d1fc0a4c69dc3` |
| `docs/audit/BENCHMARK-RAW-README.md` | `docs/upstream/audit/BENCHMARK-RAW-README.md` | 6238 | `57e445bd0283c517065faf659ca0167ec52db1c1f6b506c697e51d73b593931b` |
| `docs/audit/BENCHMARK_MANIFEST.json` | `docs/upstream/audit/BENCHMARK_MANIFEST.json` | 2637 | `adf96feb162aa7e89f25bfb742026857f7ed2d8b93f4bfbe0e7248bd5f1aadea` |
| `docs/audit/CLI-CONTRACT-PROBES.md` | `docs/upstream/audit/CLI-CONTRACT-PROBES.md` | 3345 | `141f394156ad036a160739a4f4bfae70cde2bd1ca228171f79d4812ccbb1335a` |
| `docs/audit/COMPLETION_PRECHECK.md` | `docs/upstream/audit/COMPLETION_PRECHECK.md` | 7299 | `e5407465170ea8f7dae8fb08a91df53789334ca522cb79033509964e218e1988` |
| `docs/audit/COMPUTER-USE-GUI-E2E.md` | `docs/upstream/audit/COMPUTER-USE-GUI-E2E.md` | 4384 | `a216b139e51e1e9ee9f96534545c7613fdd6806ed0a90f64cdca7d1d6b84ae91` |
| `docs/audit/COMPUTER_USE_SUBTOOLS.md` | `docs/upstream/audit/COMPUTER_USE_SUBTOOLS.md` | 8092 | `473efafa4d2f88c633cc2a8e15d2ca6c40061e4d61f4d94370f511fae2e379dd` |
| `docs/audit/DOCKER-BUILDER-EVIDENCE.md` | `docs/upstream/audit/DOCKER-BUILDER-EVIDENCE.md` | 6520 | `25aab42cf531d004c71c05e92a032c37a83640739403da1d42fa135996a55616` |
| `docs/audit/JEV-WORKER-A-CODE-MAP.md` | `docs/upstream/audit/JEV-WORKER-A-CODE-MAP.md` | 3563 | `29049216b1088b1fbcee234289b8117942d97959afc0e41889786eb4973d170c` |
| `docs/audit/JEV-WORKER-A-REPORT.md` | `docs/upstream/audit/JEV-WORKER-A-REPORT.md` | 6655 | `4fb16e6126e0c7f80b1a07cd4cc83f13526924a8339b075b172ab54f2d5c388d` |
| `docs/audit/JEV_CU_PACK.md` | `docs/upstream/audit/JEV_CU_PACK.md` | 12744 | `d41a622eb9fcc57319df9a9fe5cbe079ddf520f1bfa3d179bbf7d5c9733d9476` |
| `docs/audit/JEV_ID_MAP.md` | `docs/upstream/audit/JEV_ID_MAP.md` | 4952 | `e0dadb411b6896f04fc9786b332df249e4216763de30064c5b07bc8e96ceb5e8` |
| `docs/audit/JEV_PROVIDER_COMPATIBILITY.md` | `docs/upstream/audit/JEV_PROVIDER_COMPATIBILITY.md` | 10669 | `e19e318fc6e750debbf499dd97ee5fb818d4ef4443cb0e61a9dde68b132667db` |
| `docs/audit/PERFORMANCE.md` | `docs/upstream/audit/PERFORMANCE.md` | 13738 | `e32a5b509cf0f5b6e5695ff2570a8751383746b5eabc56456c5ed1fdb284a33e` |
| `docs/audit/PLATFORM-MATRIX.md` | `docs/upstream/audit/PLATFORM-MATRIX.md` | 5860 | `1e24fa87f61c5cbecf8af35acefd7bbc81a4e2db4ca8a6781115d7face625bbf` |
| `docs/audit/RELEASE_NOTES.md` | `docs/upstream/audit/RELEASE_NOTES.md` | 2380 | `e8981aa87c4d363a9c8d6668a30b7371aa0c71b34e2d57a6996b34cb62629316` |
| `docs/audit/RF09-CRASH-WINDOW-INVENTORY.md` | `docs/upstream/audit/RF09-CRASH-WINDOW-INVENTORY.md` | 8072 | `586b0e3d4f0d9b9122d11bd645e2f41a46a8fa241959f4d7c3f9f43b3fe3213b` |
| `docs/audit/SKILLS_USED.md` | `docs/upstream/audit/SKILLS_USED.md` | 1687 | `784f646bad3e2c2f8925e782fc67fadfb057146ec7f13a6110ea1dc48e261a67` |
| `docs/audit/SYSTEM_ONE_GUISTATE.md` | `docs/upstream/audit/SYSTEM_ONE_GUISTATE.md` | 9529 | `d920abd71e2755ad599476b4dfd21a3db4f8927198b20afa3d165476949bb54f` |
| `docs/audit/SYSTEM_ONE_QUESTIONS.json` | `docs/upstream/audit/SYSTEM_ONE_QUESTIONS.json` | 18767 | `c52e18f0aac8cb1c7f38198f979bd221387f8fbcb012ebd20e26ab543f53e1fe` |
| `docs/audit/SYSTEM_ONE_SHADOW.md` | `docs/upstream/audit/SYSTEM_ONE_SHADOW.md` | 11579 | `c5b57775b037782b88c81f9800cd10df88b98bb706807684a7f5247a2e4dcc69` |
| `docs/audit/SYSTEM_ONE_TRIAGE_PACK.md` | `docs/upstream/audit/SYSTEM_ONE_TRIAGE_PACK.md` | 17539 | `421c06248c6fa58ca8b262928d5be2d9e377e277172d6a7739733921a7dea076` |
| `docs/audit/TEST_REPORT.md` | `docs/upstream/audit/TEST_REPORT.md` | 9488 | `eb3df01b45050fa1b22f8cf6b10e2ed1ee7a3607e56c2bb6b2d1393425588b0b` |
| `docs/audit/UI-FEATURE-MAP.md` | `docs/upstream/audit/UI-FEATURE-MAP.md` | 12330 | `087121958eafd7882572b482d3598eba58df4794c365961d839d1ca0288c2c20` |
| `docs/audit/UPDATE_COMPATIBILITY.md` | `docs/upstream/audit/UPDATE_COMPATIBILITY.md` | 2359 | `652b7cc556f365a2972f07a1bccd405b6fd955bc04f80fe8df6c2f9be5e71451` |
| `docs/audit/probes-2026-09-12.json` | `docs/upstream/audit/probes-2026-09-12.json` | 1470 | `e21f329b286eb9809f234f39f832bd116446777ab16012bf3ee1488dea3fa081` |
| `docs/audit/probes-after-etappe1.json` | `docs/upstream/audit/probes-after-etappe1.json` | 2047 | `73891463a061d7934a2cdc06ad86085de6d6727da25470a478bda55fbb2df783` |
| `docs/audit/probes-after-etappe2.json` | `docs/upstream/audit/probes-after-etappe2.json` | 2817 | `b932697a6bbfd435622ec8193d8d5ffcc2e7e53de9aa9341a8a96a6334db797c` |
| `docs/audit/review_probes.py` | `docs/upstream/audit/review_probes.py` | 4215 | `e3dece30a8eb0e4663e134668022741ec5fe90c3fd8ac50c6e44a1068e166efb` |
| `docs/skaldir-upgrade/decisions.md` | `docs/upstream/skaldir-upgrade/decisions.md` | 3629 | `543679ae6ffc4c3b932ab75a4404a5b961ec185429d5a533d6b7af84513fc4fd` |
| `docs/skaldir-upgrade/implementation-audit.md` | `docs/upstream/skaldir-upgrade/implementation-audit.md` | 3814 | `d43ab9507b05e313b8a59b4cc906f01ba067b1d037f16dea59c13becf7d73a34` |
| `docs/skaldir-upgrade/ist-audit-2026-09-25.md` | `docs/upstream/skaldir-upgrade/ist-audit-2026-09-25.md` | 17915 | `0e71498e51cd76a1c2b757112c794b826296f623575513335cecb158f9bd7d06` |
| `docs/skaldir-upgrade/validation-report.md` | `docs/upstream/skaldir-upgrade/validation-report.md` | 3721 | `c03c6d3a331f64c9238698a9900425880b5cbdf59c4493b621a95c1bf86b863d` |
| `host-overlay/patch_agent.py` | `host-overlay/patch_agent.py` | 4488 | `b18f04dd4dccd8bf1ce76748ea9ff786c50567641071cfc1133c553a5ce583eb` |
| `host-overlay/patch_rf02_request_deadline.py` | `host-overlay/patch_rf02_request_deadline.py` | 9928 | `4ea0cb7e104a2153b41d5ca8d0c2243ae8100a2fcc2fa9ecb329d711d9736c86` |
| `host-overlay/patch_rf03_monotonic_stream_clock.py` | `host-overlay/patch_rf03_monotonic_stream_clock.py` | 9902 | `81bbf63c06ab32bd2b171bfbad917c3d14a19f6105a3da99ed4ed4d74ac7f4e4` |
| `host-overlay/patch_rf04_retry_budget.py` | `host-overlay/patch_rf04_retry_budget.py` | 13730 | `f1265d2af67699f421cbe7a6f1d3ab462a515755bc4048ef2b9b965779c1196c` |
| `host-overlay/patch_rf05_fallback_fitness.py` | `host-overlay/patch_rf05_fallback_fitness.py` | 13978 | `18df21c9d8149d522f8db9679881807d92bdd7121a673bb3e695ecbbd167e3a8` |
| `host-overlay/patch_rf06_compression_preserve.py` | `host-overlay/patch_rf06_compression_preserve.py` | 18433 | `b270787a29276e5e681b704b8c20f7ea6408daf8169b65ea5c39afacc2bde166` |
| `host-overlay/patch_rf07_interim_report.py` | `host-overlay/patch_rf07_interim_report.py` | 20363 | `0b3e6f5c7cc08ca4fb315bea8676c9ab8dd1b968a049fbd997f12e5ebed64cb2` |
| `host-overlay/patch_rf08_segment_continuation.py` | `host-overlay/patch_rf08_segment_continuation.py` | 22616 | `6e0ccc860b1655560f802ec9dd8df1f0bfa23768d58d00e4dbf33b0025556964` |
| `host-overlay/patch_rf09_crash_windows.py` | `host-overlay/patch_rf09_crash_windows.py` | 33616 | `ef05b77f8ec3bd0e537c71e012ad4e171ed8eed503f62e3f1903b0c0bfd07ca3` |
| `host-overlay/patch_rf10_stop_responsiveness.py` | `host-overlay/patch_rf10_stop_responsiveness.py` | 7162 | `dcac091daf983e8f7ea6b7f46e8d566f1dfe06e6253d92a89081908e3808a7da` |
| `host-overlay/patch_tool_refresh.py` | `host-overlay/patch_tool_refresh.py` | 5517 | `2a906c461714668f012f1b2976a6fcb8fd6ec8b89341b031927658166a812d25` |
| `install.sh` | `install.sh` | 1027 | `7849b7ecf4cf11dbbc02c7092fd89352e319567dae75ec84f8f2369531646e54` |
| `integrations/hermes/README.md` | `integrations/hermes/README.md` | 9341 | `09d132d65a0814b928b6ecf92acc7f398f51048fbef61863d504cb2ff3887ca8` |
| `integrations/hermes/godsv_plugin/__init__.py` | `integrations/hermes/godsv_plugin/__init__.py` | 126247 | `6b60172db8d9bf287e0f2200d2ea8922bbcecce120cd9de80601e67a97412edb` |
| `integrations/hermes/godsv_plugin/dashboard/manifest.json` | `integrations/hermes/godsv_plugin/dashboard/manifest.json` | 216 | `8b6e2fdf279f162a0e48544457b12a413bbd48bc96b7b82f0038e4e3f2e34406` |
| `integrations/hermes/godsv_plugin/dashboard/plugin_api.py` | `integrations/hermes/godsv_plugin/dashboard/plugin_api.py` | 84729 | `2b76963533cffc40a8788ec7b09e5e4dd5930d1c079d34dc48a63bcee000fa68` |
| `integrations/hermes/godsv_plugin/desktop/logo_art.js` | `integrations/hermes/godsv_plugin/desktop/logo_art.js` | 1770 | `ec3aa3ddc0a2abda29c7f4678bfb913a3b2c594b9ac5f0135232a0723c63e7cc` |
| `integrations/hermes/godsv_plugin/desktop/plugin.js` | `integrations/hermes/godsv_plugin/desktop/plugin.js` | 206833 | `e2557bc15e4c29bd412a07fe5f441e949ed150194622ea9b8f43329769cd107d` |
| `integrations/hermes/godsv_plugin/plugin.yaml` | `integrations/hermes/godsv_plugin/plugin.yaml` | 1473 | `45af18022e78e55ef076f7f4a82e2db92e742ba33818b81bc0a899505c82c5a3` |
| `package-lock.json` | `package-lock.json` | 916 | `fc18a066da90f9e9aba44a31f2d9bc0ab51b67021a176f1e49e973fe8edfdaca` |
| `package.json` | `package.json` | 261 | `6faf0df9695466e04d734d7fc687ee7e1989da9148cc96eba07775c2ec4ec202` |
| `prompts/completion_check.v1.md` | `prompts/completion_check.v1.md` | 1406 | `d393bae0205859733ff81bfea85e5fa0782833f44008af6cb40f22d9690a6b95` |
| `prompts/cu_step_plan.v1.md` | `prompts/cu_step_plan.v1.md` | 1555 | `122c32533982a62e4d2ee9bd1f7de7c823fb8a9c519d99d2c10f988e7b0db87f` |
| `prompts/incident_extract.v1.md` | `prompts/incident_extract.v1.md` | 826 | `251fe2a107af234854f17f48e26c24b863769e0933ac013f5823e3131ee5cd46` |
| `prompts/main_agent_steering.v1.md` | `prompts/main_agent_steering.v1.md` | 1969 | `9354798698d8ca73cd70a2ca7698f2d4164f588d10085eb60b321f6d18fd475e` |
| `prompts/research_note.v1.md` | `prompts/research_note.v1.md` | 1016 | `738b59bfaccbf05da891d3cd45ad9c07ce9a6b606036a2930e6896cf2243de2d` |
| `prompts/same_problem_judge.v1.md` | `prompts/same_problem_judge.v1.md` | 787 | `74ef5267a464989b8a2902f7c07e02b7f9df0b5bfaa8c412043d37f06760c909` |
| `prompts/same_problem_judge.v2.md` | `prompts/same_problem_judge.v2.md` | 1514 | `03208ea131f455fe189033307cc9fb0ec1e436428b21518606727a774cfd4984` |
| `prompts/skill_draft.v1.md` | `prompts/skill_draft.v1.md` | 881 | `97ada7dfa05e5115dd00acc9165749ffeef8a04fd60661f2a3e53dfee6e7cbd7` |
| `prompts/slop_check.v1.md` | `prompts/slop_check.v1.md` | 958 | `6759435b5dfce6e48eac14c02038521d90548bf4596184385fd755aa29fdfd0d` |
| `prompts/spend_intent.v1.md` | `prompts/spend_intent.v1.md` | 1098 | `8f2678d6a5a507fe529d8a05538eaac4bcb68ae45b70f8d4ccaccdab6c82adf6` |
| `prompts/tool_spec_draft.v1.md` | `prompts/tool_spec_draft.v1.md` | 1474 | `082a3c466dd2fb20d0d0b5d6c98fad43c95d6f53d524ed30ed2d09f1764fc804` |
| `prompts/voice_redirect.v1.md` | `prompts/voice_redirect.v1.md` | 1869 | `ad0e0dc9b1100abf56278845abb8897dde44f77b0132c8bd6f9e74102efe2b03` |
| `prompts/voice_redirect.v2.md` | `prompts/voice_redirect.v2.md` | 2500 | `43e8592b569d444c306fb2119df6879ed26b7b7a41d353a0476d55b4cc55dc5b` |
| `prompts/voice_synthesis.v1.md` | `prompts/voice_synthesis.v1.md` | 1135 | `47c2b44a20aa9e2eb5bbbc4f435f2662c97547ccdc9fe87870e603c3dc6e98e9` |
| `prompts/voice_triage.v1.md` | `prompts/voice_triage.v1.md` | 2543 | `cbd5f591f76e22d27eb5b9942fb360a67fdcfbb0ddbdbc52446670c8d7876646` |
| `prompts/voice_triage.v2.md` | `prompts/voice_triage.v2.md` | 3654 | `0eb00516ec5a4494320bae80c4c41a263658b8d779215a529ea6f901fe272714` |
| `pyproject.toml` | `pyproject.toml` | 9052 | `7a1e5a24709158810789b97360759b662cfd70b868f360f5cf673b84fa2d6983` |
| `scripts/install-cli.sh` | `scripts/install-cli.sh` | 6794 | `41fa1841402367cbdb1c460a2890cd1665d9b7f963b0a361e697e7a1fd1e7a2b` |
| `scripts/proof-codex-ux-pty.py` | `scripts/proof-codex-ux-pty.py` | 20087 | `944d5d2a9b16782581fbbb229b6892be9664471ca7c92fb07149efdb83c29bf0` |
| `skills/create-docker-tool/SKILL.md` | `skills/create-docker-tool/SKILL.md` | 12925 | `0138e30121bce0191ade86d3ff049ad4b5581e29d46ce2b1ddacc0322152eadd` |
| `src/godsv/__init__.py` | `src/godsv/__init__.py` | 4142 | `d0aa917b8b10c0ae683ad8fc0830dee9266e97a6e3884cb69a8e4a79da1f700f` |
| `src/skaldir/SYSTEM_ONE.md` | `src/skaldir/SYSTEM_ONE.md` | 3930 | `b31002d823c41c84fc3d1a68721cdaf8468ee08dfc04d61565b1992a0e74cede` |
| `src/skaldir/__init__.py` | `src/skaldir/__init__.py` | 1466 | `3e93f2807c44f639d395bdbfa5ee7ddda6d2efab59a231cab83a9c8eefee5071` |
| `src/skaldir/__main__.py` | `src/skaldir/__main__.py` | 1024 | `ffca6b2858eec6ab33de0577b6cdc5d3e7f5da705ed455e4abe5825b8849183f` |
| `src/skaldir/act_cli.py` | `src/skaldir/act_cli.py` | 52660 | `bdbcccb6f01eb7a305c90b6d19aa3c0f9d2d4660e02575e447cadc1609fe4a15` |
| `src/skaldir/act_service.py` | `src/skaldir/act_service.py` | 136938 | `818474e103de49e8f4038039a516f89bd708ff662f32077056b441c76d1378c1` |
| `src/skaldir/action_engine.py` | `src/skaldir/action_engine.py` | 24537 | `d13ccadb54effd1ad94bb3e53cd29d360fb6d5913495c67611f8804d0fb1186a` |
| `src/skaldir/adapter_http.py` | `src/skaldir/adapter_http.py` | 110643 | `975bfdf8480eda04c8487b276923e378797f59dc9b9bc0eee7148946e999d9d7` |
| `src/skaldir/adapter_http_cli.py` | `src/skaldir/adapter_http_cli.py` | 32027 | `80e7a2d728c2b9fd1e0082e5acfde6fe0323adf752fe868a924fe94759013c43` |
| `src/skaldir/adapter_mcp.py` | `src/skaldir/adapter_mcp.py` | 92544 | `bab263cafdc017e938f04ed7e4c8425be1350bb1f021b83939c7ff7d3291066e` |
| `src/skaldir/adapter_mcp_cli.py` | `src/skaldir/adapter_mcp_cli.py` | 40230 | `d2efcce179579478ebb94175f131a51bdc785192a0688e3ccc242d37a993f992` |
| `src/skaldir/adversarial.py` | `src/skaldir/adversarial.py` | 163828 | `3c85b0022bb16d486e31fbb867abb053af465a0da2ea8e92e703e7bc4ca198d8` |
| `src/skaldir/adversarial_cli.py` | `src/skaldir/adversarial_cli.py` | 35762 | `86cdc1181d28de6f46dee88768c5a637923de80918a0c8d81c4a9aa95ccc76b0` |
| `src/skaldir/agent_loop.py` | `src/skaldir/agent_loop.py` | 110123 | `79369d970517a9555f3b6f2dbf960e4875c1dcc51984b963b7aaf59d809e4dcf` |
| `src/skaldir/agent_loop_cli.py` | `src/skaldir/agent_loop_cli.py` | 54211 | `1b6ed69a139bcf8d1a29859c94756abccd7de4f56bd042a519286c7b07754c51` |
| `src/skaldir/agent_toolset.py` | `src/skaldir/agent_toolset.py` | 47732 | `5c5d4a4c5c720d2294e40d8165cfef5bfc13b54483cf2e5173c72b6f2a49dd0f` |
| `src/skaldir/api_server.py` | `src/skaldir/api_server.py` | 123508 | `b4625daa27ddb7e80dabea7e92f669c80d1014a4e909d57c2655fe43b7008da7` |
| `src/skaldir/api_server_cli.py` | `src/skaldir/api_server_cli.py` | 22920 | `2c4a891e745cef275daf37376ebf49972af2448c7b14870554240f02f37b51e5` |
| `src/skaldir/appbuild.py` | `src/skaldir/appbuild.py` | 8540 | `666f60baf310ff0b0072ad0c1afca5d93c87043a6ec1280ec360acaab0b2ed06` |
| `src/skaldir/approval.py` | `src/skaldir/approval.py` | 55422 | `af801dfce1a951c36be1293bedf3b03a4e8f1f443aedfc1e3f0910c1ebd847b7` |
| `src/skaldir/approval_cli.py` | `src/skaldir/approval_cli.py` | 55011 | `f83ad7a2bdb4662d7f5872394abb5c806fd4c3e88b23194341b31cdb908b968a` |
| `src/skaldir/art.py` | `src/skaldir/art.py` | 56178 | `203b3c18a0562640a2c7d0cd688c139db796353fb7ddbf116cd9a347205627c3` |
| `src/skaldir/assets/fonts/GeistMono-Medium.woff2` | `src/skaldir/assets/fonts/GeistMono-Medium.woff2` | 10116 | `72971bd4908d3984fa1b1426bb363730e4957e16a0f58bfbca76993095715488` |
| `src/skaldir/assets/fonts/GeistMono-Regular.woff2` | `src/skaldir/assets/fonts/GeistMono-Regular.woff2` | 9864 | `3f98383b122fe015a48536cd4a1cda855a201718923ffe74931a01597107b9b5` |
| `src/skaldir/assets/fonts/GeistSans-Medium.woff2` | `src/skaldir/assets/fonts/GeistSans-Medium.woff2` | 34716 | `40073e90816315c92e4f4381bd50b6fdc950b22b0dd010a4179046cf588d4f12` |
| `src/skaldir/assets/fonts/GeistSans-Regular.woff2` | `src/skaldir/assets/fonts/GeistSans-Regular.woff2` | 33400 | `e6ef6abb39c762ed2259cc814e6f16888c540cd84835962227b56ab2354f27f1` |
| `src/skaldir/assets/fonts/GeistSans-SemiBold.woff2` | `src/skaldir/assets/fonts/GeistSans-SemiBold.woff2` | 35292 | `9d99fbd791968493fa507ac846de561cee47b00f8100c23cad333b3cb78392d6` |
| `src/skaldir/assets/fonts/MANIFEST.json` | `src/skaldir/assets/fonts/MANIFEST.json` | 3259 | `cc006d4ed1865989af65f2793f8bf6328e12d6792be7551893f08e00249ab981` |
| `src/skaldir/assets/fonts/OFL.txt` | `src/skaldir/assets/fonts/OFL.txt` | 13926 | `c28a87d36b6224db6f8df891fa914fa339c64ee4268688f68115bfa9c6cc8b59` |
| `src/skaldir/assets/fonts/SpaceGrotesk-Bold.woff2` | `src/skaldir/assets/fonts/SpaceGrotesk-Bold.woff2` | 12840 | `35f8aec56cfd5cbfdb03cc68733a54a0b05bb3617ffcd5fd332badc0b045ca55` |
| `src/skaldir/assets/fonts/SpaceGrotesk-Medium.woff2` | `src/skaldir/assets/fonts/SpaceGrotesk-Medium.woff2` | 13312 | `1b1a8131d9edf975d9decee81e2f2bf504812f7a4f498e5500f28a613e22e64c` |
| `src/skaldir/assets/icon.png` | `src/skaldir/assets/icon.png` | 171791 | `3ee0984f1a7f4ef67e6c2c5c44e2e6f4c766cefc3a591a81cd2727fa8ff6ea2e` |
| `src/skaldir/assets/logo-mark.png` | `src/skaldir/assets/logo-mark.png` | 36727 | `9fd9b8129f0c43202e39025eb6b242dcb99cbfee7d9adffb57690ed5d299e8cf` |
| `src/skaldir/assets/logo.ansi` | `src/skaldir/assets/logo.ansi` | 20810 | `be9d5f680ca381942ed8560b9bd1242cee7c32147c76f03febe83395e9237e35` |
| `src/skaldir/assets/skaldir-face.png` | `src/skaldir/assets/skaldir-face.png` | 1101 | `036025c64508e9009b1ef481f3f685ae8869dc484bed009acb29e75b97b2b65f` |
| `src/skaldir/assets/skaldir-logo.png` | `src/skaldir/assets/skaldir-logo.png` | 806991 | `f28d563aa92adb72f760ea64b4c9fb082a3106cbd54403d2ef8618e2f0d36d95` |
| `src/skaldir/assets/skin-skaldir.yaml` | `src/skaldir/assets/skin-skaldir.yaml` | 2601 | `350c6b85f63006e15adc54084e8fdce5a88f3b1da4b947396ccebc170884d35a` |
| `src/skaldir/budget.py` | `src/skaldir/budget.py` | 13764 | `179cef3d382f0099b00766cfcec8d7fd349ee44bf30c17166db49c0c1448a697` |
| `src/skaldir/buildflow.py` | `src/skaldir/buildflow.py` | 51497 | `13ff2349a7f6a39c88af352f7005986a4bee7fe2c7e333d563d749cdbc73ebb0` |
| `src/skaldir/capability.py` | `src/skaldir/capability.py` | 30351 | `37a4253c7839a59e857b82575f3b14e078c7473f3eb7097dc7b14c268188d414` |
| `src/skaldir/capability_health.py` | `src/skaldir/capability_health.py` | 19669 | `a26690ff617c7b9142533a794c0cb20b4fb9199adc7e9409d0018523b471f650` |
| `src/skaldir/capability_mining.py` | `src/skaldir/capability_mining.py` | 76609 | `fc45525e5c0f24aba4dd4ada456ca23a355f1245eed9106c0ee6bc40f97b7c7b` |
| `src/skaldir/chat_screen.py` | `src/skaldir/chat_screen.py` | 35605 | `8eaf54ef20b7a21691e0e0e5e0df7485c223738184c439b99a85474782b1de27` |
| `src/skaldir/chatbar.py` | `src/skaldir/chatbar.py` | 150444 | `93701f1bb4320ff59aead4b78665d4954e33872aadfa15aefb35a5b32343a65f` |
| `src/skaldir/checkpoints.py` | `src/skaldir/checkpoints.py` | 92585 | `33fa654a994655b7ae1773debc8bf95dd84de8b8c48729582120bd4403083122` |
| `src/skaldir/checkpoints_cli.py` | `src/skaldir/checkpoints_cli.py` | 26269 | `ff18a3511cf283b7b86f0b5ce9ab33d310e65a67b396c7ee40b3c493f17da3c4` |
| `src/skaldir/cli.py` | `src/skaldir/cli.py` | 155235 | `30f2213a02ecbd3706f3834fe738f0a9e5c8809898182fb620f547127ceb2eaf` |
| `src/skaldir/cli_contract.py` | `src/skaldir/cli_contract.py` | 134424 | `af1141a435a9092b8e9a7cb4d2c37338aef4d8cf9d9ac9921207c11314dd7518` |
| `src/skaldir/cli_shared.py` | `src/skaldir/cli_shared.py` | 10538 | `2f91c19e7107923f1492dba1251b2f69d0d2d9f6850ca1c533d0f61745cf873a` |
| `src/skaldir/code_exec.py` | `src/skaldir/code_exec.py` | 55252 | `6800bd134a401c66c461faca9f8a2aeb4bc1cc87e576e5390fbcf142eec93302` |
| `src/skaldir/code_exec_cli.py` | `src/skaldir/code_exec_cli.py` | 30865 | `8b4d647f5f0e62439ca041fb35e11de6ce8b5aa3dc7a2233d09614aa626c02ea` |
| `src/skaldir/codectx.py` | `src/skaldir/codectx.py` | 99514 | `0ca35995243489c5eb26d33eb3574bf213ed91869e6e39c74ad30a7ddea30a30` |
| `src/skaldir/codectx_cli.py` | `src/skaldir/codectx_cli.py` | 37084 | `6d424cc5924caa4c972ff1b21c2b3548a218835c82fee94dd698effbb74aeac8` |
| `src/skaldir/coding_features.py` | `src/skaldir/coding_features.py` | 96474 | `a3b3e4c958fd50de4b0f0d98a38978cc34f79c3d9a23abf1fc6232144ac7f294` |
| `src/skaldir/coding_features_cli.py` | `src/skaldir/coding_features_cli.py` | 56251 | `7db9a03eb79309ee27a1686964c531e5993eb61bef10ce55d8f0c3d92167b5f8` |
| `src/skaldir/complete.py` | `src/skaldir/complete.py` | 59124 | `83f5be683954be8e3894ffefb2e5fbacb3aee42799255eb09d007c501e3e9692` |
| `src/skaldir/completion.py` | `src/skaldir/completion.py` | 147032 | `24ff9ca004ad93eb2ca019f4048b136dfea5ccf0e08a5690c1761a2c8f1e49c1` |
| `src/skaldir/compose_cli.py` | `src/skaldir/compose_cli.py` | 34987 | `9321f83756572e0443077ce77bd6f9df7bd1f77b1d954f4b144bb68597110686` |
| `src/skaldir/compose_service.py` | `src/skaldir/compose_service.py` | 91272 | `4d2b3bd7cf65e5b3be733d8659915b37f96f88c0c877a95f825dd84b0b28bce5` |
| `src/skaldir/computer_use.py` | `src/skaldir/computer_use.py` | 83082 | `a9286b228b9843fbe08ca25c4fd8ca83a3ea298efea0252ff23d1d42724410b4` |
| `src/skaldir/container_adapter.py` | `src/skaldir/container_adapter.py` | 26057 | `b43561fe83a67365e79d1d5ef4074d403703c360510692a8b7e8ea832080721e` |
| `src/skaldir/container_cli_templates.py` | `src/skaldir/container_cli_templates.py` | 18773 | `43b3476d4cba2b00cd5f48fa228b9803cde67ba4032b15c410b425520fb352ea` |
| `src/skaldir/container_media.py` | `src/skaldir/container_media.py` | 18399 | `1fd7ab75667830e8455195390d03a893b87ef5b69eb86abfb9d4c912400643fa` |
| `src/skaldir/context.py` | `src/skaldir/context.py` | 9843 | `7ab7746d9899e0a0e4342b2d9094358d600115606f6b7c9651b6110ba6762f94` |
| `src/skaldir/context_manager.py` | `src/skaldir/context_manager.py` | 95803 | `90260ec566c32de3a4bb655516d9797d485334562bfc85dac5ff6752d483422c` |
| `src/skaldir/cu_host_session.py` | `src/skaldir/cu_host_session.py` | 70442 | `4f2a4fc6b86d5a2da2278d4c7d11065c25964635ef25ab5bb8ea1b8dc52d5985` |
| `src/skaldir/cu_platform.py` | `src/skaldir/cu_platform.py` | 42802 | `ca5951496347398246cfe90b0f9101bec2e4a764a06d39a3ec1343fc21e8b035` |
| `src/skaldir/cut.py` | `src/skaldir/cut.py` | 13753 | `8eba6fa99b2a8da1f0353b91be4ccd17c8d8b27462d5d6fe98a889cfc8f7e4a2` |
| `src/skaldir/demo.py` | `src/skaldir/demo.py` | 3320 | `7f81bd2c929071c44df2c73597323bf5325e3b6dac4f5d9fb1e800d14fdff19a` |
| `src/skaldir/deprecation.py` | `src/skaldir/deprecation.py` | 3427 | `ec8f886e6528058835e30307bdb521f4b1b48f16f7bc8b51167798ac9c57d17e` |
| `src/skaldir/desktop.py` | `src/skaldir/desktop.py` | 33616 | `2f29fb8cf8c323b846065576ad3348deda6b1854f3e51cce8cebdbf4581c4cbc` |
| `src/skaldir/discovery.py` | `src/skaldir/discovery.py` | 48611 | `e34f6483bc63477888949c52324eb9d8e03fdbb6db578b9ee8097d191706a6fb` |
| `src/skaldir/docker_ensure.py` | `src/skaldir/docker_ensure.py` | 47043 | `4ccaab4524d522014625e8b98044eae82c835a954a081b235945543dd133b6f6` |
| `src/skaldir/docker_ensure_cli.py` | `src/skaldir/docker_ensure_cli.py` | 16639 | `d388fc5a98c161808bf6c0337dd2a23ae0a394702305e72d7df905870a1c48c9` |
| `src/skaldir/docker_runtime.py` | `src/skaldir/docker_runtime.py` | 50181 | `34924b01a1ee4705de49fe7b18d2e0d1f02c0ce0a329815d878387e88d4a0763` |
| `src/skaldir/edit_cli.py` | `src/skaldir/edit_cli.py` | 41971 | `2f4aa683a6f7c8f8a21c47eb226c956aa42b3bea24d7f2c3b498452513e4caa4` |
| `src/skaldir/edit_tx.py` | `src/skaldir/edit_tx.py` | 77277 | `ceb78e1cd888830e0baefad360653b9e856c0db865e6073b85eb6da2a7ecf032` |
| `src/skaldir/embedding.py` | `src/skaldir/embedding.py` | 1830 | `561885d284d6accc533fdfe3beb192c1b8706f0a3b80107ada9701adf6c3b75b` |
| `src/skaldir/env.py` | `src/skaldir/env.py` | 10828 | `b3355e1a1b968d326d28c70e2149e93be2032aa0dc47e019e9d4f84df6f99fc5` |
| `src/skaldir/evalharness.py` | `src/skaldir/evalharness.py` | 13003 | `99b1b2b62e73efa7f7cfb1894cef9c69d1e53b0fe7dc6f43bfeeadf0db9905e7` |
| `src/skaldir/events.py` | `src/skaldir/events.py` | 19470 | `cc33d696a4956af74c0b46a40173326eaa04dd2d4e1ea1d45bda02b5ae1ea9c7` |
| `src/skaldir/execution.py` | `src/skaldir/execution.py` | 9563 | `8ee48c34c9369df7dd582e21998a3802dfe50886f07f92921f20fa2ddedd600d` |
| `src/skaldir/first_run.py` | `src/skaldir/first_run.py` | 9384 | `7ad44afda64f6cc02dbfd219cbabfe1824c09be045d5d509fac86c89e3b5cdca` |
| `src/skaldir/gates.py` | `src/skaldir/gates.py` | 16723 | `d051ac0e3682937392d22e7cc5e607240703ee72cd40760378aed9df68a03e21` |
| `src/skaldir/goal_run.py` | `src/skaldir/goal_run.py` | 96234 | `ecc25b5d772c237e3fafb6d3559d1e8538e4e4376af89eb64ca02b52c918c5eb` |
| `src/skaldir/goal_run_cli.py` | `src/skaldir/goal_run_cli.py` | 42470 | `b01ab61bba6f5e1a365659f679b56a90a5370d8f35adc893333af43d5b0edddb` |
| `src/skaldir/guard.py` | `src/skaldir/guard.py` | 19981 | `b9c3b1a057b4bf4581009d32a41db6956c41ec34fde361156b63f75e79f40a3e` |
| `src/skaldir/host_bridge.py` | `src/skaldir/host_bridge.py` | 45282 | `464ea453b3a6a1c715bad57cfe06e99ed9b937681c461ddda0bc4b00314be5b1` |
| `src/skaldir/host_cli.py` | `src/skaldir/host_cli.py` | 33930 | `fac9be9a260824268c67058d0970680ffdfd5fb3194fca011f3f1c36b0cce414` |
| `src/skaldir/image_inspect.py` | `src/skaldir/image_inspect.py` | 64924 | `b4723e71c6b166783cdf6b4c69d5b8b816886df8bb6395d9552df7af865f3573` |
| `src/skaldir/image_inspect_cli.py` | `src/skaldir/image_inspect_cli.py` | 21602 | `1624d0dfb05e9dbe899f1c7ac41a5f2f3ccd918216cde34a7d9401becc28bae3` |
| `src/skaldir/image_pin.py` | `src/skaldir/image_pin.py` | 91803 | `98e19ea7441f27f357e47795af87f5948b5ae6ca5242a3264a069e1abbfac884` |
| `src/skaldir/image_pin_cli.py` | `src/skaldir/image_pin_cli.py` | 29055 | `29768278722f97048431727f638b0315b8a3330f68192e0865689bd7e4dfe029` |
| `src/skaldir/incidents.py` | `src/skaldir/incidents.py` | 25987 | `8d1e36d4d9ae9348d91910765fba0a7ad466330356be727880b0f7d611ef182e` |
| `src/skaldir/inject.py` | `src/skaldir/inject.py` | 2143 | `1a0806bc4789839710ba684789f9e37d38274e6118f26fbfd1160f145bac57a8` |
| `src/skaldir/jev/__init__.py` | `src/skaldir/jev/__init__.py` | 3568 | `4d7134425ae8b83c1ac21df7986801bac72e3c24b51cb20e98f346459bf643a9` |
| `src/skaldir/jev/__main__.py` | `src/skaldir/jev/__main__.py` | 527 | `b60c548ccb0a65a82168dc18f9fbe564b41bde192afaa96ec49290a2ee27aba1` |
| `src/skaldir/jev/cli.py` | `src/skaldir/jev/cli.py` | 26186 | `733c32907ff78b2d04264a8612132fc91af29fd96e1f58f190af6ed31ceec912` |
| `src/skaldir/jev/contracts.py` | `src/skaldir/jev/contracts.py` | 33919 | `59b2fe2fc698bc90633026c82d82ee8b4c08f41d161ab0bc2faff5a9f191c075` |
| `src/skaldir/jev/decide.py` | `src/skaldir/jev/decide.py` | 67754 | `90596bcc7ca4f4689abc209d518159512c04ecf82df640ec87fdeb60f8783232` |
| `src/skaldir/jev/fixture.py` | `src/skaldir/jev/fixture.py` | 8669 | `099fb3212d45283ba4f2ab528e21dfb3e4c8556a60d3486c7d13b32702561e69` |
| `src/skaldir/jev/kinds.py` | `src/skaldir/jev/kinds.py` | 6470 | `cb33202244c5b8aa4bf1fd24eb03a8caf58cc7a1a48b228002b96e0b1eb7b49b` |
| `src/skaldir/jev/ledger.py` | `src/skaldir/jev/ledger.py` | 3949 | `d8b5513c2955eb85295bc119b6c0d6ad85d77ff6d9183f65ab78149c1a0ee584` |
| `src/skaldir/jev/packs.py` | `src/skaldir/jev/packs.py` | 29995 | `b21797d0e354eeaef8754bb958b64676ddf22703414728a0cfc50e93a73c78ca` |
| `src/skaldir/jev/rules.py` | `src/skaldir/jev/rules.py` | 41812 | `5c5a3ef67a7c4ccb3e2d0264ba689cb46e12992d27eb31c3d1f03576d58439b2` |
| `src/skaldir/jev/runtime.py` | `src/skaldir/jev/runtime.py` | 72460 | `89d9a31842dc920dd6dcf516749e8357e7e8476c6a3bfcc8402e942be1a65bd1` |
| `src/skaldir/jev/selftest.py` | `src/skaldir/jev/selftest.py` | 13629 | `5b3fab3f186e24294d1b4355dd42a7162ead88c78cb4535f749baad013311606` |
| `src/skaldir/jev/wiring.py` | `src/skaldir/jev/wiring.py` | 35395 | `0b6490dd81e261a45e56f84bc85935fe67a5b28b76b97c20a586534961f573dd` |
| `src/skaldir/jevcomp/README.md` | `src/skaldir/jevcomp/README.md` | 7446 | `f957ea329a78a70b365ff2f84209c28f46b7f78e85f904efb7437fdec06bfbdc` |
| `src/skaldir/jevcomp/UPSTREAM_LICENSE` | `src/skaldir/jevcomp/UPSTREAM_LICENSE` | 1696 | `11d828fd1b49120713d94832cadcbde7bf0313a1edca9a5d5766f06efadc4834` |
| `src/skaldir/jevcomp/__init__.py` | `src/skaldir/jevcomp/__init__.py` | 11626 | `d5fe638bda2287051169b1fca8d663daf2e013e066d1fbb1b5cd139cabc06129` |
| `src/skaldir/jevcomp/compact.py` | `src/skaldir/jevcomp/compact.py` | 20236 | `bf3dbe9bea45ef56772e5a08f5af2986bc993cc06aa7e30cf9123f166e25838d` |
| `src/skaldir/jevcomp/errors.py` | `src/skaldir/jevcomp/errors.py` | 1148 | `9dc83d6fe02702b99d8c06fc40a8ed13eb3ef91be766d99867c55d312627c89b` |
| `src/skaldir/jevcomp/fixture.py` | `src/skaldir/jevcomp/fixture.py` | 3660 | `b820342894d3549cf99a11fcdb6e35821d2f614f0f2da51c5c50d76859201e04` |
| `src/skaldir/jevcomp/request.py` | `src/skaldir/jevcomp/request.py` | 3462 | `fc1ab7b8eda504b1442e645d1309b0092408026490943775271cf6a274204aca` |
| `src/skaldir/jevcomp/state.py` | `src/skaldir/jevcomp/state.py` | 15367 | `41d0b24b195ecf3e1afbece9acc1deff0030b440d8460dd25b0444f90792c1ab` |
| `src/skaldir/jevcomp/types.py` | `src/skaldir/jevcomp/types.py` | 15125 | `09c4a4d31835dfbcc8fbf19ed9e1bdb801643f0e579f1be0f085a2562d6669c8` |
| `src/skaldir/knowledge.py` | `src/skaldir/knowledge.py` | 16398 | `8cffe0b7d0570f101a10be9d0f5ac64020fc23d6f64162055900555c2d709e0d` |
| `src/skaldir/knowledge_cli.py` | `src/skaldir/knowledge_cli.py` | 8165 | `09c8932f8000d97dfeb15e75e94c4fce14599c5581c2ba7ab3340d6fb8b7c50e` |
| `src/skaldir/ledger.py` | `src/skaldir/ledger.py` | 7685 | `6748e37bb3a68856033a66f8231d03c472568385fc3f9f0e8e214d59f29195be` |
| `src/skaldir/liveinsert.py` | `src/skaldir/liveinsert.py` | 18325 | `e59ad5a4eec0c6b734fbb5d93fdd0868e095f7f4102c3f310feebd41f5f1ac64` |
| `src/skaldir/llm.py` | `src/skaldir/llm.py` | 7126 | `dc82ef316016d9a14b59a95cf2e3f73a94371f87de45197db52bbdd4b0da1eb1` |
| `src/skaldir/llm_provider.py` | `src/skaldir/llm_provider.py` | 139463 | `d89acd96e0671ae9b83723babf1a9ea5b98bacf2e29339eaf423d6eff1d38d99` |
| `src/skaldir/logo.py` | `src/skaldir/logo.py` | 33112 | `5dd754e14321b90b01e3dc312e69590060f9b28c74444ee055aa685582ab19af` |
| `src/skaldir/mcp_link.py` | `src/skaldir/mcp_link.py` | 15380 | `c68f5fd6232f024acd3b089abf1a85f60758ae744e51705a5b05b5e861662574` |
| `src/skaldir/mcpserver.py` | `src/skaldir/mcpserver.py` | 35233 | `4d061b0cb00161aadf559ca5277be9b7d176f53cd57c10b0f9c1f9b927880bf0` |
| `src/skaldir/memory.py` | `src/skaldir/memory.py` | 30167 | `d548ab12add9fa88ce8245ad3ec8cf52006bac8dac2c319d55cd9f6047c3ce6b` |
| `src/skaldir/memory_store.py` | `src/skaldir/memory_store.py` | 99778 | `3d9f3bf076206436b83a200c59831daa043d7d768c5ecf10cbbd9262cf2f01fd` |
| `src/skaldir/memory_upgrade_cli.py` | `src/skaldir/memory_upgrade_cli.py` | 34979 | `32d4d67641e905ef81af735343242f27c25b6ecd5dd45d084c106883587c6722` |
| `src/skaldir/metrics.py` | `src/skaldir/metrics.py` | 12611 | `97898b75f7d65d21ff98cd1e749085e177b5e5564ef77f6cd38fcff642dcf6d9` |
| `src/skaldir/model_cli.py` | `src/skaldir/model_cli.py` | 36914 | `90863489e1ffd574c113252482f500853cbea9606c68ed99a4209293af9486f0` |
| `src/skaldir/model_discovery.py` | `src/skaldir/model_discovery.py` | 6896 | `1091a5d9b488b1b7b08d292d3d7de484a7c1c98a05284b791c044c48ace5b08e` |
| `src/skaldir/model_picker.py` | `src/skaldir/model_picker.py` | 117677 | `9d48e7ea08a278e27dafb9c4bae281576b1522a19958e3069ca502b03aa194c0` |
| `src/skaldir/modelroles.py` | `src/skaldir/modelroles.py` | 8983 | `0bc7cce19a53bc00b68b7caf4c044d27b0b1cd9ac20c54813c675374fd542089` |
| `src/skaldir/monitor.py` | `src/skaldir/monitor.py` | 28165 | `da57ee1592960bf180831b88a1a6bafabc91c9060ab65bacc6450745ca181141` |
| `src/skaldir/nightshift.py` | `src/skaldir/nightshift.py` | 14281 | `9b10412c341ad84b048644b68d91b9f14b7dd12d9aa672ba6d33856cf5753367` |
| `src/skaldir/notes.py` | `src/skaldir/notes.py` | 9188 | `84f90687b3945847cd9174e57e7866ff41f55f68a314059e8b4e871768f12f69` |
| `src/skaldir/pact.py` | `src/skaldir/pact.py` | 4800 | `da4916ffc301f92da8d65d89c678e508c0b044495e0fd755bcc5253102778ab3` |
| `src/skaldir/patterns.py` | `src/skaldir/patterns.py` | 28998 | `bceb7e007b7793cb1a38c32623f179c6942611c209274f3bd510fd147c55753d` |
| `src/skaldir/pause.py` | `src/skaldir/pause.py` | 28889 | `f17090e05c4dba35a886c4001dd9a07719d8c82b07b05da354d5be1d9af84050` |
| `src/skaldir/perf_lazy.py` | `src/skaldir/perf_lazy.py` | 7830 | `b4f14f6e24d410a40ece16e9ce8b6dd6c170f10629cc83ab34ccef73a05755e7` |
| `src/skaldir/perf_progress.py` | `src/skaldir/perf_progress.py` | 12533 | `085b02d71e398851fda8453d7fd9652c45f2db297087588ff340d0bdd18a04e8` |
| `src/skaldir/perf_runtime.py` | `src/skaldir/perf_runtime.py` | 7353 | `c3ae090c51e38bbd16a06cda436bebf52ad22f67ec0a0482cfaf6ff023e11b24` |
| `src/skaldir/permission_profile.py` | `src/skaldir/permission_profile.py` | 92460 | `0e2e975712b63837e7b65296504aa18e371354559a5f7d6bd75f7aca2ad5ac63` |
| `src/skaldir/picker.py` | `src/skaldir/picker.py` | 33557 | `d7fac8646bb184389b1a48483b8ffb4137eee949ea22c31ae68260c4eb788aec` |
| `src/skaldir/progress.py` | `src/skaldir/progress.py` | 28410 | `6bc1369f4cfc72e230202825fd26e8fa1428c621f3ff579b66b163009fa1c9a0` |
| `src/skaldir/progress_guard.py` | `src/skaldir/progress_guard.py` | 32730 | `e3e47193120ddea22dd0a25872c6c83888980cc5e175dce2aeb9bebcdb165c85` |
| `src/skaldir/project_context.py` | `src/skaldir/project_context.py` | 52884 | `4616e3975181ef61066a611bcb5f1ca57b180c238f0771ad923a075ef1385129` |
| `src/skaldir/project_context_cli.py` | `src/skaldir/project_context_cli.py` | 23440 | `1bceec4d21d7a560cfbbf86df56daf22e6762f2231fae730863c7a7c465a1e8a` |
| `src/skaldir/prompt_builder.py` | `src/skaldir/prompt_builder.py` | 47444 | `0c045b851398db8ff394e9c157473e11591c512b12f75638eab8ce13851376b4` |
| `src/skaldir/prompt_builder_cli.py` | `src/skaldir/prompt_builder_cli.py` | 40876 | `8bc478cf3ad56d918112b3e01127df2b6baad4d3c1c5c79b8f2e9e3ff3e212c2` |
| `src/skaldir/prompt_cache.py` | `src/skaldir/prompt_cache.py` | 52376 | `20e58359c70a7e831cb10c5ddba6f4588ef57372a500650f5452678d17222871` |
| `src/skaldir/prompt_cache_cli.py` | `src/skaldir/prompt_cache_cli.py` | 22950 | `a724421cb9c01832d56dd7ce57e191f811b43914ff8a6d41b728bf44b3e66612` |
| `src/skaldir/promptrun.py` | `src/skaldir/promptrun.py` | 3687 | `2e3901e9756340b31a615a84e3a024130d29bc7514e895064854b65070dab4f4` |
| `src/skaldir/prompts.py` | `src/skaldir/prompts.py` | 2985 | `a2bee3828882f46e02a0ffba4ab5b355281d91f537aa50a9d5562ba53a04af7c` |
| `src/skaldir/provider_catalog.py` | `src/skaldir/provider_catalog.py` | 51268 | `fa38f7dc2db00e9bc5f1139f0c2152ffaeab79c7f726e0bd3e5878720049f4d9` |
| `src/skaldir/provider_cli.py` | `src/skaldir/provider_cli.py` | 52909 | `02289cea5f2ddab7729b4d772467715c097673943454b335329b5f5eb2302c42` |
| `src/skaldir/provider_config.py` | `src/skaldir/provider_config.py` | 51031 | `858a2357ce70c2c47e933c84b306ba8cacf7233b3089a0fd48932cf3fe5cff96` |
| `src/skaldir/provider_model_data.py` | `src/skaldir/provider_model_data.py` | 79891 | `ad962c8b2d451e18aea14f1672ac8cd6a7d5c10145fe2d3a2ba16d915b643e2d` |
| `src/skaldir/provider_registry_data.py` | `src/skaldir/provider_registry_data.py` | 123567 | `3dc4f74e88cd24c4a4ebac17a183daa1dfe0439fb7affbe3208035c590b5404c` |
| `src/skaldir/registry.py` | `src/skaldir/registry.py` | 20591 | `92ad87f48168311210f40b9ee88bd09056638c59137d93be6ceb74348a767e66` |
| `src/skaldir/render.py` | `src/skaldir/render.py` | 27717 | `5ceb15fefa95748663e87f86343711636ebfb75067fce6054c8dbc5c546f07c8` |
| `src/skaldir/repl.py` | `src/skaldir/repl.py` | 198860 | `b9c0c8809a286e3d774b7731bb4af5507159881bafd31119f5f1e94abf1787f6` |
| `src/skaldir/research.py` | `src/skaldir/research.py` | 27953 | `6da997e3cc1b8b545b51766243b6c82c6e5622d702bf5bacdb1844ec57185cad` |
| `src/skaldir/retrieval.py` | `src/skaldir/retrieval.py` | 4686 | `ec9fcaa3443317e5e37f8bd55726c04718363c67163a3afc60ef56df5e7915f2` |
| `src/skaldir/run_supervisor.py` | `src/skaldir/run_supervisor.py` | 75052 | `33822023669d79e2da200340d89060eded2f52a86e9bb9639b0604f78e2530f2` |
| `src/skaldir/runs_cli.py` | `src/skaldir/runs_cli.py` | 45291 | `8d3787870bca3929611f6af2e2b6ed79f601cef1dbe4478e4b482a00f7166161` |
| `src/skaldir/runs_store.py` | `src/skaldir/runs_store.py` | 141362 | `09dbc5c6abf962407a9a5531e4de4a98cb0fdc7ade854b49e1d7e6c4ba9a62b3` |
| `src/skaldir/runtime_delegation.py` | `src/skaldir/runtime_delegation.py` | 65172 | `352c5c1db8a1407682f65971ed7d0287b644983e8c8dac1297e17b4a0fc2c663` |
| `src/skaldir/secretscan.py` | `src/skaldir/secretscan.py` | 3076 | `0c0cab41624986706c854e334e80f2ef33f4fd20fe7b014e357cacd6fbd070ac` |
| `src/skaldir/security_audit.py` | `src/skaldir/security_audit.py` | 80392 | `cd9d01014bf5d7d42330f1e968148448dcec2459e6816efde676bdd2e50dabef` |
| `src/skaldir/security_audit_cli.py` | `src/skaldir/security_audit_cli.py` | 16338 | `da294f093f4dd6327806f60e9f689b96c6a4efcdc9c531e06a1443e9ed2aa1a9` |
| `src/skaldir/security_linux.py` | `src/skaldir/security_linux.py` | 97951 | `30778c2c2fdd1f9702d98d307fd07818d951ef43b8a23a80f5900e9e276d2758` |
| `src/skaldir/session_cli.py` | `src/skaldir/session_cli.py` | 74436 | `2ca3a26994c4750a883805177dbeed0d80cb8c51799ff914ab93b8e40dc93aab` |
| `src/skaldir/session_store.py` | `src/skaldir/session_store.py` | 93662 | `c71f8bb3a74ffabea2d05099653eccb2f1284f53a797f16b5545b19ecc881983` |
| `src/skaldir/settings_service.py` | `src/skaldir/settings_service.py` | 11805 | `4097923316b1d9a4afac4406644e2d87937d0927d4b53f10d35222602d1cea06` |
| `src/skaldir/setup_cli.py` | `src/skaldir/setup_cli.py` | 53769 | `53fdc3aa3ad59fb7a56ffaf2ce6cd5ef40ee4fb7ff7ae9c1623e05c644ed656e` |
| `src/skaldir/setup_flow.py` | `src/skaldir/setup_flow.py` | 119335 | `2c47701e87a3ae7fbb3ac6378f9db1eeaab44ad9956f557e69b5b42c04d3c5bb` |
| `src/skaldir/skill_builtin.py` | `src/skaldir/skill_builtin.py` | 2115 | `57b6d1e2aa1b80b12b54ae57b9ac3706fe03d93f8a2a2323a7c21dea33ee6ceb` |
| `src/skaldir/skill_install.py` | `src/skaldir/skill_install.py` | 7465 | `c02d5fe252813b0422ef9840e676438950966088933c4f6f7e31964f140e5138` |
| `src/skaldir/skills.py` | `src/skaldir/skills.py` | 23678 | `cba51ce5b197ffbcbd6e986dc7e848843c6e8134118dc19ab05e9ef67f2fcb8b` |
| `src/skaldir/skills_builtin/computer-use.md` | `src/skaldir/skills_builtin/computer-use.md` | 7377 | `d514d9de5f1e014980c001ec053a37a37a13873403f1f7138d23a914032f2985` |
| `src/skaldir/skills_builtin/docker-triage.md` | `src/skaldir/skills_builtin/docker-triage.md` | 1138 | `ea4b9c69d0e6f8412e27cd829111db0dc6585c6fd916ab45c332e33f83061d30` |
| `src/skaldir/skills_builtin/explain-code.md` | `src/skaldir/skills_builtin/explain-code.md` | 871 | `eaaec3b8ca1a97efc28ab5d57078febe1b1e392f0f83bf687a6474a93ab3df4b` |
| `src/skaldir/skills_builtin/repo-audit.md` | `src/skaldir/skills_builtin/repo-audit.md` | 1156 | `b066e61c9a03cd00ab1fa661ac92956cb6681cddd19c5ec0cae0c1bd67c29291` |
| `src/skaldir/skills_builtin/review-pr.md` | `src/skaldir/skills_builtin/review-pr.md` | 991 | `ab3ceb505deee5f5661ce05ee3f0f8b863fc365ae097afc7bc0afcedac262989` |
| `src/skaldir/skills_builtin/write-test.md` | `src/skaldir/skills_builtin/write-test.md` | 902 | `3ac0538853f472338b8ec475e4528496cb4f7baffac41bd9f57286a453bd0535` |
| `src/skaldir/slash.py` | `src/skaldir/slash.py` | 110697 | `bcd97724175a216efca8700b2f0e16d77ca5608ecd00343c29d2b05d18d98d1d` |
| `src/skaldir/spotlight.py` | `src/skaldir/spotlight.py` | 1395 | `1622693f2d5785310946ec3aceab13a2f510ab67fdc0fa2733e8183c2ac06df1` |
| `src/skaldir/step_runners.py` | `src/skaldir/step_runners.py` | 78008 | `9e476fa739be8fb3403392a044b3e1a690f6fb15a9b7a4eef1d8514e93c0f820` |
| `src/skaldir/stream_render.py` | `src/skaldir/stream_render.py` | 53564 | `16c27c686a91b09c1ebc4461194041ad2877f7b49773bea3d035b0887755a6b8` |
| `src/skaldir/streaming.py` | `src/skaldir/streaming.py` | 59465 | `31b284e7c9527632ed593e20f96e118d74a48e8a9bbf5db53f82e8758fbad0ab` |
| `src/skaldir/structured.py` | `src/skaldir/structured.py` | 55690 | `e608cc6dfd7f86db9d391a2add5ae7ca8c539e2a5b98e93d057524b302c17c14` |
| `src/skaldir/subagent_briefs.py` | `src/skaldir/subagent_briefs.py` | 25595 | `c58fb483e1833127b3787cac72f786f33fabf88f896ac808ed01cdcae3af7e41` |
| `src/skaldir/subagent_roles.py` | `src/skaldir/subagent_roles.py` | 37645 | `9de9ed5e619ef43eec1bbc78a41debb90750b4d01fa0f9b1745185ba55e38225` |
| `src/skaldir/subagents.py` | `src/skaldir/subagents.py` | 127885 | `387e11b56df6b22ce411edea2579d1844dbce9bd8ac063959c5cd3c753f98082` |
| `src/skaldir/subagents_cli.py` | `src/skaldir/subagents_cli.py` | 63962 | `491f2bb11da38b1de3db63dcfff2c3cdfb012d95d1df6fe023f0d11e5ba29622` |
| `src/skaldir/synthesis.py` | `src/skaldir/synthesis.py` | 3282 | `ad1b53abf77357b6718a04da102d83981d5c7df09631dd272016fecd4a2b32df` |
| `src/skaldir/system_one.py` | `src/skaldir/system_one.py` | 28267 | `e1ac2f989119f89f9bc4431a6164438e6c2ea7051d2d5408a3cdf576ac8b86fa` |
| `src/skaldir/system_one_adapters.py` | `src/skaldir/system_one_adapters.py` | 39812 | `43e7f7de1ad09cb8c9d03e2c59116d6f0fea6340813c117f33238a2f2a218dd8` |
| `src/skaldir/system_one_cache.py` | `src/skaldir/system_one_cache.py` | 13456 | `a81d7f28d56260c9345b5a0f76561fd9bfa312a2dc11ea53444ace62b6550d6e` |
| `src/skaldir/system_one_calibration.py` | `src/skaldir/system_one_calibration.py` | 35801 | `25d3eee23ad80df633e2046f2642a9e6ac2ed9410684225d6a378a131058ff26` |
| `src/skaldir/system_one_canary.py` | `src/skaldir/system_one_canary.py` | 15830 | `11a2c23bb6b8053cbaeb3183fb2948c20d266b87963688474832cee5e2a54bfd` |
| `src/skaldir/system_one_flags.py` | `src/skaldir/system_one_flags.py` | 8539 | `61c48182afbb572df3c9740ecff79eee2377d1dc85f422e0dbca5b2a30d48b00` |
| `src/skaldir/system_one_guistate.py` | `src/skaldir/system_one_guistate.py` | 27801 | `461b0bb39f6a77b8d1290bd367524464266b603989861094ddfd31744b065809` |
| `src/skaldir/system_one_metrics.py` | `src/skaldir/system_one_metrics.py` | 9141 | `fbb7046f0865f95bb6ae45386fb90a8badd3c434ce32e3ed64a80463e8dbf320` |
| `src/skaldir/system_one_policy.py` | `src/skaldir/system_one_policy.py` | 48537 | `26fda3ffda338b39ac55a0b0f109b659361ca4ef38976737dea9085ee91b64af` |
| `src/skaldir/system_one_questions.py` | `src/skaldir/system_one_questions.py` | 42165 | `ad2af6cda45375703dcbeac6dfb503464bbc1af729cec1fe56543bb89bca9486` |
| `src/skaldir/system_one_sanitizer.py` | `src/skaldir/system_one_sanitizer.py` | 9717 | `42d1f0e1a04dd84e04df6ceeb132ae972c409dd9c29e7242e212244fa4b60704` |
| `src/skaldir/system_one_service.py` | `src/skaldir/system_one_service.py` | 17453 | `ca7a90eb6ac65b5a4a6d819bc2b50e9495277f7030198f7c6ccf5b70b60b3c6b` |
| `src/skaldir/system_one_shadow.py` | `src/skaldir/system_one_shadow.py` | 38651 | `28724cd6ee55943675ee27d8c4d2f10d8064e45aec5acfaca289e69f0a51993c` |
| `src/skaldir/system_one_subtools.py` | `src/skaldir/system_one_subtools.py` | 35896 | `13670d91b5097a8baf647c90c9bb1dcfdd72e5f0cbf0ac4a514bdcc84e12779e` |
| `src/skaldir/system_one_treasury.py` | `src/skaldir/system_one_treasury.py` | 14653 | `ae028d99e73d17ee4ba4fed67e06f61480f9674d9541d58eed3c042e3cb56a8d` |
| `src/skaldir/system_prompt.py` | `src/skaldir/system_prompt.py` | 10520 | `b4d1673441226e8652be29895518e5a623efd593a31565c996928d5953f07ba4` |
| `src/skaldir/telemetry.py` | `src/skaldir/telemetry.py` | 79281 | `1d333dafbab7bae40e0279865fa7e95a201f6b8054e226fb27795b8ef8512059` |
| `src/skaldir/telemetry_cli.py` | `src/skaldir/telemetry_cli.py` | 27033 | `b15bacf3a26e3ef79fc3f78facd9168d3981f1e681e0e0fadaf584d015191eef` |
| `src/skaldir/theme.py` | `src/skaldir/theme.py` | 26987 | `91c22cc36656107223fe58f62552e4127fd0de7f8f85eabe7e52d0510c29bb3d` |
| `src/skaldir/tool_archive.py` | `src/skaldir/tool_archive.py` | 13482 | `a301a4e3cc12032687a7b0209b986974980b6485b28ac798934531a277400752` |
| `src/skaldir/toolbuilder.py` | `src/skaldir/toolbuilder.py` | 207766 | `fe70e6171e98817f5061d6d3f8a0d4318de3457b3dd44fb00d5908be9fd96640` |
| `src/skaldir/toolbuilder_cli.py` | `src/skaldir/toolbuilder_cli.py` | 36007 | `7ac7321d897b18e2d70e4534f314a1ccdd99e900835735b27249be90eb4c635f` |
| `src/skaldir/toolcall.py` | `src/skaldir/toolcall.py` | 30642 | `3faa2c508eed2c09fa40da03b0e3b6742054decde2da31b2cf90f85de5af4add` |
| `src/skaldir/toolhub.py` | `src/skaldir/toolhub.py` | 11299 | `53ab4b574ee6319921031064d8aa04ad927ff226fb3b6c37803c4e9ab7ce34d2` |
| `src/skaldir/tools.py` | `src/skaldir/tools.py` | 26568 | `eab1e580e7d559293d77e31ed29efe19031ef951479fa0af34f090697fbf4495` |
| `src/skaldir/tools_call_cli.py` | `src/skaldir/tools_call_cli.py` | 24532 | `383a44372b556993913f7cee53b895c936d408d2dad7876c3b480da4261ba25f` |
| `src/skaldir/tools_core/__init__.py` | `src/skaldir/tools_core/__init__.py` | 3949 | `ca769aae71a42ab89bc1c5b6bae6f6389ca5567027ede341c7a62db8afbe2456` |
| `src/skaldir/tools_core/__main__.py` | `src/skaldir/tools_core/__main__.py` | 455 | `1a911f7f50c934725a572d5f0b645a4afbdb5278efde1084b963a58cb948f21c` |
| `src/skaldir/tools_core/bridge.py` | `src/skaldir/tools_core/bridge.py` | 7545 | `a09cd7dc8c1f62e2d28ef171f5e6f1f45230a5f46de44753fe7a7774b98a56b0` |
| `src/skaldir/tools_core/cli.py` | `src/skaldir/tools_core/cli.py` | 31065 | `20edfaec7c2c4143a54cfa289ec51dfc7413b1bd5d516542699f201cc8d02ed9` |
| `src/skaldir/tools_core/contract.py` | `src/skaldir/tools_core/contract.py` | 18024 | `cf32318410fdb507ed319b6f6273291e281a3ccb379774691cd02bfc7b4af064` |
| `src/skaldir/tools_core/docker.py` | `src/skaldir/tools_core/docker.py` | 13742 | `fc82b5b5e4f1d4cdb727f7b98ac1821d5dd16f9194c9e0172f0037d0d60a374c` |
| `src/skaldir/tools_core/files.py` | `src/skaldir/tools_core/files.py` | 24959 | `fe2d65facbf76d2283d5204a8bd650bee4b459690aab7523de6c70f40ad9e1ae` |
| `src/skaldir/tools_core/patch_tool.py` | `src/skaldir/tools_core/patch_tool.py` | 17862 | `adeae2543bebf587b5b2b35863998a5fe052d09be12f9b8af3d30a029e9e4b6a` |
| `src/skaldir/tools_core/registry.py` | `src/skaldir/tools_core/registry.py` | 30954 | `2412fa3672d3552ce2c49193e147ab4f99618c082359f909ba0a9d541c21fc35` |
| `src/skaldir/tools_core/sandbox.py` | `src/skaldir/tools_core/sandbox.py` | 17060 | `28b49dce19cd22f20d019af4b612c89da674923c6bd41bf8b31a5406c02e2aa1` |
| `src/skaldir/tools_core/search.py` | `src/skaldir/tools_core/search.py` | 12519 | `d6ad1dc778fde04301e62cfd6f8b24737011f6eb0e065229b9af28ad21f2e022` |
| `src/skaldir/tools_core/terminal.py` | `src/skaldir/tools_core/terminal.py` | 15518 | `03b2120b4f80f90371049f18d331274b871792b63234d615ee9fb85e3de2d081` |
| `src/skaldir/tools_core/web.py` | `src/skaldir/tools_core/web.py` | 25085 | `48594db16d784c55242578580e0ddff733802282af75838dacf80a0578698db7` |
| `src/skaldir/toolscan.py` | `src/skaldir/toolscan.py` | 3189 | `34d491803a7bc0a3d7190ff06d56875bde5f9eeea7cf14c6bbfbbb10d05a86ad` |
| `src/skaldir/toolusage.py` | `src/skaldir/toolusage.py` | 4294 | `524adb328f00cb60e6bcbe9f65db3e8432e04bb03bd05cfff1bb70b7d2948710` |
| `src/skaldir/treasury.py` | `src/skaldir/treasury.py` | 47476 | `20819feea0f0cd6ff2a0b646b6c241e794c46f9893bd92575254b0628415b37a` |
| `src/skaldir/untrusted.py` | `src/skaldir/untrusted.py` | 21967 | `ce4f2b5138f41a98909948ebe16d7ab51471def78ac2bdd7ef3db7b71f5457fa` |
| `src/skaldir/verify_cli.py` | `src/skaldir/verify_cli.py` | 46212 | `b06acf0ad6a91a23cd9d1d99b414df4b7127ddd26b6ec9b3c37f5deeda00ee06` |
| `src/skaldir/verify_flows.py` | `src/skaldir/verify_flows.py` | 87994 | `b9e1478ad38d30b5133985196fbbe461b869b9e56554eaa41b8f81fdeef3653d` |
| `src/skaldir/verify_service.py` | `src/skaldir/verify_service.py` | 72311 | `9d8f917de8309bd07619b1297dd7bb2467476f0df2244c3a56f4846893b45f3a` |
| `src/skaldir/vision_cli.py` | `src/skaldir/vision_cli.py` | 44592 | `c41a29226597a578eb6396bce18c7d538b473e2ba64743e18309dd6544f31175` |
| `src/skaldir/vision_service.py` | `src/skaldir/vision_service.py` | 37684 | `d3c3988c54d30e7c94549918d25b1d74ce9425957c34bdc0535065f0eaee73de` |
| `src/skaldir/voice.py` | `src/skaldir/voice.py` | 26282 | `3835b380b36a859adc4243e7e799cb23ab615215e2a928d068715eb3c253df95` |
| `src/skaldir/voice_cli.py` | `src/skaldir/voice_cli.py` | 34105 | `067cd676571fb2d502fd999b4abdef3cb57476ca822fed24a66e909d60b76c8f` |
| `src/skaldir/voice_cockpit.py` | `src/skaldir/voice_cockpit.py` | 3437 | `b0154148de985f58ac13a417a62b98f30e1ae636961f6d3e6b68683fde31301b` |
| `src/skaldir/voice_service.py` | `src/skaldir/voice_service.py` | 77901 | `06a9fcbd8998ab5b1c0e37bf91586e5815eddcfe3655e4e4c26ba8c712104143` |
| `src/skaldir/warm_cli.py` | `src/skaldir/warm_cli.py` | 28928 | `99c997bcd219f3c84480b89392fb235e52aa2e302136d9e675715cf749b4207d` |
| `src/skaldir/warm_session.py` | `src/skaldir/warm_session.py` | 71633 | `0829575d564b91877933d969199ce8196b1744bf57c7e955dd3a3b98cc066d84` |
| `src/skaldir/workflows.py` | `src/skaldir/workflows.py` | 41878 | `48c31057a76ab54a78befc76708d74417c3f9aec95d0f4a4fc581eac37fdeb59` |
| `tests/conftest.py` | `tests/conftest.py` | 2853 | `78a6ecaa1916e635d5b646acecc361e8d2bc7d403648c6d95262a653faaa49cc` |
| `tests/fixtures/discovery_cases.json` | `tests/fixtures/discovery_cases.json` | 10461 | `6084cc5ed472d470c00f2d4f27b1c459482b9e9287f85ea75a2418a2866a6668` |
| `tests/fixtures/http_service/Dockerfile` | `tests/fixtures/http_service/Dockerfile` | 1022 | `8adce14f99a8ba45a48a4f435bf6dcc047fe165000f7b774430f5e929bcba094` |
| `tests/fixtures/http_service/www/cgi-bin/echo` | `tests/fixtures/http_service/www/cgi-bin/echo` | 236 | `7de16a0bb5c88f11c8ff079670a93d91feaf3347818012e1dcb1c3633e758d2e` |
| `tests/fixtures/http_service/www/cgi-bin/sha256` | `tests/fixtures/http_service/www/cgi-bin/sha256` | 453 | `076b88aa4a11917344787e3f824ebd0083b6618cc34c86456a5600aded99eba5` |
| `tests/fixtures/http_service/www/openapi.json` | `tests/fixtures/http_service/www/openapi.json` | 1961 | `38c2badcee0813f43b204b3c01c3db3f7a607499efd761c8c4f83e057bc7110c` |
| `tests/fixtures/long_project/README.md` | `tests/fixtures/long_project/README.md` | 880 | `622d28b6cb2bd865d61e5f7bc81c43b060d5915c1cf1f1d90d3614bbfdaecc41` |
| `tests/fixtures/long_project/achievable/.godsv/checks.json` | `tests/fixtures/long_project/achievable/.godsv/checks.json` | 171 | `632c1f2367965a7a66675e13f43ec6381412f1b1ecf1eb978165ff2952e54e8f` |
| `tests/fixtures/long_project/achievable/.godsv/verify.json` | `tests/fixtures/long_project/achievable/.godsv/verify.json` | 600 | `3fd39a902fba2413305ef57990a22e49015c5fe5c0c326f23574a08f5ed63996` |
| `tests/fixtures/long_project/base/.gitignore` | `tests/fixtures/long_project/base/.gitignore` | 54 | `60b8127bf5776cce98eb74a2f432fd99be086c2c63e55a2a572d3ce786a593d7` |
| `tests/fixtures/long_project/base/README.md` | `tests/fixtures/long_project/base/README.md` | 602 | `ba8d49425b673d785824190fa224e60304ca471a730848864d30b01e80849fe9` |
| `tests/fixtures/long_project/base/cli.py` | `tests/fixtures/long_project/base/cli.py` | 420 | `6eef053e480efba7501cc1c897f091863d6f61737e25f6af095295161bd99c00` |
| `tests/fixtures/long_project/base/pyproject.toml` | `tests/fixtures/long_project/base/pyproject.toml` | 210 | `986037951d429c95c6060f8d188b44e8f079aa58a58f91d9a317779bdb1c19b7` |
| `tests/fixtures/long_project/base/tests/test_slug.py` | `tests/fixtures/long_project/base/tests/test_slug.py` | 486 | `0772e019e9fa814b0b97629d5c218a73c42bb89980cfe9d5bd38d3e56697ac87` |
| `tests/fixtures/long_project/base/textkit/__init__.py` | `tests/fixtures/long_project/base/textkit/__init__.py` | 89 | `37a6d8b5fe2e3f93d73938e233da1032f694bacad767b678378c452d51ecb4bd` |
| `tests/fixtures/long_project/base/textkit/slug.py` | `tests/fixtures/long_project/base/textkit/slug.py` | 548 | `3bd6c7b1a4a748714ca50e06cc3424fbb4fb3c4b3ef3b81969387f37385b4345` |
| `tests/fixtures/long_project/base/tools/check_build.py` | `tests/fixtures/long_project/base/tools/check_build.py` | 2247 | `f4be10f432b07a674bd27fb21a68aacfc1dc83badf540ccbc067a8517931a346` |
| `tests/fixtures/long_project/base/tools/check_cli.py` | `tests/fixtures/long_project/base/tools/check_cli.py` | 1279 | `2b499bedacc7c86fc75efaac18362edffc75c0206582e54eec47dd6d4dfc418e` |
| `tests/fixtures/long_project/conftest.py` | `tests/fixtures/long_project/conftest.py` | 623 | `c44f71db61507abdcaf3cf52d2abec292bb4966abcb8e62372836590e85ef9c2` |
| `tests/fixtures/long_project/impossible/.godsv/checks.json` | `tests/fixtures/long_project/impossible/.godsv/checks.json` | 172 | `9b1be590e05b2f495ead3e1380487e59756c2fe1a2d6ea527789f2b577318e57` |
| `tests/fixtures/long_project/impossible/.godsv/verify.json` | `tests/fixtures/long_project/impossible/.godsv/verify.json` | 603 | `16acac8d943e8bc81cfce0ca63f88b50d6eeff2e6cfe179210b784f77538c4c8` |
| `tests/fixtures/long_project/impossible/tools/check_external.py` | `tests/fixtures/long_project/impossible/tools/check_external.py` | 1298 | `afca13b1f474022646189be56b404cf0acdb9a1e70850c144a7fd4f345206d54` |
| `tests/fixtures/mcp_fastmcp_server.py` | `tests/fixtures/mcp_fastmcp_server.py` | 1299 | `60cac6d8e493485f2cf4b0c527acc0bc547ded1e27b73838d6486a504bcafe6f` |
| `tests/fixtures/mcp_test_server.py` | `tests/fixtures/mcp_test_server.py` | 19099 | `f0cdf57bc1091ab0ef4d2e3a8cff369856951ebd38e5db73e94f04a9b649d303` |
| `tests/fixtures/permission_policy_cases.json` | `tests/fixtures/permission_policy_cases.json` | 10880 | `7d04f7c6c2c46b62b4f5e19c806a1ad00c4b75f98b9ea05e42947245c82dad3c` |
| `tests/fixtures/verify_flows/site/article.html` | `tests/fixtures/verify_flows/site/article.html` | 822 | `f9f98be6190e24d14077b977e2521f4e173d39e590ed191780a28f1de8121f5e` |
| `tests/fixtures/verify_flows/site/index.html` | `tests/fixtures/verify_flows/site/index.html` | 1253 | `9fdd3009121443003f3cc630468cdb7877418f446c04c9e27e21a637e63cd4bc` |
| `tests/fixtures/verify_flows/site/search.html` | `tests/fixtures/verify_flows/site/search.html` | 1426 | `edf2af4f8fb1a91fa003c0dcbdfd89c773bd4053d91feb448c75df3590e9bae7` |
| `tests/test_act_app_state.py` | `tests/test_act_app_state.py` | 7063 | `71c0b503a3697d6f24300185d74382ec9f5be06ec763fa41c3e1494aebc2cc87` |
| `tests/test_act_cli.py` | `tests/test_act_cli.py` | 19654 | `1b9dc743cd573d8ca86e9072749b15c2e1b87fcc1e0c24f5891d47864e41157c` |
| `tests/test_act_learn.py` | `tests/test_act_learn.py` | 5500 | `f5089b18d1a1555939fd4b0c659f3ceeaa6e681099a59baaaf010decf802b980` |
| `tests/test_act_run_subtool_absent.py` | `tests/test_act_run_subtool_absent.py` | 13429 | `7ee6a45cd1cd60d2b2db34548c7984c4c541338cfc9db9b422ad97566846dd23` |
| `tests/test_act_service.py` | `tests/test_act_service.py` | 28055 | `bfb9273646fe086e9508f8938a790ba63fe0d3254d1f51f77491f7ea416e874d` |
| `tests/test_act_service_container.py` | `tests/test_act_service_container.py` | 11363 | `3e97573313fd974abb37f561d1933c7bd1eefc599ad9c82f8df5b1d0e706336e` |
| `tests/test_action_engine.py` | `tests/test_action_engine.py` | 15581 | `80785b4123acccfe2c7c61c8c6eecf6fe59afa9c5484999e9803a9687ae8ab89` |
| `tests/test_adapter_http.py` | `tests/test_adapter_http.py` | 44221 | `7a7732d7fabb2f9ffcdd227db8a4b580186e8538ed3e617847b5371fd58006e6` |
| `tests/test_adapter_mcp.py` | `tests/test_adapter_mcp.py` | 42775 | `2d51081a534c067e5b10c0f284659622989111ed48d0827be9b932be4148514e` |
| `tests/test_adapter_mcp_cli.py` | `tests/test_adapter_mcp_cli.py` | 17454 | `2c555b437c830eea074ea2cf5676899424bedcf4c940d208957de9e48c45f822` |
| `tests/test_adversarial.py` | `tests/test_adversarial.py` | 35559 | `a7a5d95ab4194d780d632dc8717f81e17f24d607e9f18836e22db9e23a34a4ea` |
| `tests/test_adversarial_browser.py` | `tests/test_adversarial_browser.py` | 19526 | `2c27f262904d215daddc7eb1511fb88c010209bcf429880e55363326629b792a` |
| `tests/test_adversarial_cli.py` | `tests/test_adversarial_cli.py` | 6894 | `15124552f9291ab5a9160afaaacc6a6cedcc322308e2c7f45c8744288f86cfd7` |
| `tests/test_agent_loop.py` | `tests/test_agent_loop.py` | 47742 | `93d1b43e507f4fbb7cc1a3ba06b2d7e43cd66ba3dfd958ee23adc5de163bf8b6` |
| `tests/test_agent_loop_cli.py` | `tests/test_agent_loop_cli.py` | 15764 | `9eaa137a849549db50049d8fffca867bb225dc4d5b981a1b821abc14c09ae141` |
| `tests/test_agent_loop_trust.py` | `tests/test_agent_loop_trust.py` | 5430 | `cd1422b922b28e014452c957a776a0c48846223c9c71ef099223454817e2bbb4` |
| `tests/test_agent_toolset.py` | `tests/test_agent_toolset.py` | 10971 | `ea94b3a338fbc8346f964ca8c969f9dab02e488f0e4d32f8cee76ca46577fe3b` |
| `tests/test_api_latency_stream.py` | `tests/test_api_latency_stream.py` | 43072 | `091601f8e15e1e4caccec00c0c8a5f0960e97896795e79feb067cadaec922f51` |
| `tests/test_api_server.py` | `tests/test_api_server.py` | 42102 | `feacd3474d893737c65cac5c0926b12827e233584748ed5cff00871929b4d765` |
| `tests/test_api_server_http.py` | `tests/test_api_server_http.py` | 24634 | `e952ac102e3e0ee2b96aa2a29c837093bca820a25e40d802021b792b9f8665f4` |
| `tests/test_appbuild.py` | `tests/test_appbuild.py` | 11933 | `c866ccaccb67cbd937bf97b395aa28540c810149fdb36033daf04a42812b1021` |
| `tests/test_approval.py` | `tests/test_approval.py` | 12240 | `7161f355d49da02afb8a26830f414d640a681872fccdba498a664f29968a05e4` |
| `tests/test_approval_cli.py` | `tests/test_approval_cli.py` | 18125 | `fabb686c48d2ffaf4ea6a738f7e7121948b28bd77075f5359102eca08f54ecf2` |
| `tests/test_approval_enforcement.py` | `tests/test_approval_enforcement.py` | 5667 | `50ffaa9c1021b95e082f43b36f2fe32556c385ba14a4b4e2b9f243a59e9b7831` |
| `tests/test_approval_evidence.py` | `tests/test_approval_evidence.py` | 6524 | `7bd0770004cbc9e2a6927f82b0f47d92a3b8bad27a659fc5f03e5133da87d02e` |
| `tests/test_approval_grants.py` | `tests/test_approval_grants.py` | 8384 | `9dd0fbe17b9db27dd4857112f40ccd3428f77d43dd6473163188ed65c7b24b1c` |
| `tests/test_approval_headless.py` | `tests/test_approval_headless.py` | 6316 | `ca15c8c9f47c150fd7d1f55a837f4956ec9cb8fd5f256388e9058e8acd6110a1` |
| `tests/test_art.py` | `tests/test_art.py` | 22459 | `6b505bf225947faeac27a1f108a23cd0514b6bd7444baefb245a4c5b3207e542` |
| `tests/test_art_cli.py` | `tests/test_art_cli.py` | 5365 | `9a0d1a3d928f36d170400b12bfae751786bebb8035c4f03308bbfa3dd65a3e63` |
| `tests/test_art_transcript.py` | `tests/test_art_transcript.py` | 11294 | `2ceb9389aa7363121c42a72fa11beb8572fa58647a5a2149615b517d32157c53` |
| `tests/test_brittleness_classes.py` | `tests/test_brittleness_classes.py` | 2148 | `1b3442f62c79009260c14a66113c9be500bc1446a1689df63bd934904b4b79db` |
| `tests/test_buildflow.py` | `tests/test_buildflow.py` | 28601 | `d1aa4e9b7cefd13f10f6a3e91bf7cf0fcd29fcc39ab6a95d4e56e64655fcd081` |
| `tests/test_capability_flow.py` | `tests/test_capability_flow.py` | 11646 | `a10dd0cb692c7acd9f8e736c75bfd27fb0ac91feacc4ec1e7d874be9bb1405b8` |
| `tests/test_capability_learning.py` | `tests/test_capability_learning.py` | 33723 | `e2bb3cca94316dcb5da182e63547d4327b69db970769d2cac3005d5f934ae360` |
| `tests/test_capability_mine_container.py` | `tests/test_capability_mine_container.py` | 6994 | `1f9723c634d8ed5c83ec41e875f2c9098c8f18279903ea82e2f94b0fba5a31d7` |
| `tests/test_capability_mining.py` | `tests/test_capability_mining.py` | 12877 | `59d0b45c4c28750ba1c8e484659a9dab78afc2b056cba8f2ad53f1d7862775fe` |
| `tests/test_chat_check.py` | `tests/test_chat_check.py` | 9534 | `74c91e81f6561d8422c9c024e05e9a9940d28040646559c37f7c17f9230260be` |
| `tests/test_chat_presentation.py` | `tests/test_chat_presentation.py` | 3616 | `07c0f17532dd7c07bb9ae80ab647e43dc49b0d57f74ba5260f3275f5a8568ef6` |
| `tests/test_chat_screen.py` | `tests/test_chat_screen.py` | 22668 | `ef404f5fd6b77bf1fa6d8ddef323e18cd34a768f27deabc7b822e2ac04daf101` |
| `tests/test_chat_turn_continuity.py` | `tests/test_chat_turn_continuity.py` | 5618 | `48ed160778237e3072ae5575c93cc393aab7f4e637d5b7202f661517301089ce` |
| `tests/test_chatbar.py` | `tests/test_chatbar.py` | 63803 | `91f176859e4b30ffd9426460c419daf998331203451c3ef6927e2aa1cd2bce3e` |
| `tests/test_checkpoints.py` | `tests/test_checkpoints.py` | 35318 | `1f29a041e6f79b430f4eb7f64f9314561751ef7ea2101f17055e8257a6720e02` |
| `tests/test_checkpoints_cli.py` | `tests/test_checkpoints_cli.py` | 22882 | `8f6f5e5ee120bb67afb666b55efd37f783967e38ce397c9f033b14d6c794d6b6` |
| `tests/test_checkpoints_durability.py` | `tests/test_checkpoints_durability.py` | 17071 | `2531a301aecb429b9ef6985b3b5b4b914accda94201b84a776a08a59f11e3435` |
| `tests/test_cli_contract_dump.py` | `tests/test_cli_contract_dump.py` | 5526 | `2b3c3d3b6003e8579d4be9e37c4fb3c9dde24fed069aeb6b7160b4ba704c653e` |
| `tests/test_cli_contract_envelope.py` | `tests/test_cli_contract_envelope.py` | 8469 | `07f5e6ea3a5dad979f78893bfa7147ded24f4da88fb779fccc0b28bbb5e75ba2` |
| `tests/test_cli_contract_pretending.py` | `tests/test_cli_contract_pretending.py` | 5965 | `0d74e9a95d073f92f87f9229a923dbbe311489625cb1ea26353c0bf3b24b65c3` |
| `tests/test_cli_contract_selftest.py` | `tests/test_cli_contract_selftest.py` | 6326 | `7f308bb5635d80dbbfde18d8b639603dd8a85507d8bd0583ead6efb6622c00ee` |
| `tests/test_cli_defects_66.py` | `tests/test_cli_defects_66.py` | 13233 | `ae5b1611fc2eece8ba2351fbdd455612f1eaca28b750f86725d1d4016b56865c` |
| `tests/test_cli_e2e_api.py` | `tests/test_cli_e2e_api.py` | 23266 | `1296b38a7f6804a94dfb77c922c8c74a92c0151bed95289378dcd64edade4f54` |
| `tests/test_cli_feel.py` | `tests/test_cli_feel.py` | 33025 | `52abd3ad5b64847fb8dbc2605f1b212e208fafb852ffe04dfcefc57ec3b98ea4` |
| `tests/test_cli_logic_audit_fixes.py` | `tests/test_cli_logic_audit_fixes.py` | 8161 | `823281c8ce81be3a06d242b128cbff841fc99592a1f055927f9cca3833c8fd4e` |
| `tests/test_cli_runtime_paths.py` | `tests/test_cli_runtime_paths.py` | 3227 | `f2fd243ddad3259768d2da441832026e9816800c4fc2231dce2ba31e26d08f39` |
| `tests/test_cli_shared.py` | `tests/test_cli_shared.py` | 21394 | `578fb85fb782d7424fb99951d9204e4e6c0ad6e3e869bafdf99355cbfbd41eb7` |
| `tests/test_code_exec_cli.py` | `tests/test_code_exec_cli.py` | 14173 | `a7d2e564414f11a98f51b8555cf5a271e466cd280b83640bd814c538df4fb945` |
| `tests/test_code_exec_sandbox.py` | `tests/test_code_exec_sandbox.py` | 16803 | `1d31637f8feb673fe1334d32c0c43911a44ec065cff3bfb4d001ab514be7770f` |
| `tests/test_code_exec_tools.py` | `tests/test_code_exec_tools.py` | 11170 | `0436be0dfd9beee083b4e00d52160093b5e8427cb8de2e7cd47d5233f5b32887` |
| `tests/test_codectx.py` | `tests/test_codectx.py` | 22505 | `2d15689f6a4ef2e7c9a05a0cbaffecd459309b341d454d993f4266618d93a705` |
| `tests/test_codectx_cli.py` | `tests/test_codectx_cli.py` | 10966 | `3f137d2e349ba8e3053e5e55ea7c0941fe8e70c08543e99e2bc53df669b95495` |
| `tests/test_coding_features.py` | `tests/test_coding_features.py` | 31151 | `29ba8114eb33d645da2f4f983663f225e1e4ffa650cb4afecf64f93c49d16a11` |
| `tests/test_coding_features_cli.py` | `tests/test_coding_features_cli.py` | 19503 | `cb734c717e042aa1c25dc623d413a0663a52b97598d0652dccf0c5f2051a8693` |
| `tests/test_compaction_mode.py` | `tests/test_compaction_mode.py` | 4332 | `a1f5047202ae339e21fb6f1697c051896f21498973bf9c52366fa52e1beb894c` |
| `tests/test_complete_value_slot.py` | `tests/test_complete_value_slot.py` | 2283 | `5accb999589f87297fbc9b14c53f6c093bbbf2a4535211c5d24f95dff8a97968` |
| `tests/test_completion.py` | `tests/test_completion.py` | 19892 | `61975297026a2eea838eb917071907e39dbe25c5c8b16d6d409727f67555ab7c` |
| `tests/test_completion_loop_e2e.py` | `tests/test_completion_loop_e2e.py` | 11183 | `0a414bf55f8804524d0a0dc7ccf5e4e4fd5a0c142470241aa783845cc93c24c8` |
| `tests/test_completion_precheck.py` | `tests/test_completion_precheck.py` | 13401 | `f0a0b090024c55357b070c4690e99ecdbd840ebe0f65c731fb9609e28d575ad6` |
| `tests/test_compose_cli.py` | `tests/test_compose_cli.py` | 42242 | `cf32712ede454ded13a21b0d957f4979af5d866236cef2551f1aa82842e1b2b0` |
| `tests/test_compose_e2e.py` | `tests/test_compose_e2e.py` | 22027 | `6440a117b5430f05ef1731e0b73e6d0f3339b206b90158d82f69ca4ea7617b1b` |
| `tests/test_compose_service.py` | `tests/test_compose_service.py` | 46561 | `570494d7044b58701edf94c48d42cde0524cfdeb34833759fed5b2648fd2884d` |
| `tests/test_computer_use.py` | `tests/test_computer_use.py` | 13234 | `c49e808c75f2734fd0a04e26ad05ec3dceac835a31919c935be8c4f333e76cf8` |
| `tests/test_computer_use_jev.py` | `tests/test_computer_use_jev.py` | 11306 | `bb5acd0e546f1013e259abedf909a33d7b5c37e5a740ab3e83c463b368c690ef` |
| `tests/test_computer_use_jev_e2e.py` | `tests/test_computer_use_jev_e2e.py` | 20065 | `f10407f17f947be130d75b51f3adcf83cf50641c6f01833bc96d048f00ee93ab` |
| `tests/test_computer_use_skill.py` | `tests/test_computer_use_skill.py` | 5893 | `644154d8fb83f4a2beeb8c779ee2e19516be441ac905b9b0ad92d5638355aec8` |
| `tests/test_computer_use_task.py` | `tests/test_computer_use_task.py` | 5881 | `28b398df8b5f90192db9edbbe1ef951486f51c943b80916b001e6a61cf84726e` |
| `tests/test_container_cli_templates.py` | `tests/test_container_cli_templates.py` | 10399 | `7f76ef8e2e3dc2fe02590cd77b05c7ab0d0a179b0579cca965dac0b4e8298d68` |
| `tests/test_containers_layer_integration.py` | `tests/test_containers_layer_integration.py` | 43196 | `655bca5716db631f8aad1ede02d8863a02ebcec5d13976b4aee30722f7245056` |
| `tests/test_context_budget.py` | `tests/test_context_budget.py` | 6827 | `ae01526cbfc8c70596312835151fb57699abaa0561bc7008ed933ad78efcdea5` |
| `tests/test_context_manager.py` | `tests/test_context_manager.py` | 49966 | `0aa7bad6ae5e894e983a701b95ce9effcc48e466505fb6a03b8a672f5b71a054` |
| `tests/test_contract_sessions.py` | `tests/test_contract_sessions.py` | 13447 | `0ef780fd604081d4ad7005e2fdc75b1b70526c652ea250dee3c628658276a594` |
| `tests/test_contract_store_atomic.py` | `tests/test_contract_store_atomic.py` | 9081 | `1f324f8a60a11794d39e3bb9b7c75ca525bb1ed98d0b512a992d3b96acb1a48a` |
| `tests/test_criteria_move.py` | `tests/test_criteria_move.py` | 4441 | `f2252d0cb17d4fa2e8f2c371d41287f9e7359ee0d8c5a47ae64c53ba101b02bc` |
| `tests/test_cu_agent_entry.py` | `tests/test_cu_agent_entry.py` | 25674 | `7c2921b3348aeafb714501da0f92d63a38f5d41cb1cb1e70cf3f9bb40ad3d60b` |
| `tests/test_cu_chain_local.py` | `tests/test_cu_chain_local.py` | 9411 | `2719ec61b64168db6ebfe2b7b06244e656181d2606d9224c9ca6082cdd9f479c` |
| `tests/test_cu_lifecycle.py` | `tests/test_cu_lifecycle.py` | 6892 | `1b8795c3e951a763ecbf79935e3927b0acf9cb014b9e03135e1f322f64bba791` |
| `tests/test_cu_live_desktop.py` | `tests/test_cu_live_desktop.py` | 36193 | `588a1e2399dff1e52ee59f3736c892ac672364cb5cc6f66bfbad629919f27ecc` |
| `tests/test_cu_platform.py` | `tests/test_cu_platform.py` | 5969 | `ad00bfa930989f7da1e7c2f3e18ae53e39b66988ef3c04d6becc5a1d689a1751` |
| `tests/test_cu_screenshot.py` | `tests/test_cu_screenshot.py` | 10664 | `b04ac36c63d41d01234f43c9925bece8a218f11f250c333d402f5e39c3292dc2` |
| `tests/test_delegated_worker_capabilities.py` | `tests/test_delegated_worker_capabilities.py` | 28007 | `76ffadf1278170aac754571ab07d1f37db2c712462f32d279563036aa17c6a7e` |
| `tests/test_delegation_fence.py` | `tests/test_delegation_fence.py` | 30447 | `8d271e1d35026997f2a89558fbdaa1438197c87f61347da8b17988f5720e0243` |
| `tests/test_demo_cli.py` | `tests/test_demo_cli.py` | 2267 | `f22a05a48c27cc85f9c6816928d97bd7bd7a06d333eb4faf13ae83c6da6cd201` |
| `tests/test_dependency_declarations.py` | `tests/test_dependency_declarations.py` | 11553 | `4ca046903ff33b581c936d88478d6f3ce12e432d9d5c626f849e184da6cf8407` |
| `tests/test_desktop.py` | `tests/test_desktop.py` | 14522 | `4b2ca0eb573a2fcc4230d4291b3dc4332c6d97b90986d33ad91203b0ed956a0c` |
| `tests/test_desktop_app_home.py` | `tests/test_desktop_app_home.py` | 3833 | `a86793d7f9f69b8817a70eb6ce3218853525602be3af6bf609412df29fa075ab` |
| `tests/test_desktop_panel_js.py` | `tests/test_desktop_panel_js.py` | 704 | `5a156ba482f5ad742d4e4ded77cfb4f9557a185fd223b9fad8de8624394e52b3` |
| `tests/test_discovery.py` | `tests/test_discovery.py` | 35158 | `d3666e02baf66d61beb3cb9d0d95867c5170b964fff4d8c4f8aee553c6554a45` |
| `tests/test_docker_ensure.py` | `tests/test_docker_ensure.py` | 36460 | `bc83afdb00e2fa46e25d3463022a1c8bed92178956bf95f4f30a3d848d8a2423` |
| `tests/test_docker_runtime.py` | `tests/test_docker_runtime.py` | 28745 | `663cb162d2418a572631b7bd22a5b74da844e634b8c9c0903be18ef228511884` |
| `tests/test_docker_runtime_label_filters.py` | `tests/test_docker_runtime_label_filters.py` | 22202 | `5fb7fd05d1eced5ed3836937943d909e04d1e02ae398a2c92fc48644dd745748` |
| `tests/test_docker_runtime_p4a.py` | `tests/test_docker_runtime_p4a.py` | 22024 | `fc45bb55400227cdb4610bfceddb83536686620d4a1ff4be88d0571c8812aae9` |
| `tests/test_drift_signals.py` | `tests/test_drift_signals.py` | 6847 | `153314b85ff21a010fb2d62fcdb86ec7c3a4739cffcdcac49addfd3e12ef7460` |
| `tests/test_driver_refusal.py` | `tests/test_driver_refusal.py` | 9377 | `7697867beea41570058e9624b29e1a2dbdc983c20240aadeb2c8d493f404b7cf` |
| `tests/test_edit_tx.py` | `tests/test_edit_tx.py` | 36936 | `7d9491ebb6212aefcdf02ed53af9c402a65a8eae863b9fe6529bef3deb6a13fd` |
| `tests/test_edit_tx_cli.py` | `tests/test_edit_tx_cli.py` | 22852 | `0c3c049a2293584984737726623fbfd2493af6219ee176ef1505dd05a409f923` |
| `tests/test_end_state_stdout.py` | `tests/test_end_state_stdout.py` | 2323 | `1d8431ad8730083eb99c2951a5e89c59ef9f5beb0397b6125d17259ace82cfb7` |
| `tests/test_env_file_loading.py` | `tests/test_env_file_loading.py` | 5804 | `30703808ed5143b42129102f13f2de74660b0416bc7a4340737dc3585f130a39` |
| `tests/test_env_leak_guard.py` | `tests/test_env_leak_guard.py` | 8086 | `77a938ff4b61b46f298acf447712811f4ff08e10a64bd5370458cac1139caaaa` |
| `tests/test_eval_harness.py` | `tests/test_eval_harness.py` | 900 | `eac2a2d7bf2c6dd948680103d58354266a51557706e815bae93c922351117906` |
| `tests/test_event_feed_cursor.py` | `tests/test_event_feed_cursor.py` | 4623 | `8e37fad557cc71b6c6357a2bd33df895802e37410d029299ca68bd144d5a2a3c` |
| `tests/test_events.py` | `tests/test_events.py` | 1371 | `d0532ce9a9c86688105efcb6b77ea0e7057ccda3e914b33c65f32098394c6da1` |
| `tests/test_events_security.py` | `tests/test_events_security.py` | 4961 | `8dd9db85eeef48973dfc8d9dec60f5b1dfbf6131c11e4b9379392e35c561c7d3` |
| `tests/test_evidence_binding.py` | `tests/test_evidence_binding.py` | 2908 | `4c76b6252877f434e13741ac03b3ea95f2b6656d43a12abdaa6bb5b5625ced89` |
| `tests/test_evidence_brief.py` | `tests/test_evidence_brief.py` | 7526 | `8d6bc38b943b94033e3730991927a7e9a172560e9a929193c95415d23cf21177` |
| `tests/test_evidence_strictness.py` | `tests/test_evidence_strictness.py` | 9115 | `9a807e167ab521a497124c6cd3a770bae9a39a0fe918c685511338a365b977c5` |
| `tests/test_execution_boundary.py` | `tests/test_execution_boundary.py` | 17467 | `bec5f0bed77ce9ecd25af245cc5027b1426b73e094caa91ac528d40b89990087` |
| `tests/test_f10_claim_review.py` | `tests/test_f10_claim_review.py` | 2760 | `ed241889471dd12525e95f12a4d48b829f5fbe4628996fc2d79c5a795e840b4a` |
| `tests/test_false_loop_regression.py` | `tests/test_false_loop_regression.py` | 5858 | `1a1e9fd0a13fa24e1af3f759c956749c2ce31b87db2a53fcae84c8e54f7cd447` |
| `tests/test_first_run.py` | `tests/test_first_run.py` | 5919 | `2809e4ea28849efab67c3614606894317fbf986e2b8cf7e1fc8efbc9f764a01d` |
| `tests/test_gate_hardening.py` | `tests/test_gate_hardening.py` | 10425 | `4a7fb5108495f76aece8162e62cd5d75da766561a592a2c4208a9b798b8c35bb` |
| `tests/test_generated_tool_isolation.py` | `tests/test_generated_tool_isolation.py` | 10385 | `cede4ef859b85d3eb11eb54bb88aa12081eb5d3463f371cdc19f9504fff1e32e` |
| `tests/test_goal_drift_gate.py` | `tests/test_goal_drift_gate.py` | 3379 | `77191fb7205d49e2047f85cf887e5cea2e3c963a1ffedcfa170a34d22b790a2e` |
| `tests/test_goal_run_cli.py` | `tests/test_goal_run_cli.py` | 21809 | `6875f463d5a37f07a5583eec21d20b4f958dc5e9fdf623fb2fd7990d7fd8578f` |
| `tests/test_golden_set.py` | `tests/test_golden_set.py` | 38886 | `c84e265d863d4fd0701ff1e3fe9bd47287140c74f81934ce9f19fd039e52f128` |
| `tests/test_guard.py` | `tests/test_guard.py` | 7734 | `9425832d41ce4c77f592001ffe71bfea50ff99cd5d2f2a208fa0e303665c7015` |
| `tests/test_gv_benchmark.py` | `tests/test_gv_benchmark.py` | 29703 | `d2248caff2d5f1b0c8a8ea921a824fbe60c5e68dad8bc815b8c3b8b55c89d8ff` |
| `tests/test_gv_crash_recovery.py` | `tests/test_gv_crash_recovery.py` | 27628 | `7c7348830a36f2345ce85603586d2065c70c11b0c756f02af1378ebde4b07651` |
| `tests/test_gv_product_run_headers.py` | `tests/test_gv_product_run_headers.py` | 2262 | `36b2d915a1869f129c2f762d3bc0bfd79d979b8d3d9312380ee3dd9ccdf5beb5` |
| `tests/test_gv_update_gate.py` | `tests/test_gv_update_gate.py` | 26071 | `c9dca5b5576744c13b118e494553db35bcf603fc97a4ee6bede3d225d8adb020` |
| `tests/test_health_events.py` | `tests/test_health_events.py` | 6440 | `2ef21f75b0d55bb056997fea6eef10c055878a7f2bf0763c6b668164920bfeb7` |
| `tests/test_hermes_plugin.py` | `tests/test_hermes_plugin.py` | 24251 | `3a37412b1fe897c6195b103956356d0294f91fee7bae58dc438af23cebb8ad03` |
| `tests/test_host_bridge.py` | `tests/test_host_bridge.py` | 26400 | `de3bb98feff267e757062a6c90f35ff67d00b820b65b82b33be74b9074a67818` |
| `tests/test_host_cli.py` | `tests/test_host_cli.py` | 11065 | `f2e1652057de908b96203837ce4934874f3b928dfd68c744b7e25e4ea5e1cd49` |
| `tests/test_host_overlay.py` | `tests/test_host_overlay.py` | 1860 | `13e6b9e4f86622fa7877adfc7ef5908591c417a05b06a11b09da1c9cabf01766` |
| `tests/test_host_refresh.py` | `tests/test_host_refresh.py` | 6572 | `c5555c243b8326d174d33434f2715fa1046dc5b975f7d1e4153c691066b68f2a` |
| `tests/test_image_inspect.py` | `tests/test_image_inspect.py` | 38224 | `10acf909f10f05856e3c4021fe4c1838d654b934fec78265b5672fcea88e2a2b` |
| `tests/test_image_inspect_cli.py` | `tests/test_image_inspect_cli.py` | 14582 | `72594ebde13f2e7350863876e18a479ca999af923dfd983f6207395bc905f86a` |
| `tests/test_image_inspect_live.py` | `tests/test_image_inspect_live.py` | 10277 | `6f9e8c88bbc2cf34d16694e377a46f49e5d2b9ea000c41970de0e2c12f66c45d` |
| `tests/test_image_pin.py` | `tests/test_image_pin.py` | 49607 | `2d04eac7d7be59e073bc705e31726b667e2476bfcae218d4ad432e599a8552be` |
| `tests/test_image_pin_cli.py` | `tests/test_image_pin_cli.py` | 33451 | `e35059fc0e7a5db87884b8dc1e6f13b5959f63cadcd57e48040d13460c4b5003` |
| `tests/test_incidents.py` | `tests/test_incidents.py` | 4197 | `dcee9d401771c1c1d009de895b433e55c4b2ce21b6eb5e9af2fa562a20eec4ce` |
| `tests/test_install_cli_only.py` | `tests/test_install_cli_only.py` | 1347 | `c7f6561ddbb9f2ed753cfd22d60650dac4b9fd15d1aa32310fbb02f322cdc783` |
| `tests/test_jev_always_armed.py` | `tests/test_jev_always_armed.py` | 8516 | `f9f40fae7fa9da508b104f0b94af2ede46861c2959ac8d972e259ae82053f02f` |
| `tests/test_jev_cache_metrics.py` | `tests/test_jev_cache_metrics.py` | 12798 | `55fe1bc788cfaab693241b4158a3d4d2fc5392b52509b86f3151c1047d801094` |
| `tests/test_jev_canary_stages.py` | `tests/test_jev_canary_stages.py` | 9297 | `13b164da968f74af5a600031b77a1ecb589aa849ab0c34c4e44106eed9d0b054` |
| `tests/test_jev_compaction_asker.py` | `tests/test_jev_compaction_asker.py` | 4031 | `155d0bb4d646c38ca859176637a8c2079e78a8f1605fce17ee4b5f9071c417fd` |
| `tests/test_jev_completion_ledger.py` | `tests/test_jev_completion_ledger.py` | 28486 | `f2bec370d7f1d8313c27b0d55410d79d42817d390e90165a858d244974dd7c43` |
| `tests/test_jev_contract_v2.py` | `tests/test_jev_contract_v2.py` | 16122 | `f070ca1f37cc38f882b3c59454b7921461da6bdc86d20d91240f2709fcf5e5d8` |
| `tests/test_jev_e2e_j1_voice.py` | `tests/test_jev_e2e_j1_voice.py` | 14313 | `1ed8ae3915e4cd7d77dc29c5c2a039b70b3c71d8e397f6fff1a78bce0b22b014` |
| `tests/test_jev_e2e_j2_completion.py` | `tests/test_jev_e2e_j2_completion.py` | 15032 | `f920380613e290b4a32a2d3c9e757adca98f52ace3b198a0149dd2e50bc06f43` |
| `tests/test_jev_facade.py` | `tests/test_jev_facade.py` | 37385 | `1fbcb14f5f1bbf1edc4d9bef35b8b1f0316f023bcab10b75adfd1f724ad0682d` |
| `tests/test_jev_facade_cli.py` | `tests/test_jev_facade_cli.py` | 15775 | `af992606cdd2a7779234f30f86b2e8cdb48cd43e9d583d8e985bc80b17c143a0` |
| `tests/test_jev_facade_rules.py` | `tests/test_jev_facade_rules.py` | 15105 | `ec6e9c45898b69c85b4f8b62a0156b579fae722450af3962ee11ac20c1aab14b` |
| `tests/test_jev_flags_events.py` | `tests/test_jev_flags_events.py` | 7150 | `47c7e5ec664e0b7e6db9743e38710c4972bf1a6b61aa5183fe19afdbd98baf3a` |
| `tests/test_jev_policy_delta.py` | `tests/test_jev_policy_delta.py` | 11090 | `b852b8a7d248c3bc8c39e5f3b6d24dd2ffde26263ca9c31321281c5a450f2e73` |
| `tests/test_jev_provider_arms.py` | `tests/test_jev_provider_arms.py` | 7858 | `5a3a9e8bb204b3f1c4347ad4af3684c17bce7e16fa7150cff812666a1571137f` |
| `tests/test_jev_provider_calibration.py` | `tests/test_jev_provider_calibration.py` | 20876 | `c8476d50128c54145de0a7390784e6f571ba058d7da78d38e3e6963eed7e9e48` |
| `tests/test_jev_provider_compat.py` | `tests/test_jev_provider_compat.py` | 28330 | `2d7621beca2ff3f98150478d26bb71c6b9ab0bcc6f6152790b1e74e58b54f68c` |
| `tests/test_jev_provider_treasury.py` | `tests/test_jev_provider_treasury.py` | 14127 | `1b8e8675c9a6b3584daba035eb91279391e74985fe354ce17c9b313fbc0099c6` |
| `tests/test_jev_runtime.py` | `tests/test_jev_runtime.py` | 45985 | `5eb7d7bceb008280dac82659f5beb8500812ec60420e34e46214df6303b9cb02` |
| `tests/test_jev_runtime_live.py` | `tests/test_jev_runtime_live.py` | 3227 | `c6bd309bc05ef347bf7b1711e7217f3b52ae1cd212e672fa324ef0fdddb13fb2` |
| `tests/test_jev_service_facade.py` | `tests/test_jev_service_facade.py` | 8216 | `14577cba3c26ba6cac89a81418ed4d1696e06634ca84b57fb79117b4fc7c5d63` |
| `tests/test_jev_subtools.py` | `tests/test_jev_subtools.py` | 20525 | `9a53335773f89e483ba0fe93514ee3952e44d03a573b9359370f55356961e1c4` |
| `tests/test_jev_voice_untrusted.py` | `tests/test_jev_voice_untrusted.py` | 13915 | `92e3dcc912fc1d17560d1b5b1fcb12568cb0509ffccc517e5c2619b04ab78261` |
| `tests/test_jevcomp_compact.py` | `tests/test_jevcomp_compact.py` | 32982 | `58d36853ec4800e38c466390b98b7abed3c8f0e78793c105ef633b3a4691723a` |
| `tests/test_jevcomp_fixtures.py` | `tests/test_jevcomp_fixtures.py` | 6735 | `d79982dd7731d3c2754aa12a84ca21d8f5ff8cad1612a7c89a4fab0f20ef55a3` |
| `tests/test_jevcomp_oracle.py` | `tests/test_jevcomp_oracle.py` | 15881 | `f46a385786c962f22e59f1c213f7e2ccf514a1e8432803c255593ff34749d17f` |
| `tests/test_jevcomp_state.py` | `tests/test_jevcomp_state.py` | 12367 | `bff3febfa4d6c8de1f95797e86098e47ebf21e1b3b32ef81f98de90e42851b6a` |
| `tests/test_key_paste_sanitize.py` | `tests/test_key_paste_sanitize.py` | 1656 | `32009739e6f6a01a3885a599af6460e056f23018e9c2a5a0f230a5a23eb65151` |
| `tests/test_knowledge.py` | `tests/test_knowledge.py` | 7700 | `fd2cc7b245d89b50e8ec2187e6377c06bc33fb9b05e235f33378339af5414bdd` |
| `tests/test_knowledge_cli.py` | `tests/test_knowledge_cli.py` | 3014 | `450f932ed0ef7c8878cf7fd9b53f1dc761d078230ab3b0e340079db4c660027d` |
| `tests/test_live_surface_wiring.py` | `tests/test_live_surface_wiring.py` | 9932 | `08b43360b749e51a583dc7ebe95802b1fd0919282304ff941234b5a5f2b16c59` |
| `tests/test_llm_promptrun.py` | `tests/test_llm_promptrun.py` | 4706 | `670e7e3ff9f94dcc68ec94274d4f1a6517496b8d40fe02feaf15ee83dde31720` |
| `tests/test_llm_provider.py` | `tests/test_llm_provider.py` | 40432 | `7c45652ce960e20c6003366212f848b8ad2c2c02fa70b656af818265ee1723b9` |
| `tests/test_llm_provider_live.py` | `tests/test_llm_provider_live.py` | 1995 | `5394e7976086d3da9235bd242ed707876728a6fc69a6f62765ec5d4c79e1804d` |
| `tests/test_llm_provider_wire_defaults.py` | `tests/test_llm_provider_wire_defaults.py` | 2031 | `d55481281666380cb225ffc07a24174e34b76c1f1837aeb70ce290dba652e17f` |
| `tests/test_llm_reasoning_budget.py` | `tests/test_llm_reasoning_budget.py` | 13423 | `e82b97b3951529ff37d850cdd9c908ae2a1a1d2b6e6dd96a24e0389e528a7503` |
| `tests/test_logo.py` | `tests/test_logo.py` | 7402 | `315b53de6b4e18df17be30313650bced6cc1c491dad439a0b6198a837f577c83` |
| `tests/test_logo_art.py` | `tests/test_logo_art.py` | 8716 | `c4bb9deb549a69afc257435c9dd9083b1dcc41c29936aca9778476328eba7b3a` |
| `tests/test_mcp_link.py` | `tests/test_mcp_link.py` | 8838 | `e2a701a0b669191e43ed4e62d762be50345e338ff1fbed16ddf829b5083d3ef2` |
| `tests/test_mcp_scope.py` | `tests/test_mcp_scope.py` | 4264 | `467b1667ed7ad7bdd64d394f86760b5d8b9c543aabeaa70c53ea944279dd397d` |
| `tests/test_mcp_server.py` | `tests/test_mcp_server.py` | 12447 | `bb1a33d51464f86fb57e1f631e6f06ec5333478974724edbf71def9360afd78f` |
| `tests/test_mcp_stdio_e2e.py` | `tests/test_mcp_stdio_e2e.py` | 4745 | `f26028043c551e48fba38b8817bcf318ecd433d2b4b1106d8ffd326578fc02e0` |
| `tests/test_measured_usage.py` | `tests/test_measured_usage.py` | 2424 | `331a23a8fca3949822f313fce091aa144496acb2afbe4731c3d6b7b91bf5dc39` |
| `tests/test_memory_learning.py` | `tests/test_memory_learning.py` | 43405 | `b8e7a42ce00f7b051c397962d4efd165abd02246c258625762932275059435d4` |
| `tests/test_memory_retrieval.py` | `tests/test_memory_retrieval.py` | 4435 | `de36c2eb260273052028dd8e84bda384b92a8dafc6caca45cdc3ed2518ed8d06` |
| `tests/test_memory_unified_search.py` | `tests/test_memory_unified_search.py` | 2035 | `f96a23ae2540b1f75a2e332276f014f6326d713b3d1cf6593a0660eec69c7b39` |
| `tests/test_memory_upgrade_cli.py` | `tests/test_memory_upgrade_cli.py` | 16432 | `1364d72b8c3fda85aae94484bffa4cba710cb16019b65fb0a752799f14b8b7b9` |
| `tests/test_memory_upgrade_consolidate.py` | `tests/test_memory_upgrade_consolidate.py` | 17294 | `e5a3e1869496a3ce9c2615b36737c315f32435aac9ce440d0bb8a25b8bf3dcaf` |
| `tests/test_memory_upgrade_retrieval.py` | `tests/test_memory_upgrade_retrieval.py` | 11942 | `558fa35b86935528448ddd80631499c0a6c1b60b3f351c93cb0a1305f42c467a` |
| `tests/test_memory_upgrade_store.py` | `tests/test_memory_upgrade_store.py` | 11353 | `c75b4853f972f3ccb9e6be6b10c07c38a115f5e90f3ed4a0054cb64d520e93db` |
| `tests/test_metrics.py` | `tests/test_metrics.py` | 2593 | `be854d11b916583d31fd9d7b314cdac3a32c853eb46e8dbf2c4b51822c7eb18b` |
| `tests/test_model_discovery.py` | `tests/test_model_discovery.py` | 3898 | `e51875dd9f4ac58a468474a74fab5908483e7095cd3568f1b939aaf8f865fd45` |
| `tests/test_model_live_selection.py` | `tests/test_model_live_selection.py` | 4346 | `2470a9cc5e67461989d02e092260f4ed3b6df069dc5e2a57e099aabae9cc4dbe` |
| `tests/test_model_picker.py` | `tests/test_model_picker.py` | 43871 | `cb37f0e67552271d91290d43cd0b6687988fa29531fe4593a704d9b2e05d3a6f` |
| `tests/test_model_routing.py` | `tests/test_model_routing.py` | 4900 | `8b673f1198f52a625cb57537cb4ac3fa4ddf6f65da9f76b92f4ebfd85c515789` |
| `tests/test_monitor.py` | `tests/test_monitor.py` | 3987 | `d8cf029b88465a1eba4a47d8f8a69724f5e09d3b54e0768e5af139ec172d6a70` |
| `tests/test_monitor_joint_reading.py` | `tests/test_monitor_joint_reading.py` | 2413 | `f0734d04a27cf73b6b6ad6f0fa327bbda8c8f210ba2a774be1b116f54e2f0e10` |
| `tests/test_nightshift.py` | `tests/test_nightshift.py` | 4539 | `6d55f06cb5bad358cff0d6141c1ca12ba12dd5a9b9c4ef9cab1c5929b70bfab2` |
| `tests/test_no_intervention.py` | `tests/test_no_intervention.py` | 2199 | `a33cd00dad95cddec88b68d420a0f018a41f0daec33d16e291593ee597a35577` |
| `tests/test_no_orphan_modules.py` | `tests/test_no_orphan_modules.py` | 18624 | `8dc38899d66df5df445aa1f4685d681d8311560cbeecfccbadee69d3ac6f7ec1` |
| `tests/test_notes.py` | `tests/test_notes.py` | 3502 | `aefc611bb4e17baf1b9c40ba0409b0a22abdc2aa4aa2576247626fb5195c5f86` |
| `tests/test_packaging.py` | `tests/test_packaging.py` | 11164 | `cc0a0316ba20f5d9df6e62d6427ee60ded5c6e677cd1efc362057c9cf0aefa8b` |
| `tests/test_packaging_install.py` | `tests/test_packaging_install.py` | 7605 | `b417536297c75d01bfe3de2d6fde0e356cc7517c9549e843a88988d02b1bd9cb` |
| `tests/test_pact_ledger.py` | `tests/test_pact_ledger.py` | 4052 | `232b9c225fdd71ece02eedc78ed6806a35993578ca5e5c751e313c3a6314b5a1` |
| `tests/test_panel_actions.py` | `tests/test_panel_actions.py` | 20541 | `4ee0f6c440d81d7d78932ab2ebf041fe2ba1796f69c222b7687986c9c8b268cb` |
| `tests/test_panel_api.py` | `tests/test_panel_api.py` | 13110 | `efd2e1d461a45fce990d831a8ed1691363a79f738948838dd6929771892d9182` |
| `tests/test_panel_trust_boundary.py` | `tests/test_panel_trust_boundary.py` | 8069 | `7d99517a2ac13b5cb1539bec102177e0865fa9f070170260558a77ce0f3b9712` |
| `tests/test_pause.py` | `tests/test_pause.py` | 7915 | `df97061d645bc33a2ead9bcdb23cf63c93597d6f59be8d951de1787e3ee68a67` |
| `tests/test_pause_hard.py` | `tests/test_pause_hard.py` | 13552 | `6d8dbd31c24669a1d046b92eb0e5da9503cca34727c3dd07ee4bd354cb3f3637` |
| `tests/test_pause_novel_strategy.py` | `tests/test_pause_novel_strategy.py` | 2539 | `aa84bad3735d0e794fe60cbffae50501a3b43902e6b0d7efe4405c842e7121e6` |
| `tests/test_perceived_speed.py` | `tests/test_perceived_speed.py` | 23011 | `e08700238c2a5005a1a4300f727dc3cea599c2ed984d658bd6dd7711f2847f10` |
| `tests/test_perf_cli_budget.py` | `tests/test_perf_cli_budget.py` | 10786 | `47c316bcbfb20e2185033ef1970377d25f8d4fa7172874f0f5eaa589705925ac` |
| `tests/test_perf_cli_lazy.py` | `tests/test_perf_cli_lazy.py` | 7823 | `1853c899d950d8d8421ce939354b42a11ed30fcff79e7e2e7c7ceb43f8c93116` |
| `tests/test_perf_cli_scale.py` | `tests/test_perf_cli_scale.py` | 4369 | `fcddf4ccbe940afc2b1ff6ead4bc3cc19e7ee19dad67314cffd4b8051595f3b6` |
| `tests/test_perf_codex_compare.py` | `tests/test_perf_codex_compare.py` | 8324 | `c36688239b39d2502bc539028322d4f2a128f815b78e4c0c329fae09b3a1a97e` |
| `tests/test_perf_gv_harness.py` | `tests/test_perf_gv_harness.py` | 3732 | `3e107a607a222a0414f29a6abcdf6c9936285824374deef3e52e193124ea4cd9` |
| `tests/test_perf_progress.py` | `tests/test_perf_progress.py` | 5996 | `c7dcc83484ca57edf812dcd0d35c5e67fe63b96d9a0b090d49d0080246c3c02f` |
| `tests/test_perf_runtime.py` | `tests/test_perf_runtime.py` | 8684 | `6e28bd961abb2a7ea28c267cdc0e5fa9b09c874560c17b9752d3b5d10107fa3d` |
| `tests/test_perf_selftest_probes.py` | `tests/test_perf_selftest_probes.py` | 6477 | `3f8a37c9b46b0e922fb09041c3081b4febf355fac82b20abc92202494cea9e9b` |
| `tests/test_permission_policy_matrix.py` | `tests/test_permission_policy_matrix.py` | 5346 | `da8bbe7523bb66f0fef51eb03cc187b956ac9bea65f1191fb607b4b949743095` |
| `tests/test_permission_profile.py` | `tests/test_permission_profile.py` | 20015 | `6453e2a6aa980a94a3a8bf6e1427e24ae25c6ae1676ac0059ec41c805e82c3e2` |
| `tests/test_permission_profile_env.py` | `tests/test_permission_profile_env.py` | 12584 | `539cfee7241750dfe28b4cb485a3e5eb7a70c20e1addb77179816eb2da5db569` |
| `tests/test_permission_profile_landlock.py` | `tests/test_permission_profile_landlock.py` | 26139 | `1b827060e75a349ff7fc82bd70d51efee0c29a7c36b9860f5aa38c2cd5294f4b` |
| `tests/test_permission_profile_nondrift.py` | `tests/test_permission_profile_nondrift.py` | 4781 | `faf4a4b2beaacbf2c220333c522ff10569efaa889ddc8b56329a1491994f529d` |
| `tests/test_permission_profile_prompt.py` | `tests/test_permission_profile_prompt.py` | 5909 | `e51face2b926883434e3978283eebc7b25f7a1f33b962fcdcee9584c0db4364c` |
| `tests/test_permission_profile_sandbox.py` | `tests/test_permission_profile_sandbox.py` | 9869 | `f2dd729304d6f2282e8cbf7d2a80ce5ed9914dd78b1a042d4556f41dd39c5ef5` |
| `tests/test_picker_widget.py` | `tests/test_picker_widget.py` | 22782 | `caa9053e247d71d6783a371c19549b304dd05d0263e30ce8a7b8563623cd3009` |
| `tests/test_platform_guard.py` | `tests/test_platform_guard.py` | 14323 | `262be8856e348314282e8b472a8c6b7042dbc5df5c427b5668ca270feeed68fc` |
| `tests/test_plugin_incident_wiring.py` | `tests/test_plugin_incident_wiring.py` | 8260 | `2ae1ae7d57ac624da7a0474ef2c4647a8d661ae9178b8a0da0e41bf64d4727f7` |
| `tests/test_plugin_installation_is_complete.py` | `tests/test_plugin_installation_is_complete.py` | 9537 | `24ccdf6b6aa67556f51eb283a364e727536af5f35ac9d3cd7c055ba06bcbd0f7` |
| `tests/test_plugin_release.py` | `tests/test_plugin_release.py` | 11885 | `dc044cef3c943a856adbbff45f17ded039f57918d124a4726b14c36de479fda6` |
| `tests/test_plugin_robustness.py` | `tests/test_plugin_robustness.py` | 3445 | `20607d285c46bad7b859a39aab5882a937612533ea35e1e2a8052e3cd623c781` |
| `tests/test_plugin_self_contained_import.py` | `tests/test_plugin_self_contained_import.py` | 2757 | `72e23c0d2e833a9ce5f3f99b6cd0291d872e4906b251b429126d4af5ce27f61c` |
| `tests/test_product_run_helper.py` | `tests/test_product_run_helper.py` | 1573 | `485355714305603a11d085348b7518ac9e4883f7a307f1a57b573fe83318f500` |
| `tests/test_project_context.py` | `tests/test_project_context.py` | 26375 | `5a7baa99df290fba2ab9e33343b8c31309ad51f69891def7d2934cfdd271901b` |
| `tests/test_project_context_cli.py` | `tests/test_project_context_cli.py` | 14278 | `499360d6817ed14184b095df821d4e616dc84b724423ea54e122a08fd59af57d` |
| `tests/test_prompt_builder.py` | `tests/test_prompt_builder.py` | 17733 | `0a748d7ebb2f10aa85c4de9a01829836d3d4591215479ad0ce4483ce66492779` |
| `tests/test_prompt_builder_stability.py` | `tests/test_prompt_builder_stability.py` | 22084 | `9faaa7984e02fa6d45636371d0922c09e0623628f2088136b4a41d5b7a15b200` |
| `tests/test_prompt_cache.py` | `tests/test_prompt_cache.py` | 36803 | `6f88c57709b8e8a27d4c8e87b4defed52cc497b65825b01bada1a19f41b363a4` |
| `tests/test_prompt_section_budget_warning.py` | `tests/test_prompt_section_budget_warning.py` | 2156 | `4d19a5ce4431d6675b75897ccf310d1dc5950aaa3408222d875168627ca847a9` |
| `tests/test_prompts.py` | `tests/test_prompts.py` | 1453 | `e26699cb1ef97d1d63557bba6474e0522ba83124fad83ec6141cfb99fe14085f` |
| `tests/test_provider_catalog.py` | `tests/test_provider_catalog.py` | 22099 | `cdbb42092ab9eb2a072e714f7289977a04969e1fa5a420bafa896f1aace19098` |
| `tests/test_provider_catalog_breadth.py` | `tests/test_provider_catalog_breadth.py` | 37526 | `c04d23dbe82bb83cd23bb8e27928b65b9e2145520ca82bcc30481f2fd58f9517` |
| `tests/test_provider_catalog_live.py` | `tests/test_provider_catalog_live.py` | 4870 | `2dde4a01164e10a12f2fa1a6c5677a379eb9633c7b3dc8e11bf5fd599ce156d7` |
| `tests/test_provider_cli.py` | `tests/test_provider_cli.py` | 33270 | `7d99c4c82fa93ea3ce57155274c2b556ac35690355417353a1d91f10d3e61aa2` |
| `tests/test_provider_config.py` | `tests/test_provider_config.py` | 27384 | `bb3b4ec5651f3fd933a880e5689a5bf24960fe0e74a23718c7252127e012c2a1` |
| `tests/test_provider_model_refresh.py` | `tests/test_provider_model_refresh.py` | 854 | `4deac88964005d9e8220d7312f66c0166950602d58ade4e4d3f6747cb876caeb` |
| `tests/test_provider_plan_support.py` | `tests/test_provider_plan_support.py` | 6951 | `a3dd68d24d5bd8aa2fd5ce7eff505706ce7430f92171387ce7666038e16db6cb` |
| `tests/test_registry_context.py` | `tests/test_registry_context.py` | 21042 | `4c546951aa089b97e4ccb18f46f5fa47dce0db8eed77ff22c62f01bb1cf930d2` |
| `tests/test_release_extras.py` | `tests/test_release_extras.py` | 7460 | `ef0098e99dea2516360e675cd06870ef3f9093ad7f413e6c280c00e232db0e93` |
| `tests/test_rename_compat.py` | `tests/test_rename_compat.py` | 15318 | `04c8dcdfd511d371e7b6170b40c8abe27d1cfe85eda2d4832390a8d77a33239e` |
| `tests/test_repl_codex_ux.py` | `tests/test_repl_codex_ux.py` | 35521 | `9b75b9b8553f96dc8b7a3e2e085cace6e8e47a4505a1ff4819096c69b655a70f` |
| `tests/test_repl_shell.py` | `tests/test_repl_shell.py` | 26427 | `c5a38e7a5bb6acd4ccbcf547c8156a753133120b7d8fddbf62a38d07b2640e07` |
| `tests/test_repl_wiring.py` | `tests/test_repl_wiring.py` | 8548 | `72e72b731334b040b3677e15ffe391895db34f438284950813c0b14dedc618d3` |
| `tests/test_research.py` | `tests/test_research.py` | 5214 | `b9584546900a938d1a6d3ffbefe0b0ac135865ab74b70dae1ced2c36a8039e6b` |
| `tests/test_research_reuse.py` | `tests/test_research_reuse.py` | 2987 | `ab210cf80ba1c6c52d811f95b2b323da6b817059659b90a91769ea29b06b3e8d` |
| `tests/test_rf02_request_deadline.py` | `tests/test_rf02_request_deadline.py` | 4863 | `9020c7487f85825da8acb1a9734c5d5a447efa02913e388d8849263c14013ed5` |
| `tests/test_rf03_monotonic_stream_clock.py` | `tests/test_rf03_monotonic_stream_clock.py` | 4826 | `0569c051214dc2b692904f7f48bcfe2093345cf11a27e7b3896071af012d0bc6` |
| `tests/test_rf07_interim_report.py` | `tests/test_rf07_interim_report.py` | 2352 | `15efcf448fa459bcbc3ecb396619ae69af6f371df0fce86742e735caf0a17cfb` |
| `tests/test_rf08_segment.py` | `tests/test_rf08_segment.py` | 8433 | `99f6ac4bb722e89a2f82d889c7794f8c941752e4208ed9ce56332fbbaaad2387` |
| `tests/test_rf09_crash_windows.py` | `tests/test_rf09_crash_windows.py` | 9634 | `e38e43e0a8418f07a6c2d5584e772091425901d8fb01c9911eb5aa88dec16d6a` |
| `tests/test_rf10_stop_responsiveness.py` | `tests/test_rf10_stop_responsiveness.py` | 3812 | `46d3496ea32eec0f3a9b1f06e5b640adf448f4321ec0e1a7cc43d54cbbc4e43f` |
| `tests/test_rf_test_provider.py` | `tests/test_rf_test_provider.py` | 6706 | `46852adb30be0c1c33d1993a2df2a1b91e407b658e149b3e6c83b34595da6b68` |
| `tests/test_run_chat_handover.py` | `tests/test_run_chat_handover.py` | 6716 | `2f92a8eeb4ac1d9f4120e7d973e8b02a8a9f27d841546c39a4aa108dbf4a1395` |
| `tests/test_run_messages.py` | `tests/test_run_messages.py` | 9790 | `864a68fae644ceb9f2e9444eb4493992bea85a8e0e118a79a9e56845bf1a99b4` |
| `tests/test_run_supervisor.py` | `tests/test_run_supervisor.py` | 39451 | `1f0fedc1a795d99d55eb717a158fdba90ca052df6401aadd0b126c7b18490e2f` |
| `tests/test_runs_entry.py` | `tests/test_runs_entry.py` | 2248 | `be69772151775c0b1ae405f546e64bcb3a5ae9cc38e4e407533e599f5e7252d7` |
| `tests/test_runs_store.py` | `tests/test_runs_store.py` | 30590 | `10100543235a0b8e41d9c38f6369b7e8680b892176845388cb4dc8f35dcbf78d` |
| `tests/test_runs_store_cli.py` | `tests/test_runs_store_cli.py` | 30833 | `1ae374d35a3d62dfff99340a19c6091269ededb1460550ae54becdd23512104c` |
| `tests/test_runs_store_durability.py` | `tests/test_runs_store_durability.py` | 14768 | `dd0d2c26e5ece93c8a37f6de02375108f7f00e54b93f3c118d95d7e15335e5d4` |
| `tests/test_runtime_final_wiring.py` | `tests/test_runtime_final_wiring.py` | 27876 | `60705562bfba2111bf03810e4901cb2efb48eb179f2721586fabbf65e6e4fccd` |
| `tests/test_runtime_integration_close.py` | `tests/test_runtime_integration_close.py` | 29844 | `72cd59deae8a54deeb929e89e3bd4aeb3c5e114e1d7b5565dc7aa59aea9556a1` |
| `tests/test_schemathesis_cli.py` | `tests/test_schemathesis_cli.py` | 23708 | `6668f25fb1fd514a497dad4e736244772548111e608a57d9f0f943dda2bb9b4e` |
| `tests/test_secfix16.py` | `tests/test_secfix16.py` | 7972 | `e14da4d6de45749259ccaf677236517b86f5f538fe8d7ac891d45545a582e6f4` |
| `tests/test_security_audit.py` | `tests/test_security_audit.py` | 20268 | `00a1c2721f91ba2922caa8f908ba572f8179e7c5d1708cd6de8fb937b1152a63` |
| `tests/test_security_audit_cli.py` | `tests/test_security_audit_cli.py` | 10893 | `927ba7db9ee4b3bdf1222fdb73883e2766743cc58730a38ecf2689b9426f26e4` |
| `tests/test_security_audit_fixes.py` | `tests/test_security_audit_fixes.py` | 12900 | `5eb41ef6646b2b548ad37b735b368b09494fcfc473c2c9622537ef31c5e15eeb` |
| `tests/test_security_audit_regressions.py` | `tests/test_security_audit_regressions.py` | 12688 | `4dd83ded6dc36eca9b19f9e40dcd4446d8c90327c334b408a2bc895214e4513a` |
| `tests/test_security_coverage_cli.py` | `tests/test_security_coverage_cli.py` | 1437 | `0b3e4081a20112d1c2eae8741324bd935b9357d6d447720a920e78dcafefca64` |
| `tests/test_security_gate_failclosed.py` | `tests/test_security_gate_failclosed.py` | 4381 | `33fab3df4d2cd91b48616e50d273dec2c96336c41c5db179c668d493a8c2d9c4` |
| `tests/test_security_linux.py` | `tests/test_security_linux.py` | 28381 | `5e087a097a58f40644ef2c469baa83358ad8283692d147798b36ca3b75d1fd20` |
| `tests/test_semantic_gate.py` | `tests/test_semantic_gate.py` | 9947 | `7d21d7fa6ae4ea3662c4344af455aab84e8384941a711341ea01725bfae10810` |
| `tests/test_session_goal_scope.py` | `tests/test_session_goal_scope.py` | 6855 | `770fc3e25fbfbfc33fb7e7d22b6d0d5451166063bd287fb6a0af0ab4f2ce2a57` |
| `tests/test_session_isolation.py` | `tests/test_session_isolation.py` | 6860 | `ddbe1c013cd25c0a6841ae9342a39b13f1e0d9682bc967a4b921731a9a573ed3` |
| `tests/test_session_store.py` | `tests/test_session_store.py` | 36845 | `389e027ff6ce5ff2e15aa13e96ce9b04e7004229ba893fc8ef8464f4ac84776e` |
| `tests/test_session_store_cli.py` | `tests/test_session_store_cli.py` | 26140 | `0da58f2aaf8c575c9b4f7b99bfb281e705a2878feb1880c4d6c43768021bdbd1` |
| `tests/test_setup_cli.py` | `tests/test_setup_cli.py` | 13125 | `4c2ce0ad9f731fa61f1d359ffac9709c805a90de7eb5373031e018f0ebba8f3e` |
| `tests/test_setup_flow.py` | `tests/test_setup_flow.py` | 34715 | `24dd5d37b869fbd7cfc67d9c5e02f119cf61e48af47f8c0e6c8927f5f312ee0c` |
| `tests/test_setup_role_provider.py` | `tests/test_setup_role_provider.py` | 10751 | `b1450761198702cbcfd14811b6ea6e568012746d3fc4b6f0159ce4ec6351299b` |
| `tests/test_setup_selectors.py` | `tests/test_setup_selectors.py` | 20735 | `860c8472dcd41a66cdc24932bf2c3d1c9d57ddccff86cf965f793025e01731e3` |
| `tests/test_setup_ux.py` | `tests/test_setup_ux.py` | 25409 | `ab526fb94d11ad19d03d35d3b40c2ac89e1e5fc10acf2c3f9b4ebf07e4d8cf23` |
| `tests/test_shared_budget.py` | `tests/test_shared_budget.py` | 6469 | `197b2ebd9f11e16d0d14825b2e9b0781b25b89f0b9313a352303ce266bcb93ed` |
| `tests/test_shared_integration_16.py` | `tests/test_shared_integration_16.py` | 25297 | `2db4592d046454211f1317b6daad274e1f7abe5cc3218c77cc741c0104f85a51` |
| `tests/test_skill_install.py` | `tests/test_skill_install.py` | 4452 | `8ffb29524430d358d70d7b8e6fa9f21a0745271162c0f7bdcb9b0f7af8711618` |
| `tests/test_skills.py` | `tests/test_skills.py` | 13213 | `efc92489b43add1a679b36f6b34249a59ffe6e93756a8cfd2d59ec9e0a73e4ca` |
| `tests/test_skills_builtin.py` | `tests/test_skills_builtin.py` | 1801 | `a9ab1e35c5c39badb037d283fdea59934af377fa7b594483bd12b0e8afbcf274` |
| `tests/test_slash_completion.py` | `tests/test_slash_completion.py` | 19253 | `b4eb9349f9df5942fe4da68793a503fd5995a3b71eba9cad81bc6fb55499827a` |
| `tests/test_slash_palette.py` | `tests/test_slash_palette.py` | 44443 | `022cce254adbaaa7994cd4535202732ea76a59b9a41410551e70765849b99e10` |
| `tests/test_step_runners.py` | `tests/test_step_runners.py` | 40172 | `dfa8ee4ec263b89c8fb282df142949d152770436c8382a21bc92133c7cd9e7d5` |
| `tests/test_step_runners_gui.py` | `tests/test_step_runners_gui.py` | 11025 | `4894fd14d1843c6a3aa2c35b1343f105285fb69ea4dede3f1f49367cfd67fd5f` |
| `tests/test_strategy_novelty.py` | `tests/test_strategy_novelty.py` | 4011 | `88db92eb9d1a1010f198cfdc1b491de23a6aa3d1e3d5377a092a8f7e4b2edb72` |
| `tests/test_stream_incremental_chat.py` | `tests/test_stream_incremental_chat.py` | 8894 | `4463e45e048ce041f31652656bbb1e590b05c3a4a8e26001fbcdde902f2f5a1b` |
| `tests/test_stream_render.py` | `tests/test_stream_render.py` | 44148 | `eb65345978fe39e73adb6033ffa680d972062209ea7479e5937f9d6b181c21f0` |
| `tests/test_streaming.py` | `tests/test_streaming.py` | 27927 | `1950a23ab4383ef46a21725c0415ad9527050a9ea7fbbc67daf6665b949a4a8a` |
| `tests/test_strictness.py` | `tests/test_strictness.py` | 1727 | `dd00f7decb4a3d69b3c9ecc1a1db3a9e6b5fd9b4c8243623e35c198394c3a977` |
| `tests/test_structured.py` | `tests/test_structured.py` | 30074 | `58eef82ae10b508bc567e4cc2d81edaea7973b145b8ce974a4ca56790ffdc246` |
| `tests/test_structured_live.py` | `tests/test_structured_live.py` | 8869 | `466a16e008f2e4b0aa78f3694fff542807f002ea931c3aa171c4edb6b6395fb6` |
| `tests/test_subagent_briefs.py` | `tests/test_subagent_briefs.py` | 9030 | `9b6f3b1327cf427243b69dbcb39e01309c68146f25586f98ab60843b9782e63f` |
| `tests/test_subagent_models.py` | `tests/test_subagent_models.py` | 14313 | `daf85c6f3fdc6ffd2b8cabbd81c43646118f84cc9a9438d7280b087e9ad0e0e4` |
| `tests/test_subagent_named_roles.py` | `tests/test_subagent_named_roles.py` | 4569 | `f224affeb121d952c611bceacfd8bd678631ffffc26c99226ae1202db31b1223` |
| `tests/test_subagent_roles.py` | `tests/test_subagent_roles.py` | 15656 | `350201198facc9130a7c997b470afaaafcafb09973c189ce4bb82df815b170d4` |
| `tests/test_subagents.py` | `tests/test_subagents.py` | 43111 | `373b4b2b767afbd3575d7b49d7af6b408e7eab9df11ff1fd24c0f35a41e04ee3` |
| `tests/test_subagents_cli.py` | `tests/test_subagents_cli.py` | 34784 | `c0f688871f93f9cdce2b924f57fb6435c17d3d46971a69ee7e8ba86c9bfc8335` |
| `tests/test_surface_close.py` | `tests/test_surface_close.py` | 20795 | `2c4c7e5bf869232829bd0dfbc74331b6fa31e0fdd79de528dd7418e63e27fe36` |
| `tests/test_synthesis.py` | `tests/test_synthesis.py` | 3947 | `89d2094bb9523f8be461f3f9e00aa270f45d09974a6d51a40d8b9dae626da6d2` |
| `tests/test_system_one_adapters.py` | `tests/test_system_one_adapters.py` | 11112 | `3a890e48f9e7e387f03514f28e632a5559f07325fd4adcb1441effaa082b644c` |
| `tests/test_system_one_completion_wiring.py` | `tests/test_system_one_completion_wiring.py` | 5410 | `06739d68480f959bff38fe9f1009c2f0808cf682dd9de5b194e53df485e51b0b` |
| `tests/test_system_one_contract.py` | `tests/test_system_one_contract.py` | 14529 | `26edd3f7a382e1a9684caacbd8f8a9f2851f1c482a525cdb823023805af46beb` |
| `tests/test_system_one_flags.py` | `tests/test_system_one_flags.py` | 13222 | `6f56320fff50fc2e8643705f87eb93210005220a451e940b66b60fa2f2469ce8` |
| `tests/test_system_one_guistate.py` | `tests/test_system_one_guistate.py` | 22619 | `0644d137da3103286ae34b728a0e1f3c2f1497839f9bdd5756f920668284955a` |
| `tests/test_system_one_policy.py` | `tests/test_system_one_policy.py` | 19542 | `a5eca07db73c983781b61690a703ea4b923a9e61b65bf6be96624c9f0b0a1001` |
| `tests/test_system_one_questions.py` | `tests/test_system_one_questions.py` | 9090 | `8b87bb12a6f4e083c29690156722729f279b62d92b5352f13f0b7cbc25c3c2eb` |
| `tests/test_system_one_sanitizer.py` | `tests/test_system_one_sanitizer.py` | 9493 | `c8952a306d3c96318783db47a737c9eb9158470a6b05ee357889d2d9f5f0a8c9` |
| `tests/test_system_one_shadow.py` | `tests/test_system_one_shadow.py` | 25709 | `4b3fe9bd1ea25488ef47f7c6f5cda15b108bf10bb177fee695e1ff8b0dde484d` |
| `tests/test_system_one_triage_entry.py` | `tests/test_system_one_triage_entry.py` | 6886 | `f7082220616898ac70d0d53832d69fa48245e82f9fb1bd1e926350d60be0a7c3` |
| `tests/test_system_one_triage_pack.py` | `tests/test_system_one_triage_pack.py` | 16382 | `578009f899ae4dd6ef959726c4c49f83d722156f09113aefdcfdace0bde91837` |
| `tests/test_system_prompt.py` | `tests/test_system_prompt.py` | 6069 | `bbaf3653febb07471f7a22274913c56478da7a149f198abd9d292d1d57e7e21c` |
| `tests/test_telemetry.py` | `tests/test_telemetry.py` | 35731 | `27dabb55683db325ba7bbb8214effb26f7973720b652e6c8e25b71635b71c10e` |
| `tests/test_testing_result_contract.py` | `tests/test_testing_result_contract.py` | 1749 | `bc50c90ceb8c856b2c1b363b2caa3c5059ab20bd05fa6d971884a325cd217f9f` |
| `tests/test_testing_tool_gate.py` | `tests/test_testing_tool_gate.py` | 3059 | `1e63b2d0425cec002a5e8975d21db7d59cc1a1d2071fe3bc48d2d519436f970b` |
| `tests/test_testing_tool_stats.py` | `tests/test_testing_tool_stats.py` | 2482 | `d717037f3d2555dd963463a3f778514d0d4b9ec094714e6adf4c8e42263faa45` |
| `tests/test_theme.py` | `tests/test_theme.py` | 8647 | `5d264f4abf552a1a3044970976dc16ab76414fa569a6dde8236bf0e96df62d5f` |
| `tests/test_theme_cli.py` | `tests/test_theme_cli.py` | 8244 | `91060dfc99d54f30622b3a7d7a77a751a3d0a8a0f389f5975759fb3e5eb959ca` |
| `tests/test_theme_render.py` | `tests/test_theme_render.py` | 12303 | `3a064dff9c4a16491ed6ce57f98ef3916ad729935052d82944e9c1b4fc5642b4` |
| `tests/test_token_economy.py` | `tests/test_token_economy.py` | 23828 | `66fcc67fb84b1881da4421771583c2761b3f2e26668192d364d26e6db188e5ac` |
| `tests/test_tool_module_resolution.py` | `tests/test_tool_module_resolution.py` | 2033 | `624279f4b9eee7b54e03350b2a9d95eec0e8705dac470abf66dd1489863e4f55` |
| `tests/test_tool_scopes.py` | `tests/test_tool_scopes.py` | 21303 | `f9b7be009e8b089519f9ff58346a808fcc23574acad7cab0a653df1e96b51f64` |
| `tests/test_tool_search_integration.py` | `tests/test_tool_search_integration.py` | 3029 | `c9ea851d3198e0c7d2d0c6d722022e2bc79db99b5f0e491fdf58d0ad5fdc71b7` |
| `tests/test_toolbuild_cli_client.py` | `tests/test_toolbuild_cli_client.py` | 6631 | `415136c597b76580d92a28f04de07b962ba12722f4ba96a55b82090cacfa7174` |
| `tests/test_toolbuild_client_wiring.py` | `tests/test_toolbuild_client_wiring.py` | 5465 | `02c49fe1c01c63d2fb8026ad8d63e0f6a2f3a624bd0c6e84a89c5d2f3ff77285` |
| `tests/test_toolbuilder.py` | `tests/test_toolbuilder.py` | 37673 | `118244eb0967fad1ddf88d79497319ed820aae8191ddeefd83740e772e6ba2fe` |
| `tests/test_toolbuilder_argument_templates.py` | `tests/test_toolbuilder_argument_templates.py` | 32596 | `5abf3360c22d7a81496405bb4849d08096f07159962fedb331702cf5704894ab` |
| `tests/test_toolbuilder_cli_teardown_evidence.py` | `tests/test_toolbuilder_cli_teardown_evidence.py` | 11960 | `481f6e5942c9f9ce89efea255d638f8240563e7a0063e3088bbfd8912805ee24` |
| `tests/test_toolbuilder_container.py` | `tests/test_toolbuilder_container.py` | 52737 | `f5a37fbe8cdbd0f29e4f6b654489daf7fc3704c5e7cc7ad7b4d0b631aba957f0` |
| `tests/test_toolgates.py` | `tests/test_toolgates.py` | 4767 | `e97475b8f5e6fb352c47b1d8976851f3bc8584faa89481254abf43d0dfa9eae0` |
| `tests/test_toolhub.py` | `tests/test_toolhub.py` | 3959 | `8ecd2ca13ab9d9d3f9c4feb8b85205bcd090b9913e6118cb704388c30b6f5e8f` |
| `tests/test_tools_call_cli.py` | `tests/test_tools_call_cli.py` | 8715 | `939e155611133aa57d31df6bb8347331c3ff1938fffc88a0d7f209ae289fdc00` |
| `tests/test_tools_core_contracts.py` | `tests/test_tools_core_contracts.py` | 14003 | `9ec5d6987568d777e49ddde1b5d814b05b4d61d9ef511c3d7be611dbabfeb0b1` |
| `tests/test_tools_core_registry.py` | `tests/test_tools_core_registry.py` | 19502 | `8ac60d2829c76dfe6075e48bb92adf0f0015cfc73a8bcab42a3aee7aa0d03e31` |
| `tests/test_tools_core_tools.py` | `tests/test_tools_core_tools.py` | 31653 | `bd006a228c57abbbf9cbfa0b9799fca07055c775154f8dcd097d6140985fb83b` |
| `tests/test_tools_store.py` | `tests/test_tools_store.py` | 12710 | `32ed3348d831aa2448788fe2db5f24c1567feecb6cb2067677cc40e9eb2ff3eb` |
| `tests/test_toolscan.py` | `tests/test_toolscan.py` | 2109 | `642942cb35f0e9752d5c42778a9a130f9b0f7e8836b4ae47bb2ae0aeba97c900` |
| `tests/test_trace_mining.py` | `tests/test_trace_mining.py` | 10809 | `58ff818086c6fe5de18011a7e61bc3000d2e764e39e499855e022d96bdb27846` |
| `tests/test_trajectory.py` | `tests/test_trajectory.py` | 3532 | `0dc694fcf4baf2ecd1be1e60cbba59a4a3ac95ef61355f5af0ada6ffaa7ff2e5` |
| `tests/test_treasury.py` | `tests/test_treasury.py` | 13271 | `ea786a95cad55a62768b63f9a9169206c99110a914ef448896db14021e6297bf` |
| `tests/test_treasury_hardening.py` | `tests/test_treasury_hardening.py` | 21012 | `ed9792322ce00df970d9161c0609455251deaa20b543748b4ab44aadec659b93` |
| `tests/test_treasury_toolcall.py` | `tests/test_treasury_toolcall.py` | 6366 | `f26c5f6ad2cec0c485aa6a8e27eab771d517be159a69a1a8869143eda0a6d3cd` |
| `tests/test_typesafe_decision_provider.py` | `tests/test_typesafe_decision_provider.py` | 9661 | `331e0eb1b7e019488b5788f645090d8fc3c6ed0edc49bfb10fe9d3246fcdaa95` |
| `tests/test_ui_feed_coverage.py` | `tests/test_ui_feed_coverage.py` | 2822 | `348cf9392703bd1e024edc5d985f02d766379b186befaa81cfe5cdd7acd714a0` |
| `tests/test_ui_overlay.py` | `tests/test_ui_overlay.py` | 30918 | `7a59b0dc570000e5f1e2c06cc349fea916c4959b06a053b5456025dc7a826854` |
| `tests/test_usage_stats.py` | `tests/test_usage_stats.py` | 1675 | `dc31827cfec4ed04c9dd7491d64df4d27cabd43d574fae342bc90913ea3e2724` |
| `tests/test_verification_closure.py` | `tests/test_verification_closure.py` | 31384 | `3196780391596d4cd135d7f6b1ca2da985bdfbc0ce8ad4df9e45740df3441d4a` |
| `tests/test_verifier_readonly_gate.py` | `tests/test_verifier_readonly_gate.py` | 1143 | `c9abb480ea37416008e274a1bc0dac30908156fed7449b4a34b99e5b138f5660` |
| `tests/test_verify_flows.py` | `tests/test_verify_flows.py` | 32657 | `a66d14ea0d97bbfa8655ded964b64d4bf7a31c9ef8e23e4ef11dda5b99dbeeb8` |
| `tests/test_verify_service.py` | `tests/test_verify_service.py` | 24133 | `0efb94189eafb3ca298e169882de487a22fb6fcc08b8183a88645ee0fd7dc1c4` |
| `tests/test_verify_service_cli.py` | `tests/test_verify_service_cli.py` | 21554 | `d6798aa177baa3bbb3d03d018ff7ea223eb04c3295f06b17b43523aacff95d8a` |
| `tests/test_vision.py` | `tests/test_vision.py` | 38071 | `9d7cb91d9dc4fd264a4a199ace33c4b69f78779be14cf0b70cc21b058fa18633` |
| `tests/test_vision_cli.py` | `tests/test_vision_cli.py` | 28937 | `5f851586e54a153abb07ca259fe5650b99bcdb18a686d8acd73aa26172ea5fc6` |
| `tests/test_voice_cockpit.py` | `tests/test_voice_cockpit.py` | 3330 | `ecbaa6686d3ae634749d7682ec08053508c52c10da8fa76483c1198cd4f62249` |
| `tests/test_voice_inject.py` | `tests/test_voice_inject.py` | 3281 | `8d2fac490262613d1948c6516b7529800fb9b41c6d10585d7e84c792480b48de` |
| `tests/test_voice_quality.py` | `tests/test_voice_quality.py` | 2812 | `b4d2539133f63ca45d8772c20233bb9474c08937385d7b08b879b19463df9483` |
| `tests/test_voice_service.py` | `tests/test_voice_service.py` | 29896 | `6410678a8df56853ef0bcefcd5ed5d8605fdd34642d792ee66c049acdaf459f7` |
| `tests/test_warm_session.py` | `tests/test_warm_session.py` | 25799 | `cea71526b79b333b2b6260021800561a798433989a0ed6b405f604f00d252fd3` |
| `tests/test_warm_session_equivalence.py` | `tests/test_warm_session_equivalence.py` | 10691 | `20a9a09fbeb910f34579806a28fb22f17081bb85e47a1d03a49af20982f81d16` |
| `tests/test_workflow_runner.py` | `tests/test_workflow_runner.py` | 7175 | `ebec3a16483c286a8a089f1fd83768d6178bcd12f46349fb32320ab764c3b72b` |
| `tests/test_workflows.py` | `tests/test_workflows.py` | 14109 | `94af5b73c6052e067e01857bbcae018502ed1b361ef950389745d27204af486f` |
| `tools/acceptance_evidence.py` | `tools/acceptance_evidence.py` | 15130 | `74e10ec911785ff247ca89f2e2c48d26b5bdea9caf650885564068db7d0888d8` |
| `tools/arxiv_search.py` | `tools/arxiv_search.py` | 1542 | `e7740808c449c18e5b9967ceac9240349a79710bc0641f0f6d672e3caac0e8f5` |
| `tools/bing_search.py` | `tools/bing_search.py` | 2897 | `bf2acface2d62998d13605f169e4b5a0b13d86340ab54a0307e282b59e6919e0` |
| `tools/build_artifact_manifest.py` | `tools/build_artifact_manifest.py` | 10005 | `35af00f4e33d80e00c851b86bfc7a2ed0001a30b8f5209a4f14046d837cb2294` |
| `tools/build_audit_register.py` | `tools/build_audit_register.py` | 13726 | `15b55a24527bd87a986b1235b74d187c42f42c63b9f53cbf10b55fb7654f58cc` |
| `tools/chat_acceptance_pty.py` | `tools/chat_acceptance_pty.py` | 18148 | `d3bdc745411f69776fbb87ec310b4604ad168cb6af9d9ee52e83dd5e65932bdd` |
| `tools/chat_capture.py` | `tools/chat_capture.py` | 10201 | `9fd7903cd64313f544a609540800230df1292556efe1775f6e0e3499f20e8ad3` |
| `tools/chat_screen_replay.py` | `tools/chat_screen_replay.py` | 4260 | `f8f2eef80a7add9865aa7f8f744c923402e8d96610c61834564463fdc01752f2` |
| `tools/check_desktop_plugin.mjs` | `tools/check_desktop_plugin.mjs` | 93099 | `d39eb6421ba535baf813016101e6fcffcb7fa2de453d44f23f6aaf7fd731cbd9` |
| `tools/check_findings_criteria.py` | `tools/check_findings_criteria.py` | 4139 | `8c0a94cc518eb9db255f3f970078278932c4d33dac28e915ddbeac6c9aba009c` |
| `tools/check_ui_overlay_anchors.py` | `tools/check_ui_overlay_anchors.py` | 2697 | `371f4ce06a64f944c0a6189bf3ee86be37736af30430de4506d707a49aa4c9ec` |
| `tools/command_index.py` | `tools/command_index.py` | 20433 | `08852af0a681175ec642fd2ddc4715fcc49ddc7ac0f1af43f6f57f5dea9aeaa7` |
| `tools/composition_probe.py` | `tools/composition_probe.py` | 7637 | `2f5f42e75c757b1d6a38be4889dd3eab35f9e1e150ec628f9724fde567953b71` |
| `tools/cu_agent_gui_probe.py` | `tools/cu_agent_gui_probe.py` | 14658 | `21a6a05a092b88dcdca72af76e897e7d243d602720f5c0792b29962b416e44d3` |
| `tools/cu_gui_e2e_probe.py` | `tools/cu_gui_e2e_probe.py` | 11683 | `46aec855a928f424f59438c14c54b62d3b4487f975cbd7eeb1bce212c796f060` |
| `tools/docker_lifecycle_probe.py` | `tools/docker_lifecycle_probe.py` | 4505 | `37f7aac2cf1de5f81b7c68479040c60f16357fdda6ca4fa2b4539656d8317a66` |
| `tools/docker_runtime_live_probe.py` | `tools/docker_runtime_live_probe.py` | 3893 | `538f40cf6bd2cb9e8ae5f7dd8cbf522b688d7ca80e1ec3afaab66ac0f947f717` |
| `tools/eval_benchmark.py` | `tools/eval_benchmark.py` | 17882 | `5dd3479fc2875640d75743374a413a8a36d2e06e83314b0139d532827b066973` |
| `tools/goal_stream_probe.py` | `tools/goal_stream_probe.py` | 10202 | `cf1fa825617c57f8f6b83a639fc8efe20d4e6a345e5529ae957a22f69fe11828` |
| `tools/gv_api_perf.py` | `tools/gv_api_perf.py` | 37962 | `4d48a63973f6eb548009c26e49b0e47fc399a85f2b4ffe7b51228779e06d5723` |
| `tools/gv_benchmark.py` | `tools/gv_benchmark.py` | 57074 | `2ba257b99f845b51f522d5bc89fed2632e1449bf07a0bdabfa793dc67318174d` |
| `tools/gv_cdp_console_stream.py` | `tools/gv_cdp_console_stream.py` | 2480 | `be4cc00c1977a7c48574ddd865986dffcc377ed12c734fc414c751197099ad8b` |
| `tools/gv_cdp_script_search.py` | `tools/gv_cdp_script_search.py` | 2157 | `eb788ea2847f310252aeeb15fcb7bae4cf8fa38691765caa41092ab687eb4d95` |
| `tools/gv_checkpoints_proof.py` | `tools/gv_checkpoints_proof.py` | 22402 | `58dbe6ef12afbefb612bd2fd1b2b0cdb32fedcc0227e61fb42ea227b87e15fef` |
| `tools/gv_cli_e2e.py` | `tools/gv_cli_e2e.py` | 104357 | `e5463d4f344b3c11368866204e9ec897f944e11868fa1184fe420d70aa822b4c` |
| `tools/gv_cli_perf.py` | `tools/gv_cli_perf.py` | 27064 | `90dc3e2989e3b9fd194fe8e5e9ab92ac3130739f1ce2d52ffcf2bef319385ee3` |
| `tools/gv_cli_reference.py` | `tools/gv_cli_reference.py` | 29599 | `30d9b5106f62519483a1be807f6be6737c5d9d5d9a003d6ee4358f176a3abd5c` |
| `tools/gv_cli_visual_audit.py` | `tools/gv_cli_visual_audit.py` | 19615 | `66afeddb15fc18d23ef526149e5900e68d7d6f252a3df569976b0083f6f46658` |
| `tools/gv_console.mjs` | `tools/gv_console.mjs` | 1973 | `73b913d9ca57b135e44125ce3ea165c46a573992cf4bc0d2d2739e5e7214ee93` |
| `tools/gv_container_layer_probe.py` | `tools/gv_container_layer_probe.py` | 21119 | `10f9ce325db86567fe82a5f8f51e12eb9fab5f66d3373859eb258d09144a85dd` |
| `tools/gv_crash_recovery.py` | `tools/gv_crash_recovery.py` | 32283 | `decfcbb26f00a67d3822df31ba24462225c7c88fd6ed8f9f3898ddd5e4b8d68d` |
| `tools/gv_discovery_eval.py` | `tools/gv_discovery_eval.py` | 26535 | `ceea9971efdde46976615759595859940002031feb996061a498505a446fe675` |
| `tools/gv_dom_probe.mjs` | `tools/gv_dom_probe.mjs` | 1473 | `f57583d1e018574d9eb7273e0966b51f48290f7d4cc3e5adfdf4de4acdb17017` |
| `tools/gv_eval.mjs` | `tools/gv_eval.mjs` | 1246 | `18629eef36b524e99c6d5d4ba31c96e711ce281faad4e56342c2775ed889d25b` |
| `tools/gv_eval_longhorizon.py` | `tools/gv_eval_longhorizon.py` | 21146 | `cfef091693a3933ceefce4501241b701b4ce7918c831dd7768919c657fa6fc22` |
| `tools/gv_goal_run_e2e.py` | `tools/gv_goal_run_e2e.py` | 36473 | `7a40799a13ef4141c0b7d94901d4656ac81bd4cabc3a6a6fb5db9a1616d32e8f` |
| `tools/gv_gui_image_rebuild.sh` | `tools/gv_gui_image_rebuild.sh` | 1249 | `fc0e30a53a848636789c5d2f0bd79f7750d80d415e1472acad0ef2997131bede` |
| `tools/gv_long_project_e2e.py` | `tools/gv_long_project_e2e.py` | 78528 | `819c9dc4c90240d4cb35541c636dd8828a05d88aeefe405f43ef5e480013c498` |
| `tools/gv_perceived_speed.py` | `tools/gv_perceived_speed.py` | 52431 | `f518c7a1fc44a59e2a973c8fd69117647b0c124d3b5bb2e6b5db969d8f8bdd58` |
| `tools/gv_perf_codex_compare.py` | `tools/gv_perf_codex_compare.py` | 45539 | `9e4155ff3e0ef1f0de1b0c7da74504b3bb0dbdb3a20b1b7cc4345210cf779aaf` |
| `tools/gv_plugin_validate.py` | `tools/gv_plugin_validate.py` | 1087 | `bd61f405f42ad3d2541ca27509b36ffc4c524b3d458ba58b84767b4c1a194220` |
| `tools/gv_product_run.py` | `tools/gv_product_run.py` | 9890 | `85b49087720017c57d1e7932c939676d74ac4e6a80629c1516f36ce8d8b6ff33` |
| `tools/gv_readme_refresh.py` | `tools/gv_readme_refresh.py` | 18290 | `29deb69323f2ac96ed98236909ac043206901a114de8e3a625462d168c728b77` |
| `tools/gv_schemathesis_run.py` | `tools/gv_schemathesis_run.py` | 69578 | `d48d78b9610eeb3e2bde16decc65d4b2f53c01262380fa511389aa9e92778d30` |
| `tools/gv_seed_turn.py` | `tools/gv_seed_turn.py` | 7052 | `f957ccca6c258007c6df5713bac1b31f71d5d0dcfc5d4f93d0e700510e042dd6` |
| `tools/gv_setup_proof.py` | `tools/gv_setup_proof.py` | 10148 | `49fbb2a93e60d9ad4f03b0e09a0a0fccad208fcfa4b09d86609dece3d1a2aa32` |
| `tools/gv_shot.mjs` | `tools/gv_shot.mjs` | 4047 | `a2c2879a8df9519a5e9abce6faff89896b220edf0f12e27b38e71d984983062e` |
| `tools/gv_stream_look.py` | `tools/gv_stream_look.py` | 28776 | `dae5b1e3b6ee841e1edbccad5534f9782fcef9f96fcaaa7f972e67c56bbb0d44` |
| `tools/gv_stream_perf.py` | `tools/gv_stream_perf.py` | 36686 | `46abad0922689803eede8f14b76e2f00e34a1eaaedc1a3d0e78b5d57c7380458` |
| `tools/gv_stream_ttfb.py` | `tools/gv_stream_ttfb.py` | 36123 | `1c934b31979ae6ae60fd44a92116f79f015478e211b05d261c745bc80a0d693a` |
| `tools/gv_tool_turn.py` | `tools/gv_tool_turn.py` | 2433 | `d8659a58dd1f9ce9c5a6e52e0899cd99d6c0520fd3a3713c4ad140827dbb5258` |
| `tools/gv_ui_audit.py` | `tools/gv_ui_audit.py` | 33256 | `ec18357cc5af007b50b93e38ab4ac4791db01529896231eab16be1b3850c0608` |
| `tools/gv_ui_e2e.mjs` | `tools/gv_ui_e2e.mjs` | 21991 | `a5b9bbd86500124b014609e91a8526b8a06ae4e876fdd6772574d041822b43dc` |
| `tools/gv_update_gate.py` | `tools/gv_update_gate.py` | 27490 | `f7e5201643e2ea66f08b31e9514b67e4290ab0919702e807e9c3f3eac53e6ff3` |
| `tools/gv_visual_pass.py` | `tools/gv_visual_pass.py` | 13550 | `e21e9ceafdde8f412563fb8ab6825720690356b6a33994491dd3b48bf3a0d46a` |
| `tools/gv_visual_pass.sh` | `tools/gv_visual_pass.sh` | 32378 | `5ce648b3dacd795290732ad0ea8016fe69b72598197f2a5981654c05752a2816` |
| `tools/gv_warm_perf.py` | `tools/gv_warm_perf.py` | 28513 | `2031c0f0ef9055155509422bb7406c8ab748a69f907f3592b6a5beaf2f82b67a` |
| `tools/gv_wave1_clean_install.py` | `tools/gv_wave1_clean_install.py` | 7189 | `b073bb02261878c61f1447c46c2e04f16a7d14568ad0791e02adc9eac6588e89` |
| `tools/gv_wave2_goal_probe.sh` | `tools/gv_wave2_goal_probe.sh` | 2708 | `f224aa3551a6cf749513bfafa9efe48777688aaf3e7b27ffda1498ae921e8186` |
| `tools/host_engine_regression_probe.py` | `tools/host_engine_regression_probe.py` | 4362 | `182e122876c605fe925c0fbb51c8865369e9a26d981e211f2ec0ad3edfbf1e46` |
| `tools/host_rf02_deadline_probe.py` | `tools/host_rf02_deadline_probe.py` | 4814 | `669e9216b0a4d72adfd2218d08717eebd2d50260a8310eaef22ed72c053c59a9` |
| `tools/host_rf04_retry_budget_probe.py` | `tools/host_rf04_retry_budget_probe.py` | 5670 | `bdaae40be0ef28ed09c89bf4273c7bc21fbe39181603361de21415b2ffb8111e` |
| `tools/host_rf05_fallback_probe.py` | `tools/host_rf05_fallback_probe.py` | 4805 | `5227f81c9563c45a9c0dd216f30f72be49bbdb1857dbc8e50442f8de70e70274` |
| `tools/host_rf07_interim_report_probe.py` | `tools/host_rf07_interim_report_probe.py` | 4581 | `17d880d5f273ec1a6cb5c013a6ca543e717ab6f927ccc99b2cd7721426e764ce` |
| `tools/host_rf08_segment_probe.py` | `tools/host_rf08_segment_probe.py` | 13146 | `ece2bc54a306f8442b0bc6356d08b2c1c6f6f0497368d49d43132bd850e06b21` |
| `tools/host_rf09_crash_probe.py` | `tools/host_rf09_crash_probe.py` | 8154 | `53e58516b6fc39c232bc80dae2ea4089189b0000cbf575ed2b9e26b0baf15c4f` |
| `tools/host_rf10_stop_probe.py` | `tools/host_rf10_stop_probe.py` | 5045 | `8538a36ee27963273ccb660fee1de645908c024621b1c33b3ea0801c089de404` |
| `tools/host_rf_monotonic_probe.py` | `tools/host_rf_monotonic_probe.py` | 3940 | `3198ad6d81656b19109ff51ad05c410e57eaeaab70723af2aad27696b2d85b7d` |
| `tools/injection_acceptance_probe.py` | `tools/injection_acceptance_probe.py` | 5655 | `7bda5f37b32c74a3ffbfe386a70aca42f924d3d1692a139733796e4069d7737b` |
| `tools/jev_acceptance_probe.py` | `tools/jev_acceptance_probe.py` | 9088 | `41721b2dd01d4c777250c6580ed638fac0920372509202dccfc54e5528ca2284` |
| `tools/jev_benchmark_arms.py` | `tools/jev_benchmark_arms.py` | 10629 | `363cb1d57e8aee7ad06fa70fe01c8f0ce7693ca83ef59e79b520990ce7160126` |
| `tools/jev_contract_probe.py` | `tools/jev_contract_probe.py` | 13283 | `d2be7139bbe0b5917abdeff9970d6960ae676e05146bf2b93955a2c88deb57d1` |
| `tools/perf-gv/README.md` | `tools/perf-gv/README.md` | 4898 | `2d79b8d30ecf134e2eb3c61bbdc3a34555c39fa3df54e201989e1e41d672d071` |
| `tools/perf-gv/baseline.json` | `tools/perf-gv/baseline.json` | 2203 | `cf0990b8b90e75796e01a44579d363945c051596cc86560c94a2224faffbc237` |
| `tools/perf-gv/lib/baseline.mjs` | `tools/perf-gv/lib/baseline.mjs` | 2599 | `e5d5bd8cbc5cda2cacb9988d9912fbe10a95dabe4a132880ba941401b508293c` |
| `tools/perf-gv/lib/cdp.mjs` | `tools/perf-gv/lib/cdp.mjs` | 6136 | `abb503313c5a4d3e3a91adf50886e4619e7c6f4b8191a516f84c2f63d176e2a9` |
| `tools/perf-gv/lib/launch.mjs` | `tools/perf-gv/lib/launch.mjs` | 16851 | `f498945c933b1a6eb957a9285a99a7f70ed13ddbc2463f8c138c9f700649bdee` |
| `tools/perf-gv/lib/paths.mjs` | `tools/perf-gv/lib/paths.mjs` | 777 | `8d8d04aa18d743d694332dbdfb75f763cd5c0ca68b6ac8e5c80479d28e32b9cd` |
| `tools/perf-gv/lib/stats.mjs` | `tools/perf-gv/lib/stats.mjs` | 2700 | `7e5861533bce2feeb4c7234628bfd8482e31c887a128af0eed85addcbf045f2e` |
| `tools/perf-gv/run.mjs` | `tools/perf-gv/run.mjs` | 12047 | `5153bec32298327d41a4aaafcef7740a94ae0a1541f13ded70446525d04fc76d` |
| `tools/perf-gv/scenarios/cold-start.mjs` | `tools/perf-gv/scenarios/cold-start.mjs` | 919 | `2392dbb5c422601590340fcf5fcd95d28aaad8030084431bf5aa2e88992eeec3` |
| `tools/perf-gv/scenarios/first-token.mjs` | `tools/perf-gv/scenarios/first-token.mjs` | 2909 | `a523c96d15a91dee13f157fa81c116d9781757a878f03745f40707857c70e257` |
| `tools/perf-gv/scenarios/idle-cost.mjs` | `tools/perf-gv/scenarios/idle-cost.mjs` | 11504 | `bc092839d00c1c8b0a95991ea8ca7bf20b822b342ad7a3c95330f0774bdfde07` |
| `tools/perf-gv/scenarios/index.mjs` | `tools/perf-gv/scenarios/index.mjs` | 1423 | `1fa40de2be6171e7659431b7d01af4892fb61e1dd87a7defd615a6bffbbcfec2` |
| `tools/perf-gv/scenarios/keystroke.mjs` | `tools/perf-gv/scenarios/keystroke.mjs` | 3249 | `b0154c0564ad4806654b42450464c06605210a18ab21a972fb7e3389fea21c56` |
| `tools/perf-gv/scenarios/multitab.mjs` | `tools/perf-gv/scenarios/multitab.mjs` | 18099 | `2f7068836a92d5543f2a3bf0a0bd3aec30b6e21e7ab08c5d33ba22c20ee0b63d` |
| `tools/perf-gv/scenarios/profile-switch.mjs` | `tools/perf-gv/scenarios/profile-switch.mjs` | 2136 | `e54803633c5fd8adb0127f3483d034595fcaa1cc3e2728964b407bd280720cc5` |
| `tools/perf-gv/scenarios/render-churn.mjs` | `tools/perf-gv/scenarios/render-churn.mjs` | 10102 | `0775f8331a7c82e366f29dcee580c1b93f09b843727157c7745bd999dc80a8e1` |
| `tools/perf-gv/scenarios/right-pane.mjs` | `tools/perf-gv/scenarios/right-pane.mjs` | 12008 | `28be5c27d4167db51dd14a282f6d87b0548835aef114708a3b2b84500195a326` |
| `tools/perf-gv/scenarios/session-load.mjs` | `tools/perf-gv/scenarios/session-load.mjs` | 4297 | `0cc3d9f5bd67db703447d7a272e6049397e00660db947def7e0b67f5b4d186a2` |
| `tools/perf-gv/scenarios/session-switch.mjs` | `tools/perf-gv/scenarios/session-switch.mjs` | 2401 | `a51e4d3a93a041957c63e2077a3206dd903595ce129ca5d207abb79f24c30d27` |
| `tools/perf-gv/scenarios/stream-history.mjs` | `tools/perf-gv/scenarios/stream-history.mjs` | 721 | `58b09a5c717ab09bb022bd3f03960733141525c5a1590f8546259b89e94925e7` |
| `tools/perf-gv/scenarios/stream.mjs` | `tools/perf-gv/scenarios/stream.mjs` | 8003 | `cee62468bfe19cc6efa49eae4c2f7cb6ca14ea53c9f5b2850e75a00227d6a2b5` |
| `tools/perf-gv/scenarios/submit.mjs` | `tools/perf-gv/scenarios/submit.mjs` | 3464 | `91fd9a63dd978d80f6a72fecf08e7abf9adef3c024d6ce30a78c88ba1d7ba909` |
| `tools/perf-gv/scenarios/transcript.mjs` | `tools/perf-gv/scenarios/transcript.mjs` | 1729 | `ba6c6edfc9589d4ca4a6413a5b0cc9b467ada02967f1db1e87ffc94aeac78076` |
| `tools/perf-gv/serve.mjs` | `tools/perf-gv/serve.mjs` | 1770 | `ac0a5c3d14b1a1e9de9e775e309d9d6ae82b8fe56d5e4c8511572148f0f9e15e` |
| `tools/provider_probe.py` | `tools/provider_probe.py` | 23562 | `c013474a9e1b58a6644d160c0e01093bec0e0cfd960b5112f1bb065e379e7458` |
| `tools/reference_catalog_probe.py` | `tools/reference_catalog_probe.py` | 8021 | `86c6cbda44c49c9512170534872e1cc48c720af59ab50c2b297fb4210be13981` |
| `tools/register_linkage_report.py` | `tools/register_linkage_report.py` | 3001 | `49dcb9d0409316c9202e8a291de8c85f0d37939af65027553703313c1ad27db2` |
| `tools/render_logo_art.py` | `tools/render_logo_art.py` | 3824 | `4ee9d7e0fcbf244feec10f0238fead3de2070537cef78bef005b6957db4787e7` |
| `tools/rf_measure.py` | `tools/rf_measure.py` | 10140 | `7ccafa2bf1846ef0302c085d4b3a510942d5d0ad20935067a3ef0fea9b9fa6e1` |
| `tools/rf_stage1_compose.sh` | `tools/rf_stage1_compose.sh` | 4170 | `dd478b8a0efd760a470e9844754b1e00d531d4641cc3d4250ab7202b7864abd5` |
| `tools/rf_status.py` | `tools/rf_status.py` | 2874 | `bb49f395df44d30467fcf8b5ff66b4f2c9915720fdefb179a8d45c38c2351e49` |
| `tools/rf_test_provider.py` | `tools/rf_test_provider.py` | 13722 | `84e77b51730125485ecddbf7ae43bee6e0317d026f9e4e57990c1cb02e0021b6` |
| `tools/schemas/pandoc-md-html.json` | `tools/schemas/pandoc-md-html.json` | 823 | `92b120f3b7cbb48e2db863d2204725f8ead876f24d2536fde1f28191d3e972fd` |
| `tools/setup_capture.py` | `tools/setup_capture.py` | 27813 | `0a43af46f2193c6ff8de52ebe354e344ce03a67bdcbafacc2f24e04707b040bc` |
| `tools/surface_close_capture.py` | `tools/surface_close_capture.py` | 12431 | `2608d585ce2841ef7f91a317cedf839ad4fbdc3df7f6e2c905e4e32e9e095f7a` |
| `tools/ui-demo/components.json` | `tools/ui-demo/components.json` | 427 | `2eecc6bff06df21cd23abbc609ff9d8a95952937eac186f9e0f7f0889ec29455` |
| `tools/ui-demo/index.html` | `tools/ui-demo/index.html` | 312 | `5b69909d1bc8d77e451b4adc410d5607bdcf2b473aa9baf1c4ad82f75f66494f` |
| `tools/ui-demo/package-lock.json` | `tools/ui-demo/package-lock.json` | 315187 | `b00ccdf59576c1678b61150ea1b8ec00ade180fe21dafb47cc3dd09651e3e3a9` |
| `tools/ui-demo/package.json` | `tools/ui-demo/package.json` | 1684 | `57667133fecfe02e8f7d86571c90ae98108fc09a973bdfa6fa1eca893cb9286a` |
| `tools/ui-demo/src/App.tsx` | `tools/ui-demo/src/App.tsx` | 38753 | `6ee555a54167e34222f4da024da481d3bb24c121d61d78ae32cd9aedc0f4f36f` |
| `tools/ui-demo/src/components/ai-elements/agent.tsx` | `tools/ui-demo/src/components/ai-elements/agent.tsx` | 3966 | `346548a76033e3e1bdec841b1ef2fc5569426ed29d5bdcac6c8f0af307a87b68` |
| `tools/ui-demo/src/components/ai-elements/artifact.tsx` | `tools/ui-demo/src/components/ai-elements/artifact.tsx` | 3362 | `b1ed83b8ef6f38dd045e33e7e7a0960bef43182f308e1169a9467c293f677917` |
| `tools/ui-demo/src/components/ai-elements/attachments.tsx` | `tools/ui-demo/src/components/ai-elements/attachments.tsx` | 11375 | `841c70af1fb6b7d284b4dcd5ab650a87f0ea9298cbc41e6f6f409e13c2dba155` |
| `tools/ui-demo/src/components/ai-elements/audio-player.tsx` | `tools/ui-demo/src/components/ai-elements/audio-player.tsx` | 6189 | `60207e30c5890777ade9519b6dc15d7f8e26104b940244c0876efad5b0a7dea7` |
| `tools/ui-demo/src/components/ai-elements/canvas.tsx` | `tools/ui-demo/src/components/ai-elements/canvas.tsx` | 618 | `7bc92905e97c182c9a2f52b421a4d70414c8700a66039af9a4466587f74d2687` |
| `tools/ui-demo/src/components/ai-elements/chain-of-thought.tsx` | `tools/ui-demo/src/components/ai-elements/chain-of-thought.tsx` | 6225 | `fc5dbf976c10d86514a3744a36fe9770d6efd7c0a8b7f011d713d88c5c4d4c59` |
| `tools/ui-demo/src/components/ai-elements/checkpoint.tsx` | `tools/ui-demo/src/components/ai-elements/checkpoint.tsx` | 1627 | `d107a68d9041d0076e1cb76cb386c5034c24225c3777ef8853fc1f355391a2a0` |
| `tools/ui-demo/src/components/ai-elements/code-block.tsx` | `tools/ui-demo/src/components/ai-elements/code-block.tsx` | 13803 | `691ccd82abf8c573b7b1f9393527990fe26a3d6b98999597b98974054668d8e9` |
| `tools/ui-demo/src/components/ai-elements/commit.tsx` | `tools/ui-demo/src/components/ai-elements/commit.tsx` | 9766 | `112005c9e471817420bc36b786389f35c402474bdf9e11d09e3e42e73de42647` |
| `tools/ui-demo/src/components/ai-elements/confirmation.tsx` | `tools/ui-demo/src/components/ai-elements/confirmation.tsx` | 3877 | `a52e00ccf4704bc20d8f778c06cc47ec7633c1f70bb5877e77562a89f1f180f8` |
| `tools/ui-demo/src/components/ai-elements/connection.tsx` | `tools/ui-demo/src/components/ai-elements/connection.tsx` | 570 | `3da80a551da8a7eb4949a0b64b5329af463037dc15e6945e60ae66dcd903e71f` |
| `tools/ui-demo/src/components/ai-elements/context.tsx` | `tools/ui-demo/src/components/ai-elements/context.tsx` | 9889 | `2b1e1a0fefbc7605e8287bbf2e761acb8f02595fbdfe111b73c5b6d9e91aebd6` |
| `tools/ui-demo/src/components/ai-elements/controls.tsx` | `tools/ui-demo/src/components/ai-elements/controls.tsx` | 573 | `bb5612a4ffeed5088c0934aabf2f11913f5ad3136e95f609109b09355ccddcbe` |
| `tools/ui-demo/src/components/ai-elements/conversation.tsx` | `tools/ui-demo/src/components/ai-elements/conversation.tsx` | 4413 | `1875e32cc23a6e6c7291baa3ca2b34b5086ef8ab680bf14b6372da64e9c69553` |
| `tools/ui-demo/src/components/ai-elements/edge.tsx` | `tools/ui-demo/src/components/ai-elements/edge.tsx` | 3223 | `f7929c87f5f2828d9e5cf65b9dffd73dec0a0fc8c8c0dce8f7ef55e33b610049` |
| `tools/ui-demo/src/components/ai-elements/environment-variables.tsx` | `tools/ui-demo/src/components/ai-elements/environment-variables.tsx` | 7922 | `c55b7988d7ea07cec2f5652e6f10219c3d89cb7dae6f7fed229e3a27f2903207` |
| `tools/ui-demo/src/components/ai-elements/file-tree.tsx` | `tools/ui-demo/src/components/ai-elements/file-tree.tsx` | 7652 | `3c3c7ea261b75cf90fc0b5341c05fa3930e87e359901f25f04a66cfc160543dc` |
| `tools/ui-demo/src/components/ai-elements/image.tsx` | `tools/ui-demo/src/components/ai-elements/image.tsx` | 493 | `9360ca3e8945a64cc8d5c71a9304d7c8371d66f52b535f838bfe3ef3f55291ce` |
| `tools/ui-demo/src/components/ai-elements/inline-citation.tsx` | `tools/ui-demo/src/components/ai-elements/inline-citation.tsx` | 6695 | `614f58ffe4ade7da284f6624b66ad12ea1bb559796c8a8ef0eb227708bf6a9a6` |
| `tools/ui-demo/src/components/ai-elements/jsx-preview.tsx` | `tools/ui-demo/src/components/ai-elements/jsx-preview.tsx` | 7442 | `fac1e742140fa2d2e30b07eb20fe60023a86596e5bb8a74dfb13130e7f3f5ada` |
| `tools/ui-demo/src/components/ai-elements/message.tsx` | `tools/ui-demo/src/components/ai-elements/message.tsx` | 8554 | `2affb1d58d6948cb254e6cec888c60dfa46f2f9b9936b3df3fb7fe4b3230ed74` |
| `tools/ui-demo/src/components/ai-elements/mic-selector.tsx` | `tools/ui-demo/src/components/ai-elements/mic-selector.tsx` | 9361 | `382bd6bea20c9389ba31f4cae42ca4b944a7263da89765a383f82113d694c98c` |
| `tools/ui-demo/src/components/ai-elements/model-selector.tsx` | `tools/ui-demo/src/components/ai-elements/model-selector.tsx` | 5050 | `42b19903e6589ca2b8880d1d331fe46d3b3676c08044d050bec971fe4d2feeff` |
| `tools/ui-demo/src/components/ai-elements/node.tsx` | `tools/ui-demo/src/components/ai-elements/node.tsx` | 1976 | `87560e6c1240d340f670ad8ec6ec54a67ad45868485aa47e10fb6c9b0a92c211` |
| `tools/ui-demo/src/components/ai-elements/open-in-chat.tsx` | `tools/ui-demo/src/components/ai-elements/open-in-chat.tsx` | 16612 | `859acab6dd2b27dd55d920b0d734418c825adbf9335942589c187ec16d3fe378` |
| `tools/ui-demo/src/components/ai-elements/package-info.tsx` | `tools/ui-demo/src/components/ai-elements/package-info.tsx` | 6043 | `90469499889abfe0e31247e57bec47c814c254a4b31f7b206bd5115b1e6cb243` |
| `tools/ui-demo/src/components/ai-elements/panel.tsx` | `tools/ui-demo/src/components/ai-elements/panel.tsx` | 401 | `cecadda0af7dc89b9fdf25acaa2617d8cbe1426d46afa86760f8c761fce4e515` |
| `tools/ui-demo/src/components/ai-elements/persona.tsx` | `tools/ui-demo/src/components/ai-elements/persona.tsx` | 8194 | `a643c12a4b76fb464c208cd0ce23ab8f2645bae469e6e72aec951d6a37c15522` |
| `tools/ui-demo/src/components/ai-elements/plan.tsx` | `tools/ui-demo/src/components/ai-elements/plan.tsx` | 3544 | `0185cc2068f8efa65959504375d48e1206186a99a767456bad63340d9eb43262` |
| `tools/ui-demo/src/components/ai-elements/prompt-input.tsx` | `tools/ui-demo/src/components/ai-elements/prompt-input.tsx` | 38934 | `6b032a8efeb41e9dcf7383518367da6b21c3c63e906543fc8c90e1a1d59b4f6d` |
| `tools/ui-demo/src/components/ai-elements/queue.tsx` | `tools/ui-demo/src/components/ai-elements/queue.tsx` | 6238 | `2bb5855e67a75ffffa5a2d2cd6a416e8fb6a698deb7b324b375aecf82af7e3a6` |
| `tools/ui-demo/src/components/ai-elements/reasoning.tsx` | `tools/ui-demo/src/components/ai-elements/reasoning.tsx` | 6257 | `efe4d92ee54441133bcb2d45cb327551719bcfd95ffd13e840adb98eaa0e8b8a` |
| `tools/ui-demo/src/components/ai-elements/sandbox.tsx` | `tools/ui-demo/src/components/ai-elements/sandbox.tsx` | 3404 | `a51c2f073151e148dfbf1d9675edeb932691d16661cf4edee76610202ac3bf73` |
| `tools/ui-demo/src/components/ai-elements/schema-display.tsx` | `tools/ui-demo/src/components/ai-elements/schema-display.tsx` | 12722 | `744cc77b6c14fece5da72dde75ae569d0e0f7f98b3e272cb697a8c8cb2b7c23b` |
| `tools/ui-demo/src/components/ai-elements/shimmer.tsx` | `tools/ui-demo/src/components/ai-elements/shimmer.tsx` | 2121 | `24bcb9b1dd45d616bd50dc6500e9f414ccabf440efb249e8d92522344b9057d6` |
| `tools/ui-demo/src/components/ai-elements/snippet.tsx` | `tools/ui-demo/src/components/ai-elements/snippet.tsx` | 3225 | `ddba8bade52a4eca51271d14e0158cfb08d969aed23e89f4f70889b82bd0aa2a` |
| `tools/ui-demo/src/components/ai-elements/sources.tsx` | `tools/ui-demo/src/components/ai-elements/sources.tsx` | 1858 | `99ec3be1614088f3a436701da05246e288e5f7c47129c5a109fd44e08e722b25` |
| `tools/ui-demo/src/components/ai-elements/speech-input.tsx` | `tools/ui-demo/src/components/ai-elements/speech-input.tsx` | 9615 | `fb76ddeed3b4797fad39dc8aebc645c45fa1713759b68eae356b3473b1321945` |
| `tools/ui-demo/src/components/ai-elements/stack-trace.tsx` | `tools/ui-demo/src/components/ai-elements/stack-trace.tsx` | 13787 | `64eb3491863f576a6fba991ff490202f548b79a41a81e07aa3d91cc8d5741a6c` |
| `tools/ui-demo/src/components/ai-elements/suggestion.tsx` | `tools/ui-demo/src/components/ai-elements/suggestion.tsx` | 1342 | `e9f9a951bf808895da7fb690921f6e4e04d777defe1f85dc8cc0881ccdcd344c` |
| `tools/ui-demo/src/components/ai-elements/task.tsx` | `tools/ui-demo/src/components/ai-elements/task.tsx` | 2345 | `1b566d9cda580442691acd5725a55c4c8e77edad3fe0181a51d3b222ae92a74c` |
| `tools/ui-demo/src/components/ai-elements/terminal.tsx` | `tools/ui-demo/src/components/ai-elements/terminal.tsx` | 6138 | `79cd3a90c39452bbd8e98915fa4f26cb62dccb66379f703460b7de3ccb09a5be` |
| `tools/ui-demo/src/components/ai-elements/test-results.tsx` | `tools/ui-demo/src/components/ai-elements/test-results.tsx` | 11436 | `1739ce52f8920326ff3ac36ecfd0e4d6c906d5a111455a6643280cd255dc1fac` |
| `tools/ui-demo/src/components/ai-elements/tool.tsx` | `tools/ui-demo/src/components/ai-elements/tool.tsx` | 5034 | `4e920248cf250ca34dc2e77792922bd8e393e26f8ba81c0e100008a285b73878` |
| `tools/ui-demo/src/components/ai-elements/toolbar.tsx` | `tools/ui-demo/src/components/ai-elements/toolbar.tsx` | 442 | `2f24af7cd4e37cba5031ba9e933549e98a8ed5ba64be062cefbeedba7d2c1521` |
| `tools/ui-demo/src/components/ai-elements/transcription.tsx` | `tools/ui-demo/src/components/ai-elements/transcription.tsx` | 3284 | `afebcf3542b91a6640d31996bf7fbb492d10ba9b1227d8eacc8b3959a1b47448` |
| `tools/ui-demo/src/components/ai-elements/voice-selector.tsx` | `tools/ui-demo/src/components/ai-elements/voice-selector.tsx` | 11095 | `2dbb1dda9e21e75c16f57deded10fd3220d8b6da0a6c9d41b65f814668606f38` |
| `tools/ui-demo/src/components/ai-elements/web-preview.tsx` | `tools/ui-demo/src/components/ai-elements/web-preview.tsx` | 7082 | `986bd134ab30fb062c5ba396928dbc92bfa80b35602ccff264c9e5711d036377` |
| `tools/ui-demo/src/components/ui/accordion.tsx` | `tools/ui-demo/src/components/ui/accordion.tsx` | 2024 | `8be83ff9ed96c00f4adea42021535a2b77aff7cf627ba9bc152f718e7cf625ec` |
| `tools/ui-demo/src/components/ui/alert.tsx` | `tools/ui-demo/src/components/ui/alert.tsx` | 1604 | `d03dcae5cdb63c381c1144ca9a0c49ba0e46466967d7deb94662034042acb034` |
| `tools/ui-demo/src/components/ui/avatar.tsx` | `tools/ui-demo/src/components/ui/avatar.tsx` | 2892 | `f757031690c648e3b115907bff6ded599076ea7245715d32b98dad2bd2153899` |
| `tools/ui-demo/src/components/ui/badge.tsx` | `tools/ui-demo/src/components/ui/badge.tsx` | 1766 | `13a9380dfff853ee730c14a52df49f22aecd31f40f2998d7c1e7ec458b6e1650` |
| `tools/ui-demo/src/components/ui/button-group.tsx` | `tools/ui-demo/src/components/ui/button-group.tsx` | 2193 | `a6e8aa70b17f29f6ec3011ac63ff89a4f4f117f9a1d4bfb9d7dcf25c68e0b33b` |
| `tools/ui-demo/src/components/ui/button.tsx` | `tools/ui-demo/src/components/ui/button.tsx` | 2382 | `79dd6f75f8136394442202d6b8b922fb269eaad0a5dba579397c9d5b41f893bb` |
| `tools/ui-demo/src/components/ui/card.tsx` | `tools/ui-demo/src/components/ui/card.tsx` | 1977 | `ef8305b12c3112dab42a4708b41b1ebcc042fa5ab4c20f039da8d45b09ed5059` |
| `tools/ui-demo/src/components/ui/carousel.tsx` | `tools/ui-demo/src/components/ui/carousel.tsx` | 5533 | `51317af5b3ea819ef588396b8ca2ab1e4715a6372a72ce299ca198750d45c7e6` |
| `tools/ui-demo/src/components/ui/collapsible.tsx` | `tools/ui-demo/src/components/ui/collapsible.tsx` | 781 | `eb4ee2a9ddb3c566c509753a79e4f4c9425d9f8474d55f61f8c8694928b6a509` |
| `tools/ui-demo/src/components/ui/command.tsx` | `tools/ui-demo/src/components/ui/command.tsx` | 4809 | `8063fbe58e0c7ca013403cb58871b7c2ce104dbf3d8f7b4162d4e7107db9abfa` |
| `tools/ui-demo/src/components/ui/dialog.tsx` | `tools/ui-demo/src/components/ui/dialog.tsx` | 4294 | `0d2e82ed5e7040abda714a0471aff54535e05bad6249da049cc0c3bf3ae024e5` |
| `tools/ui-demo/src/components/ui/dropdown-menu.tsx` | `tools/ui-demo/src/components/ui/dropdown-menu.tsx` | 8408 | `d954efb735f13734751207ad22cb0cddd7f6ec53c59d16675d103dae08cb61fb` |
| `tools/ui-demo/src/components/ui/hover-card.tsx` | `tools/ui-demo/src/components/ui/hover-card.tsx` | 1502 | `497d153d1a8f52a0e0d08ec7689a721b34016732ffbee09f864bed800933d5cf` |
| `tools/ui-demo/src/components/ui/input-group.tsx` | `tools/ui-demo/src/components/ui/input-group.tsx` | 5042 | `64ac3cc99fd037a38801516d6f0862c38d94a224413c0566621c99b87f8a523c` |
| `tools/ui-demo/src/components/ui/input.tsx` | `tools/ui-demo/src/components/ui/input.tsx` | 952 | `0c9457181f6ddc80969bcf854e92c362903a55b6e889fbef3fd85343ecc4af5b` |
| `tools/ui-demo/src/components/ui/popover.tsx` | `tools/ui-demo/src/components/ui/popover.tsx` | 2333 | `d8bfac795e9750a8994836b9f773ddceb4892730f9ee1e02e498fb6892c3be44` |
| `tools/ui-demo/src/components/ui/progress.tsx` | `tools/ui-demo/src/components/ui/progress.tsx` | 725 | `e012e1f7e6e66d11229d1233eccf91954edcd9179b418ff1d114dc651b86fd35` |
| `tools/ui-demo/src/components/ui/scroll-area.tsx` | `tools/ui-demo/src/components/ui/scroll-area.tsx` | 1615 | `20ad7c7f2cf5ef1bd9cb6a2c7ea5a81946465512906cec1700c55c4f28a4b6bb` |
| `tools/ui-demo/src/components/ui/select.tsx` | `tools/ui-demo/src/components/ui/select.tsx` | 6343 | `962830436bd0b7ee50e556f0747e277d6a86869ed27d167516346b15f26309d8` |
| `tools/ui-demo/src/components/ui/separator.tsx` | `tools/ui-demo/src/components/ui/separator.tsx` | 684 | `ad7d5403fe337160e41dbf04147896e448ae3a747b8d06660d627d74d60021bc` |
| `tools/ui-demo/src/components/ui/spinner.tsx` | `tools/ui-demo/src/components/ui/spinner.tsx` | 321 | `5d040fc49cb54c0296070644053b5a8840ddbfca89a430546bf308df3fffad65` |
| `tools/ui-demo/src/components/ui/switch.tsx` | `tools/ui-demo/src/components/ui/switch.tsx` | 1384 | `b43b0fd1a9a753d9aa777eeab68e468d37fa18a7ef74341c616c204160ea0390` |
| `tools/ui-demo/src/components/ui/tabs.tsx` | `tools/ui-demo/src/components/ui/tabs.tsx` | 3772 | `e1df6e2f95ece373ca909c1d54b8ba8cf4c47dd425c1784bbb9a01c38dd0936b` |
| `tools/ui-demo/src/components/ui/textarea.tsx` | `tools/ui-demo/src/components/ui/textarea.tsx` | 749 | `5cafcfeeee29534f449387a16b585f5923d2faab1a0cdc50a874416b175f59ce` |
| `tools/ui-demo/src/components/ui/tooltip.tsx` | `tools/ui-demo/src/components/ui/tooltip.tsx` | 1820 | `611e4305879b2773eafe76bf3c5caa69d8e47367fd97887a71634a27828f4433` |
| `tools/ui-demo/src/lib/utils.ts` | `tools/ui-demo/src/lib/utils.ts` | 217 | `06a8c921ea24873d8992b01e400c1310a69b926ff7a4d1b128763851cf6e89cd` |
| `tools/ui-demo/src/main.tsx` | `tools/ui-demo/src/main.tsx` | 180 | `f797b2b17093f5f970e593a828da258d2b5cb7e74d370d037249fac92d63073b` |
| `tools/ui-demo/src/skaldir/app/ChatBarApp.tsx` | `tools/ui-demo/src/skaldir/app/ChatBarApp.tsx` | 11386 | `08c6cf4a4131e90deff9881979028d212f2731c3dc0742c9e4f47cdcc81bdc3e` |
| `tools/ui-demo/src/skaldir/app/LeerPanels.tsx` | `tools/ui-demo/src/skaldir/app/LeerPanels.tsx` | 19549 | `80a306571fd07652abd3493a6422d4637db4ae26c6fb9397bc1b64a84010f277` |
| `tools/ui-demo/src/skaldir/chatbar.tsx` | `tools/ui-demo/src/skaldir/chatbar.tsx` | 12022 | `80778d9e1ecf7ce02c1c4ccbcae1c3ee4852e208fb27b9b4f045b533f4e56989` |
| `tools/ui-demo/src/skaldir/computer-use.tsx` | `tools/ui-demo/src/skaldir/computer-use.tsx` | 6119 | `8835210359297a8b49bf68e77a540ae50ecdbd13c807eeb5b4974c52ea874bc5` |
| `tools/ui-demo/src/skaldir/context-widget.tsx` | `tools/ui-demo/src/skaldir/context-widget.tsx` | 4530 | `262c3456843f41e4bf802d65001086bee1f8fe0b90ecd222b48c3863c62c456d` |
| `tools/ui-demo/src/skaldir/subagent-chat.tsx` | `tools/ui-demo/src/skaldir/subagent-chat.tsx` | 7017 | `feac9b28f430a54b7f458d06206aa7ee3edb164dc7e4b9b3f76e99c5598b70ed` |
| `tools/ui-demo/src/skaldir/subagent-panel.tsx` | `tools/ui-demo/src/skaldir/subagent-panel.tsx` | 7837 | `3b9b2d6a812dad53e966f27107e56de1a18fc7dab3bd25ec232276e3e4f30369` |
| `tools/ui-demo/src/styles.css` | `tools/ui-demo/src/styles.css` | 4598 | `de700724d5662048c1946ceac3d5b164a4b13ebfbae4d31abd12dfbf83228dff` |
| `tools/ui-demo/src/ui.tsx` | `tools/ui-demo/src/ui.tsx` | 3409 | `1f8136b93b3378d952bf5eca5f5f230b3ef2e0bcecedeb6a3975791f2665a791` |
| `tools/ui-demo/tsconfig.json` | `tools/ui-demo/tsconfig.json` | 458 | `8783e22afa62f5600a85aabc54693de07329dcbc13940b7a579cf338e978c8f6` |
| `tools/ui-demo/vite.config.ts` | `tools/ui-demo/vite.config.ts` | 381 | `7427702c9a6fbbc687871e4bb58b55c1434cd58e9490d658265c27a7fd13fc55` |
| `tools/ui-workbench/.claude/agents/motion-reviewer.md` | `tools/ui-workbench/.claude/agents/motion-reviewer.md` | 2447 | `00b4bbcb09e5990b800051545da28b8722b4f7b217d6aebcb2bb696a9cad74b1` |
| `tools/ui-workbench/.claude/skills/motion/SKILL.md` | `tools/ui-workbench/.claude/skills/motion/SKILL.md` | 4500 | `e9e9b884afe14b07c3c71fe707a5fa2407b2d645c75686402927496dd7e596f3` |
| `tools/ui-workbench/.claude/skills/motion/best-practices/base-ui.md` | `tools/ui-workbench/.claude/skills/motion/best-practices/base-ui.md` | 2737 | `cf1e79a446965c965a19a07e6c92527c8f661f87e0f16e321d4136fa7f1dd0de` |
| `tools/ui-workbench/.claude/skills/motion/best-practices/index.md` | `tools/ui-workbench/.claude/skills/motion/best-practices/index.md` | 2670 | `88f80af1af8e88c0eb9ad85ad70dd5f61c002c3a96055a05eeafc58bb2430bdf` |
| `tools/ui-workbench/.claude/skills/motion/best-practices/motion.md` | `tools/ui-workbench/.claude/skills/motion/best-practices/motion.md` | 947 | `92dee7c7b3d39ed02956102fbc0c9790e4f45a11b80bd5846a8336871b555d8a` |
| `tools/ui-workbench/.claude/skills/motion/best-practices/react.md` | `tools/ui-workbench/.claude/skills/motion/best-practices/react.md` | 2115 | `bdfbe7810d96e0b6ebfe36db3823027b1cc011285f7e2f60f7ad26c08bf4337f` |
| `tools/ui-workbench/.claude/skills/motion/best-practices/vue.md` | `tools/ui-workbench/.claude/skills/motion/best-practices/vue.md` | 1354 | `935e462042b5f02d086d680e757f29b5e0d3177781c3aab9cc3838eed88bd292` |
| `tools/ui-workbench/.claude/skills/motion/codex/index.md` | `tools/ui-workbench/.claude/skills/motion/codex/index.md` | 6143 | `590c1d3891019a501b77389dde78d9cb259e66bcb87746e5c7d06d8414fccaf6` |
| `tools/ui-workbench/.claude/skills/motion/css-spring/index.md` | `tools/ui-workbench/.claude/skills/motion/css-spring/index.md` | 2545 | `98ae2faa1c68b5d2527e1f6bb3d94a1fb0482d24099ad6f9c5cfcd3ebb430a85` |
| `tools/ui-workbench/.claude/skills/motion/performance-audit/index.md` | `tools/ui-workbench/.claude/skills/motion/performance-audit/index.md` | 1652 | `90cd82c9c1e492d529e30bfe457498cd83cf8bfd235ca5b9f0789e425248cbd4` |
| `tools/ui-workbench/.claude/skills/motion/transition-preview/index.md` | `tools/ui-workbench/.claude/skills/motion/transition-preview/index.md` | 2240 | `64962b3757518956e56599d31bea8f7bced604b4ab35a14e2e1cf3494aa87ace` |
| `tools/ui-workbench/.mcp.json` | `tools/ui-workbench/.mcp.json` | 200 | `c0ec7dc260199e18eeced0faf0a34e5ed286b5fa68cb18a690ca72182c26431f` |
| `tools/ui-workbench/README.md` | `tools/ui-workbench/README.md` | 2710 | `44e6ed9ab1bb3ec643ac555337eee90a3e9ce103fae6647cea30529fee113fdf` |
| `tools/ui-workbench/components.json` | `tools/ui-workbench/components.json` | 701 | `5dd0b8cd252e91b774b91bfa713d0872c1e2973a23b6b40021037cbf3750c683` |
| `tools/ui-workbench/cosmos.config.json` | `tools/ui-workbench/cosmos.config.json` | 182 | `942bdf7a69ebc857907bc1a2d2695fb910f171c36d117774714683d2936151c8` |
| `tools/ui-workbench/fixtures/AppChat.fixture.tsx` | `tools/ui-workbench/fixtures/AppChat.fixture.tsx` | 24939 | `992945a49d3c86deff90badba71a1b5c0c95d32902db3b3f2e84ba740788a009` |
| `tools/ui-workbench/fixtures/AppComposer.fixture.tsx` | `tools/ui-workbench/fixtures/AppComposer.fixture.tsx` | 18700 | `03d2b3764f07b6b2f2c5ce0f5fcca9b4e862ee4626a2165c577096e6236de584` |
| `tools/ui-workbench/fixtures/AppOverlays.fixture.tsx` | `tools/ui-workbench/fixtures/AppOverlays.fixture.tsx` | 27984 | `11b780465c12fe99fc4c13614cdb18688c5409836a52cc6cee3212cba62c10f6` |
| `tools/ui-workbench/fixtures/AppPanels.fixture.tsx` | `tools/ui-workbench/fixtures/AppPanels.fixture.tsx` | 29568 | `f61875b1f2058f3bd5093a638fdbd453233424f43924ebaaf6fda7944842f41b` |
| `tools/ui-workbench/fixtures/AppSettings.fixture.tsx` | `tools/ui-workbench/fixtures/AppSettings.fixture.tsx` | 31429 | `cd6055b11c2b15c8cd5c06c5ec7d96977261e32a7e55c7ccec3965b1e6499f7a` |
| `tools/ui-workbench/fixtures/AppShell.fixture.tsx` | `tools/ui-workbench/fixtures/AppShell.fixture.tsx` | 30697 | `7fe7c13d1b324c69d3309e1d8f7b8136ebe8e0d6070fd5b9b84da6231c88deaf` |
| `tools/ui-workbench/fixtures/ApprovalCards.fixture.tsx` | `tools/ui-workbench/fixtures/ApprovalCards.fixture.tsx` | 31705 | `f00eae466013ba9edca4aad4ebf9b2e33855285524fb12be16e6189c75fc3555` |
| `tools/ui-workbench/fixtures/ArtifactPreview.fixture.tsx` | `tools/ui-workbench/fixtures/ArtifactPreview.fixture.tsx` | 50137 | `fce8c51128f46d4610cea862a0b70e9d16e66f0d31780b1040c76ede167cba94` |
| `tools/ui-workbench/fixtures/CapabilityMining.fixture.tsx` | `tools/ui-workbench/fixtures/CapabilityMining.fixture.tsx` | 48839 | `1bc3164b8b71ecec77b50bd7b2abd8f96838794f79c8274cef8f8f411d7b78e0` |
| `tools/ui-workbench/fixtures/ChatActions.fixture.tsx` | `tools/ui-workbench/fixtures/ChatActions.fixture.tsx` | 58722 | `7cacba2f0ef281205519897f92dc1782be8d7dc2ed865f8b97cd6453aad602b2` |
| `tools/ui-workbench/fixtures/ChatBarRef.fixture.tsx` | `tools/ui-workbench/fixtures/ChatBarRef.fixture.tsx` | 24673 | `c77e168e22b7d32a7119b8f0d1e7ef0c6fff357a66faf2661cc9355a6d5c9d91` |
| `tools/ui-workbench/fixtures/ComputerUse.fixture.tsx` | `tools/ui-workbench/fixtures/ComputerUse.fixture.tsx` | 7637 | `61667407cb18d7de2fcdb12119d8ac6d518903190a1c7d5e767990917059e6d7` |
| `tools/ui-workbench/fixtures/ContextWidget.fixture.tsx` | `tools/ui-workbench/fixtures/ContextWidget.fixture.tsx` | 6697 | `d69ac58340911a1e1ecab0bf70dcc940000d33eba1eb354fe6b264ec13f2d9f5` |
| `tools/ui-workbench/fixtures/ErrorCards.fixture.tsx` | `tools/ui-workbench/fixtures/ErrorCards.fixture.tsx` | 14567 | `6fb8219ebcc0be3cb574638fafe53933fa8b6a5dfdc531c6a1bae233fd75ce85` |
| `tools/ui-workbench/fixtures/GoalGraph.fixture.tsx` | `tools/ui-workbench/fixtures/GoalGraph.fixture.tsx` | 46331 | `e4e6bfcef80c9f1b009667faa1ea8fa98ee416f530802f25cb30b00837e664c0` |
| `tools/ui-workbench/fixtures/GoalPact.fixture.tsx` | `tools/ui-workbench/fixtures/GoalPact.fixture.tsx` | 57591 | `2151ebba301e08c7023776bc6a6bfb22f2640dab5611d8e3921867d7417bb81f` |
| `tools/ui-workbench/fixtures/GvOrb.fixture.tsx` | `tools/ui-workbench/fixtures/GvOrb.fixture.tsx` | 3895 | `c92ae6442e596df2d0d7a2deab235e4d776fa876a58e91bf47eaaab35e6b87be` |
| `tools/ui-workbench/fixtures/Killswitch.fixture.tsx` | `tools/ui-workbench/fixtures/Killswitch.fixture.tsx` | 38546 | `019f7723392ef6a8721484434aa60951e5c23f8f37edfb16f7dbb380db11ef9c` |
| `tools/ui-workbench/fixtures/Onboarding.fixture.tsx` | `tools/ui-workbench/fixtures/Onboarding.fixture.tsx` | 60392 | `be579e5b3d292f63e86180d3f5e30daf3374465b259fed73f2836e3f9becf5c0` |
| `tools/ui-workbench/fixtures/OrbWidgets.fixture.tsx` | `tools/ui-workbench/fixtures/OrbWidgets.fixture.tsx` | 7672 | `8d65769eb17952515fb1fcb0e17662f226d175c83644bda2d937bc7e035906da` |
| `tools/ui-workbench/fixtures/PanelManager.fixture.tsx` | `tools/ui-workbench/fixtures/PanelManager.fixture.tsx` | 33586 | `9aa4ceefd7ef7d78c18ab044bc172ae75137a483f9b4ae400c0e677e0a05e3eb` |
| `tools/ui-workbench/fixtures/Primitives.fixture.tsx` | `tools/ui-workbench/fixtures/Primitives.fixture.tsx` | 4276 | `5c5c6e4d1f156de0786b6923ff166c16b9a4cbeb59f0a24be7c4a6c2bc4d6598` |
| `tools/ui-workbench/fixtures/SettingsAndSessions.fixture.tsx` | `tools/ui-workbench/fixtures/SettingsAndSessions.fixture.tsx` | 31061 | `e684cebe1f5bcdf7be6839138be6447c04e88e01cdabd77e3a848b9d63bfe707` |
| `tools/ui-workbench/fixtures/SkaldirApp.fixture.tsx` | `tools/ui-workbench/fixtures/SkaldirApp.fixture.tsx` | 52852 | `286ed485013c3e15f5031e84833f506bac567c3f48cbda9a8f0fe9ba35011829` |
| `tools/ui-workbench/fixtures/SkaldirFullUI.fixture.tsx` | `tools/ui-workbench/fixtures/SkaldirFullUI.fixture.tsx` | 54331 | `2e9b74ab33c05ef0db90600037da242732f7bc1160ec2a6c06964ca094267563` |
| `tools/ui-workbench/fixtures/SourceCards.fixture.tsx` | `tools/ui-workbench/fixtures/SourceCards.fixture.tsx` | 26130 | `c217c9e2ce10f2234f6ccb4235f02f984dcad051fbcf3b550485c1c977b8b9bf` |
| `tools/ui-workbench/fixtures/StartAndLogo.fixture.tsx` | `tools/ui-workbench/fixtures/StartAndLogo.fixture.tsx` | 19234 | `2b1fcc62530ba2d8a71a6c4685a8bf00f350e21cfe25c9a6b0161771530d4843` |
| `tools/ui-workbench/fixtures/StateRows.fixture.tsx` | `tools/ui-workbench/fixtures/StateRows.fixture.tsx` | 34786 | `6411feeaace6483a7f6bf75e2ebf00ef56a1721d0320a746b66f529587cb44ba` |
| `tools/ui-workbench/fixtures/SubagentChat.fixture.tsx` | `tools/ui-workbench/fixtures/SubagentChat.fixture.tsx` | 8355 | `5876b14c100e6494edd44d8ad3d7d9bd55f4dd44bf46a008b6c3a725bee0ab6e` |
| `tools/ui-workbench/fixtures/SubagentPanel.fixture.tsx` | `tools/ui-workbench/fixtures/SubagentPanel.fixture.tsx` | 8677 | `1501e5fb5c7f5a0071f40cbf2a543fcdcb7907e6e980730badb9ae4ec23da7b1` |
| `tools/ui-workbench/fixtures/ThinkingOrbs.fixture.tsx` | `tools/ui-workbench/fixtures/ThinkingOrbs.fixture.tsx` | 6207 | `b944b072b9b5189cb93ce9b7aa8858c4ebd9eaad2c91e84179ca3e32c2012b89` |
| `tools/ui-workbench/fixtures/ToolCards.fixture.tsx` | `tools/ui-workbench/fixtures/ToolCards.fixture.tsx` | 23618 | `d3793b2adc3db4e51e99e073c9ac01429a421c366d5e42311ee2a22eec5e8a92` |
| `tools/ui-workbench/fixtures/VoiceLines.fixture.tsx` | `tools/ui-workbench/fixtures/VoiceLines.fixture.tsx` | 30130 | `3c2735c728742f3c0c8a8ca7fcd61b0d3716664e7e6bce6f70e54f99c6f120b9` |
| `tools/ui-workbench/fixtures/Widgets-Chat.fixture.tsx` | `tools/ui-workbench/fixtures/Widgets-Chat.fixture.tsx` | 9905 | `89653aae4b70a5c49562b675d9a531e7b640ef3e8cefcc02a332991281e454ce` |
| `tools/ui-workbench/fixtures/_barrierefrei.tsx` | `tools/ui-workbench/fixtures/_barrierefrei.tsx` | 4521 | `42b7bd3dae3378dde652c54addcc0c3e69d461dff9940fb20ef0558318df260f` |
| `tools/ui-workbench/fixtures/_tokens.ts` | `tools/ui-workbench/fixtures/_tokens.ts` | 1795 | `b78a26b1ead6408ceca5a597aa75b63aba04fb4f09287c9b5dec23d3ea4e38fc` |
| `tools/ui-workbench/index.html` | `tools/ui-workbench/index.html` | 313 | `dda99688e584f5db7c2134775cd931675880be37549f77cfdcea63f199918b1a` |
| `tools/ui-workbench/package-lock.json` | `tools/ui-workbench/package-lock.json` | 130833 | `bd609fe7ad9a02de6a2a1f904d9a0ea3e5f0ec9ce20a80d3e33467c4da5bef5b` |
| `tools/ui-workbench/package.json` | `tools/ui-workbench/package.json` | 621 | `e3c609ea1b3fad5c28130a0b65a19a8b16df01b9f5a8ded9424dd0fb8cde2f3e` |
| `tools/ui-workbench/vite.config.ts` | `tools/ui-workbench/vite.config.ts` | 1199 | `fb46b885cef63ef1e6952129ea72757723feea20c7af4a8bf528925f9377da94` |
| `tools/vendor_thinking_orbs.mjs` | `tools/vendor_thinking_orbs.mjs` | 6812 | `8d6c3c651d259ed60ef3203eb381d3ee4dca74ff99330654ac9f8ee8172b697c` |
| `uv.lock` | `uv.lock` | 315861 | `827d45aea30543be3d7a36bff8d005619a7d1abc093a9cc72b3528ae425ea543` |

## 4. Ausgeschlossene Pfade (Auszug, Gruppierung)

| Grund | Wurzel | Anzahl |
|---|---|---|
| generated tree outputs/ (Testoutputs, Screenshots) | `outputs/` | 1315 |
| rohes Probe-Evidence-Log (audit/evidence/) | `audit/` | 290 |
| generated tree ui-artifacts/ (Testoutputs, Screenshots) | `ui-artifacts/` | 42 |
| Runtime-Datenbank / Live-Runlog | `cu_runs.jsonl/` | 1 |
| Runtime-Datenbank / Live-Runlog | `events.db/` | 1 |

Nicht generierte Einzelausschlüsse:

- `cu_runs.jsonl` — Runtime-Datenbank / Live-Runlog
- `events.db` — Runtime-Datenbank / Live-Runlog

Die vollständige Ausschlussliste steht in `logs/migration-excluded.txt` (git-ignored) und ist über `git ls-files`/`git ls-files --others --exclude-standard` in der Quelle am oben genannten HEAD reproduzierbar.

