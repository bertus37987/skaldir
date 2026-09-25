# CAPABILITY-GAPS — Was Menschen im Internet können, Agenten aber (noch) fehlen

Stand: 11.09.2026. Zweck: ehrliche Landkarte für Skaldir Agent. Spaltenlogik je Fähigkeit:
**Stand heute** → **Wie wir es angehen** → **Bleibt Mensch?** Prioritäten: P1 = Kern für Skaldir Agent, P2 = später, P3 = bewusst menschlich.

---

## 1. Identität & Konten

| Fähigkeit | Stand heute | Wie wir es angehen | Mensch? |
|---|---|---|---|
| Konto anlegen (Signup) | Captchas, E-Mail-Verify, Telefonnummer, „Sind Sie ein Mensch?" blocken Agenten | Masked-E-Mail (Alias) + Passwortmanager; dedizierte Identität (§5 DESIGN-2); Captcha-Fallbacks (Mensch im Loop oder lizenzierte Solver, ToS prüfen) | Teilweise (Captcha/Telefon) |
| Identitätsnachweis (KYC) | Ausweis-Hochladen, Selfie-Video, Adressnachweis → unmöglich ohne Mensch | Gar nicht anstreben; Nutzer bleibt der Kontoinhaber | **Ja** |
| Zwei-Faktor / Passkeys | TOTP machbar; WebAuthn/Passkeys an Geräte gebunden; SMS-Codes brauchen Nummer | TOTP im Tresor; Passkey auf dediziertem Gerät/Hardware-Key des Nutzers; SMS nur mit dedizierter Nummer | Teilweise |
| AGB „akzeptieren" | Klick = Rechtsgeschäft; ohne Zustimmungs-Berechtigung heikel | Nur mit Nutzer-Approval; Vertragstexte zusammenfassen + markieren | **Ja** (Zustimmung) |
| Reputation über Zeit | Neue Accounts wirken jung; Karma/Trust brauchen Historie | Ledger + konsistente Persona; Geduld statt Tricks | Nein (langfristig lösbar) |

## 2. Rechtlich handeln

| Fähigkeit | Stand heute | Wie wir es angehen | Mensch? |
|---|---|---|---|
| Verträge schließen/kündigen | Rechtsfolge; Agent ist nicht rechtsfähig | Agent bereitet vor (Analyse, Dokument, Portal ausfüllen bis „Absenden"), Mensch unterschreibt/sendet | **Ja** |
| Rechnungen/Steuern | Buchhaltung machbar, Abgabe oft registriert | Agent als Vorbereiter/Prüfer, nie alleiniger Steuerpflichtiger | **Ja** |
| DSGVO/Compliance | Rollen, Löschfristen, Auskünfte | Agent überwacht Fristen, formuliert Anfragen | Teilweise |

## 3. Bezahlen & Handel

| Fähigkeit | Stand heute | Wie wir es angehen | Mensch? |
|---|---|---|---|
| Karten/SEPA/PayPal zahlen | 3DS/SCA verlangt oft den Menschen; Fraud-Systeme blocken Bots; Secret-Handling kritisch | Treasury-Stufen (0→2); Mandate (AP2); virtuelle Karten mit Caps; Approval über 2FA | Teilweise (bleibt beim Menschen bis Stufe 2, dann nur Kleinbeträge) |
| Abos abschließen/kündigen | Klicken bis Vertrag; Kündigungs-Labyrinthe | Agent findet/bereitet vor; Kündigung = Approval; Fristen im Ledger | Teilweise |
| Rückerstattungen/Disputes | Verhandlung, Fristen, Beweise | Agent führt den Schriftverkehr, Mensch entscheidet Eskalationen | Teilweise |
| Preisvergleich/Verhandlung | Agent recherchiert super; echte Verhandlung (Mensch-Mensch) fehlt | Vergleich + Vorschlag; Verhandlung über Chat meist möglich, Telefon nicht | Teilweise |

## 4. Telefon & Live-Mensch-Interaktion

| Fähigkeit | Stand heute | Wie wir es angehen | Mensch? |
|---|---|---|---|
| Anrufe (Service, Termine, IVR) | Voice-Agenten können IVR & einfache Calls; Menschen merken Bot sofort; rechtliche Grauzone (Aufzeichnung, Zustimmung) | Optionales Modul (Telefonie-Provider + Voice); nur mit Offenlegung; **kein** Call ohne Zweck und Freigabe | Meistens |
| Video-Calls | Interface da, Präsenz/Verhandlung schwach | Nicht anstreben | **Ja** |
| Überzeugen/Empathie | LLMs wirken freundlich, aber verhandeln schlecht unter Druck | Voice schlägt Formulierungen vor, Mensch agiert | **Ja** |

## 5. Anti-Bot & CAPTCHA

| Fähigkeit | Stand heute | Wie wir es angehen | Mensch? |
|---|---|---|---|
| „I am human"-Nachweise | Detektion über Fingerprint, Verhalten, Netzwerk; Löser-Dienste existieren („usable assistive tech" argumentieren Anbieter) | Grundsatz: **Respect over Evasion** — faire Frequenz, robots.txt, echte Werte; Captcha geschäftlich = Mensch oder lizenzierter Dienst (ToS!) | Oft |
| Un-Detection (patchright/camoufox) | Technisch möglich, aber Wettrüsten | Nur für legitime, eigene Logins (dediziertes Profil), nie für Scraping-Exzesse | Politik entscheidet |

## 6. GUI & Apps ohne API → **THE HANDS**

| Fähigkeit | Stand heute | Wie wir es angehen | Mensch? |
|---|---|---|---|
| Desktop-Apps bedienen (Fachsoftware, lokale Tools) | Computer Use kann es prinzipiell; Erfolgsraten niedrig (~31 % binär auf OSWorld 2.0, 08/2026), langsam, flaky | THE HANDS: VM-first, A11y+Vision hybrid, Verifikationsschleife, Aufzeichnung; API > Browser > Desktop | Teilweise (immer in VM, mit Review) |
| Apps **testen** (GUI-E2E) | Playwright für Web sehr gut; Desktop-Tests selten automatisiert | Test-Suite-Generator: Touren, Assertions, reproduzierbare Fehlerberichte | Nein (langfristig Agenten-Aufgabe) |
| Multitasking/Shortcuts „aus dem Muskelgedächtnis" | Agents sind seriell; Shortcuts situativ | Task-Graph + Parallelisierung (Careful: geteilte Zustände) | Nein |

## 7. Sehen & Verifizieren

| Fähigkeit | Stand heute | Wie wir es angehen | Mensch? |
|---|---|---|---|
| Tutorials/Video gucken und umsetzen | Long-Video-Verstehen bröckelt; Umsetzung fehlt | Video-Transkript + Keyframes → Skill-Entwurf (Forge-Mining aus Medien!) | Teilweise |
| „Sieht das richtig aus?" (UI, Ergebnis, Produkt) | Vision-Fähigkeiten da, Urteil schwach; OSWorld nennt „visual-spatial precision" als Kernlücke | Screenshots als Pflichtbeleg in Delivery-Reports; Voice fordert „zeig mir den Beleg" | Teilweise |
| Betrug/Phishing „riechen" | Agenten sind gute Phishing-Opfer (Injection-Vektor #1) | Trust-Tiers für Quellen, Spotlighting, Domain-Reputations-Checks, Zahlungs-Stopp | Teilweise |

## 8. Soziale Normen & Plattformkompetenz

| Fähigkeit | Stand heute | Wie wir es angehen | Mensch? |
|---|---|---|---|
| Ton, Timing, Frequenz | Agents overposten; Spamfilter fressen kalte Mails; Community-Regeln unbekannt | Plattform-Regelwerk als Note; Frequenz-Limits; Entwurf → Freigabe-Flow für heikle Kanäle | Teilweise |
| E-Mail an Fremde (Deliverability) | Spamfilter; Domain-Reputation; Kalte Mails | Eigene Domain mit SPF/DKIM/DMARC, warmup; Volumen-Limits; Opt-in bevorzugt | Teilweise |
| Moderations-/Sanktionsrisiko | Formal meist im Griff; Regeln ändern sich | Night-Shift prüft „verbundene Konten/Regeländerungen"; Banner: „diese Seite könnte ToS-kritisch sein" | Teilweise |

## 9. Langfristigkeit & Gedächtnis → **THE LEDGER**

| Fähigkeit | Stand heute | Wie wir es angehen | Mensch? |
|---|---|---|---|
| Über Wochen/Monate an etwas dranbleiben | Sessions „vergessen"; Agents springen | THE LEDGER + Night Shift + Cron: Fristen, Wiedervorlagen, Statuspflege | Nein (Cron + Ledger) |
| Beziehungen pflegen | Kontakte, Kontext, Anlässe | Kontakt-Notizen (L2), Erinnerungen (Cron) | Teilweise |
| „Das war vor 3 Wochen"—Kontinuität | Kontextfenster endet; Memory hilft, aber unscharf | Ledger + Session-Archiv + Retrieval; sichtbar in jeder Session | Nein |

## 10. Physische Welt

| Fähigkeit | Stand heute | Wie wir es angehen | Mensch? |
|---|---|---|---|
| Post, Unterschrift, Bargeld, Gegenstände | Unmöglich | Agent bereitet vor, koordiniert Lieferungen, Mensch vollstreckt | **Ja** |

## 11. Verantwortung & Urteil

| Fähigkeit | Stand heute | Wie wir es angehen | Mensch? |
|---|---|---|---|
| Haftung tragen | Agent kann nicht haften | Deshalb: Approvals, Audit, Mandate, „der Mensch ist Kontoinhaber" im Design | **Ja** |
| Ethik in Graubereichen | LLMs haben Heuristiken, keine Verantwortung | Voice markiert Graubereiche und eskaliert an den Menschen | **Ja** |
| Consent/Privatheit Dritter | Agenten überschreiten Grenzen leicht | Regeln: keine Dritt-Daten ohne Zweck; Recording nur mit Hinweis | **Ja** |

## 12. Was NICHT auf diese Liste gehört

Agenten sind Menschen schon heute **überlegen** bei: Geschwindigkeit bei Lesen/Suchen/Vergleichen, 24/7-Verfügbarkeit, paralleles Verarbeiten vieler Quellen, Gedächtnis *wenn* es strukturiert ist (unser Projekt!), Konsistenz bei Standards. Das Ziel ist nicht „Mensch-Kopie", sondern: **menschlich vergleichbar in den Lücken oben (P1), überlegen in Tempo/Ausdauer, und Mensch-behalten bei Verantwortung und physischer Welt.**

---

## Priorisierung für Skaldir Agent
- **P1 (bauen):** GUI-/App-Bedienung & App-Tests (Hands), Langfristigkeit (Ledger), Anti-Halluzination/Verifikation (Voice), Konto-Login light (dedizierte Identität), API/Handel „vorbereiten".
- **P2 (später):** Deliverability/Mail, Video-Verstehen, IVR-Telefonie, Preis-Verhandlung.
- **P3 (bewusst Mensch):** KYC, Vertragsabschluss, Haftung, echte Verhandlung, physische Welt.
