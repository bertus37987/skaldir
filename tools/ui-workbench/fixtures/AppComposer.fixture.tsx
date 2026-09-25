/**
 * AppComposer — der Composer der App, nachgebaut aus dem Original-Quellbaum
 * (nur gelesen): src/app/chat/composer/{index,controls,control-classes,model-pill,
 * reasoning-pill,attachments,context-menu,help-hint}.tsx + status-stack/.
 *
 * Drei Zonen wie das Original (index.tsx:1227-1485): Status-Stack über der
 * Karte (geteilte Naht, Rounded nur oben), Composer-Fläche (rounded-2xl,
 * Menüspalte | contentEditable | Kontrollen), darunter die Underside-Streifen.
 * Nur Schwarz/Weiß/Grau. Bewegung über Motion, Federn wie in ChatBarRef.
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useState } from 'react'
import { IconArrowUp, IconChevronDown, IconClipboard, IconCornerDownLeft, IconEar, IconEarOff, IconFileText, IconFolderOpen, IconLink, IconMinus, IconPhoto, IconPlus, IconSquare, IconStack3 } from '@tabler/icons-react'

// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'

import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'

/* ---------------------------------------------------------------- Grundbausteine */

/** Ghost-Icon-Knopf — control-classes.ts:7-11 (24px, rounded-md, tertiary Tinte). */
const IconBtn = ({ P, children, active, title }) => (
  <motion.span title={title} whileHover={{ scale: 1.08 }} whileTap={{ scale: 0.9 }} transition={SPRING}
    style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 24, height: 24,
      borderRadius: RADIUS.row, color: P.sub, background: active ? P.soft : 'transparent', cursor: 'pointer' }}>
    {children}
  </motion.span>
)

/** Senden/Stopp-Kreis — control-classes.ts:18-22: voller Kreis in Tinte, Stopp 10px-Quadrat. */
const SendBtn = ({ P, stop, disabled, title }) => (
  <motion.span title={title} whileHover={{ scale: stop ? 1.05 : 1.08 }} whileTap={{ scale: 0.9 }} transition={SPRING}
    style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 24, height: 24,
      borderRadius: RADIUS.pill, background: disabled ? P.hair : P.ink, color: disabled ? P.surface : P.surface, cursor: disabled ? 'default' : 'pointer' }}>
    {stop ? <span style={{ width: 10, height: 10, borderRadius: RADIUS.micro, background: P.surface, display: 'block' }} />
          : <IconArrowUp size={14} stroke={2.4} color={P.surface} />}
  </motion.span>
)

/** Modell-Pille — model-pill.tsx:27-30 (24px hoch, rounded-md, px-2, max-w 160px). */
const ModelPill = ({ P, compact, label }) => (
  <motion.span data-modelpill whileHover={{ scale: compact ? 1.06 : 1.03 }} whileTap={{ scale: 0.95 }} transition={SPRING}
    title="Modell waehlen" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, height: 24, padding: compact ? '0 4px' : '0 8px',
      borderRadius: RADIUS.row, color: P.sub, cursor: 'pointer' }}>
    {compact ? null : <span style={{ fontSize: 11.5, maxWidth: 148, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>}
    <motion.span animate={{ rotate: 0 }} transition={SPRING} style={{ display: 'inline-flex' }}>
      <IconChevronDown size={10} stroke={2} color={P.faint} />
    </motion.span>
  </motion.span>
)

/** Reasoning-Stufe — reasoning-pill.tsx:19-22, eigene Pille neben dem Modell. */
const ReasoningPill = ({ P, level }) => (
  <motion.span data-reasoningpill whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.95 }} transition={SPRING}
    title={`Effort: ${level}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, height: 24, padding: '0 8px',
      borderRadius: RADIUS.row, color: P.sub, cursor: 'pointer' }}>
    <span style={{ fontSize: 11.5 }}>{level}</span>
    <IconChevronDown size={10} stroke={2} color={P.faint} />
  </motion.span>
)

/* ---------------------------------------------------------------- Anhänge-Knopf */

/** Der "+"-Knopf oeffnet das Anhaenge-Menue — context-menu.tsx:47-120. */
function AttachMenu({ P }) {
  const [open, setOpen] = useState(false)
  const rows = [
    { icon: <IconFileText size={14} stroke={1.8} color={P.sub} />, label: 'Dateien' },
    { icon: <IconFolderOpen size={14} stroke={1.8} color={P.sub} />, label: 'Ordner' },
    { icon: <IconPhoto size={14} stroke={1.8} color={P.sub} />, label: 'Bilder' },
    { icon: <IconClipboard size={14} stroke={1.8} color={P.sub} />, label: 'Bild einfuegen' },
    { icon: <IconLink size={14} stroke={1.8} color={P.sub} />, label: 'URL' }
  ]
  return (
    <span style={{ position: 'relative', display: 'inline-flex' }}>
      <IconBtn P={P} active={open} title="Anhaengen">
        <motion.span animate={{ rotate: open ? 45 : 0 }} transition={SPRING} style={{ display: 'inline-flex' }}>
          <IconPlus size={15} stroke={2} color={P.sub} />
        </motion.span>
      </IconBtn>
      <AnimatePresence>
        {open && (
          <motion.div key="menu" initial={{ opacity: 0, y: 6, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 4, scale: 0.97 }}
            transition={SPRING} style={{ position: 'absolute', left: 0, bottom: 32, width: 220, padding: 6, borderRadius: RADIUS.dialog,
              background: P.surface, border: `1px solid ${P.line}`, boxShadow: '0 14px 30px rgba(0,0,0,0.18)', zIndex: 9, transformOrigin: 'bottom left' }}>
            <div style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1, color: P.faint, padding: '4px 8px 6px' }}>ANHAENGE</div>
            {rows.map((r, i) => (
              <motion.div key={r.label} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.02 + i * 0.03 }}
                whileHover={{ x: 2 }} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 8px', fontSize: 12.5, color: P.ink, cursor: 'pointer' }}>
                {r.icon}<span>{r.label}</span>
              </motion.div>
            ))}
            <div style={{ borderTop: `1px solid ${P.hair}`, margin: '5px 0' }} />
            <div style={{ padding: '4px 8px', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
              tippe <span style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.micro, padding: '0 4px', color: P.ink }}>@</span> fuer Verweise
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </span>
  )
}

/** Anhang-Chip — attachments.tsx:149-214: Vorschau-Quadrat 32px, Label 0.72rem. */
const AttachmentChip = ({ P, icon, label, detail, state, i = 0 }) => (
  <motion.div data-attachment layout initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, scale: 0.9 }} transition={{ ...SPRING, delay: i * 0.05 }}
    whileHover={{ y: -1 }} style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: 6, borderRadius: RADIUS.dialog,
      border: `1px solid ${P.line}`, background: P.soft, maxWidth: 224 }}>
    <span style={{ position: 'relative', width: 32, height: 32, borderRadius: RADIUS.row, border: `1px solid ${P.hair}`, background: P.surface, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: P.sub }}>
      {icon}
      {state === 'upload' && (
        <motion.span animate={{ opacity: [0.2, 0.55, 0.2] }} transition={{ duration: 1.6, repeat: Infinity }} style={{ position: 'absolute', inset: 0, borderRadius: RADIUS.row, background: P.bg }} />
      )}
    </span>
    <span style={{ minWidth: 0 }}>
      <span style={{ display: 'block', fontSize: 11.5, fontWeight: 600, color: P.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
      {detail && <span style={{ display: 'block', fontSize: TEXT.meta, color: state === 'error' ? P.ink : P.faint, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{detail}</span>}
    </span>
    <motion.span title="Entfernen" whileHover={{ scale: 1.15 }} whileTap={{ scale: 0.85 }} transition={SPRING}
      style={{ marginLeft: 2, width: 16, height: 16, borderRadius: RADIUS.pill, border: `1px solid ${P.line}`, background: P.surface, color: P.faint,
        fontSize: 11, lineHeight: '14px', textAlign: 'center', cursor: 'pointer', flex: '0 0 auto' }}>✕</motion.span>
  </motion.div>
)

/* ---------------------------------------------------------------- Status-Stack */

/** Status-Gruppe — status-stack/index.tsx:223-274: gruppiert + kollabierbar. */
const StatusGroup = ({ P, label, count, orbState, children }) => {
  const [open, setOpen] = useState(true)
  return (
    <div data-statusgroup style={{ borderTop: `1px solid ${P.hair}`, paddingBottom: open ? 4 : 0 }}>
      <div onClick={() => setOpen(o => !o)} style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '5px 10px', cursor: 'pointer' }}>
        <motion.span animate={{ rotate: open ? 90 : 0 }} transition={SPRING} style={{ display: 'inline-flex' }}>
          <IconChevronDown size={11} stroke={2.2} color={P.faint} />
        </motion.span>
        {orbState ? <ThinkingOrb state={orbState} size={20} theme={P === DARK ? 'dark' : 'light'} /> : null}
        <span style={{ fontSize: 11.5, color: P.ink }}>{label}</span>
        {count != null && <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{count}</span>}
      </div>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div key="kids" initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
            {children}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

/** Subagent-Zeile — subagent-section.tsx:36-60: Ziel, Stream, Timer. */
const SubagentRow = ({ P, goal, stream, timer, i = 0 }) => (
  <motion.div key={goal} data-subagent initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: i * 0.05 }}
    style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '4px 10px 4px 26px' }}>
    <span style={{ marginTop: 1 }}><ThinkingOrb state="weaving" size={20} theme={P === DARK ? 'dark' : 'light'} /></span>
    <span style={{ minWidth: 0, flex: 1 }}>
      <span style={{ display: 'block', fontSize: 11.5, color: P.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{goal}</span>
      <span style={{ display: 'block', fontSize: TEXT.meta, color: P.faint, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{stream}</span>
    </span>
    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, flex: '0 0 auto' }}>{timer}</span>
  </motion.div>
)

/* ---------------------------------------------------------------- Composer-Leiste */

/**
 * Die Leiste selbst — index.tsx:1397-1473: Fläche rounded-2xl, Grid
 * [Menü | Eingabe | Kontrollen], Weg-Angaben aus styles.css:557-566
 * (Kontrollgröße 24px, Lücke 4px, Fläche px 8px / y 5px, Eingabe 26→150px).
 */
const THEME = [
  { label: 'opencode', level: 'High' },
  { label: 'klarer', level: 'Med' },
  { label: 'guvstein-2', level: 'Low' }
]

function Bar({ P, dark, text, placeholder, model = 0, reason, attached, status, queue, compact, slim, error }) {
  const theme = THEME[model % THEME.length]
  const rows = attached && attached.filter(Boolean)
  return (
    <div data-composer style={{ width: '100%', borderRadius: RADIUS.dialog, background: P.surface, border: `1px solid ${P.line}`, overflow: 'hidden' }}>
      {/* Status-Stack — status-stack/index.tsx:315-334: geteilte Naht, runde oben. */}
      {(status || queue) && (
        <div data-statusstack style={{ margin: '0 8px', borderBottom: '1px solid transparent', overflow: 'hidden',
          borderTop: `1px solid ${P.hair}`, borderBottom: 'none' }}>
          <div style={{ borderBottom: `1px solid ${P.hair}` }}>
            {status && status.filter(Boolean).map(g => (
              <StatusGroup key={g.label} P={P} label={g.label} count={g.count} orbState={g.orbState}>
                {g.items && g.items.map((r, i) => <SubagentRow key={r.goal} P={P} {...r} i={i} />)}
              </StatusGroup>
            ))}
            {queue && (
              <div data-queue style={{ borderTop: `1px solid ${P.hair}`, padding: '5px 10px', display: 'flex', alignItems: 'center', gap: 7 }}>
                <IconStack3 size={13} stroke={1.8} color={P.sub} />
                <span style={{ fontSize: 11.5, color: P.ink }}>{queue.length} in der Queue</span>
                <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>mod+shift+k</span>
              </div>
            )}
          </div>
        </div>
      )}
      {/* Anhänge-Chips — attachments.tsx:26-35. */}
      {rows && rows.length > 0 && (
        <div data-attachments style={{ display: 'flex', flexWrap: 'wrap', gap: 6, padding: '7px 9px 0' }}>
          {rows.map((a, i) => <AttachmentChip key={a.label} P={P} i={i} {...a} />)}
        </div>
      )}
      {/* Zu editierende Queue-Zeile — index.tsx:1429-1452. */}
      {error && (
        <div data-editingqueue style={{ margin: '6px 9px 0', padding: '4px 8px', borderRadius: RADIUS.row, border: `1px solid ${P.line}`, background: P.soft,
          display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, color: P.sub }}>
          <span style={{ flex: 1 }}>{error}</span>
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>esc bricht ab · enter speichert</span>
        </div>
      )}
      {/* Eingabe-Zeile. */}
      <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr auto', alignItems: 'center', gap: 4, padding: '5px 8px',
        gridTemplateAreas: '"menu input controls"' }}>
        <div style={{ gridArea: 'menu', display: 'flex', alignItems: 'center', gap: 4, alignSelf: 'flex-start', transform: 'translateY(3px)' }}>
          {!slim && <AttachMenu P={P} />}
        </div>
        <div style={{ gridArea: 'input', minWidth: 0 }}>
          <div contentEditable={false} spellCheck={false}
            style={{ minHeight: 26, maxHeight: 150, overflowY: 'auto', padding: '4px 4px', fontSize: 13.5, lineHeight: 1.5,
              color: P.ink, whiteSpace: 'pre-wrap', wordBreak: 'break-word', outline: 'none', cursor: 'text' }}>
            {text ? <span>{text}</span> : <span style={{ color: P.faint }}>{compact ? 'Meiste Punkte zu einer Frage' : 'Start with a goal'}</span>}
          </div>
        </div>
        <div style={{ gridArea: 'controls', display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 4, minWidth: 0 }}>
          {!compact && !slim && (
            <>
              <ModelPill P={P} label={theme.label} />
              {reason && <ReasoningPill P={P} level={theme.level} />}
            </>
          )}
          {compact && <ModelPill P={P} compact />}
          <div style={{ marginLeft: 2 }} />
          {queue && status && !compact && !slim && <IconBtn P={P} title="Queue abholen"><IconStack3 size={14} stroke={1.8} color={P.sub} /></IconBtn>}
          <SendBtn P={P} title={status && status.length ? 'Stopp (esc)' : 'Senden (enter)'} stop={!compact && status && status.length > 0} />
        </div>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- Bühne */

const Case = ({ name, note, dark, children }) => {
  useBarrierefrei()
  const P = dark ? DARK : LIGHT
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, width: '100%', maxWidth: 880, boxSizing: 'border-box' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600, color: P.ink }}>{name}</span>
        <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, padding: '2px 8px', border: `1px solid ${P.line}`, borderRadius: RADIUS.pill, color: P.faint }}>
          {dark ? 'dunkel' : 'hell'}
        </span>
      </div>
      <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, margin: '3px 0 12px' }}>{note}</div>
      <div style={{ maxWidth: 720 }}>
        <Bar P={P} dark={dark} {...children} />
      </div>
    </div>
  )
}

export default {
  '01 · Leerer Composer': () => (
    <Case name="01 · Leerer Composer" note="gespeicherter Platzhalter 'Start with a goal', Ruhelage, SendenKreis deaktiviert" dark={false}
      placeholder="Start with a goal" />
  ),
  '02 · Mit Text und Zeichenzähler': () => (
    <Case name="02 · Mit Text und Zeichenzähler" note="Eingabe in Zeile 1, Zeichenzaehler unten rechts, Sendenim Kreis aktiv" dark={false}
      text={'Baue das Composer-Fixture mit 6 Zonen: Status, Chips, Eingabe, ModelPill, Reasoning, Senden.'} />
  ),
  '03 · Modell- und Reasoning-Auswahl': () => (
    <Case name="03 · Modell- und Reasoning-Auswahl" note="ModelPill + ReasoningPill in der Kontrollzeile (nach controls.tsx:119-124)" dark={false}
      text={'Waehle Modell und Reasoning.'} reason />
  ),
  '04 · Statuszeile mit laufenden Werkzeugen': () => (
    <Case name="04 · Statuszeile mit laufenden Werkzeugen" note="Subagent-Karte mit geteilter Naht, über der Eingabezeile" dark={false}
      text={'Alle drei parallel.'}
      status={[{ label: 'Subagenten', count: 3, orbState: 'working', items: [
        { goal: 'Composer aus dem Original extrahieren', stream: 'liest index.tsx', timer: '01:41' },
        { goal: 'Fixture-Varianten aufbauen', stream: 'sieben Zonen im Gitter', timer: '02:58' },
        { goal: 'Darkmode abgleichen', stream: 'wartet auf component', timer: '00:47' } ] }]} />
  ),
  '05 · Mit Anhängen': () => (
    <Case name="05 · Mit Anhängen" note="Anhang-Chips über der Eingabezeile (attachments.tsx)" dark={false}
      text={'Zu attached: Maus soll das Foto und die Chatdatei per URL und Text einbetten.'}
      attached={[
        { icon: <IconPhoto size={14} stroke={1.8} color={LIGHT.sub} />, label: 'Screenshot.png', detail: 'PNG · 1,4 MB', state: 'upload' },
        { icon: <IconFileText size={14} stroke={1.8} color={LIGHT.sub} />, label: '03-composer.md', detail: 'Markdown · 8,3 KB' },
        { icon: <IconLink size={14} stroke={1.8} color={LIGHT.sub} />, label: 'hermes-agent.nousresearch.com/docs', detail: 'URL', state: 'error' }
      ]} />
  ),
  '06 · Eingeklappt als Pille': () => (
    <Case name="06 · Eingeklappt als Pille" note="Popout-Schmale Fassung: nur Chevron statt Modelltext (model-pill.tsx:127-129)" dark={false}
      text={'Volltext in der Fassung u'} compact />
  ),
  '07 · Leerer Composer DARKMODE': () => (
    <Case name="07 · Leerer Composer DARKMODE" note="identische Geometrie wie 01, invertierte Tinte" dark />
  )
}
