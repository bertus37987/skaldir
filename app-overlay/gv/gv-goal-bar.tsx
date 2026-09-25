/**
 * The goal bar in the chat bar (Welle 1 — AUFBAU/02 §c).
 *
 * One read-only strip in the composer DOCK, mounted between the micro-action
 * strip (the top-most thing in the lane, `status-stack/index.tsx:286-289`) and
 * the status stack: it is an in-flow dock child, so the dock's own measurement
 * (`--composer-measured-height`, `use-composer-metrics.ts:118-142`) covers its
 * height and the thread clears it without a second number.
 *
 * Facts this file obeys:
 *
 *  * **Only existing fields.** The runtime goal arrives through the store's
 *    STRICT positive-list parser (`store/session-control.ts:271-343`:
 *    required `title, status, turns_used, max_turns, contract, subgoals, gates`;
 *    optional `created_at, updated_at, paused_reason, last_verdict, last_reason,
 *    wait_barrier`). One invented field makes the whole snapshot `null` and this
 *    surface disappears without an error — so nothing here invents a field.
 *  * **No completion ticks.** The contract has no "criterion done" field (E4 in
 *    HERMES-ABGLEICH/12-skaldir-eigenheiten.md:75); criteria are rendered as a
 *    numbered list, never with a checkmark that has no data behind it.
 *  * **Read-only.** The only control is `StatusSection`'s own collapse button.
 *    Actions (`/goal pause`, criterion edits, "view details") stay where they
 *    already are — the existing goal card in the status stack.
 *  * **No orb.** No run, no orb: `GvOrb` is deliberately not imported here.
 *  * **Existing building blocks only** — `composerDockCard('top')`,
 *    `StatusSection`, `StatusRow`, `Codicon`; no new primitive, no new i18n key
 *    (every string comes from `statusStack`/`statusStack.control`, which are
 *    already translated in all languages — R9).
 */
import { memo } from 'react'

import { useSessionValue } from '@/app/chat/composer/status-stack/session-control-utils'
import { composerDockCard } from '@/components/chat/composer-dock'
import { StatusRow } from '@/components/chat/status-row'
import { StatusSection } from '@/components/chat/status-section'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { $sessionControlBySession, type SessionControlGoal } from '@/store/session-control'

/**
 * Owner wording (AUFBAU/00-PLAN.md:37) for "no goal set" — shown in BOTH
 * collapse states, once, in place of any example rows. Kept as a literal on
 * purpose: a new i18n key would have to be added to every language file before
 * the mount (R9), and this sentence is the product's own call to action.
 */
const EMPTY_GOAL_TEXT = '@goal define a goal and i actually get it done.'

interface GvGoalBarProps {
  /** Session whose runtime goal is shown. `null` (no session yet) is the empty state. */
  sessionId: string | null
}

/** The chat bar's goal strip. Reads the same store slice the status stack reads. */
export function GvGoalBar({ sessionId }: GvGoalBarProps) {
  const entry = useSessionValue($sessionControlBySession, sessionId)
  const goal = entry?.snapshot?.goal ?? null

  return (
    <div className="px-[5px] pb-1.5" data-godsv="goal-bar" data-slot="gv-goal-bar">
      <div
        className={cn(
          composerDockCard('top'),
          // Same fused-capsule skin as the status stack right below it
          // (status-stack/index.tsx:318-321): inset, rounded top, square bottom,
          // transparent bottom border so the shared seam stays single.
          'mx-2 overflow-hidden rounded-b-none border-b border-b-transparent'
        )}
      >
        {goal ? <GoalSection goal={goal} /> : <EmptyGoalSection />}
      </div>
    </div>
  )
}

/**
 * No goal set. The sentence is the section's LABEL, not its body, and the body
 * stays empty: that way it appears exactly once in the collapsed state (header)
 * and exactly once in the expanded state — and the collapse button keeps a
 * real accessible name (axe `button-name`, the audit's critical check).
 */
const EmptyGoalSection = memo(function EmptyGoalSection() {
  return (
    <StatusSection
      children={null}
      icon={<Codicon className="text-muted-foreground/70" name="target" size="0.8rem" />}
      label={EMPTY_GOAL_TEXT}
    />
  )
})

/**
 * The live goal. Header, icon and state words are the ones the existing goal
 * card uses (`session-control-goal.tsx:145-180`) so the two can never disagree
 * about the state; the body is the read-only part of the details dialog
 * (`:516-621`): objective, the five contract fields, the wait/pause reason, the
 * criteria, the quality gates.
 *
 * Capped and scrollable: an open goal with many criteria must not push the
 * thread off screen — the status stack caps itself at 40vh the same way.
 */
const GoalSection = memo(function GoalSection({ goal }: { goal: SessionControlGoal }) {
  const { t } = useI18n()
  const s = t.statusStack
  const ctrl = s.control

  const visibleState: 'waiting' | 'active' | 'paused' | 'done' = goal.wait_barrier
    ? 'waiting'
    : goal.status === 'paused'
      ? 'paused'
      : goal.status === 'done'
        ? 'done'
        : 'active'

  const iconClass =
    goal.last_verdict === 'blocked'
      ? 'text-red-500'
      : visibleState === 'done'
        ? 'text-muted-foreground/70'
        : visibleState === 'active'
          ? 'text-emerald-500'
          : 'text-amber-500'

  const stateLabel =
    goal.last_verdict === 'blocked'
      ? s.goalBlocked
      : visibleState === 'waiting'
        ? s.goalWaiting
        : visibleState === 'paused'
          ? s.goalPaused
          : visibleState === 'done'
            ? s.goalDone
            : s.goalActive

  const turnsLabel =
    visibleState === 'done'
      ? ctrl.goalDoneTurns(goal.turns_used)
      : goal.max_turns > 0
        ? ctrl.goalActiveTurns(goal.turns_used, goal.max_turns)
        : goal.turns_used > 0
          ? ctrl.goalTurn(goal.turns_used)
          : null

  const headerLabel = [stateLabel, turnsLabel, ctrl.criteriaHeader(goal.subgoals.length)]
    .filter((part): part is string => Boolean(part))
    .join(' · ')

  // Only the contract fields that carry text: an empty value would render a
  // bare label. Which fields exist is NOT decided here — the parser already
  // guarantees all five keys.
  const contractFields: Array<[string, string]> = [
    [ctrl.contractOutcome, goal.contract.outcome],
    [ctrl.contractVerification, goal.contract.verification],
    [ctrl.contractConstraints, goal.contract.constraints],
    [ctrl.contractBoundaries, goal.contract.boundaries],
    [ctrl.contractStopWhen, goal.contract.stop_when]
  ].filter(([, value]) => value.length > 0) as Array<[string, string]>

  return (
    <StatusSection
      // Collapsed: the objective, one line. The full body is one click away.
      icon={<Codicon className={iconClass} name="target" size="0.8rem" />}
      label={headerLabel}
      preview={<div className="truncate px-1 text-xs text-foreground/92">{goal.title}</div>}
    >
      <div className="max-h-[32vh] overflow-y-auto">
        <div className="space-y-1.5 px-1 py-1">
          {/* Objective */}
          <div className="text-xs leading-relaxed text-foreground/92 break-words">{goal.title}</div>

          {/* Why it is where it is — same precedence as the card (:335-347) */}
          {goal.wait_barrier && (
            <div className="text-[0.7rem] italic text-muted-foreground/80">
              {goal.wait_barrier.reason
                ? `${ctrl.waitBarrierTitle}: ${goal.wait_barrier.reason}`
                : ctrl.waitBarrierTitle}
            </div>
          )}
          {!goal.wait_barrier && goal.paused_reason && (
            <div className="text-[0.7rem] italic text-muted-foreground/80">{goal.paused_reason}</div>
          )}
          {!goal.wait_barrier && !goal.paused_reason && goal.last_reason && (
            <div className="text-[0.7rem] italic text-muted-foreground/80">{goal.last_reason}</div>
          )}

          {/* Contract */}
          {contractFields.map(([label, value]) => (
            <StatusRow key={label}>
              <span className="shrink-0 text-[0.68rem] font-semibold uppercase tracking-wider text-muted-foreground/75">
                {label}
              </span>
              <span className="min-w-0 text-xs leading-relaxed text-foreground/90 break-words">{value}</span>
            </StatusRow>
          ))}

          {/* Criteria — text only. There is no per-criterion "done" field in the
              contract (E4), so there is no tick here on purpose. */}
          <div className="mt-2 border-t border-(--ui-stroke-tertiary)/40 pt-1.5">
            <div className="flex items-center gap-1.5 px-1.5 pb-1 text-[0.68rem] font-medium text-muted-foreground/75">
              <span aria-hidden="true" className={`inline-flex ${iconClass}`}>
                <Codicon name="target" size="0.68rem" />
              </span>
              <span>{ctrl.criteriaHeader(goal.subgoals.length)}</span>
            </div>
            {goal.subgoals.length > 0 ? (
              goal.subgoals.map((subgoal, index) => (
                <StatusRow key={`${index + 1}-${subgoal}`} leading={<span className="text-muted-foreground/60 tabular-nums">{index + 1}.</span>}>
                  <span className="text-xs leading-relaxed text-foreground/85 break-words">{subgoal}</span>
                </StatusRow>
              ))
            ) : (
              <div className="px-1.5 text-xs text-muted-foreground/70">{ctrl.noCriteria}</div>
            )}
          </div>

          {/* Quality gates — command, retries, timeout, last exit code */}
          {goal.gates.length > 0 && (
            <div className="mt-2 border-t border-(--ui-stroke-tertiary)/40 pt-1.5">
              <div className="px-1.5 pb-1 text-[0.68rem] font-semibold uppercase tracking-wider text-muted-foreground/75">
                {ctrl.qualityGatesTitle}
              </div>
              {goal.gates.map((gate, index) => (
                <StatusRow key={`${index}-${gate.command}`}>
                  <span className="min-w-0 font-mono text-[0.7rem] text-foreground/95 break-words">{gate.command}</span>
                  <span className="shrink-0 text-[0.68rem] text-muted-foreground/80">
                    {ctrl.gateAttempts(gate.attempts, gate.max_retries)}
                  </span>
                  <span aria-hidden="true" className="shrink-0 text-[0.68rem] text-muted-foreground/40">
                    ·
                  </span>
                  <span className="shrink-0 text-[0.68rem] text-muted-foreground/80">
                    {ctrl.gateTimeout(gate.timeout_seconds)}
                  </span>
                  <span aria-hidden="true" className="shrink-0 text-[0.68rem] text-muted-foreground/40">
                    ·
                  </span>
                  <span className="shrink-0 text-[0.68rem] text-muted-foreground/80">
                    {ctrl.gateLastExit(gate.last_exit_code)}
                  </span>
                </StatusRow>
              ))}
            </div>
          )}
        </div>
      </div>
    </StatusSection>
  )
})
