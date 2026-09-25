// Where the app tree lives — the vite/electron project this harness drives.
//
// Stock layout: three levels up from this file inside the checkout
// (apps/desktop/scripts/perf/lib → apps/desktop). The GV copy lives in the
// gods-voice repo, which has no such tree, so the default is the **GV source
// mirror** that `godsvoice desktop build-app` refreshes
// ($HERMES_HOME/godsv/app-src/apps/desktop — the overlay-patched tree).
//
// Override with PERF_DESKTOP_DIR when measuring a different tree.

import { homedir } from 'node:os'
import { join } from 'node:path'

export const HERMES_HOME = process.env.HERMES_HOME || join(homedir(), '.hermes')

export const DESKTOP_DIR =
  process.env.PERF_DESKTOP_DIR || join(HERMES_HOME, 'godsv', 'app-src', 'apps', 'desktop')
