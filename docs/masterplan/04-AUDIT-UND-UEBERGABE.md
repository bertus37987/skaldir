# Umsetzungsaudit, Abgrenzung und Übergabe

## 1. Was dieses Paket ersetzt

Dieses Paket ist die neue maßgebliche Beschreibung der zuletzt geklärten Nutzerwünsche. Frühere Detailpläne bleiben nützliche Referenzen, soweit sie nicht widersprechen. Besonders die alte Annahme einer zeilenorientierten CLI ist durch die gewählte Vollbild-TUI ersetzt; die frühere pauschale Trennung von Hintergrundarbeit und Nutzerdesktop ist durch erlaubte sichtbare exklusive Hostkontrolle präzisiert.

Den vorherigen Slash-Anhang nicht unbemerkt überschreiben. Es existierten zwei Paketkopien, von denen eine einen neueren Abschnitt 20 enthielt. Der neue Masterplan wird bewusst als separates konsistentes Paket ausgeliefert. Alte Anhänge enthalten keine aktuelle vollständige Produktabnahme.

## 2. Produktentscheidungen versus Implementierungsentscheidungen

**Verbindlich:** Hauptagent für Code, parallele Tools, Worker für Exploration, echter Fedora-Host, X11/Wayland-Ziel, globale Doppel-Escape-Pause, Desktoplease/Warteschlange, getestete automatisch genutzte Subtools, allgemeiner/Container-Builder, kombinierbare Workflows, Jev/Voice/VERIFY-Rollen, Money-/Irreversibilitätsapprovals, dauerhafte Runs, selektives Markdown-Memory und gewählte TUI.

**Vorschläge:** genaue Feldnamen, State-Enums, 500-ms-Doppeldruckfenster, Performancezielwerte, SearXNG als Suchprovider, konkrete Testobjekte, Layoutproportionen, Default-Approval-Persistenz. Bestehende bessere Mechanismen bevorzugen und Entscheidungen dokumentieren.

**Noch offen:** konkrete Video-/Kreativanwendung und Testassets, Defaultkostenbudget sowie grundlegende AGENTS.md/SOUL.md-Selbständerungsrechte. Die Tool-Detailtiefe, Tab-Regel und Ecke des Hostindikators sind inzwischen entschieden; siehe Datei 03. Nicht alle offenen Punkte verlangen jetzt eine Nutzerfrage: lokale Fakten zuerst feststellen, optionale Produktdetails später gezielt klären. Unabhängige Arbeit fortsetzen.

## 3. Anforderungsaudit

Die automatisch erzeugte [05-ANFORDERUNGSMATRIX.md](05-ANFORDERUNGSMATRIX.md) listet alle Anforderungs-IDs. Sie ist eine Arbeitsvorlage, keine vorausgefüllte Bewertung des Codes.

Für jeden Eintrag ergänzen:

```text
ID:
Status:
Aktiver Nutzereinstieg:
Reale Dateien/Symbole:
Geprüfter HEAD plus relevante Arbeitsbaumrevision:
Beobachtetes Verhalten:
Lücke:
Entscheidung: behalten / reparieren / erweitern / begründet ersetzen
Test-IDs und tatsächlich ausgeführte Befehle:
Nachweisgrad: statisch / Vertrag / Integration / Live
Belege:
Restgrenze:
```

Kein „vorhanden“ nur aufgrund eines Namens. Umgekehrt keine doppelte Implementierung, wenn vorhandener Executor oder vorhandene Registry die Anforderung bereits erfüllt. Pro zusammenhängendem Change mehrere IDs abdecken, wenn dieselbe Zuständigkeit betroffen ist.

## 4. Entscheidungsprotokoll

Bei einer größeren Änderung festhalten: welches reproduzierte Problem, warum vorhandene Lösung nicht genügt, kleinstmögliche Alternative, Zuständigkeit, Datenmigration, Rückweg, Abnahme und messbarer Nutzen. Bei nicht bestätigter Ursache „Hypothese“ schreiben.

Vereinbarte Produktgrenzen nicht aus Bequemlichkeit entfernen. Ein Wayland-Blocker verlangt präzisen Status und einen konkreten Integrationspfad; bloß auf X11 umzustellen erfüllt das Zweiplattformziel nicht. Ein fehlender kostenloser Searchprovider verlangt Fehler/Fallback, keinen erfundenen Treffer.

## 5. Abschluss und Handoff

Abschlussbericht gliedern in tatsächlich implementiert, automatisiert geprüft, live bestätigt, extern blockiert und noch offen. Codezustand und Datum jeder Messung nennen. Keine historischen Fullsuite-Zahlen als Beleg nach späteren Änderungen verwenden.

Handoff für nächste Sitzung: Projektzustand, Änderungen, offene Prioritäten, laufende Ressourcen und sichere Statusabfrage. PIDs nicht als dauerhafte Identität behandeln. Nutzerfenster und gemeinsame Änderungen erhalten. Aktuelle Paketversion und Fundstelle dokumentieren.

## 6. Primärquellen für technische Integrationsgrenzen

Am 25. September 2026 geprüft:

- [SearXNG Search API](https://docs.searxng.org/dev/search_api.html): strukturierte Suchschnittstelle; öffentliche Instanzen können Ausgabeformate einschränken. Kein Verfügbarkeitsversprechen.
- [Playwright Browser](https://playwright.dev/python/docs/browsers): unterstützte Browser/Builds. Keine automatische Zusage für bestehende beliebige Zen-Profile.
- [XDG GlobalShortcuts](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html): globale Shortcuts unabhängig vom Appfokus; konkrete Doppel-Escape-Sequenz und Backend separat prüfen.
- [XDG RemoteDesktop](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html): sitzungsbezogene Eingabe-/Desktopintegration; Hostunterstützung und Rechte separat prüfen.

Diese Quellen begründen technische Optionen und Grenzen. Die Architektur und Abnahmen dieses Pakets sind daraus und aus den Nutzerentscheidungen abgeleitete Entwurfsentscheidungen, keine von den Quellen garantierten Skaldir-Fähigkeiten.
