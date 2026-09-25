# Skaldir Agent Desktop-App — native Integration

Die **Skaldir Agent-App ist ein eigener Build der Desktop-App** (dieselbe Engine,
dasselbe Design — eigenes Gesicht). Kein Plugin-Schritt, kein „Reload plugins":
Panel, Theme, Splash und Branding sind **eingebacken**.

## Bauen

```bash
godsvoice desktop build-app        # inkrementeller Mirror + Overlay + npm run pack
godsvoice desktop build-app --force  # Mirror komplett neu verlinken
```

Pipeline (`src/godsv/appbuild.py` + `app-overlay/patch_app.py`):

1. **Mirror** — der Hermes-Quellbaum wird per `rsync --link-dest` als
   Hardlink-Spiegel unter `~/.hermes/godsv/app-src/` gehalten. Der Checkout
   selbst wird **nie** angefasst; Updates sind inkrementell und billig.
2. **Overlay** — exakt verankerte Patches (jeder Anker muss genau einmal
   treffen, sonst bricht der Build **laut** ab statt halb-gebrandet zu
   schippern) + Dateien (Plugin, Mark, App-Icon). Alle Schreibvorgänge laufen
   über temp + `os.replace` und brechen so die Hardlinks — der Quellbaum kann
   nicht korrumpiert werden (durch Test abgesichert).
3. **Pack** — `npm run pack` (vite build + electron-builder `--dir`,
   `--publish never`) im Mirror.
4. **Deploy** — der gepackte Baum geht nach `~/.hermes/godsv/app/linux-unpacked/`.
   Der Launcher (`desktop.hermes_app_binary`) bevorzugt ihn automatisch;
   `godsvoice desktop start` startet dann die native GV-App.

## Welche UI-Teile getauscht werden — und warum

| UI-Teil | Datei | Vorher | Jetzt (nativ im Build) |
|---|---|---|---|
| **Splash-Wordmark** (Empty-Chat/Boot) | `src/components/chat/intro.tsx` | `HERMES AGENT` | `SKALDIR AGENT` + **Logo-Mark** über dem Lettering |
| **Splash-Copy** | `src/components/chat/intro.tsx` (Konstanten) | „What should Hermes look at?" etc. | personality-basiert; Fallback-Copy neutralisiert |
| **Brand-Badge** (About, Updates, Install-Overlay) | `src/components/brand-mark.tsx` | nous-girl auf weißer Kachel | GV-Mark auf Charcoal-Kachel |
| **App-Icon** (Linux-Pack) | `apps/desktop/assets/icon.png` | Hermes-Icon | Skaldir Agent-Icon (Ohr, Gold) |
| **App-Name / ID / Binary** | `apps/desktop/package.json` | `Hermes` / `com.nousresearch.hermes` / `Hermes` | `Skaldir Agent` / `dev.godsvoice.app` / `gods-voice` |
| **Fenster-/Dokument-Titel** | `index.html`, `src/main.tsx` (HUD), `electron/main.ts` (Update-Dialog) | „Hermes", „Hermes HUD", „Hermes update" | „Skaldir Agent", „Skaldir Agent HUD", „Skaldir Agent update" |
| **Theme-Default** | `src/themes/presets.ts` | `nous` | `gods-voice` (Gold auf warmem Charcoal — light+dark) |
| **Theme-Liste** | `src/themes/presets.ts` (`BUILTIN_THEMES`) | — | + `gods-voice` (wählbar in Appearance) |
| **/skin-Aliase** | `src/themes/use-skin-command.ts` | `default/gold → nous` | `default/gold → gods-voice` |
| **Panel + Chat-Branding** | NEU: `src/plugins/godsv/plugin.js` | (Stock-Panel leer) | **gebündeltes Plugin**, registert beim Boot: Panel-Tabs (Live/Pausen/Memory/Skills/Treasury/System) + Chat-Empty-Branding — ohne Installation |
| **Nous-Portal-Texte** | `src/i18n/en.ts` | „Nous Portal Support", „Sign in to Nous Portal", „Check Portal status", nous-subscription-Hinweise | neutralisiert („Status & Support", „Sign in to activate", …) — Links bleiben funktional |
| **nous-girl-Asset** | `apps/desktop/public/nous-girl.jpg` | im Build ausgeliefert | entfernt |

**Nicht angetastet** (bewusst): Engine, Session-Logik, Provider-Auth (funktional),
alle anderen Themes, i18n der übrigen Sprachen. Die App liest dasselbe
`$HERMES_HOME` wie die Stock-App — Sessions/Runtime geteilt, nur die Identität
ist eigen.

**Warum als Patch-Overlay statt Fork:** Der Mirror macht Updates der Stock-App
inkrementell und billig; die exakt verankerten Patches schlagen fehl, wenn
Upstream eine Zeile verschiebt — dann wird der Build fällig nachgezogen statt
still zu driften. Nach einem `hermes`-Update: einfach `godsvoice desktop build-app`.
