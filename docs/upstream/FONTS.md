# Fonts — die drei Gesichter der App

Die Design-Vorgabe (`01_PRODUCT_UX_DESIGN.md`:46) nennt genau drei Rollen:
**Geist Sans** für die UI, **Space Grotesk** für die Wortmarke und **Geist
Mono** (oder ein gleichwertiger Mono) für Code/`data`.

| Rolle | Familie (CSS) | Gewichte | Dateien |
|---|---|---|---|
| UI | `Geist Sans` | 400 / 500 / 600 | GeistSans-Regular/-Medium/-SemiBold.woff2 |
| Wortmarke | `Space Grotesk` | 500 / 700 | SpaceGrotesk-Medium/-Bold.woff2 |
| Code | `Geist Mono` | 400 / 500 | GeistMono-Regular/-Medium.woff2 |

Die Dateien liegen in `src/godsv/assets/fonts/` (nur Latin-Subset, keine
Kyrillisch/Griechisch/Vietnam-Subsets); `MANIFEST.json` listet Herkunft,
Gewichte, Byte-Größen und die exakten CSS-Familiennamen.
## Bezug (reproduzierbar)
```bash
cd /tmp && npm pack @fontsource/geist-sans@5 @fontsource/space-grotesk@5 @fontsource/geist-mono@5
```
Verwendet: **@fontsource/geist-sans@5.3.0**, **@fontsource/space-grotesk@5.3.0**,
**@fontsource/geist-mono@5.3.0**; die Latin-`<weight>-normal.woff2` aus
`package/files/` wurden unter den obigen Namen kopiert.
**Wichtig:** Fontsource deklariert im CSS die Familie als `Geist Sans` (nicht
`Geist`); die `name`-Tabelle der Dateien sagt bei Space Grotesk abweichend
`Space Grotesk Light`. Für `@font-face` gelten die Werte aus
`css_font_family_names` im Manifest.
## Lizenz (OFL-1.1) + Attribution
Alle drei Familien stehen unter der **SIL Open Font License 1.1**; der volle
Text (pro Familie mit Kopfzeile) liegt in `src/godsv/assets/fonts/OFL.txt`.
Attribution: Geist Sans/Geist Mono — (C) Vercel, mit basement.studio;
Space Grotesk — (C) 2020 The Space Grotesk Project Authors (Florian Karsten).
## Einbindung im App-Build
Die App bäckt die Fonts ein statt sie zur Laufzeit zu laden: der Overlay-Schritt
`app-overlay/patch_app.py` kopiert die woff2 in den App-Baum und ergänzt die
`@font-face`-Regeln (`css_font_family_names`). Dieser Overlay wird hier **nicht**
angefasst; dieses Dokument und die Assets sind reine Vendor-Ablage.
