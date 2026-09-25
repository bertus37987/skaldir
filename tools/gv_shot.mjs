/**
 * God's Voice — screenshot the REAL packaged Electron app (pure node + CDP).
 *
 * Usage:
 *   node gv_shot.mjs <cdp-port> <out.png> [--tab <label>] [--size 1600x1000]
 *                     [--wait 1200] [--reload]
 *
 * --size resizes the real window (Browser.setWindowBounds), --tab clicks the
 * sidebar/nav entry with that exact label before capturing. Writes a real
 * PNG of the running app — used for the UI-overhaul checkpoints.
 */
import { writeFileSync, mkdirSync } from 'node:fs'
import { dirname } from 'node:path'

const [port = '9333', out = 'ui-artifacts/shot.png'] = process.argv.slice(2)
const arg = (name, fallback = null) => {
  const i = process.argv.indexOf(name)
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback
}
const tabLabel = arg('--tab')
const size = arg('--size')
const emulate = arg('--emulate')
const waitMs = Number(arg('--wait', '1400'))
const doReload = process.argv.includes('--reload')

const res = await fetch(`http://127.0.0.1:${port}/json`)
const targets = await res.json()
const page = targets.find(
  (t) => t.type === 'page' && !t.url.startsWith('devtools://') && t.webSocketDebuggerUrl
)
if (!page) {
  console.error('no page target:', targets.map((t) => `${t.type} ${t.url}`).join(' | '))
  process.exit(2)
}

const ws = new WebSocket(page.webSocketDebuggerUrl)
await new Promise((resolve, reject) => {
  ws.onopen = resolve
  ws.onerror = reject
})
let id = 0
const pending = new Map()
ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data)
  if (pending.has(msg.id)) pending.get(msg.id)(msg)
}
const call = (method, params = {}) => {
  id += 1
  ws.send(JSON.stringify({ id, method, params }))
  return new Promise((resolve) => pending.set(id, resolve))
}
const evaluate = async (expression) => {
  const out = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  return out.result?.result?.value
}

if (size) {
  const [w, h] = size.split('x').map(Number)
  const win = await call('Browser.getWindowForTarget', { targetId: page.id })
  const windowId = win.result?.windowId
  if (windowId) {
    await call('Browser.setWindowBounds', {
      windowId,
      bounds: { windowState: 'normal' }
    })
    await new Promise((r) => setTimeout(r, 300))
    await call('Browser.setWindowBounds', {
      windowId,
      bounds: { left: 0, top: 0, width: w, height: h }
    })
    await new Promise((r) => setTimeout(r, 800))
  }
}

if (doReload) {
  await call('Page.reload', {})
  await new Promise((r) => setTimeout(r, 5000))
}

// optional viewport emulation (real window has a min width; emulate to test smaller sizes)
let emSize = null
if (emulate) {
  const [w, h] = emulate.split('x').map(Number)
  emSize = { w, h }
  await call('Emulation.setDeviceMetricsOverride', {
    width: w,
    height: h,
    deviceScaleFactor: 1,
    mobile: false
  })
  await new Promise((r) => setTimeout(r, 700))
}

if (tabLabel) {
  await evaluate(`(async () => {
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
    window.location.hash = '#/gods-voice'
    await sleep(600)
    let attempts = 0
    while (attempts < 20) {
      const el = [...document.querySelectorAll('button, a, [role="tab"]')]
        .find((b) => b.textContent.trim() === ${JSON.stringify(tabLabel)})
      if (el) { el.click(); await sleep(1100); return true }
      await sleep(400); attempts += 1
    }
    return false
  })()`)
}

await new Promise((r) => setTimeout(r, waitMs))
const shotParams = { format: 'png' }
if (emSize) shotParams.clip = { x: 0, y: 0, width: emSize.w, height: emSize.h, scale: 1 }
const shot = await call('Page.captureScreenshot', shotParams)
const data = shot.result?.data
if (!data) {
  console.error('capture failed:', JSON.stringify(shot).slice(0, 300))
  process.exit(3)
}
if (emSize) await call('Emulation.clearDeviceMetricsOverride', {})
mkdirSync(dirname(out), { recursive: true })
writeFileSync(out, Buffer.from(data, 'base64'))
console.log(`saved ${out} (${Buffer.from(data, 'base64').length} bytes)`)
ws.close()
