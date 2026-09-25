# Arbeitsauftrag an den Coding-Agenten

Stand: 25. September 2026. Dieses Paket konsolidiert die zuletzt ausdrücklich geklärten Nutzerwünsche. Es ist ein Implementierungsauftrag auf Basis des bestehenden Skaldir, kein Auftrag für ein neues Produkt von null.

Lies zuerst [01-MASTERPLAN.md](01-MASTERPLAN.md), dann [02-ABNAHME.md](02-ABNAHME.md). Für die Oberfläche gilt [03-TUI-ENTSCHEIDUNGEN.md](03-TUI-ENTSCHEIDUNGEN.md). Der neue explizite Nutzerentscheid lautet **Vollbild-TUI mit getrennten Bereichen für Chat, Tools und Goal**. Die vier UI-Fragen sind beantwortet; Datei 03 enthält die verbindlichen Antworten. Vor Umsetzung zuerst den Transferauftrag `../../TRANSFER-UND-IMPLEMENTIERUNG.md` lesen.

## Auftrag

1. Prüfe Repository-Regeln, tatsächlichen Arbeitsbaum, aktiven Einstieg, Provider und bestehende Implementierung.
2. Ordne jede Anforderungs-ID dieses Pakets realen Codepfaden und bestehenden Tests zu.
3. Behalte funktionierende Mechanismen; integriere, repariere und ergänze die Lücken in kleinen zusammenhängenden Änderungen.
4. Prüfe früh technische Machbarkeit von globalem Doppel-Escape und Host-Desktop-Steuerung auf Fedora X11 und Wayland.
5. Arbeite selbstständig weiter bis zu den anwendbaren Abnahmen. Eine ausgefüllte Gap-Matrix oder weitere Konzeptbeschreibung allein erfüllt den Auftrag nicht.
6. Bei echter externer Blockade den Zustand ehrlich dokumentieren und unabhängige Arbeit fortsetzen.
7. Liefere konkrete Änderungen, tatsächlich ausgeführte Tests, offene Grenzen und einen aktualisierten Handoff.

## Vorhandener Projektkontext

**Neues Zielrepository:** `/home/willy/Agentic_Projects/Skaldir_Agent`. Das alte Projekt wird anhand des Transferauftrags selektiv übernommen; ab dann ausschließlich hier implementieren.

Gemeldetes und zuletzt lesend gefundenes Repository: `/home/willy/projects/gods-voice`. Der beim vorherigen Slash-Audit beobachtete HEAD war `a8fcaa1beb2cd3da81262e46b6378d8ecd66cc0a`; ein umfangreicher dirty tree lag vor. Dies ist kein aktueller unveränderlicher Codezustand. Nutzeränderungen bewahren; keine pauschalen Resets, keine ungefragten Commits/Pushes.

Historische Testzahlen und Handoff-Aussagen sind Hinweise, keine aktuelle Produktabnahme. Das vorhandene `AGENTS.md` nennt die acht Layer EXPAND, ACT, COMPOSE, ROUTE/Jev, SUPERVISE/Voice, VERIFY, ENFORCE und PERSIST. Diese Architektur sinnvoll weiterverwenden.

## Vorrang der aktuellen Entscheidungen

Gegenüber älteren Plänen jetzt verbindlich:

- Host-Desktop und vorhandene Anwendungen/Konten sind Ziel des Hauptagenten; kein stiller Ersatz durch isolierten Browser.
- Sichtbare exklusive Desktopkontrolle darf länger dauern; Warteschlange ist erwünscht.
- Doppel-Escape muss global, auch ohne fokussierte CLI, funktionieren.
- Approve Everything benötigt weiterhin Zustimmung für neue Geldausgaben und irreversible Schäden.
- Nur der Hauptagent verändert Projektcode; Subagents für größere Recherche/Exploration, bei Bedarf mit isolierter Container-GUI.
- Getestete und bereinigte Subtools werden automatisch aktiv und wiederverwendet.
- Jev ist schnelle Entscheidungs-/Triageebene, Voice Supervisor, VERIFY separate Ergebnisprüfung.
- Terminalschließen ist kein Stop; ausdrücklicher Stop bleibt über Neustarts erhalten.
- Vollbild-TUI ist gewählt. Rote klare Blocker-/Fehlermeldungen sind ausdrücklich gewünscht; nicht nur Farbe als Signal verwenden.
- Breites selektives Markdown-Memory, projektbezogene AGENTS.md und SOUL.md, minimale unnötige Kontextlast.

Die Anforderungen sind deutsch dokumentiert. Produkttexte vorerst in der bestehenden englischen Produktsprache belassen, bis der Nutzer ausdrücklich eine andere Sprache wählt. Der Desktop-Indikator kann entsprechend „Skaldir controls the desktop“ heißen. Eine kleine Host-Status-/Notstopp-Integration ist erforderlich, aber keine neue allgemeine Desktop-App.

## Erwartete Arbeitsartefakte im Repository

An bestehende Dokumentationsstruktur anpassen: Implementierungsaudit, Entscheidungen, Testergebnisse, Performance-Baseline, Fedora-Backend-Matrix und Handoff. Pro Anforderung mindestens Status, Codepfad, aktiver Einstieg, Nachweisgrad, Test-ID und Restlücke. Statuswerte: fehlt / teilweise / implementiert-unbestätigt / getestet / live-bestätigt / extern-blockiert / begründet-nicht-anwendbar.

Keinen globalen „alles fertig“-Status aus Anzahl registrierter Commands oder grüner Unit-Tests ableiten.

## Paketübersicht

| Datei | Zweck |
|---|---|
| [01-MASTERPLAN.md](01-MASTERPLAN.md) | Produktentscheidungen, 134 Anforderungen, Architektur und Umsetzungsetappen |
| [02-ABNAHME.md](02-ABNAHME.md) | 52 technische Szenarien, Fedora-Matrix und echte Produktaufgaben |
| [03-TUI-ENTSCHEIDUNGEN.md](03-TUI-ENTSCHEIDUNGEN.md) | Bestätigte Vollbild-TUI, dezenter Stil und bestätigte UI-Details |
| [04-AUDIT-UND-UEBERGABE.md](04-AUDIT-UND-UEBERGABE.md) | Auditverfahren, Abgrenzungen und Quellen |
| [05-ANFORDERUNGSMATRIX.md](05-ANFORDERUNGSMATRIX.md) | Vollständige ID-Liste für den Codeabgleich |
| [06-LESEINDEX.md](06-LESEINDEX.md) | Exakte Abschnittszeilen und SHA-256-Hashes |
