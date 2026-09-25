# Leseindex und Revisionen

Stand: 25. September 2026. Physische Zeilen, 1-basiert und inklusive. Index selbst ausgeschlossen.

## 00-START-HIER.md

[00-START-HIER.md](00-START-HIER.md) · 58 Zeilen · `sha256:32edbc24f7cda9c69b729cf0b58d08f90d0816f3554aa5d50a49128db551cefa`

| Abschnitt | Zeilen |
|---|---|
| Arbeitsauftrag an den Coding-Agenten | 1–58 |
| Auftrag | 7–16 |
| Vorhandener Projektkontext | 17–24 |
| Vorrang der aktuellen Entscheidungen | 25–41 |
| Erwartete Arbeitsartefakte im Repository | 42–47 |
| Paketübersicht | 48–58 |

## 01-MASTERPLAN.md

[01-MASTERPLAN.md](01-MASTERPLAN.md) · 439 Zeilen · `sha256:1518eb8510576c30086be81a481a4795f7cfb5962c013719dd9cd5b8f292d20e`

| Abschnitt | Zeilen |
|---|---|
| Skaldir — verbindlicher Produkt- und Architekturplan | 1–439 |
| 1. Ziel und Prioritäten | 5–28 |
| 1.1 Prioritäten | 11–22 |
| 1.2 Produktgrenzen | 23–28 |
| 2. Verbindliche Bestandsaufnahme und Migration | 29–64 |
| 2.1 Zu untersuchende bestehende Komponenten | 37–51 |
| 2.2 Bekannte Fehler zuerst in Regressionen überführen | 52–64 |
| 3. Architektur: eine Runtime, mehrere Oberflächen | 65–96 |
| 4. Alle normalen Agentenfunktionen sind Pflicht | 97–131 |
| 4.1 Kostenloser Webzugang | 120–131 |
| 5. Tool-Vertrag und schnelle parallele Ausführung | 132–161 |
| 6. Hauptagent und Workerrollen | 162–171 |
| 7. Approval-Modi und Rücknehmbarkeit | 172–191 |
| 8. Vollständiges Computer Use auf dem echten Fedora-Host | 192–239 |
| 8.1 Fähigkeiten | 194–207 |
| 8.2 Desktopkontrolle und Warteschlange | 208–217 |
| 8.3 Globaler Doppel-Escape: eigener früher Machbarkeits- und Abnahmepunkt | 218–229 |
| 8.4 Browserlebenszyklus | 230–239 |
| 9. Gelernte GUI-Subtools | 240–253 |
| 10. Skills als bedarfsgeladene Arbeitsanleitungen | 254–263 |
| 11. Allgemeiner Tool Builder und Docker Tool Builder | 264–287 |
| 11.1 Allgemeiner Builder | 266–275 |
| 11.2 Containerwerkzeuge | 276–287 |
| 12. COMPOSE, Workflows und Zeittrigger | 288–305 |
| 13. Persistente Goals, Verifikation, Stop und Resume | 306–327 |
| 14. Jev, The Voice und geringe Kontrollkosten | 328–341 |
| 15. Memory, Markdown und Kompaktierung | 342–369 |
| 15.1 Dateiwissen | 344–357 |
| 15.2 Kontext reduzieren, Originalbelege erhalten | 358–369 |
| 16. Vollbild-TUI und später API | 370–387 |
| 17. Geschwindigkeit und Observability | 388–399 |
| 18. Umsetzung in überprüfbaren Etappen | 400–414 |
| 19. Tests, Migration und Abschluss | 415–428 |
| 20. Ausdrücklich zurückgestellte Punkte | 429–439 |

## 02-ABNAHME.md

[02-ABNAHME.md](02-ABNAHME.md) · 223 Zeilen · `sha256:e0a28bffc51c6c08d0192445a18bec42a97e607a3262944e779149b2df7a1b5c`

| Abschnitt | Zeilen |
|---|---|
| Test- und Produktabnahme | 1–223 |
| 1. Nachweisstufen und Prüfaufwand | 5–15 |
| 2. Technische Vertragsprüfungen | 16–74 |
| 3. Verbindliche Fedora-Abnahme | 75–92 |
| 4. Produktprüfung A: normale Coding-Aufgabe | 93–100 |
| 5. Produktprüfung B: Browserrecherche als erste Computer-Use-Abnahme | 101–118 |
| Pflichtablauf | 105–118 |
| 6. Produktprüfung C: anspruchsvolles Stock-Research-Tool mit lokalem Dashboard | 119–150 |
| Vereinbarbarer Funktionsumfang | 125–138 |
| Abnahme | 139–150 |
| 7. Produktprüfung D: Videobearbeitung | 151–165 |
| 8. Produktprüfung E: Blender | 166–173 |
| 9. Produktprüfung F: weiteres Kreativprogramm | 174–179 |
| 10. Langlauf, Lernen und Wiederverwendung | 180–201 |
| L-01: Subtool-Lernen | 182–185 |
| L-02: Workflow und Scheduler | 186–189 |
| L-03: Wiederaufnahme | 190–193 |
| L-04: Voice/Jev | 194–197 |
| L-05: Memory und Compaction | 198–201 |
| 11. Performanceprotokoll | 202–218 |
| 12. Abschlussbericht | 219–223 |

## 03-TUI-ENTSCHEIDUNGEN.md

[03-TUI-ENTSCHEIDUNGEN.md](03-TUI-ENTSCHEIDUNGEN.md) · 131 Zeilen · `sha256:42ab9c70d7f601ae859089d0bec11db911339192c40dcd669ecbd8346fee65c5`

| Abschnitt | Zeilen |
|---|---|
| Vollbild-TUI: bestätigtes Design und offene Details | 1–131 |
| 1. Vom Nutzer bestätigt | 5–18 |
| 2. Bestätigte Tool-Detailtiefe | 19–28 |
| 3. Layoutvorschlag | 29–63 |
| Chat | 52–55 |
| Goal | 56–59 |
| Tools | 60–63 |
| 4. Status und Interaktion | 64–91 |
| Bestätigte Approval-Bedienung | 76–83 |
| Stop und Detach | 84–91 |
| 5. Figur und Bewegung | 92–103 |
| 6. Desktop-Indikator außerhalb der TUI | 104–115 |
| 7. TUI-Abnahme | 116–126 |
| 8. Bestätigung vom 25. September 2026 | 127–131 |

## 04-AUDIT-UND-UEBERGABE.md

[04-AUDIT-UND-UEBERGABE.md](04-AUDIT-UND-UEBERGABE.md) · 61 Zeilen · `sha256:d9d1594fae3ea1ce523ba607b277ea1e286ce556e515c3e2b610a8d6d6463955`

| Abschnitt | Zeilen |
|---|---|
| Umsetzungsaudit, Abgrenzung und Übergabe | 1–61 |
| 1. Was dieses Paket ersetzt | 3–8 |
| 2. Produktentscheidungen versus Implementierungsentscheidungen | 9–16 |
| 3. Anforderungsaudit | 17–39 |
| 4. Entscheidungsprotokoll | 40–45 |
| 5. Abschluss und Handoff | 46–51 |
| 6. Primärquellen für technische Integrationsgrenzen | 52–61 |

## 05-ANFORDERUNGSMATRIX.md

[05-ANFORDERUNGSMATRIX.md](05-ANFORDERUNGSMATRIX.md) · 142 Zeilen · `sha256:42e7058fd042441d8d47f65dbe760cde3f681a98e80cbaf165978b429ce5dfb4`

| Abschnitt | Zeilen |
|---|---|
| Vollständige Anforderungsmatrix | 1–142 |

## 07-BROWSER-UPGRADE.md

[07-BROWSER-UPGRADE.md](07-BROWSER-UPGRADE.md) · 262 Zeilen · `sha256:331b72ca3f0d260e6001f87d676914f647769b16ee79c83adde0c6201c467b55`

| Abschnitt | Zeilen |
|---|---|
| Browser-Upgrade für Skaldir | 1–262 |
| 1. Von Willy bestätigte technische Empfehlung | 5–16 |
| 2. Geprüfte Kandidaten | 17–27 |
| 3. Entscheidende Grenze: Zen und bestehende Sitzungen | 28–40 |
| 4. Browser-Layer statt lose Shell-Befehle | 41–58 |
| 5. Geschwindigkeit und korrektes Batching | 59–71 |
| 6. Fensterflut zuverlässig verhindern | 72–79 |
| 7. Subtools und gemischte Workflows | 80–89 |
| 8. Kontrolle, UI und Verifikation | 90–99 |
| 9. Setup-Auftrag an Hermes | 100–111 |
| 10. Abnahmetests | 112–133 |
| 11. Primärquellen und Nachweisgrenzen | 134–145 |
| 12. Konkrete Prüfpunkte im inzwischen vorhandenen Zielcode | 146–161 |
| 13. Minimale Session- und Auftragsverträge | 162–205 |
| Session | 166–178 |
| Einzelaktion | 179–197 |
| Batch | 198–205 |
| 14. Fehlerklassen und erlaubte Reaktionen | 206–222 |
| 15. Umsetzung in überprüfbaren Paketen | 223–259 |
| Paket A: Inventar und Fensterlebenszyklus | 225–233 |
| Paket B: Ein vertikaler strukturierter Browserpfad | 234–243 |
| Paket C: Tabs, Batches, Lesepfad und Performance | 244–247 |
| Paket D: Host/DOM-Wechsel und Wiederaufnahme | 248–251 |
| Paket E: Ein gelerntes Browser-Subtool | 252–255 |
| Paket F: Produktabnahme | 256–259 |
| 16. Copy-paste-Auftrag für Hermes | 260–262 |

