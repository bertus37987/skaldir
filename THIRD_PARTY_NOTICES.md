# Third-Party Notices — God's Voice (`godsv`)

God's Voice v1.0.0 — Copyright (c) 2026 Willy (bertus37987).
Dieses Projekt steht unter der **Apache License 2.0** (siehe `LICENSE`).

## Python-Abhängigkeiten

Die folgende Liste dokumentiert alle Dritt-Pakete (inkl. Entwicklungs- und
transitiver Abhängigkeiten) des Entwicklungs-Umfelds (`pyproject.toml`-Kern:
`PyYAML`; Extras: `mcp` → `fastmcp`, `treasury` → `cryptography`; `dev`:
`pytest`, `ruff`, `bandit`). Lizenztexte liegen jeweils unter
`*.dist-info/licenses/` bzw. als `LICENSE`-Dateien in den Paketen.

| Paket | Version | Lizenz | Kategorie | Zweck |
|---|---|---|---|---|
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause | direkt | Krypto-Primitive — Extras `treasury` und `dev`; zusätzlich transitiv via fastmcp (Keyring/JWT/Authlib) |
| fastmcp | 4.0.3 | Apache-2.0 | direkt | MCP-Server/Client-Framework (Extra `mcp`) |
| PyYAML | 6.0.3 | MIT | direkt | YAML-Parser/-Emitter — Kern-Abhängigkeit (Konfiguration/Steuerdateien); auch von bandit und fastmcp-slim[server] genutzt |
| bandit | 1.9.4 | Apache-2.0 | dev | Security-Scanner (SAST, Extra `dev`) |
| pytest | 8.4.2 | MIT | dev | Test-Framework (Extra `dev`) |
| ruff | 0.16.7 | MIT | dev | Linter/Formatter (Extra `dev`; Rust-Binary) |
| aiofile | 3.12.3 | Apache-2.0 | transitiv | Asynchrone Datei-I/O (via py-key-value-aio[filetree]) [via fastmcp] |
| annotated-types | 0.8.0 | MIT | transitiv | Typ-Constraints für Pydantic [via fastmcp] |
| anyio | 4.15.1 | MIT | transitiv | Async-Framework auf asyncio/Trio (via mcp) [via fastmcp] |
| attrs | 26.1.0 | MIT | transitiv | Klassen ohne Boilerplate (via jsonschema) [via fastmcp] |
| Authlib | 1.8.0 | BSD-3-Clause | transitiv | OAuth/OIDC-Client- und Server-Funktionen (via fastmcp-slim[client,server]) [via fastmcp] |
| backports.tarfile | 1.2.0 | MIT | transitiv | tarfile-Backport für Python <3.12 (via jaraco.context) [via fastmcp] |
| beartype | 0.22.9 | MIT | transitiv | Runtime-Typprüfer (via py-key-value-aio) [via fastmcp] |
| cachetools | 7.1.8 | MIT | transitiv | Memoizing-/Cache-Collections (via py-key-value-aio) [via fastmcp] |
| caio | 0.12.4 | Apache-2.0 | transitiv | Asynchrone Datei-I/O Linux/macOS/Windows (via aiofile) [via fastmcp] |
| cffi | 2.1.1 | MIT-0 | transitiv | C Foreign Function Interface (via cryptography) [via cryptography, fastmcp] |
| click | 8.5.0 | BSD-3-Clause | transitiv | CLI-Toolkit (via uvicorn) [via fastmcp] |
| cyclopts | 4.25.2 | Apache-2.0 | transitiv | CLI-Framework auf Basis von Type-Hints (via fastmcp-slim[server]) [via fastmcp] |
| dnspython | 2.8.0 | ISC | transitiv | DNS-Toolkit (via email-validator) [via fastmcp] |
| docstring_parser | 0.18.0 | MIT | transitiv | Docstring-Parsing reST/Google/NumPy (via cyclopts) [via fastmcp] |
| email-validator | 2.3.0 | Unlicense | transitiv | E-Mail-Adressvalidierung (via pydantic[email]) [via fastmcp] |
| exceptiongroup | 1.3.1 | MIT | transitiv | PEP-654-ExceptionGroups-Backport (via fastmcp-slim[client,server]) [via fastmcp] |
| fastmcp-slim | 4.0.3 | Apache-2.0 | transitiv | FastMCP-Kern ohne Extras — von fastmcp mit [client,server] eingebunden [via fastmcp] |
| griffelib | 2.3.0 | ISC | transitiv | Code-Signatur-/Struktur-Analyse, Griffe-Fork (via fastmcp-slim[server]) [via fastmcp] |
| h11 | 0.16.0 | MIT | transitiv | HTTP/1.1-Protokollimplementation (via httpcore2) [via fastmcp] |
| httpcore2 | 2.12.0 | BSD-3-Clause | transitiv | Low-Level-HTTP-Transport (via httpx2) [via fastmcp] |
| httpx2 | 2.12.0 | BSD-3-Clause | transitiv | HTTP-Client (via mcp) [via fastmcp] |
| idna | 3.19 | BSD-3-Clause | transitiv | Internationalisierte Domainnamen (via mcp/httpx2) [via fastmcp] |
| importlib_metadata | 9.0.1 | Apache-2.0 | transitiv | Metadaten-Backport (via keyring) [via fastmcp] |
| iniconfig | 2.3.0 | MIT | transitiv | INI-Parsing für Testkonfiguration (via pytest) [via pytest] |
| jaraco.classes | 3.4.0 | MIT | transitiv | Klassen-Utilities (via keyring) [via fastmcp] |
| jaraco.context | 6.1.2 | MIT | transitiv | Context-Manager/Decorators (via keyring) [via fastmcp] |
| jaraco.functools | 4.6.0 | MIT | transitiv | Functools-Erweiterungen (via keyring) [via fastmcp] |
| jeepney | 0.9.0 | MIT | transitiv | D-Bus-Wrapper, Linux (via keyring → SecretStorage) [via fastmcp] |
| joserfc | 1.7.5 | BSD-3-Clause | transitiv | JOSE-RFCs: JWS/JWE/JWK/JWA/JWT (via authlib) [via fastmcp] |
| jsonref | 1.1.0 | MIT | transitiv | JSON-Reference-Auflösung (via fastmcp-slim[server]) [via fastmcp] |
| jsonschema | 4.26.0 | MIT | transitiv | JSON-Schema-Validierung (via mcp) [via fastmcp] |
| jsonschema-path | 0.5.0 | Apache-2.0 | transitiv | JSON-Schema-Zugriff über Pfade (via fastmcp-slim[server]) [via fastmcp] |
| jsonschema-specifications | 2025.9.1 | MIT | transitiv | JSON-Schema-Metaschemas als Registry (via jsonschema) [via fastmcp] |
| keyring | 25.7.0 | MIT | transitiv | Credential-/Secret-Speicher (via py-key-value-aio[keyring]) [via fastmcp] |
| markdown-it-py | 4.2.0 | MIT | transitiv | Markdown-Parser (via rich) [via fastmcp, bandit] |
| mcp | 2.2.0 | MIT | transitiv | Offizielles MCP-Python-SDK (via fastmcp-slim) [via fastmcp] |
| mcp-types | 2.2.0 | MIT | transitiv | MCP-Wire-Typen (via mcp) [via fastmcp] |
| mdurl | 0.1.2 | MIT | transitiv | Markdown-URL-Utilities (via markdown-it-py) [via fastmcp, bandit] |
| more-itertools | 11.1.0 | MIT | transitiv | Weitere Iterator-Utilities (via jaraco.*) [via fastmcp] |
| openapi-pydantic | 0.5.1 | MIT | transitiv | OpenAPI-Schemas als Pydantic-Modelle (via fastmcp-slim[server]) [via fastmcp] |
| opentelemetry-api | 1.44.0 | Apache-2.0 | transitiv | Tracing-/Telemetrie-API (via mcp) [via fastmcp] |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | transitiv | Versions-/Requirement-Utilities (via pytest; auch fastmcp-slim[server]) [via fastmcp, pytest] |
| pathable | 0.6.0 | Apache-2.0 | transitiv | Objektorientierte Pfadzugriffe (via jsonschema-path) [via fastmcp] |
| platformdirs | 4.11.8 | MIT | transitiv | Plattform-Standardverzeichnisse (via fastmcp-slim) [via fastmcp] |
| pluggy | 1.6.0 | MIT | transitiv | Plugin-/Hook-System (via pytest) [via pytest] |
| py-key-value-aio | 0.4.5 | Apache-2.0 | transitiv | Async-Key-Value-Stores (via fastmcp-slim[client,server]) [via fastmcp] |
| pycparser | 3.0 | BSD-3-Clause | transitiv | C-Parser in Python (via cffi) [via cryptography, fastmcp] |
| pydantic | 2.13.5 | MIT | transitiv | Datenvalidierung über Type-Hints (via fastmcp-slim u. a.) [via fastmcp] |
| pydantic-settings | 2.15.0 | MIT | transitiv | Settings-Management (via fastmcp-slim) [via fastmcp] |
| pydantic_core | 2.46.5 | MIT | transitiv | Pydantic-Validierungskern in Rust (via pydantic) [via fastmcp] |
| Pygments | 2.21.0 | BSD-2-Clause | transitiv | Syntax-Highlighting (via rich/pytest) [via fastmcp, pytest, bandit] |
| PyJWT | 2.14.0 | MIT | transitiv | JWT-Implementierung (via mcp; [crypto]-Extra → cryptography) [via fastmcp] |
| pyperclip | 1.11.0 | BSD-3-Clause ⚠ | transitiv | Clipboard-Zugriff (via fastmcp-slim[server]) [via fastmcp] |
| python-dotenv | 1.2.3 | BSD-3-Clause | transitiv | .env-Loader (via fastmcp-slim) [via fastmcp] |
| python-multipart | 0.0.32 | Apache-2.0 | transitiv | multipart/form-data-Parser (via mcp) [via fastmcp] |
| referencing | 0.37.0 | MIT | transitiv | JSON-Referencing (via jsonschema) [via fastmcp] |
| rich | 15.0.0 | MIT | transitiv | Terminal-Rendering (via fastmcp-slim; auch bandit) [via fastmcp, bandit] |
| rich-rst | 2.1.0 | MIT | transitiv | reStructuredText-Renderer für rich (via cyclopts) [via fastmcp] |
| rpds-py | 2026.6.3 | MIT | transitiv | Persistente Datenstrukturen in Rust (via jsonschema/referencing) [via fastmcp] |
| SecretStorage | 3.5.0 | BSD-3-Clause | transitiv | FreeDesktop Secret Service, Linux (via keyring) [via fastmcp] |
| sse-starlette | 3.4.11 | BSD-3-Clause | transitiv | Server-Sent-Events für Starlette (via mcp) [via fastmcp] |
| starlette | 1.6.0 | BSD-3-Clause | transitiv | ASGI-Framework (via mcp) [via fastmcp] |
| stevedore | 5.9.1 | Apache-2.0 | transitiv | Dynamisches Plugin-Loading (via bandit) [via bandit] |
| truststore | 0.10.4 | MIT | transitiv | Native System-Truststores (via httpx2/httpcore2) [via fastmcp] |
| typing-inspection | 0.4.4 | MIT | transitiv | Runtime-Typ-Introspektion (via mcp/pydantic) [via fastmcp] |
| typing_extensions | 4.16.0 | PSF-2.0 | transitiv | Typ-Hint-Backports (via fastmcp-slim u. a.) [via fastmcp] |
| uncalled-for | 0.4.0 | MIT | transitiv | Asynchrone Dependency-Injection (via fastmcp-slim[server]) [via fastmcp] |
| uvicorn | 0.52.4 | BSD-3-Clause | transitiv | ASGI-Server (via mcp) [via fastmcp] |
| watchfiles | 1.2.0 | MIT | transitiv | Datei-Watching/Reload (via fastmcp-slim[server]) [via fastmcp] |
| websockets | 17.1 | BSD-3-Clause | transitiv | WebSocket-Protokoll (via fastmcp-slim[server]) [via fastmcp] |
| zipp | 4.1.0 | MIT | transitiv | pathlib-Wrapper für Zip-Dateien (via importlib-metadata) [via fastmcp] |

## Hinweise

- **Auswahl:** `pyperclip` wird in den Metadaten nur generisch als „BSD" geführt;
  der mitgelieferte Lizenztext entspricht BSD-3-Clause (Platzhalter unausgefüllt) —
  vor Weitergabe gegen Upstream bestätigen. `cryptography` (Apache-2.0 OR
  BSD-3-Clause) und `packaging` (Apache-2.0 OR BSD-2-Clause) sind dual lizenziert.
- **Host-Abhängigkeit:** God's Voice läuft als Plugin auf **Hermes Agent**
  (MIT-Lizenz, separates Projekt — https://github.com/NousResearch/hermes-agent).
  Es wird kein Hermes-Code eingebettet oder weitergegeben; die Integration nutzt
  die dokumentierte Plugin-Schnittstelle.
- **Systemwerkzeuge (optional, nicht gebündelt):** `bwrap` (Sandbox),
  `podman` (Container-Isolation), ImageMagick (`convert`, Icon-Skalierung),
  `sqlite3`/`fcntl` (Python-Stdlib, PSF-2.0).
- **Eigene Artefakte:** `src/godsv/assets/icon.png` (Desktop-Icon) und
  `src/godsv/assets/logo.ansi` (CLI-Banner) sind eigene Werke des Projekts,
  keine Dritt-Inhalte.
- **Prompt-/Textinhalte:** Alle Prompt-Dateien unter `prompts/` sind Eigenent-
  wicklungen des Projekts.

---

# Third-Party Notices — appendix (English)

Appended 2026-09-18 for godsv **1.5.0** (the German section above documents the Python
environment as of 1.0.0 and is kept unchanged). Sources for every line below: the npm lockfile of
the desktop shell, each package's own `package.json` / `LICENSE*` on disk, `dist-info` metadata of
the installed Python environment, and the GitHub license API plus raw `LICENSE` files for projects
that are not packages. Where a license could not be read it is marked
`UNKNOWN — must be resolved before shipping`.

Full audit, per-component purpose and the shipping-blocker list: `MVP/07-credits.md`.

## A. Desktop shell (Electron) — declared dependencies

103 declared dependencies, all resolved through the npm lockfile of the desktop tree. Grouped by the
license read from the registry/lockfile; every non-MIT entry was verified against the package's own
on-disk license text.

### MIT (92)

`@assistant-ui/core` 0.2.23, `@assistant-ui/react` 0.14.24, `@assistant-ui/react-streamdown` 0.3.5,
`@audiowave/react` 0.6.2, `@babel/core` 7.29.7, `@chenglou/pretext` 0.0.6, `@codemirror/commands` 6.10.4,
`@codemirror/language` 6.12.4, `@codemirror/language-data` 6.5.2, `@codemirror/state` 6.7.1,
`@codemirror/view` 6.43.6, `@dnd-kit/core` 6.3.1, `@dnd-kit/sortable` 10.0.0, `@dnd-kit/utilities` 3.2.2,
`@electron/rebuild` 4.2.0, `@icons-pack/react-simple-icons` 13.11.1, `@lezer/highlight` 1.2.3,
`@nanostores/react` 1.1.0, `@nous-research/ui` 0.18.2, `@rolldown/plugin-babel` 0.2.3,
`@tabler/icons-react` 3.44.0, `@tailwindcss/typography` 0.5.20, `@tailwindcss/vite` 4.3.3,
`@tanstack/react-query` 5.101.2, `@tanstack/react-virtual` 3.14.6, `@testing-library/dom` 10.4.1,
`@testing-library/react` 16.3.2, `@types/babel__core` 7.20.5, `@types/d3-force` 3.0.10,
`@types/hast` 3.0.5, `@types/node` 22.20.1, `@types/qrcode` 1.5.6, `@types/react` 19.2.17,
`@types/react-dom` 19.2.3, `@vitejs/plugin-react` 6.0.3, `@xterm/addon-fit` 0.11.0,
`@xterm/addon-serialize` 0.14.0, `@xterm/addon-unicode11` 0.9.0, `@xterm/addon-web-links` 0.12.0,
`@xterm/addon-webgl` 0.19.0, `@xterm/xterm` 6.0.0, `babel-plugin-react-compiler` 1.0.0, `bippy` 0.5.43,
`blobatar` 2.0.0, `clsx` 2.1.1, `cmdk` 1.1.1, `concurrently` 10.0.4, `cross-env` 10.1.0,
`dnd-core` 14.0.1, `driver.js` 1.8.0, `electron` 40.10.2, `electron-builder` 26.15.3,
`emojibase-data` 16.0.3, `esbuild` 0.28.1, `fflate` 0.8.3, `frimousse` 0.3.0, `get-windows` 9.3.0,
`hast-util-from-html-isomorphic` 2.0.0, `hast-util-to-text` 4.0.2, `ignore` 5.3.2, `jsdom` 29.1.1,
`katex` 0.16.47, `mermaid` 11.16.1, `motion` 12.42.2, `nanostores` 1.4.2, `node-pty` 1.1.0,
`prettier` 3.9.5, `qrcode` 1.5.4, `radix-ui` 1.6.7, `rcedit` 5.0.2, `react` 19.2.7,
`react-arborist` 3.13.2, `react-dnd-html5-backend` 14.1.0, `react-dom` 19.2.7, `react-router` 8.3.0,
`react-shiki` 0.9.3, `remark-math` 6.0.0, `shiki` 4.3.1, `simple-git` 3.36.0, `tailwind-merge` 3.6.0,
`tailwindcss` 4.3.3, `tsx` 4.23.1, `tw-shimmer` 0.4.12, `unicode-animations` 1.0.3, `unified` 11.0.5,
`unist-util-visit-parents` 6.0.2, `use-stick-to-bottom` 1.1.6, `vfile` 6.0.3, `vite` 8.2.0,
`vitest` 4.1.10, `wait-on` 9.0.10, `web-haptics` 0.0.6

`radix-ui` pulls in the full `@radix-ui/react-*` primitive set (accordion, dialog, dropdown-menu,
popover, select, tabs, tooltip and ~50 more) — all MIT.

Tailwind CSS ships its license banner inside the built stylesheet:
`/*! tailwindcss v4.3.3 | MIT License | https://tailwindcss.com */`.

### Apache-2.0 (7)

`@playwright/test` 1.62.1, `@streamdown/code` 1.1.1, `@streamdown/math` 1.0.2,
`class-variance-authority` 0.7.1, `remend` 1.3.0, `streamdown` 2.5.0, `typescript` 6.0.3

### ISC (1)

`d3-force` 3.0.0

### CC-BY-4.0 (1) — attribution required in the product

`@vscode/codicons` 0.0.45 — icon font and icons under **CC-BY-4.0**, source code under **MIT**
(the package ships both `LICENSE` and `LICENSE-CODE`). Credit:
*VS Code Codicons, © Microsoft Corporation, licensed under CC BY 4.0.* This component ships inside
the application bundle, so the attribution must also appear in the in-app credits.

### Dual-licensed — election recorded

`dompurify` 3.4.13 — `(MPL-2.0 OR Apache-2.0)`. **We elect Apache-2.0**; the license text shipped
in the package is the Apache-2.0 text. doMPurify is used unmodified.

### Internal workspace package

`@hermes/shared` (private workspace package of the Hermes tree we build from; the tree itself is MIT).

## B. Browser / Electron stack

| Component | Version | License | License text shipped with the app |
|---|---|---|---|
| Electron | 40.10.2 | MIT | `LICENSE.electron.txt` (next to the app binary) |
| Chromium | 144.0.7559.236 | BSD-3-Clause and other Chromium third-party licenses | `LICENSES.chromium.html` (≈15.7 MB, next to the app binary) |
| Node.js | embedded in Electron | MIT (covered by Electron's license) | `LICENSE.electron.txt` |

**These files are part of the release directory and must never be stripped** by a custom
`files`/`extraResources` filter.

## C. Bundled fonts

| Font | License | Notice |
|---|---|---|
| Geist Sans, Geist Mono | SIL Open Font License 1.1 | © 2023 Vercel, made in collaboration with basement.studio. Vendored from `@fontsource/geist-sans@5.3.0` / `@fontsource/geist-mono@5.3.0`. Full OFL text: `src/godsv/assets/fonts/OFL.txt` (copied into the app tree as `apps/desktop/src/gv-fonts/OFL.txt`). |
| Space Grotesk | SIL Open Font License 1.1 | © 2020 The Space Grotesk Project Authors (Florian Karsten). Vendored from `@fontsource/space-grotesk@5.3.0`. OFL text in the same file. |
| JetBrains Mono | SIL Open Font License 1.1 | Upstream `JetBrains/JetBrainsMono` is OFL-1.1. **Note:** `apps/desktop/src/styles.css` currently labels it Apache-2.0 — a defect to fix. |
| VS Code Codicons (`codicon.ttf`) | CC-BY-4.0 | © Microsoft Corporation. Ships in the app bundle → in-app attribution required. |
| KaTeX math fonts | MIT | Shipped with KaTeX 0.16.47. |
| **Collapse, Mondwest, Rules Compressed, Rules Expanded** | **UNKNOWN — must be resolved before shipping** | Shipped from `@nous-research/ui` 0.18.2. The package declares MIT but contains **no license file for these fonts**. Remove the corresponding `@font-face` rules or obtain the license. |

## D. Build / test / audit tooling

Python environment (dev and audit paths; not shipped with the CLI):

| Package | Version | License |
|---|---|---|
| pytest | 8.4.2 | MIT |
| ruff | 0.16.7 | MIT |
| bandit | 1.9.4 | Apache-2.0 |
| fastapi | 0.141.1 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| httpcore | 1.0.9 | BSD-3-Clause |
| **certifi** | 2026.7.22 | **MPL-2.0** — file-level copyleft; used unmodified (patches to MPL files would require source disclosure) |
| playwright (Python) | 1.63.0 | Apache-2.0 |
| axe-playwright-python | 0.1.8 | MIT |
| pyee | 13.0.1 | MIT |
| greenlet | 3.5.6 | MIT AND PSF-2.0 |
| annotated-doc | 0.0.5 | MIT |

Version corrections to the German table above: `fastmcp` **4.0.4** (was 4.0.3),
`uvicorn` **0.53.0** (was 0.52.4), `httpx2` **2.13.0** (was 2.12.0).

Build-time-only Node packages (not present in the built application):

| Package | Version | License |
|---|---|---|
| lightningcss + 12 platform packages | 1.32.0 / 1.33.0 | MPL-2.0 — Tailwind 4's CSS engine; emits CSS only, no code in `dist` |
| caniuse-lite | 1.0.30001806 | CC-BY-4.0 — browser data used at build time; not in `dist` |
| khroma (via mermaid) | 2.1.0 | MIT — read from the bundled `license` file; the `package.json` omits the field |

**Not installed, therefore not yet credited, but required by the MVP test plan:**
Schemathesis (MIT) and Testcontainers (Apache-2.0). Credit them with their resolved versions once installed.

**Named in internal planning but not present anywhere:** AI Elements. Either adopt it or drop it from the list.

## E. MCP servers and host tooling

| Server / tool | Version | License | Status |
|---|---|---|---|
| computer-use-linux (`@agent-sh/computer-use-linux`) | 0.7.0 | MIT | registered, 18 tools |
| Playwright MCP (`@playwright/mcp`) | 0.0.81 | Apache-2.0 | registered, 26 tools |
| Chrome DevTools MCP (`chrome-devtools-mcp`) | 1.9.0 | Apache-2.0 | registered, 29 tools |
| godsv-tools (`godsvoice mcp serve`) | 1.5.0 | Apache-2.0 (this project) | registered |
| Postman MCP (`@postman/postman-mcp-server`) | 2.12.3 | Apache-2.0 | installed, not registered |
| Grafana MCP (`mcp-grafana`) | 1.5.1 | Apache-2.0 | installed, not registered |
| agent-workspace-linux (`@agent-sh/agent-workspace-linux`) | 0.3.2 | MIT | installed (not an MCP) |
| cua-driver (Computer Use) | 0.28.2 | **UNKNOWN — no license file in the local install.** Upstream `trycua/cua` declares MIT; confirm from the release artifact before shipping. | installed |
| @storybook/mcp | — | not credited | installed but unusable (no Storybook exists) |

## F. Container and sandbox runtime

| Component | Version | License | Note |
|---|---|---|---|
| Docker Engine | 29.6.1 | Apache-2.0 | host runtime dependency of the Tool Builder |
| Docker Compose | v5.3.1 | Apache-2.0 | host tooling |
| Podman | 5.8.1 | Apache-2.0 | used rootless to build/run the CU image |
| runc | via Docker/Podman | Apache-2.0 | host runtime |
| bubblewrap (`bwrap`) | system tool | **LGPL-2.1** | **Optional external system dependency — never bundle, link or modify.** Kept as a separate process, which is what LGPL-2.1 permits. |

Local Computer-Use image `localhost/godsv-cu-x11:latest` (`docker/cu-x11.Containerfile`):
`FROM docker.io/library/alpine:latest` + `xvfb xdotool xmessage xterm xwd ttf-dejavu bash`.
Its composite licenses: musl **MIT**, **BusyBox GPL-2.0**, **apk-tools GPL-2.0**, Xorg utilities
**MIT/X11**, xdotool **BSD-3-Clause**, ttf-dejavu **DejaVu license**, **bash GPL-3.0**.
**Do not redistribute this image without publishing the corresponding GPL sources.**

## G. Referenced projects

Credited as sources and references — no third-party code from these is embedded, with the single
exception noted:

| Project | License | How we use it |
|---|---|---|
| **NousResearch/hermes-agent** | MIT | The host we run on and whose desktop shell we rebuild as the Skaldir app. Its MIT license and Chromium/Electron notice files are carried through in the packaged release. |
| **tamaratran/fast-jev-compaction** | MIT | **Integrated dependency (source port), not a reference.** The Jev context compaction that runs today: `src/skaldir/jevcomp/**` (Python), called live by `src/skaldir/context_manager.py` and wired into the goal-locked run. Conformance is pinned against the upstream TypeScript through `node` (`tests/test_jevcomp_oracle.py`). Full record: section J. |
| **browser-use/jev-ultrafast** | MIT | Technical reference for the adversarial release-test suite only; not adopted. |
| SWE-agent/mini-swe-agent | MIT | Agent-loop reference and benchmark baseline. |
| trycua/cua | MIT | Computer-Use driver and benchmark reference. |
| browser-use/browser-use | MIT | Browser-agent concept reference. |
| PrefectHQ/fastmcp | Apache-2.0 | Upstream of the `mcp` extra. |
| microsandbox | Apache-2.0 | Sandboxing evaluation. |

## H. Out-of-scope: software NOT bundled but declared as a requirement

`docker`, `docker-compose`, `podman`, `bwrap`, `sqlite3`, `ImageMagick` (`convert`), and the MCP
servers listed in section E are **invoked as external processes**; none of their code is embedded in
the product. They are named here for completeness and because the product requires them to be present.

## I. Shipping blockers

1. **GSAP 3.15.0** — `"Standard 'no charge' license"`, not an OSI license (arrives as a peer
   dependency of `@nous-research/ui` and as a dependency of the `web` workspace). Verified absent from
   the built desktop bundle; **never ship it** — gate the build on it.
2. **`@vscode/codicons` (CC-BY-4.0)** — ships in the bundle; the attribution must be visible **in the app**.
3. **Collapse / Mondwest / Rules fonts** — ships in the bundle; license **UNKNOWN**.
4. **`localhost/godsv-cu-x11` image** — GPL-2.0/GPL-3.0 components; do not redistribute without sources.
5. `dompurify` election (Apache-2.0) must stay recorded; `certifi` (MPL-2.0) recorded.
6. `pyperclip` BSD variant unconfirmed (copyright holder placeholders unfilled in the bundled text).
7. `cua-driver` license unconfirmed from the artifact.
8. JetBrains Mono mislabelled Apache-2.0 in our own source; upstream is OFL-1.1.

## J. Embedded third-party code — `fast-jev-compaction` (MIT) ported to Python

This is the one exception to section G's "no third-party code is embedded" rule: the Jev context
compaction is a **source port** of the upstream repository, with attribution. It is an
**integrated dependency of the shipped product**, not a reference: the code that compacts a live
run's context is this port, and nothing else compacts it.

| Fact | Value |
|---|---|
| Upstream | `tamaratran/fast-jev-compaction` — https://github.com/tamaratran/fast-jev-compaction |
| Versions | Claude Code plugin **0.3.0**; npm package **fast-jev-compaction@0.2.0** |
| Pinned revision | commit `e3f262a7f4d42bd8dd32ced30d26176f7cb545b0` (re-cloned and re-verified 2026-09-19: `git rev-parse HEAD` returned this exact sha, `package.json` `"version": "0.2.0"`) |
| License | **MIT**, `Copyright (c) 2025` (the upstream LICENSE file carries no holder name) |
| Runtime deps of upstream | none (`"engines": { "node": ">=18" }`) — Node is **not** a runtime requirement of this product |
| Embedded as | **`src/skaldir/jevcomp/**`** (Python 3.12). (The older spelling `src/godsv/jevcomp/**` in earlier revisions of this file is the pre-rename path; that directory no longer exists. `src/godsv/` holds one re-export module only.) |
| Full license text in tree | `src/skaldir/jevcomp/UPSTREAM_LICENSE` (the notice is also repeated in each ported module's header) |

### J.1 Which files came from it, and which did not

| File | Origin |
|---|---|
| `src/skaldir/jevcomp/state.py` | port of upstream `src/state.ts` (`estimateTokens`, `truncate`, `isPinned`, `collectToolCalls`, `goalFromMessages`, `fitState`, the fitting stages) |
| `src/skaldir/jevcomp/compact.py` | port of upstream `src/compact.ts` + `src/request.ts` (`resolveOptions`, `DEFAULT_OPTIONS`, `questionsFor`, `batchCalls`, `decideCall`, `applyDecisions`, `messageChars`, `compact`) |
| `src/skaldir/jevcomp/types.py` | port of upstream `src/types.ts` + `src/messages.ts`, as dataclasses |
| `src/skaldir/jevcomp/request.py` | port of the upstream wire helpers (`buildJevRequest`, `parseJevResponse`, `noulAnswer`) |
| `src/skaldir/jevcomp/errors.py` | **Skaldir's own** — the typed error taxonomy (`JevCompactionError` and subclasses). Upstream throws plain `Error`; the message texts are identical on purpose. |
| `src/skaldir/jevcomp/fixture.py` | **Skaldir's own** — offline test/replay askers. Not upstream code. |
| `src/skaldir/jevcomp/__init__.py`, `README.md` | **Skaldir's own** — package API, the ported-function map and the deviation list. |

**Deliberately NOT ported:** the upstream HTTP client (`client.ts`), `compactMessages`/`JevClient`,
the API key handling, and the Claude Code hook (`hooks/fast-jev.ts`). Skaldir injects its own
provider (`ProviderJevAsker` in `src/skaldir/context_manager.py`); no key, no URL and no HTTP
client live inside this package. Nothing from the upstream is vendored as `.ts`/`.js` and no npm
dependency was added — the upstream package is not installed, imported or shipped.

### J.2 The port is the code that runs (proof)

* **The live caller.** `src/skaldir/context_manager.py` imports the port and calls
  `compact(view.port, self.asker, options)` — that call is the only compaction engine in the
  product. `goal_run.py` builds a `ContextManager` per turn, so a goal-locked run compacts through
  the port; `skaldir context compact|usage` is the same code reached from the CLI.
* **Conformance oracle (Node).** `tests/test_jevcomp_oracle.py` copies the upstream TypeScript
  into a temp dir, runs it through `node` on a shared fixture and diffs tokens, truncation,
  pinning, every fitting stage and its counts, the full state JSON, the batching, the decisions,
  the stats, the rebuilt messages and the error messages. Measured 2026-09-19 against the clone of
  the pinned commit: **9 passed, 0 skipped** (`node v26.8.1`,
  `GODSV_JEVC_OMPACTION_UPSTREAM` unset → the default `/tmp/fast-jev-compaction` was used).
* **The ported unit suites.** `tests/test_jevcomp_state.py`, `test_jevcomp_compact.py`,
  `test_jevcomp_fixtures.py` — measured 2026-09-19 together with the oracle: **75 passed**;
  together with `tests/test_context_manager.py` and the Jev façade suites: **320 passed, 1 skipped**
  (the skip is the repo's own opt-in live-provider test).
* **A live run whose decisions are the port's.** `/home/willy/Documents/Hermes_Skaldir_Agent_Docs/MVP/78-jev-live.md`
  §3 runs a real `AgentLoop` whose compaction asker is the configured model and shows the port's
  own per-call decisions (`keep` / `drop_result` / `drop_call`) with the model's probabilities and
  the upstream `stats` object (`calls`, `resultsDropped`, `callsDropped`, `pinned`, `requests`,
  `stateStage`, `stateTokens` — upstream's camelCase wire schema, produced by `compact.stats.to_wire()`,
  not by Skaldir code).

Obligation discharged: the MIT copyright and permission notice is carried in the ported file
headers and in `src/skaldir/jevcomp/UPSTREAM_LICENSE`; the deviations of the port are listed in the
`skaldir.jevcomp` package docstring and in `src/skaldir/jevcomp/README.md`.

---

# Third-Party Notices — appendix K (English) — CLI ship-docs wave

Appended 2026-09-18 for godsv **1.5.0**. This section resolves the open credit item of section D
(“Not installed, therefore not yet credited”: Schemathesis, Testcontainers), confirms the
licences of the MCP servers and the container runtimes named in sections E/F, and adds the
front-end and reference credits that the CLI documentation names.

**How to read the source column:** every row names the file it was read from on this machine —
`dist-info` metadata, the package's own licence file, the RPM `LICENSE` field, or an upstream
`LICENSE` file fetched from the project's repository. No row is filled in from a project
website, a name or a memory. Where the source is only a metadata field (not the licence text
itself) the row says so.

## K.1 Test / API tooling — now installed, therefore credited (resolves section D)

| Package | Version | License | Source read | Role |
|---|---|---|---|---|
| **Schemathesis** | 4.27.3 | **MIT** | `.venv/…/schemathesis-4.27.3.dist-info/METADATA` (`License-Expression: MIT`, `Classifier: License :: OSI Approved :: MIT License`) + the licence file it ships: `MIT License, Copyright (c) 2019 Kiwi.com` | property-based testing of our own HTTP surfaces |
| **Testcontainers (Python)** | 4.15.0 | **Apache-2.0** | `testcontainers-4.15.0.dist-info/METADATA` (`License-Expression: Apache-2.0`) + `licenses/LICENSE.txt` | real ephemeral services for tests |
| **Hypothesis** (arrives with Schemathesis) | 6.168.0 | **MPL-2.0** | `hypothesis-6.168.0.dist-info/METADATA` (`License-Expression: MPL-2.0`) + `LICENSE.txt` | the property engine behind Schemathesis |
| **docker (docker-py)** (arrives with Testcontainers) | 7.2.0 | **Apache-2.0** | `docker-7.2.0.dist-info/METADATA` (`License-Expression: Apache-2.0`) + `LICENSE` | container control from tests |

## K.2 Account-free API runners (the Postman replacement for tests)

| Package | Version | License | Source read | Role |
|---|---|---|---|---|
| **Bruno CLI** (`@usebruno/cli`) | 4.1.0 | **MIT** | `~/.local/lib/node_modules/@usebruno/cli/license.md` — “MIT License, Copyright (c) 2022 Anoop M D, Anusree P S and Contributors”; binary is `bru` (4.1.0) | runs `.bru` collections locally, no account, no cloud |
| **Newman** | 6.2.2 | **Apache-2.0** | `~/.local/lib/node_modules/newman/LICENSE.md` (Apache License 2.0, full text) | runs Postman collections without a Postman account |

Neither has a cloud dependency and neither ships with the product: both are invoked as external
processes during tests.

## K.3 MCP servers — licence confirmation of the section E rows

| Server | Version | License | Source read |
|---|---|---|---|
| **Grafana MCP** (`mcp-grafana`) | 1.5.1 | **Apache-2.0** | `~/.local/share/uv/tools/mcp-grafana/lib/python3.12/site-packages/mcp_grafana-1.5.1.dist-info/METADATA` → `License: Apache-2.0` |
| **Postman MCP** (`@postman/postman-mcp-server`) | 2.12.3 | **Apache-2.0** | `~/.local/lib/node_modules/@postman/postman-mcp-server/LICENSE` (full Apache-2.0 text) |

Registration note (kept for the audit trail): Hermes' OSV malware preflight blocks this package
**by name** (advisory `MAL-2025-190909`, which covers 2.4.10–2.4.12 only, while we run 2.12.3).
Pin the version when registering — unpinned is blocked. Never work around a security guard
silently.

## K.4 Container and sandbox runtime — licence fields read from RPM metadata

| Component | Version | License (as packaged) | Source read |
|---|---|---|---|
| **docker-ce** | 29.6.1 | **Apache-2.0** | `rpm -q --qf '%{LICENSE}' docker-ce` |
| **docker-ce-cli** | 29.6.1 | **Apache-2.0** | `rpm -qf $(which docker)` → `docker-ce-cli-29.6.1-1.fc44.x86_64` |
| **Podman** | 5.8.1 | **`Apache-2.0 AND BSD-2-Clause AND BSD-3-Clause AND ISC AND MIT AND MPL-2.0`** (Fedora composite expression) | `rpm -q --qf '%{LICENSE}' podman` |
| **bubblewrap** (`bwrap`) | 0.11.0 | **`LGPL-2.0-or-later`** per Fedora's metadata | `rpm -q --qf '%{LICENSE}' bubblewrap` |

**Correction to section F:** section F lists bubblewrap as `LGPL-2.1`; Fedora's package metadata
says `LGPL-2.0-or-later`, and upstream's own tag is `LGPL-2.1+`. The difference is book-keeping
only — bubblewrap is an **external system process**: never bundled, never linked, never modified,
which is what the LGPL family permits. Record the concrete string of whichever distribution you
package for.

## K.5 Front-end credits — verified from the installed trees

Desktop shell (`app-src/apps/desktop`, the tree that ships):

| Package | Version | License | Source read |
|---|---|---|---|
| **Tabler Icons** (`@tabler/icons-react`) | 3.44.0 | **MIT** | `app-src/node_modules/@tabler/icons-react/package.json` (`license` field) |
| **Motion** (`motion`) | 12.42.2 | **MIT** | `app-src/node_modules/motion/package.json` |
| **Radix UI** (`radix-ui`) | 1.6.7 | **MIT** | `app-src/node_modules/radix-ui/package.json` |
| **cmdk** | 1.1.1 | **MIT** | `app-src/node_modules/cmdk/package.json` |
| **Tailwind CSS** (`tailwindcss`) | 4.3.3 | **MIT** | `app-src/node_modules/tailwindcss/package.json` + the licence banner in the built stylesheet: `/*! tailwindcss v4.3.3 \| MIT License \| https://tailwindcss.com */` |
| **Geist** (Geist Sans / Geist Mono) | `@fontsource/geist-sans` / `-mono` 5.3.0 | **SIL Open Font License 1.1** | vendored OFL text in tree: `src/godsv/assets/fonts/OFL.txt` (© 2023 Vercel, in collaboration with basement.studio) |
| **thinking-orbs** | 0.3.1 | **MIT** (© Jakub Antalik) | `node_modules/thinking-orbs/LICENSE` + the inline attribution header inside the vendored block of `integrations/hermes/godsv_plugin/desktop/plugin.js` (“thinking-orbs v0.3.1 (MIT (c) Jakub Antalik) — inlined, not imported”); the copy is gated by `node tools/vendor_thinking_orbs.mjs --check` → “vendored copy is current” |

UI lab (`tools/ui-demo` — a local design/verification harness, `private: true`, **not** part of
the shipped shell; verified absent from `app-src/apps/desktop`):

| Package | Version | License | Source read |
|---|---|---|---|
| **React Flow** (`@xyflow/react`) | 12.11.6 | **MIT** | `tools/ui-demo/node_modules/@xyflow/react/package.json` |
| **`@xyflow/system`** (React Flow core) | 0.0.82 | **MIT** | `tools/ui-demo/node_modules/@xyflow/system/package.json` |
| **Recharts** | 3.10.1 | **MIT** | `tools/ui-demo/node_modules/recharts/package.json` |
| **AI Elements** (Vercel) | shadcn registry package 1.9.0; 48 generated component files | **Apache-2.0** | `…/.npm/_npx/*/node_modules/ai-elements/LICENSE` — “Copyright 2023 Vercel, Inc. … Apache License, Version 2.0”. **Correction to section D**, which read “not present anywhere”: the components *are* present, as generated source under `tools/ui-demo/src/components/ai-elements/` (48 `.tsx` files) |
| `@rive-app/react-webgl2` | 4.34.3 | **MIT** | `tools/ui-demo/node_modules/@rive-app/react-webgl2/package.json` (metadata field only) |
| `lucide-react` | 1.47.0 | **ISC** | `tools/ui-demo/node_modules/lucide-react/package.json` (metadata field only — the package's own licence file was not opened) |
| `embla-carousel-react` | 8.6.0 | **MIT** | `tools/ui-demo/node_modules/embla-carousel-react/package.json` (metadata field only) |
| `media-chrome` | 4.19.2 | **MIT** | `tools/ui-demo/node_modules/media-chrome/package.json` (metadata field only) |
| `tokenlens` | 1.3.1 | **MIT** | `tools/ui-demo/node_modules/tokenlens/package.json` (metadata field only) |

## K.6 Referenced projects — licence read from the upstream LICENSE file

| Project | License | Source read | How we use it |
|---|---|---|---|
| **NousResearch/hermes-agent** | **MIT** (© 2025 Nous Research) | local host tree: `/home/willy/.hermes/hermes-agent/LICENSE` | the host we run on (CLI, gateway, desktop shell, plugin API); its code is not embedded |
| **tamaratran/fast-jev-compaction** | **MIT** (© 2025) | upstream `LICENSE` (raw.githubusercontent.com, `main`) + the carried copy `src/godsv/jevcomp/UPSTREAM_LICENSE` | source ported to Python for Jev context compaction (section J) |
| **browser-use/jev-ultrafast** | **MIT** (© 2026 Browser Use) | upstream `LICENSE` (raw.githubusercontent.com, `main`) | technical reference for the adversarial release-test suite **only** — not adopted, nothing embedded |

## K.7 Still open after this appendix

1. **Metadata-only rows** in K.5 (`lucide-react` ISC in particular): the `license` field of
   `package.json` was read, the shipped licence text was not. All of them are UI-lab dependencies
   and do not ship; confirm the text before any of them enters the app bundle.
2. **Fedora vs upstream licence strings** (bubblewrap LGPL-2.0-or-later vs LGPL-2.1+; Podman's
   composite expression): correct in the sense of the packaged artifact, but re-read after any
   distribution upgrade.
3. **Unchanged blockers** from section I: GSAP 3.15.0 (not OSI — gate the build),
   `@vscode/codicons` CC-BY-4.0 (ships → in-app attribution required), the Collapse/Mondwest/
   Rules fonts (UNKNOWN licence), the `godsv-cu-x11` image (GPL parts — do not redistribute
   without sources), `cua-driver` (licence unconfirmed from the artifact), `pyperclip` (BSD
   variant unconfirmed).
4. **Provider configuration module** (`src/godsv/provider_config.py`) is credited as our own code
   — no third-party content — but note the shipping-relevant drift recorded in
   `README-CLI.md` §10: its docstring advertises a `godsvoice provider add …` command that does
   **not** exist in the CLI contract.

## Rename notice — the product is called **Skaldir Agent**

Appended 2026-09-18 (RENAME 2, non-Python surfaces). Everything above predates the rename and
keeps its original wording; nothing in it is invalidated by the new name:

- the section heading and the copyright line at the top still read `God's Voice` — this file is
  **append-only** for this wave, so they were deliberately not rewritten (they are licence facts,
  not product text);
- the host-dependency row names the same host as before (`Hermes Agent`), unchanged;
- every third-party project listed above keeps its own name and its own credit — the rename does
  not touch, relicense or re-attribute any dependency.

Product name: **Skaldir Agent** (slug `skaldir`). The technical identifiers are unchanged and are
load-bearing for the installed runtime: package/module `godsv`, console script `godsvoice`,
environment variables `GODSV_*`, plugin id `godsv`, runtime directory `~/.hermes/godsv`.
The full mapping, the compatibility list (with the consequence and the safe migration per item)
and the inventory of every occurrence that was kept on purpose are in `docs/RENAME.md`.

---

## OpenAI Codex — Apache-2.0 (study record, appended 2026-09-18)

**Project under study:** OpenAI Codex — <https://github.com/openai/codex>
**Revision read:** `7498521d288b9b3b96ffba4eedf089d8d6e06a84` (2026-09-18T00:46:19Z), read-only clone.
**Licence:** Apache License, Version 2.0 (`LICENSE`, 201 lines, unmodified Apache-2.0 text;
also declared as `"license": "Apache-2.0"` in `codex-cli/package.json`).
**Study:** `Documents/Hermes_Skaldir_Agent_Docs/MVP/42-codex-adoption.md`.

**Status of this record: design studied, no code copied.** No file of the Codex project has been
copied into this repository in this wave. If code is ever copied or adapted from Codex, the
obligations below become immediate and per-file.

### Obligations that apply to any future derivative (Apache-2.0 §4)

* **§4(a)** — ship the Apache-2.0 licence text to every recipient of the derivative.
* **§4(b)** — every modified/adapted file carries a prominent notice stating that it was changed;
  this is a **per-file header**, not a line in this table.
* **§4(c)** — retain all copyright, patent, trademark and attribution notices from the upstream
  source form of the files we adapt.
* **§4(d)** — reproduce the upstream `NOTICE`, which reads in full:

  ```
  OpenAI Codex
  Copyright 2025 OpenAI

  This project includes code derived from [Ratatui](https://github.com/ratatui/ratatui), licensed under the MIT license.
  Copyright (c) 2016-2022 Florian Dehau
  Copyright (c) 2023-2025 The Ratatui Developers
  ```

  The second stanza matters: Codex itself contains MIT-licensed Ratatui code, so a derivative that
  touches `codex-rs/tui/**` inherits **both** the Apache-2.0 notice and the Ratatui MIT attribution.
* **§6 (Trademarks)** — no trademark licence is granted. "Codex" and "OpenAI" may be used only for
  reasonable and customary use in describing the origin of the work. Nothing we ship may be named
  "Codex" or imply endorsement.

### Optional dependency (not installed, not required by this project)

| Package | Version | Licence | Notes |
|---|---|---|---|
| `openai-codex` (Python SDK) | 0.154.0 | Apache-2.0 | per PyPI metadata; pulls `openai-codex-cli-bin` |
| `openai-codex-cli-bin` (pinned Codex CLI runtime) | 0.154.0 | Apache-2.0 | per PyPI metadata; 127.4 MB `manylinux_2_17_x86_64` wheel |

Neither is a dependency of this project. If either is ever added, it must be added behind an explicit
opt-in, not as part of the default install, and the row must be re-measured (version, size, licence) at
pin time.

---

## Coding-context harness (`src/skaldir/codectx.py`) — patterns referenced, **no code copied**

Appended 2026-09-18 with the FINAL-2 block (repo map · search · symbols/diagnostics · context pack).

Three code assistants expose the same four capabilities to their model, and that shared shape is
what this harness re-implements from scratch:

| Harness | Capability referenced | How it was referenced here |
|---|---|---|
| **Claude Code** (Anthropic, proprietary) | a ranked repository view + task-driven context selection under a token budget | its **published behaviour/docs only** — its source is not available, so nothing was read or copied |
| **Cline** (Apache-2.0) | symbol listing per file (`list_code_definition_names`) and capped file search | public tool documentation; the CLI installed at `~/.local/bin/cline` carries a **launcher**, not a readable bundle, so its implementation was **not** inspected |
| **OpenCode** (`sst/opencode`, MIT) | `grep`/`glob`/`read` tools with result caps, `AGENTS.md` auto-loading, LSP-driven diagnostics | public tool documentation; the installed artefact (`~/.opencode/bin/opencode`, 177 MB) is a **compiled binary** whose embedded tool definitions could not be read in this session |

**Status of this record: pattern study, no code copied, no dependency added.** No file of any of the
three projects exists in this repository. `skaldir.codectx` imports only the Python standard library
(`ast`, `re`, `json`, `os`, `subprocess`, …) and this project's own modules, so no licence obligation
attaches to any of the three rows above.

### Tools the harness *invokes* (host binaries; none is bundled or redistributed)

| Tool | Version measured | Licence | How it is used |
|---|---|---|---|
| **ruff** | 0.16.7 (already a `dev` dependency, credited in section A) | MIT | `ruff check --output-format=json` for Python diagnostics; probed (`which ruff`, then `python -m ruff`), never assumed |
| **git** | system binary | GPL-2.0 | `git status --porcelain`, `git blame --line-porcelain`, `rev-parse`; invoked, never linked |
| **Universal Ctags** | 6.2.1 (system binary) | **GPL-2.0** | `ctags -f - --output-format=json` for non-Python symbol lookup **when installed**. GPL-2.0 obligations attach only to distribution of the binary — this project ships neither the binary nor a module of it; a host without `ctags` gets an honest `blocked` answer naming the tool instead |

---

## Streaming responsiveness (`src/skaldir/streaming.py`) — design studied, **no code copied**

Appended 2026-09-18 with the CLINE-PERF-1 block (chunk-coalesced streaming, incremental rendering,
the measured Enter→byte). Reference: **github.com/cline/cline**, **Apache-2.0**, cloned read-only
into `/tmp/cline-study/cline` at commit **`3a4a14a0a6503ff9fc797790e53d0967830aff8a`** (2026-09-18).

| Mechanism | Where it was read | How it was used here |
|---|---|---|
| Coalesce high-volume process output into **at most one UI update per 48 ms**, with a bounded pending tail, dropped-character accounting and a final flush on stop (`COMMAND_PROGRESS_FLUSH_INTERVAL_MS`, `MAX_COMMAND_OUTPUT_CHARS = 48_000`) | `sdk/packages/core/src/extensions/tools/executors/bash.ts` — `createCommandProgressEmitter` | re-implemented in Python as `streaming.Coalescer` (a time-driven check in the write path, because a shell has no event loop to run a timer). Two deliberate differences, both measured and documented in `MVP/53-cline-perf.md`: the first chunk renders immediately, and `flush()` obeys the bound while `drain()` does not |
| Bound the memory of a long stream by shape: head verbatim + rolling tail | `bash.ts` — `createRollingCollector` | `streaming.RollingBuffer` |
| Parallel tool calls: a per-tool `executionMode`, a runtime default, and **only adjacent parallel calls overlap** (a sequential call is a barrier), with all calls prepared before any executes | `sdk/packages/agents/src/agent-runtime.ts` — `executeToolCalls`, `prepareToolExecution` | `streaming.plan_tool_batches` (the rule) and `BatchTracker`/`render.batch_line` (one line per wave) |
| A live indicator driven by real values, with a frame interval rather than a per-event redraw | `apps/cli/src/tui/components/robot-animation.tsx`; `hooks/use-agent-events.ts` (only the last entry is rebuilt per delta) | `render.activity_line` + the `Activity` quiet-period rule (draw only when the command has been silent) |
| Long-running work does not freeze the surface: coalesced progress plus a capped detached log with a retention reaper | `bash.ts` — `createDetachedLog`, `scheduleDetachedLogCleanup` | the same insight motivates pacing a shell stream; the detached-log half is noted as a PERSIST-layer gap in `MVP/53-cline-perf.md` §7 |

**Status of this record: pattern study, no code copied, no dependency added.** No file of Cline's
exists in this repository; `src/skaldir/streaming.py` is Python written for this project and imports
only the standard library (`argparse`, `io`, `json`, `os`, `sys`, `time`, …) plus this project's own
modules. Cline's own licence header was not reproduced because nothing of its code was taken; it is
named here as the design reference, and the commit above is the exact revision studied.

### Added with the token-level transfer (`src/skaldir/stream_render.py`) — same reference, same commit

Appended 2026-09-18 with the CLINE-STREAM-2 block (the model's **answer** stream, not a process's
output: `llm_provider.on_delta` → `agent_loop` → `StreamRenderer` → the terminal). Read for this:
`apps/cli/src/tui/hooks/use-agent-events.ts` and `components/chat-message-list.tsx`.

| Mechanism | Where it was read | How it was used here |
|---|---|---|
| A **delta-per-event** stream folded into the **last entry only** (`updateLastEntry(prev => … text: prev.text + chunk)`), with every untouched entry returned as the same object so the renderer skips it | `use-agent-events.ts`, `chat-message-list.tsx` | the rule, translated to a byte stream: `StreamRenderer` **only ever appends**. Its single in-place write is its own footer line, cleared (`\r\x1b[K`) immediately before every append. There is no "redraw the block" path at all — the terminal cannot un-write what it printed |
| The rate bound lives where the stream meets **a consumer**, not in the stream | `bash.ts` — `COMMAND_PROGRESS_FLUSH_INTERVAL_MS = 48` | reused as-is: `streaming.Coalescer`'s 60 ms bound paces the answer stream, and `StreamRenderer.delta` is the consumer. No second pacing implementation was written |
| A running tool has **its own status surface**, separate from the growing transcript (one updating line, with state) | `chat-message-list.tsx` | `StreamRenderer`'s activity footer: `⠋ streaming · 1.7 s · 29 deltas · ~435 (est. chars/4) tok · 15 updates`, rewritten in place, drawn only at a line boundary so it can never land inside a line the model was writing |
| Long streams must not fight the rest of the surface for the renderer | the React tree (one reconciler arbitrates) | **not transferable**: a terminal has no arbiter. Handled instead by `close_call()` (flush the tail before any other line is printed) and by `progress.RunProgress` drawing nothing while content flows — the defect this fixed is in `MVP/58-streaming.md` §6, measured at 63/64 intact answer lines before the fix and 64/64 after |

**Status of this record: pattern study, no code copied, no dependency added.** `stream_render.py` is
Python written for this project; it imports the standard library (`io`, `sys`, `threading`, `time`,
`dataclasses`, `typing`) plus this project's own `render` module. Nothing of Cline's is vendored, and
no licence obligation attaches to this repository beyond naming the reference — the same revision as
above is the exact tree studied.

---

**ripgrep is deliberately *not* used**, although `rg` 15.2.0 is installed: the search engine here is
`re` from the standard library, so the feature does not depend on an external binary at all. `rg`
therefore appears in no licence row of this file.

---

## MiniMax Code — MIT (study record), appended 2026-09-19

The SUBAGENT-ROLES + EVIDENCE-BRIEF block (`MVP/93-subagent-roles.md`) studied a reference agent
harness before designing its own. Reference repository: **github.com/MiniMax-AI/minimax-code**,
licence read from the repository's own `LICENSE` file: **MIT License, Copyright (c) 2026 MiniMax
Code** (the file was fetched in full and read; MIT permits copying with attribution, so the licence
was *not* the reason nothing was copied). Revision studied: `main` at commit
**`a5639bcc6146754e01f1ae18bb88545f18299fd6`** (2026-09-19T13:42:09Z), with the role table last
touched at **`c59cf5377045aa1a3e699c242d089b73b7cdc2ad`**; the five files were read from
`raw.githubusercontent.com` into `/tmp/mm-study/` and are not part of this repository.

| Mechanism | Where it was read | How it was used here |
|---|---|---|
| Three canonical roles (`explore` · `worker` · `verifier`) as a *named vocabulary* with a "when to use" sentence each, plus a reserved-name resolver | `packages/shared/src/subagent-roles.ts` — `SUBAGENT_ROLES`, `CANONICAL_SUBAGENT_ROLES`, `resolveCanonicalSubagentRole` | the role vocabulary of `src/skaldir/subagent_roles.py` (same three names, plus a fourth `main` row so "who may do everything" is explicit; `validate_role`/`canonical_role` are the resolver) |
| The **ceiling applied to the already-filtered tool list** by a pure helper, so two catalog owners cannot drift — and `task`/`task_append` gated together as one delegation entry point | `packages/agent-tools/src/desktop/canonical-tool-policy.ts` — `filterCanonicalNativeToolCeiling`, `DELEGATION_TOOL_NAMES` | `subagent_roles.provision_tools` / `ceiling_filter` (pure) and `DELEGATION_TOOLS = {spawn, spawn_append}`; the *enforcement* half (a dispatcher that refuses by name) is this project's, since the harness here has no tool loop of its own |
| The **Evidence Brief**: a bounded packet (objective + digest, the claim, changed files, commands, a recent message tail, a `truncated` flag) fitted to a hard character limit by dropping tail → changes → baseline → claim → objective, with `<`/`>` escaped so worker-authored text cannot forge the prompt tag it is rendered inside | `packages/agent-modules/goal/src/verification/evidence-brief.ts` — `assembleVerificationEvidence`, `fitBrief`, `safeJsonStringify`, `MAX_VERIFICATION_BRIEF_CHARS = 14_000` | `src/skaldir/subagent_briefs.py` — `build_evidence_brief`, `render_evidence_brief`, `DEFAULT_MAX_BRIEF_CHARS = 14_000`; same drop order, plus an explicit **changed-file digest** and an explicit evidence-ref list, and a raise instead of a silent over-long packet |
| The **read-only verifier child**: its own `maxTurns`/`maxTokens`/timeout, a bounded reason, evidence gaps extracted for the worker, a schema retry, and — load-bearing — "the child can only return untrusted text; the host owns every settlement" | `packages/agent-modules/goal/src/verification/subagent.ts` — `createSubagentVerifierAdapter`, `buildSubagentPrompt`, `verdictGaps` | `RoleToolDispatcher("verifier")` + `verify_service.semantic_gate`: the gate is read-only by construction, its budget comes from `ROLE_BUDGETS["verifier"]`, a refusal must name a reason, and the deterministic `PASS` is never replaced — the gate can only keep it from being promoted |

**Status of this record: concept study, no code copied, no dependency added, nothing vendored.** No
TypeScript file of MiniMax's exists in this repository and no MiniMax package is imported. Every line
implemented here is Python written for this project against its own layers (`verify_service`,
`subagents`, `jev`), and the design differences are stated in `MVP/93-subagent-roles.md` §5 rather
than glossed over. The MIT notice above is recorded because the reference was read in full, not
because an obligation attaches: the rule of this project is to name what was studied.
