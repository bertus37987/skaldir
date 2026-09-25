/**
 * God's Voice — UI E2E against the REAL packaged Electron app (pure node).
 *
 * Usage: node gv_ui_e2e.mjs <cdp-port>
 * The app must be running with --remote-debugging-port=<cdp-port>.
 *
 * Checks (spec §24): models renders + role selectors, tools list + build queue
 * + mcp section, treasury add-credential flow present, error states render,
 * and no plaintext secret material anywhere in the DOM.
 */
const port = process.argv[2] ?? '9333'
const checks = []
const check = (name, ok, detail = '') => {
  checks.push({ name, ok: Boolean(ok), detail })
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${ok || !detail ? '' : ` — ${detail}`}`)
}

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
  if (out.result?.exceptionDetails) {
    return { error: out.result.exceptionDetails.exception?.description || 'evaluate failed' }
  }
  return out.result?.result?.value
}

const probe = `(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
  const txt = () => document.body.innerText
  // fill the visible composer and hand the real send to the node side: it
  // performs an Input.dispatchMouseEvent click on the Send button (synthetic
  // KeyboardEvents from JS never submit). Parks on window.__gvSendAt.
  // multiple composer instances exist across open tabs; always pick VISIBLE
  const visibleComposer = () => {
    const els = [...document.querySelectorAll('[contenteditable="true"], textarea')].filter(
      (el) => el.getBoundingClientRect().width > 0
    )
    return els[els.length - 1] || null
  }
  const sendDraft = async (fixture, text) => {
    fixture.focus()
    document.execCommand('insertText', false, text)
    await sleep(400)
    let sendRoot = fixture.closest('form') || fixture.parentElement
    for (let i = 0; i < 6 && sendRoot; i += 1) {
      if (sendRoot.querySelector('button')) break
      sendRoot = sendRoot.parentElement
    }
    const sendBtns = sendRoot
      ? [...sendRoot.querySelectorAll('button')].map((b) => ({
          label: (b.getAttribute('aria-label') || b.title || b.innerText || '').trim(),
          r: b.getBoundingClientRect()
        }))
      : []
    const sendBtn = sendBtns.find((b) => /send/i.test(b.label)) || sendBtns[sendBtns.length - 1]
    if (sendBtn && sendBtn.r.width > 0) {
      window.__gvSendAt = { x: sendBtn.r.x + sendBtn.r.width / 2, y: sendBtn.r.y + sendBtn.r.height / 2 }
      for (let w = 0; w < 30 && window.__gvSendAt; w += 1) await sleep(500)
    } else {
      fixture.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true, cancelable: true }))
      await sleep(1200)
    }
  }
  const clickTab = async (label) => {
    const el = [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === label)
    if (!el) return false
    el.click()
    await sleep(1300)
    return true
  }
  const idle = async (budget = 25) => {
    for (let i = 0; i < budget; i += 1) {
      if (!/Waking up/.test(document.body.innerText.slice(-500))) return true
      await sleep(1000)
    }
    return false
  }
  const out = { title: document.title, hash: location.hash }
  let warm = 0
  while ((!/Live/.test(txt()) || !location.hash.includes('gods-voice')) && warm < 30) {
    if (warm === 2 && !location.hash.includes('gods-voice')) {
      window.location.hash = '#/gods-voice'
    }
    await sleep(1000)
    warm += 1
  }
  out.warmupSeconds = warm
  // after the wake-up: the surface we actually ended on, and whether the panel is mounted
  out.hashAfterWake = location.hash
  out.panelMounted =
    /SKALDIR/.test(txt()) && /Treasury/.test(txt()) && /Verification/.test(txt())
  // V2-01/V2-11: cockpit/activity left the nav — only unique-function views remain.
  out.tabsPresent = ['Verification','Tools','Settings','Treasury']
    .filter((t) => txt().includes(t))
  out.navGone = ['Cockpit','Activity'].every((t) =>
    ![...document.querySelectorAll('button')].some((b) => b.textContent.trim() === t)
  )
  out.clickedSettings = await clickTab('Settings')
  out.settingsSubtabs = ['Models','Tools','Computer Use','Memory','Skills','System']
    .filter((t) => txt().includes(t))
  out.panelAsideGone = ![...document.querySelectorAll('aside')].some((a) =>
    String(a.className).includes('w-44')
  )
  out.areaNavRows = [...document.querySelectorAll('button')].filter((b) =>
    ['Verification','Tools','Settings','Treasury'].includes(b.textContent.trim())
  ).length
  out.clickedModels = await clickTab('Models')
  out.modelsRendersRoles = /Watcher \\/ Triage/.test(txt()) && /Investigator \\/ Research/.test(txt())
  out.modelsHasTestButton = /Test model/.test(txt())
  out.modelsHasRegistryOrigin = /Hermes Default/.test(txt())
  out.clickedTools = await clickTab('Tools')
  out.toolsRenders = /Installed/.test(txt()) && /Build Queue/.test(txt())
  out.toolsHasRegistryEntry = /money_convert/.test(txt())
  const detailBtn = [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === 'money_convert_v2')
  out.toolDetailBtn = Boolean(detailBtn)
  out.toolDetailShowsUsage = false
  if (detailBtn) {
    detailBtn.click()
    await sleep(1500)
    out.toolDetailShowsUsage = /Usage & Errors/.test(txt()) && /Calls/.test(txt())
    const back = [...document.querySelectorAll('button')].find((b) => b.textContent.includes('back to Tools'))
    if (back) {
      back.click()
      await sleep(800)
    }
  }
  if (!out.toolsHasRegistryEntry) {
    out.toolsHasRegistryEntry = /money_convert/.test(txt())
  }
  out.capabilityRequestsRender = /Capability Requests/.test(txt()) && /CAPABILITY REQUEST/.test(txt())
  out.capabilityRequestShowsAgent = /requested by: main-agent/.test(txt())
  out.capabilityRequestShowsHandle = /secret:\\/\\//.test(txt())
  // Subtabs live under Settings; after the tool-detail navigation above they
  // may not be mounted — re-assert the Settings surface before CU selection.
  await clickTab('Settings')
  out.clickedCu = await clickTab('Computer Use')
  await sleep(900)
  out.cuRenders = /Isolation/.test(txt()) && /API > DOM > Vision/.test(txt())
  const cuBtn = [...document.querySelectorAll('button')].find((b) => (b.textContent || '').includes('Run demo'))
  out.cuDemoBtn = Boolean(cuBtn)
  out.cuRunShows = false
  if (cuBtn) {
    cuBtn.click()
    await sleep(2500)
    out.cuRunShows = /channel:/.test(txt())
  }
  out.clickedVerification = await clickTab('Verification')
  await sleep(900)
  out.evalsRenders = /Token metrics/.test(txt()) && /MEASURED/.test(txt()) && /ESTIMATED/.test(txt())
  const evBtn = [...document.querySelectorAll('button')].find((b) => (b.textContent || '').includes('Run eval'))
  out.evalBtn = Boolean(evBtn)
  out.evalRunShowsResult = false
  if (evBtn) {
    evBtn.click()
    await sleep(9000)
    out.evalRunShowsResult = /passed/.test(txt())
  }
  out.clickedTreasury = await clickTab('Treasury')
  out.treasuryRendersCredentialFlow = /Add credential/.test(txt()) || /GODSV_VAULT_KEY/.test(txt())
  out.treasuryTestButton = false
  out.treasuryTestResult = false
  const credTest = [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === 'test')
  if (credTest) {
    out.treasuryTestButton = true
    credTest.click()
    await sleep(3500)
    out.treasuryTestResult = /✓|✗|testing/.test(txt())
  }
  out.clickedCompletion = await clickTab('Verification')  // re-open; treasury checks moved off the view
  await sleep(1800)
  out.completionRenders = /the gate/i.test(txt()) && /(VERIFIED_COMPLETE|INCOMPLETE|WORKING|VERIFYING|NO GOAL)/.test(txt())
  out.completionTimeline = /Completion timeline/.test(txt())
  out.completionVerifyBtn = [...document.querySelectorAll('button')].some((b) => b.textContent.includes('Verify now'))
  out.completionVerifyShows = false
  let verifyBtn = [...document.querySelectorAll('button')].find((b) => b.textContent.includes('Verify now'))
  if (!verifyBtn) {
    // self-heal (idempotent runs): no live goal in the latest session — begin
    // one through the chat channel; the button must reappear for THIS session
    window.location.hash = '#/'
    await sleep(1400)
    const fixture = visibleComposer()
    if (fixture) {
      await sendDraft(fixture, '@goal E2E verify fixture: reply with ok')
      await idle(20)
    }
    await clickTab('Verification')
    // the Verify button appears only once the goal landed in the store —
    // bounded element wait instead of a single sleep
    for (let w = 0; w < 30 && ![...document.querySelectorAll('button')].some((b) => b.textContent.includes('Verify now')); w += 1) await sleep(1000)
    verifyBtn = [...document.querySelectorAll('button')].find((b) => b.textContent.includes('Verify now'))
    out.completionVerifyBtn = Boolean(verifyBtn)
    if (verifyBtn && !verifyBtn.disabled) {
      verifyBtn.click()
      await sleep(9000)
      out.completionVerifyShows = /last verdict|no verdict yet|INCOMPLETE|VERIFIED_COMPLETE/.test(txt())
    }
  }
  if (verifyBtn && !verifyBtn.disabled) {
    verifyBtn.click()
    await sleep(9000)
    out.completionVerifyShows = /last verdict|no verdict yet|INCOMPLETE|VERIFIED_COMPLETE/.test(txt())
  }
  // Cockpit tab left the nav — "open cockpit" now resolves through the panel
  // home route the chat actions use; assert the tab is absent instead.
  out.clickedBackLive = null

  // chat integration: goal status strip above the composer + @goal mention rows
  // Chat integration checks need a session with a LIVE GOAL: open a fresh
  // session if there is no edit-goal affordance yet and begin the goal through
  // the chat channel (real send via the node-side CDP handshake).
  const chatHash = window.location.hash
  if (chatHash.indexOf('#/2026') !== 0) {
    window.location.hash = '#/'
  }
  await sleep(1200)
  if (!document.querySelector('button[aria-label="Edit goal"]')) {
    const newBtn = [...document.querySelectorAll('button')].find((b) => (b.getAttribute('aria-label') || '').startsWith('New session'))
    if (newBtn) {
      newBtn.click()
      await sleep(2500)
    }
    const goalFixture = visibleComposer()
    if (goalFixture) {
      await sendDraft(goalFixture, '@goal E2E goal-edit fixture: reply ok')
      // wait for the goal to land in the strip (bounded element wait, never innerText)
      for (let w = 0; w < 60 && !document.querySelector('button[aria-label="Edit goal"]'); w += 1) await sleep(1000)
    }
  }
  out.chatGoalStrip = Boolean(document.querySelector('[data-godsv-strip]'))
  out.approvalsPill = document.querySelector('[data-godsv-approvals]')?.getAttribute('data-godsv-approvals') || null
  out.goalEditOpens = false
  const goalEditBtn = document.querySelector('button[aria-label="Edit goal"]')
  if (goalEditBtn) {
    goalEditBtn.click()
    await sleep(500)
    out.goalEditOpens = Boolean(document.querySelector('input[aria-label="Goal text"]'))
    const cancelGoal = [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === 'Cancel')
    if (cancelGoal) cancelGoal.click()
    await sleep(300)
  }
  for (let w = 0; w < 25 && !document.querySelector('[data-godsv-feed]'); w += 1) await sleep(1000)
  const feedEl = document.querySelector('[data-godsv-feed]')
  out.chatFeed = Boolean(feedEl)
  out.chatFeedText = feedEl ? feedEl.textContent.slice(0, 240) : ''
  const gvEditor = visibleComposer()
  out.composerWidth = 0
  out.composerRadius = ''
  out.composerPadEnd = ''
  const composerEl = document.querySelector('[class~="group/composer"]')
  if (composerEl) {
    const cs = getComputedStyle(composerEl)
    out.composerWidth = Math.round(composerEl.getBoundingClientRect().width)
    out.composerRadius = cs.borderRadius
    out.composerPadEnd = String(Math.round(window.innerHeight - composerEl.getBoundingClientRect().bottom)) + 'px'
  }
  out.atGoalMention = false
  if (gvEditor) {
    // wait for a running turn to end — a busy turn suppresses the mention
    // popover (the self-heal above just started one). The Stop button is the
    // reliable busy signal; innerText idle() is not.
    for (let w = 0; w < 90; w += 1) {
      const stopBtn = [...document.querySelectorAll('button')].find((b) =>
        /stop/i.test((b.getAttribute('aria-label') || b.title || '').trim())
      )
      if (!stopBtn) break
      await sleep(1000)
    }
    gvEditor.click()
    gvEditor.focus()
    await sleep(300)
    document.execCommand('selectAll')
    document.execCommand('delete')
    await sleep(150)
    document.execCommand('insertText', false, '@go')
    await sleep(2500)
    out.atGoalMention = document.body.innerText.includes('acceptance criterion')
    document.execCommand('undo')
    await sleep(150)
    document.execCommand('undo')
    await sleep(200)
    document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    gvEditor.blur()
  }
  // inspect drawer: clicking a feed row opens the evidence pane
  out.inspectOpens = false
  const feedBtn = document.querySelector('[data-godsv-feed] button')
  if (feedBtn) {
    feedBtn.click()
    await sleep(2000)
    out.inspectOpens = /God's Voice event/.test(txt()) && /Open the cockpit/.test(txt())
  }
  // keyboard flow: Escape closes the drawer and hands focus back to the composer
  out.inspectEscCloses = false
  out.focusBackInComposer = false
  if (out.inspectOpens) {
    const esc = { key: 'Escape', keyCode: 27, bubbles: true, cancelable: true }
    document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', esc))
    document.body.dispatchEvent(new KeyboardEvent('keydown', esc))
    await sleep(700)
    out.inspectEscCloses = !/Open the cockpit/.test(txt())
    const ed = document.querySelector('[contenteditable="true"]')
    out.activeAfterEsc = document.activeElement?.tagName || null
    out.focusBackInComposer = Boolean(ed) &&
      (ed === document.activeElement || Boolean(document.activeElement?.closest?.('[class~="group/composer"]')))
  }
  // retry once at the end: the mention popover can lose a race right after
  // the drawer dance, so re-verify the plugin row in a quiet composer
  if (!out.atGoalMention) {
    window.location.hash = '#/'
    await sleep(1500)
    const ed3 = document.querySelector('[contenteditable="true"]')
    if (ed3) {
      await idle()
      ed3.click()
      ed3.focus()
      await sleep(400)
      document.execCommand('insertText', false, '@go')
      await sleep(2600)
      out.atGoalMention = document.body.innerText.includes('acceptance criterion')
      document.execCommand('undo')
      await sleep(200)
      document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
      ed3.blur()
    }
  }
  window.location.hash = '#/gods-voice'
  await sleep(800)
  const html = document.documentElement.outerHTML
  out.domSecretLeak = [
    /sk-[A-Za-z0-9]{16,}/,
    /tok-[A-Za-z0-9]{8,}/,
    /GODSV_VAULT_KEY=\\S{6,}/,
    /secret:\\/\\/[^"<]*\\?token=/
  ].some((r) => r.test(html))
  return JSON.stringify(out)
})()`

const probePromise = evaluate(probe)
let probeSettled = false
probePromise.then(() => { probeSettled = true })
// mid-probe handoff: probes park on window.__gvSendAt until this side performs
// a real CDP click on the composer Send button (synthetic events never submit).
// Supports several sends per run (verify self-heal + goal-edit fixture).
let handoffs = 0
for (let i = 0; i < 2400 && !probeSettled; i += 1) {
  await new Promise((r) => setTimeout(r, 250))
  const at = await evaluate('window.__gvSendAt ? JSON.stringify(window.__gvSendAt) : null')
  if (!at) continue
  try {
    const { x, y } = JSON.parse(at)
    await call('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 })
    await call('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 })
    handoffs += 1
    console.log(`self-heal: sent draft #${handoffs} via real CDP click at ${Math.round(x)},${Math.round(y)}`)
  } catch (e) {
    console.log('self-heal: click failed', e?.message || e)
  }
  await evaluate('window.__gvSendAt = null')
}
const result = await probePromise
if (result?.error) {
  console.error('probe failed:', result.error)
  process.exit(3)
}
const data = JSON.parse(result)
console.log(JSON.stringify(data, null, 1))

// The product's visible name is "Skaldir Agent" (app window title, panel registration in
// plugin.js). The old expectation pinned the pre-rename label "God's Voice" and could never
// pass in the shipped app — a stale check, not a defect. Accept the registered name and
// record which one was seen so a rename cannot hide behind this line.
check(
  'title is the registered product name',
  data.title === 'Skaldir Agent' || data.title === "God's Voice",
  data.title
)
// Claim what the app can actually guarantee: the panel surface is mounted after the wake-up,
// whatever route the shell happens to show (the sweep below changes routes on purpose, so a
// hash substring was the wrong instrument — it failed on `#/gods-settings` and on session routes).
check(
  'panel surface is reachable and mounted',
  data.panelMounted === true || String(data.hash).includes('gods-voice'),
  `mounted=${data.panelMounted} hash=${data.hash} after ${data.warmupSeconds}s`
)
check('four areas remain as app navigation (cockpit/activity removed per V2-01/V2-11)', data.tabsPresent?.length === 4, String(data.tabsPresent))
check('cockpit and activity nav tabs are gone', data.navGone === true)
check('integrated app nav: no second nav layer inside the page', data.panelAsideGone === true && data.areaNavRows >= 4, `asideGone=${data.panelAsideGone} rows=${data.areaNavRows}`)
check('settings view bundled with 6 sub-tabs', data.clickedSettings === true && data.settingsSubtabs?.length === 6, String(data.settingsSubtabs))
check('models tab opens', data.clickedModels === true)
check('models renders roles', data.modelsRendersRoles === true)
check('models has Test model button', data.modelsHasTestButton === true)
check('models shows Hermes registry origin', data.modelsHasRegistryOrigin === true)
check('tools tab opens with sections', data.toolsRenders === true)
check('tools shows registry entries', data.toolsHasRegistryEntry === true)
check('tool detail opens and shows usage ledger', data.toolDetailBtn === true && data.toolDetailShowsUsage === true)
check('tools shows capability requests card', data.capabilityRequestsRender === true)
check('capability request names the main agent', data.capabilityRequestShowsAgent === true)
check('capability request shows secret handle', data.capabilityRequestShowsHandle === true)
check('cu tab renders runners + policy', data.cuRenders === true)
check('cu demo button present', data.cuDemoBtn === true)
check('cu demo runs in the real app', data.cuRunShows === true)
check('verification view (completion supervisor) opens', data.clickedVerification === true && data.evalsRenders === true)
check('eval run button present', data.evalBtn === true)
check('eval run executes in the real app', data.evalRunShowsResult === true)
check('treasury shows credential flow (or vault-locked hint)', data.treasuryRendersCredentialFlow === true)
check('credential self-test works in the app (when a credential exists)', !data.treasuryTestButton || data.treasuryTestResult === true)
check('completion tab opens with goal + status', data.clickedCompletion === true && data.completionRenders === true)
check('completion tab renders the loop timeline', data.completionTimeline === true)
check('completion verify button present + runs in the real app', data.completionVerifyBtn === true && data.completionVerifyShows === true)
// 'back to Live' removed with the cockpit nav tab (V2-01): the panel home
// route /gods-voice stays reachable from chat actions instead.
check('chat goal strip rendered above the composer', data.chatGoalStrip === true)
check('approvals pill shows a real engine mode', ['manual', 'smart', 'off'].includes(data.approvalsPill), String(data.approvalsPill))
check('goal edit opens inline', data.goalEditOpens === true)
check('composer bar capped (centered, shorter)', data.composerWidth > 500 && data.composerWidth < 830, String(data.composerWidth))
check('composer bar floats further off the bottom', parseFloat(data.composerPadEnd) > 45, String(data.composerPadEnd))
check('composer bar radius softened', parseFloat(data.composerRadius) >= 18, String(data.composerRadius))
check('chat feed shows live gate/voice events', data.chatFeed === true && /Verification|VOICE|GATE/.test(data.chatFeedText || ''), data.chatFeedText)
check('feed row opens the inspect drawer', data.inspectOpens === true)
check('inspect drawer closes on Escape', data.inspectEscCloses === true)
check('focus returns to the composer after Escape', data.focusBackInComposer === true, `active=${data.activeAfterEsc}`)
check('@goal mention row offered by the plugin', data.atGoalMention === true)
check('no plaintext secret material in DOM', data.domSecretLeak === false)

ws.close()
const failed = checks.filter((c) => !c.ok)
console.log(`\ndesktop UI E2E: ${failed.length ? `${failed.length} FAIL` : 'all checks passed'}`)
process.exit(failed.length ? 1 : 0)
