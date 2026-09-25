/**
 * God's Voice — DOM probe for the packaged app (pure node, no deps).
 *
 * Usage: node gv_dom_probe.mjs <port> '<expression>'
 * The app must have been started with --remote-debugging-port=<port>.
 */
const port = process.argv[2] ?? '9333'
const expression = process.argv[3] ?? 'JSON.stringify({title: document.title})'

const res = await fetch(`http://127.0.0.1:${port}/json`)
const targets = await res.json()
const page = targets.find((t) => t.type === 'page' && !t.url.startsWith('devtools://') && t.webSocketDebuggerUrl)
if (!page) {
  console.error('no page target. targets:', targets.map((t) => `${t.type} ${t.url}`).join('\n'))
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
function call(method, params = {}) {
  id += 1
  ws.send(JSON.stringify({ id, method, params }))
  return new Promise((resolve) => pending.set(id, resolve))
}

const out = await call('Runtime.evaluate', {
  expression,
  returnByValue: true,
  awaitPromise: true
})
const result = out.result?.result
if (out.result?.exceptionDetails) {
  console.error('evaluate failed:', JSON.stringify(out.result.exceptionDetails, null, 1))
  process.exit(3)
}
console.log(result?.value ?? JSON.stringify(result, null, 1))
ws.close()
