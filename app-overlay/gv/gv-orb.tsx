/**
 * The one working indicator for the host chat (composer status stack).
 *
 * `thinking-orbs` is vendored INTO the Skaldir Agent plugin (a runtime plugin
 * blob cannot resolve a relative import — see plugin.js), and that plugin
 * publishes the library on `globalThis.__GV_THINKING_ORB__` when it registers,
 * firing `gv-orb-ready`. This component is the host-side door to the SAME
 * implementation: no second animation, no second state model, no braille
 * spinner. A host where the plugin is not loaded renders a static dot — a
 * missing indicator is better than a competing one.
 */
import { useEffect, useState, type ReactElement } from 'react'

type ThinkingOrbProps = {
  'aria-label'?: string
  className?: string
  paused?: boolean
  size?: number
  state?: string
  theme?: string
}

type OrbRuntime = { ThinkingOrb: (props: ThinkingOrbProps) => ReactElement }

function readOrbRuntime(): OrbRuntime | null {
  const runtime = (globalThis as { __GV_THINKING_ORB__?: OrbRuntime }).__GV_THINKING_ORB__

  return runtime && typeof runtime.ThinkingOrb === 'function' ? runtime : null
}

export function GvOrb({
  ariaLabel,
  className,
  paused = false,
  size = 20,
  state = 'working'
}: {
  ariaLabel?: string
  className?: string
  paused?: boolean
  size?: number
  state?: string
}) {
  const [runtime, setRuntime] = useState<OrbRuntime | null>(readOrbRuntime)

  useEffect(() => {
    if (runtime) return

    const onReady = () => setRuntime(readOrbRuntime())

    document.addEventListener('gv-orb-ready', onReady)

    return () => document.removeEventListener('gv-orb-ready', onReady)
  }, [runtime])

  if (!runtime) {
    return (
      <span
        aria-label={ariaLabel}
        className={className}
        data-godsv-orb="pending"
        role="img"
      >
        <span className="inline-block size-1.5 rounded-full bg-current" />
      </span>
    )
  }

  const { ThinkingOrb } = runtime

  return (
    <span aria-label={ariaLabel} className={className} data-godsv-orb={state} role="img">
      <ThinkingOrb paused={paused} size={size} state={state} theme="auto" />
    </span>
  )
}
