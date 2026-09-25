// Launch a standalone, fully isolated perf instance and leave it running so you
// can attach the harness (`node tools/perf-gv/run.mjs`) or DevTools to it. Ctrl-C
// tears it down and removes its temp dirs.
//
//   node tools/perf-gv/serve.mjs                 # :9222, temp HERMES_HOME + user-data-dir
//   PERF_PORT=9400 node tools/perf-gv/serve.mjs  # custom CDP port
//
// This serves the DEV instance built from the app tree (PERF_DESKTOP_DIR, default
// $HERMES_HOME/godsv/app-src/apps/desktop). To measure the packaged app, use
// `PERF_APP_BINARY=… run.mjs … --spawn --prod` instead.
//
// This is the isolation seam: because it uses its own --user-data-dir the
// Electron single-instance lock never collides with a running `hgui`.

import { startIsolatedInstance } from './lib/launch.mjs'

if (process.env.PERF_APP_BINARY) {
  console.error(
    '[perf:serve] PERF_APP_BINARY is set — this script serves the DEV instance from the tree.\n' +
      '             Use: PERF_APP_BINARY=… node tools/perf-gv/run.mjs cold-start --spawn --prod'
  )
  process.exit(2)
}

const port = Number(process.env.PERF_PORT ?? 9222)
const devPort = Number(process.env.PERF_DEV_PORT ?? 5174)

console.log(`[perf:serve] starting isolated instance (CDP :${port}, dev :${devPort})…`)

const instance = await startIsolatedInstance({
  port,
  devPort,
  hermesHome: process.env.PERF_HERMES_HOME,
  userDataDir: process.env.PERF_USER_DATA
})

console.log(`[perf:serve] READY — attach with: npm run perf -- --port ${port}`)

let closing = false
const shutdown = () => {
  if (closing) {
    return
  }

  closing = true
  console.log('\n[perf:serve] tearing down…')
  instance.teardown()
  process.exit(0)
}

process.on('SIGINT', shutdown)
process.on('SIGTERM', shutdown)
