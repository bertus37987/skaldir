/**
 * Gesprächsverlauf (Kategorie AppChat) — Nachrichten, Antworten, Codeblöcke,
 * Werkzeugzeilen, Fehler, Anhänge, Meta-Zeilen und Delegation im Verlauf.
 *
 * Quelle (nur lesen): /home/willy/.hermes/godsv/app-src/apps/desktop/src/
 *   components/assistant-ui/thread/{user-message,assistant-message,timeline-timestamp}.tsx
 *   components/assistant-ui/markdown-text.tsx, thread/user-message-text.tsx
 *   components/assistant-ui/tool/{fallback,delegate}.tsx
 *   components/chat/code-card.tsx, chat/scaffold-row.tsx
 * Extraktionsbericht: designs/APP-EXTRAKTION/02-chat.md
 *
 * 8 Varianten, hell/dunkel-Umschalter im Kopf jeder Buehne. Nur Schwarz/Weiss/
 * Grau, Symbole nur aus @tabler/icons-react, Bewegung mit Feder.
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useState } from 'react'
import { IconCheck, IconChevronRight, IconCopy, IconCornerDownRight, IconExternalLink, IconFileText, IconPaperclip, IconRotateClockwise, IconX } from '@tabler/icons-react'

// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'

import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'

/* ---------------------------------------------------------------- Grundmaße
 * Extrahiert: --conversation-text-font-size 0.8125rem (13px) ist die Schrift
 * des Verlaufs, Assistent line-height 1.5, Nutzernachricht 1.3. Spalte:
 * max-width min(86%, 44rem), zentriert. Scaffolding-Text 0.6875rem (11px) in
 * 18px Zeile, Meta 0.625rem (10px) tabular. Werkzeugkörper-Gap 6px.
 */
const MSG_TEXT = 13
const MSG_LINE_ASSIST = 1.5
const MSG_LINE_USER = 1.3
const TOOL_TEXT = 11
const TOOL_LINE = 18
const META_TEXT = TEXT.meta
const COL_MAX = 560

/** Verlaufsspalte: wie min(86%,44rem) der Original-App, zentriert. */
const Col = ({ children }) => (
  <div style={{ maxWidth: COL_MAX, width: '100%', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 6 }}>
    {children}
  </div>
)

/** Nutzerzeile über der Blase: "Message from Sender" — 0.6875rem/20px, halbes Grau, zentriert. */
const SenderLine = ({ P, children, trailing }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '2px 8px', fontSize: TEXT.meta, lineHeight: '20px', color: P.sub, alignSelf: 'center' }}>
    <IconCornerDownRight size={13} stroke={2} color={P.faint} />
    <span>{children}</span>
    {trailing && <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, tabularNums: true, color: P.faint }}>{trailing}</span>}
  </div>
)

/* -------------------------------------------------------- 01 · Nutzernachricht
 * Quelle: thread/user-message.tsx:67-68 (USER_BUBBLE_BASE_CLASS), user-text 13px/1.3.
 * Blase: border, px-3 py-2 (12px/8px), rechtsbündig, Hintergrund = Bubble-Surface;
 * Zeilenklemme auf 4 Zeilen mit weichem Masken-Fade unten (55% -> transparent).
 */
function Nutznachricht ({ P, dark }) {
  return (
    <Col>
      <SenderLine P={P} trailing='14:32'>Message from Nutzer</SenderLine>
      <motion.div data-chatbubble initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
        style={{ alignSelf: 'flex-end', width: '86%', maxWidth: '44em', padding: '8px 12px', borderRadius: RADIUS.module,
          background: P.surface, border: `1px solid ${P.line}`, textAlign: 'left',
          fontSize: MSG_TEXT, lineHeight: MSG_LINE_USER, color: P.ink, fontFamily: SANS, overflow: 'hidden' }}>
        Lies tools/ui-workbench/fixtures/ToolCards.fixture.tsx und nimm die Canvas-Werte (maxWidth 420, Radius 12)
        für die Werkzeugkarten im Skaldir-UI — nur die Maße, nicht die Farben.
      </motion.div>
    </Col>
  )
}

/* -------------------------------------------------------- 02 · Assistenten-Antwort mit Markdown
 * Quelle: thread/assistant-message.tsx:253, markdown-text.tsx.
 * Wirft Darstellung: padding-left =<message-text-indent>, 13px/1.5, breite Textflaeche,
 * Schwaerze: Absatz-Abstand 0.5em oben (Paragraphen-Gap), keine Blase. Kursiv-Feinheiten entfallen;
 * Inline-Code: eigene kleine Fläche → siehe 03.
 */
function Assistentenantwort ({ P, dark }) {
  return (
    <Col>
      <motion.div data-assistprob initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
        style={{ width: '100%', maxWidth: '44em', padding: '0 0 0 16px', fontSize: MSG_TEXT, lineHeight: MSG_LINE_ASSIST,
          color: P.ink, fontFamily: SANS }}>
        <p style={{ margin: 0 }}>Das Werkzeug-kapitel ist fertig: alle acht Karten<strong> halten Abstand</strong>,
          die Spur klappt mit Feder.</p>
        <p style={{ margin: '8px 0 0' }}>Details in <span style={{ fontFamily: MONO, fontSize: 11, padding: '1px 5px',
          background: P.hair, borderRadius: RADIUS.micro, color: P.ink }}>02-chat.md</span>,
          orphan-Fall (08) listet die Standlücken.</p>
        <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
          <li style={{ marginBottom: 4 }}>Hell: Druck LIGHT.bg, Flaeche LIGHT.surface.</li>
          <li>Dunkel: beide Werte kippen mit der Palette.</li>
        </ul>
      </motion.div>
    </Col>
  )
}

/* -------------------------------------------------------- 03 · Codeblock im Verlauf
 * Quelle: components/chat/code-card.tsx — CodeCard: rounded-[0.625rem] (10px), Hintergrund
 * = --ui-bg-editor, KEIN Rand, keine Kopfzeile, kein Sprachlabel. Body: font-mono 0.7rem (11.2px)
 * leading-relaxed, <pre> px-2 py-1.5 (8px/6px), horizontale Scrollbar. Copy-Druck erscheint
 * unten-rechts bei hover (opacity 0 -> 1). Codezeilen aus der Original-App.
 */
function Codeblock ({ P, dark }) {
  const [copied, setCopied] = useState(false)
  const code = [
    "--conversation-text-font-size: 0.8125rem;",
    "--conversation-line-height: 1.125rem;",
    "--conversation-turn-gap: 0.375rem;",
    "--conversation-tool-font-size: 0.6875rem;"
  ]
  return (
    <Col>
      <motion.div data-codecard initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
        whileHover={{ y: -1 }} style={{ position: 'relative', borderRadius: RADIUS.module, background: P.hair,
          overflow: 'hidden', padding: '6px 8px', fontFamily: MONO, fontSize: 11.2, lineHeight: 1.55, color: P.ink }}>
        {code.map((l, i) => (
          <div key={l} style={{ whiteSpace: 'pre', display: 'flex' }}>
            <span style={{ color: P.faint, width: 18, flexShrink: 0 }}>{i + 1}</span>
            <span>{l}</span>
          </div>
        ))}
        <motion.button data-copybutton onClick={() => setCopied(true)} whileTap={{ scale: 0.92 }} transition={SPRING}
          style={{ position: 'absolute', top: 6, right: 6, display: 'inline-flex', alignItems: 'center', gap: 4,
            fontSize: TEXT.meta, fontFamily: MONO, color: P.ink, background: P.surface, border: `1px solid ${P.line}`,
            borderRadius: RADIUS.row, padding: '3px 7px', cursor: 'pointer' }}>
          {copied ? <IconCheck size={10} stroke={2.4} /> : <IconCopy size={10} stroke={2} />}
          {copied ? 'kopiert' : 'kopieren'}
        </motion.button>
      </motion.div>
    </Col>
  )
}

/* -------------------------------------------------------- 04 · Werkzeug-Aufruf (Laufzeile)
 * Quelle: tool/fallback.tsx (ScaffoldRow, ToolRunHeader) + chat/scaffold-row.tsx:
 *  - Glyphenzelle size-3.5 (14px), gleucher Rahmen auf jeder Zeile
 *  - Label 0.6875rem (11px) in 18px Zeile, Ruftext ca. 64% des Tints, Meta 10px tabular ~44%
 *  - Caret ChevronRight 10px, rotiert 90 Grad wenn offen, dabei Zeitstempel rechts.
 * Der Orb haengt nicht am Kopf: Live-Text shimmer. Hier working-Orb size 20 — im Original GvOrb 16.
 */
function WerkzeugAufruf ({ P, dark }) {
  const [open, setOpen] = useState(false)
  return (
    <Col>
      <div data-scaffoldrow onClick={() => setOpen(o => !o)} style={{ cursor: 'pointer', borderRadius: RADIUS.row, padding: '3px 8px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <motion.span animate={{ rotate: open ? 90 : 0 }} transition={{ duration: 0.15 }}
            style={{ display: 'inline-flex', color: P.faint, width: 14, justifyContent: 'center' }}>
            <IconChevronRight size={10} stroke={2.2} />
          </motion.span>
          <span style={{ display: 'inline-flex', alignItems: 'center' }}>
            <ThinkingOrb state='working' size={20} theme={dark ? 'dark' : 'light'} />
          </span>
          <motion.span animate={{ opacity: [1, 0.55, 1] }} transition={{ duration: 1.6, repeat: Infinity, ease: 'easeInOut' }}
            style={{ fontSize: TOOL_TEXT, lineHeight: '18px', color: P.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            Liest 3 Dateien und prueft die Canvas-Werte</motion.span>
          <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 8, fontFamily: MONO, fontSize: META_TEXT, color: P.faint, tabularNums: true }}>
            <span>4 Aufrufe</span><span>14:32</span>
          </span>
        </div>
        <AnimatePresence initial={false}>
          {open && (
            <motion.div key='ergebnis' data-toolcollapse initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
              <div style={{ marginTop: 4, marginLeft: 20, display: 'flex', flexDirection: 'column', gap: 4 }}>
                {[['tools/ui-workbench/fixtures/ToolCards.fixture.tsx', '0,4 s'],
                  ['tools/ui-workbench/fixtures/ChatBarRef.fixture.tsx', '0,2 s'],
                  ['designs/APP-EXTRAKTION/02-chat.md', '0,1 s']].map(([f, t], i) => (
                  <motion.div key={f} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: i * 0.05 }}
                    style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: TOOL_TEXT, lineHeight: '18px', color: P.sub,
                      borderBottom: i === 2 ? 'none' : `1px solid ${P.hair}`, paddingBottom: i === 2 ? 0 : 3 }}>
                    <span style={{ color: P.ink, fontFamily: MONO, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f}</span>
                    <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{t}</span>
                  </motion.div>
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </Col>
  )
}

/* -------------------------------------------------------- 05 · Werkzeug-Ergebnis (aufgeklappt)
 * Quelle: tool/fallback.tsx:626-742 — Aufklappkoerper: gap-1.5 (6px) im Block, Kopieren
 * (Copy Button 3.5em) rechts oben bei hover; stdout/stderr als <pre> in TOOL_SECTION_PAYLOAD_Class:
 * max-h-20 (80px), px-2 py-1.5, mono 0.65rem (10.4px) bzw. 0.7rem (11.2px); stderr bekommt
 * eigens Label. Rahmenshell: rounded-[5px] (0.3125rem), 1px stroke.
 */
function WerkzeugErgebnis ({ P, dark }) {
  return (
    <Col>
      <motion.div data-toolblock initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
        style={{ borderRadius: RADIUS.micro, border: `1px solid ${P.line}`, overflow: 'hidden', maxWidth: COL_MAX, background: P.surface }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '5px 8px' }}>
          <IconCheck size={14} stroke={2.2} color={P.ink} />
          <span style={{ fontSize: TOOL_TEXT, lineHeight: '18px', color: P.ink, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            ruff check src tests tools</span>
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, tabularNums: true }}>1,2 s · exit 0</span>
        </div>
        <div data-toolexperiment style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: 6, borderTop: `1px solid ${P.hair}` }}>
          <div style={{ position: 'relative' }}>
            <span style={{ display: 'block', marginBottom: 4, fontSize: TEXT.meta, fontWeight: 600, letterSpacing: 1.1, textTransform: 'uppercase', color: P.faint }}>stdout</span>
            <pre style={{ margin: 0, maxHeight: 80, overflow: 'auto', padding: '6px 8px', fontFamily: MONO, fontSize: TEXT.meta, lineHeight: 1.55, color: P.ink,
              whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{`checks: src tests tools
0 errors, 0 warnings`}</pre>
            <span data-tinyhover style={{ position: 'absolute', top: 0, right: 0, display: 'inline-flex', alignItems: 'center', gap: 4,
              fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, cursor: 'pointer', opacity: 0.5 }}>
              <IconCopy size={10} stroke={2} /> kopieren
            </span>
          </div>
          <div>
            <span style={{ display: 'block', marginBottom: 4, fontSize: TEXT.meta, fontWeight: 600, letterSpacing: 1.1, textTransform: 'uppercase', color: P.faint }}>stderr</span>
            <pre style={{ margin: 0, padding: '6px 8px', fontFamily: MONO, fontSize: TEXT.meta, lineHeight: 1.55, color: P.faint,
              whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>()</pre>
          </div>
        </div>
      </motion.div>
    </Col>
  )
}

/* -------------------------------------------------------- 06 · Fehler im Verlauf
 * Quelle: assistant-message.tsx:260-278 — Error-Card: alles in --dt-destructive-Tint
 * (Rand 35% Blend, Fuellung 7%, Text 78% mix), 0.78rem (12.5px) leading 5 (20px), px-3 py-2,
 * rounded-lg (8px). Kopf = Schlagzeile + Dismiss-X-Druck (14px), darunter Knopfreie
 * (aui-error-action) mit 11px-Labels und 3px-Icons (KeyRound, RotateCcw, chooser).
 * Grau-Alternative: swarm evaluierbar — hier nur mit Reduce-Wegen.
 */
function Fehler ({ P, dark }) {
  const [dismissed, setDismissed] = useState(false)
  return (
    <Col>
      <AnimatePresence>
        {!dismissed && (
          <motion.div data-errorcard initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, height: 0 }} transition={SPRING}
            style={{ width: '100%', maxWidth: '44em', marginTop: 6, padding: '8px 12px', borderRadius: RADIUS.row,
              border: `1px solid ${P.ink}`,
              background: P.soft,
              fontSize: 12.5, lineHeight: '20px', color: P.ink, fontFamily: SANS }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600 }}>Modell Antwort konnte nicht erzeugt werden</div>
                <div style={{ opacity: 0.8, marginTop: 2 }}>Nach 3 Versuchen war keine Verbindung zum Anbieter stabil.</div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
                  {[['Wiederholen', IconRotateClockwise], ['anderes Modell waehlen', IconExternalLink], ['neue Sitzung', IconX]].map(([t, Ico], i) => (
                    <motion.span key={t} data-erroraction whileHover={{ scale: 1.04 }} whileTap={{ scale: 0.95 }} transition={SPRING}
                      onClick={() => setDismissed(t === 'Wiederholen')}
                      style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11, padding: '4px 9px',
                        border: `1px solid ${P.ink}`, borderRadius: RADIUS.row, cursor: 'pointer' }}>
                      <Ico size={10} strokeWidth={2} /> {i === 0 ? <b style={{ fontWeight: 600 }}>{t}</b> : t}
                    </motion.span>
                  ))}
                </div>
              </div>
              <motion.span whileHover={{ opacity: 1 }} transition={SPRING} onClick={() => setDismissed(true)}
                style={{ cursor: 'pointer', opacity: 0.7, marginTop: 2 }}><IconX size={14} stroke={2.2} /></motion.span>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </Col>
  )
}

/* -------------------------------------------------------- 07 · Anhang in der Nachricht
 * Quelle: thread/user-message.tsx (attachmentRefs, DirectiveContent, gap-1.5) und
 * components/chat/preview-attachment.tsx — Zeile unterm Blasen: Chip mit Endung,
 * Größe, oeffnet in der Vorschau. Im Original: runde 16px-Avatar bei 'Message from Sender'.
 * Hier: Chip mit 11px-Sans, Pfad Gekuerzt, Knopf oeffnen rechts.
 */
function Anhang ({ P, dark }) {
  return (
    <Col>
      <SenderLine P={P} trailing='14:36'>Message from Nutzer</SenderLine>
      <motion.div data-attachmentbubble initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
        style={{ alignSelf: 'flex-end', width: '86%', maxWidth: '44em', display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{ padding: '8px 12px', borderRadius: RADIUS.module, background: P.surface, border: `1px solid ${P.line}`,
          fontSize: MSG_TEXT, lineHeight: MSG_LINE_USER, color: P.ink, fontFamily: SANS }}>
          Diesem Draft liegt der Sketch zuletzt zugrunde — nutze ihn als Referenz:
        </div>
        <motion.div data-attachment initial={{ opacity: 0, x: 6 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.12 }}
          style={{ alignSelf: 'flex-end', display: 'flex', alignItems: 'center', gap: 8, padding: '5px 9px',
            borderRadius: RADIUS.row, background: P.hair, border: `1px solid ${P.line}` }}>
          <IconPaperclip size={13} stroke={2} color={P.sub} />
          <span style={{ fontSize: 11.5, color: P.ink, fontFamily: SANS }}>sketch-chat-v2.md</span>
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>4,1 KB · md</span>
          <motion.span whileHover={{ scale: 1.04 }} transition={SPRING} data-attachmentopen
            style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: TEXT.meta, fontFamily: MONO,
              padding: '2px 7px', border: `1px solid ${P.line}`, borderRadius: RADIUS.micro, cursor: 'pointer', color: P.ink, background: P.surface }}>
            <IconExternalLink size={9} stroke={2.2} /> oeffnen
          </motion.span>
        </motion.div>
      </motion.div>
    </Col>
  )
}

/* -------------------------------------------------------- 08 · Meta-Zeile (Zeitstempel + gesteuert)
 * Quelle: thread/timeline-timestamp.tsx:79-92 (0.625rem/leading-4/tabular, Tint ~44%),
 * system-message.tsx:117-124 ("steered" Zeile, 0.6875rem/20px, px-2 py-0.5, zentriert,
 * muted-foreground/60; Ausruf "steered" in /55, Trennpunkt · in /35, Zeit hinten).
 */
function MetaZeile ({ P, dark }) {
  return (
    <Col>
      <motion.div data-metarow initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={SPRING} style={{ width: '100%', maxWidth: '44em' }}>
        <div style={{ fontSize: TEXT.meta, lineHeight: '16px', color: P.faint, fontFamily: MONO, tabularNums: true, padding: '1px 8px' }}>
          14:32:07 → 14:33:41
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '2px 8px', fontSize: TEXT.meta, lineHeight: '20px', color: P.sub }}>
          <IconExternalLink size={11} stroke={2} color={P.faint} />
          <span>gesteuert</span>
          <span style={{ color: P.line }}>·</span>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            Zieh die <span style={{ fontFamily: MONO, fontSize: TEXT.meta }}>ToolCard</span>-Werte nach unten</span>
        </div>
      </motion.div>
    </Col>
  )
}

/* -------------------------------------------------------- 09 · Delegierter Aufruf (Delegation)
 * Quelle: tool/delegate.tsx:96-133 — Karte rounded-xl (12px), Rand stroke, px-3 (12px) py-2 (8px),
 * gap-0.5 (2px), Glyph box 14px (GvOrb 16 weaving bzw. Haken 3.5em done). Titel zersellbar
 * (truncate), Meta "modell · 2:14" 10px, darunter Laufleiste pl-5 (20px) mit Finstatisch-Effekt.
 */
function Delegation ({ P, dark }) {
  const activity = ['Erzeugt den Bericht: Chat-Verlauf', 'Grep: tool-fallback.tsx:626:öffne', 'Dokumentiert Wertekorridor: 2-chat.md']
  return (
    <Col>
      <motion.div data-delegatecard initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
        style={{ width: '100%', maxWidth: '44em', padding: '8px 12px', borderRadius: RADIUS.module,
          border: `1px solid ${P.line}`, display: 'flex', flexDirection: 'column', gap: 2, background: P.surface }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
          <span style={{ width: 14, height: 14, flexShrink: 0, display: 'grid', placeItems: 'center' }}>
            <ThinkingOrb state='weaving' size={20} theme={dark ? 'dark' : 'light'} />
          </span>
          <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            fontSize: TOOL_TEXT, lineHeight: '18px', color: P.ink }}>
            Unterauftritt verfassen</span>
          <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, tabularNums: true }}>klarer · local · 01:24</span>
        </div>
        <div style={{ paddingLeft: 20, overflow: 'hidden' }}>
          <motion.div key='ticker' initial={{ y: 10, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={{ ...SPRING, delay: 0.15 }}
            style={{ fontSize: TOOL_TEXT, lineHeight: '18px', color: P.faint, fontFamily: SANS }}>
            {activity[activity.length - 1]}
          </motion.div>
        </div>
      </motion.div>
    </Col>
  )
}

/* -------------------------------------------------------- Buehne + Exporte */

const BUEHNE = {
  hell: LIGHT, dunkel: DARK
}

const Buehne = ({ name, note, children, las = 'hell' }) => {
  useBarrierefrei()
  const [dunkel, setDunkel] = useState(las === 'dunkel')
  const P = dunkel ? DARK : LIGHT
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, width: '100%', maxWidth: 640, boxSizing: 'border-box',
      color: P.ink, textAlign: 'left' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
        <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600, color: P.ink }}>{name}</div>
        <span style={{ flex: 1 }} />
        <motion.span data-themetoggle onClick={() => setDunkel(d => !d)} whileTap={{ scale: 0.94 }} transition={SPRING}
          title='hell/dunkel schalten' style={{ fontFamily: MONO, fontSize: TEXT.meta, padding: '3px 9px',
            border: `1px solid ${P.line}`, borderRadius: RADIUS.pill, cursor: 'pointer', color: P.ink, background: P.surface }}>
          {dunkel ? 'DUNKEL' : 'HELL'}
        </motion.span>
      </div>
      <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginBottom: 12 }}>{note}</div>
      {children({ P, dark: dunkel })}
    </div>
  )
}

export default {
  '01 · Nutzernachricht': () => (
    <Buehne name='01 · Nutzernachricht' note='Bubble 12px-Radius, px-3 py-2, 13px/1.3, Rechtsstuend, Absenderzeile 10.5px'>
      {({ P, dark }) => <Nutznachricht P={P} dark={dark} />}
    </Buehne>
  ),
  '02 · Assistenten-Antwort mit Markdown': () => (
    <Buehne name='02 · Assistenten-Antwort' note='ohne Blase: 13px/1.5, 0 bis 16px Einzug, Aufzaehlungen und Stroeng, Paragraphen-Gap 8px'>
      {({ P, dark }) => <Assistentenantwort P={P} dark={dark} />}
    </Buehne>
  ),
  '03 · Codeblock': () => (
    <Buehne name='03 · Codeblock' note='CodeCard: 10px-Radius, tintierte Flaeche, kein Rand/Kopf/Sprache; Mono 11.2px/1.55, px-2 py-1.5, Copy bei Kopfhoehe'>
      {({ P, dark }) => <Codeblock P={P} dark={dark} />}
    </Buehne>
  ),
  '04 · Werkzeug-Aufruf': () => (
    <Buehne name='04 · Werkzeug-Aufruf' note='ScaffoldRow: Glyph 14px, Label 11px/18px, Meta 10px tabular rechts, Caret dreht 90 Grad, Klappen mit Feder'>
      {({ P, dark }) => <WerkzeugAufruf P={P} dark={dark} />}
    </Buehne>
  ),
  '05 · Werkzeug-Ergebnis': () => (
    <Buehne name='05 · Werkzeug-Ergebnis' note='Shell 5px-Radius, stdout/stderr als Label-Blöcke, Mono 10.4/10.4px, max-h 80px, Copy bei hover'>
      {({ P, dark }) => <WerkzeugErgebnis P={P} dark={dark} />}
    </Buehne>
  ),
  '06 · Fehler im Verlauf': () => (
    <Buehne name='06 · Fehler im Verlauf' note='12.5px/20px, Rand 35% + Flaeche 7% des Fehler-Tints (hier grau), Knopfreie mit Wiederholen zuerst, X rechts'>
      {({ P, dark }) => <Fehler P={P} dark={dark} />}
    </Buehne>
  ),
  '07 · Anhang in der Nachricht': () => (
    <Buehne name='07 · Anhang' note='Zeile unterm Blasen, Chip mit Name, Groesse, Endung und oeffnen; Blase wie 01'>
      {({ P, dark }) => <Anhang P={P} dark={dark} />}
    </Buehne>
  ),
  '08 · Zeit- und Meta-Zeile': () => (
    <Buehne name='08 · Meta-Zeile' note='Zeitstempel 10px/16px tabular, gesteuert-Zeile 10.5px/20px zentriert, Ausruf · Trennpunkt · Sache'>
      {({ P, dark }) => <MetaZeile P={P} dark={dark} />}
    </Buehne>
  )
}
