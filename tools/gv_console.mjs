/**
 * God's Voice — capture renderer console output across a reload (dev helper).
 * Usage: node gv_console.mjs <port> <seconds> [filterSubstring]
 */
const [port = '9333', seconds = '8', filter = ''] = process.argv.slice(2)
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
const lines = []
const fmtArg = (a) => (a && 'value' in a ? String(a.value) : a && a.description ? a.description : JSON.stringify(a))
ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data)
  if (pending.has(msg.id)) {
    pending.get(msg.id)(msg)
    return
  }
  if (msg.method === 'Runtime.consoleAPICalled') {
    lines.push(`[${msg.params.type}] ${msg.params.args.map(fmtArg).join(' ')}`)
  } else if (msg.method === 'Runtime.exceptionThrown') {
    const d = msg.params.exceptionDetails
    lines.push(`[exception] ${d.exception?.description || d.text}`)
  } else if (msg.method === 'Log.entryAdded') {
    const e = msg.params.entry
    lines.push(`[${e.level}] ${e.text}`)
  }
}
const call = (method, params = {}) => {
  id += 1
  ws.send(JSON.stringify({ id, method, params }))
  return new Promise((resolve) => pending.set(id, resolve))
}
await call('Runtime.enable', {})
await call('Log.enable', {})
await call('Page.enable', {})
await call('Page.reload', {})
await new Promise((r) => setTimeout(r, Number(seconds) * 1000))
const re = filter ? new RegExp(filter, 'i') : null
for (const line of lines) {
  if (!re || re.test(line)) console.log(line.slice(0, 500))
}
console.log(`--- ${lines.length} lines total, ${lines.filter((l) => !re || re.test(l)).length} shown`)
ws.close()
