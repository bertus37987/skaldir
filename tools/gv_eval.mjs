/**
 * God's Voice — evaluate JS in the REAL app over CDP (dev helper).
 * Usage: node gv_eval.mjs <port> "<expression>"
 */
const [port = '9333', expression = '1+1'] = process.argv.slice(2)
const res = await fetch(`http://127.0.0.1:${port}/json`)
const targets = await res.json()
const page = targets.find(
  (t) => t.type === 'page' && !t.url.startsWith('devtools://') && t.webSocketDebuggerUrl
)
if (!page) {
  console.error('no page target')
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
const out = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
if (out.result?.exceptionDetails) {
  console.error('eval error:', out.result.exceptionDetails.exception?.description || 'failed')
  process.exit(3)
}
console.log(JSON.stringify(out.result?.result?.value, null, 1))
ws.close()
