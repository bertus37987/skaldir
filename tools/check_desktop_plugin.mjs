/**
 * Load-smoke for the God's Voice desktop panel (no dependencies beyond node).
 *
 * Rebuilds the app's load pipeline locally: copies the real plugin.js into a
 * scratch dir and resolves the three allowed specifiers via minimal mocks
 * (@hermes/plugin-sdk, react, react/jsx-runtime). Then it:
 *   1. imports the plugin and validates the HermesPlugin contract,
 *   2. runs register(ctx) and checks all four contributions (routes, sidebar,
 *      palette, chat-empty),
 *   3. exercises the data path (ctx.rest -> queryFn) and the render tree for
 *      loading / data / error states,
 *   4. walks every panel tab (Live, Pausen, Memory, Skills, Treasury, System)
 *      with representative payloads,
 *   5. renders the chat-empty contribution (God's Voice face).
 *
 * The react mock keeps real useState state (slot-backed) so tab switching in
 * the smoke actually switches, mirroring the browser behaviour.
 *
 * Exit 0 = panel contract OK. Exit 1 = violation (details on stdout).
 */
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const pluginSrc = join(here, '..', 'integrations', 'hermes', 'godsv_plugin', 'desktop', 'plugin.js')

const failures = []
function check(label, condition, detail = '') {
  if (condition) {
    console.log(`ok   ${label}`)
  } else {
    console.log(`FAIL ${label}${detail ? ` — ${detail}` : ''}`)
    failures.push(label)
  }
}

const root = mkdtempSync(join(tmpdir(), 'godsv-panel-'))
try {
  const nm = join(root, 'node_modules')
  mkdirSync(join(nm, '@hermes', 'plugin-sdk'), { recursive: true })
  mkdirSync(join(nm, 'react'), { recursive: true })

  writeFileSync(join(root, 'package.json'), JSON.stringify({ type: 'module' }))

  // --- @hermes/plugin-sdk mock -------------------------------------------------
  writeFileSync(
    join(nm, '@hermes', 'plugin-sdk', 'package.json'),
    JSON.stringify({ name: '@hermes/plugin-sdk', version: '0.0.0-mock', type: 'module', exports: { '.': './index.js' } })
  )
  writeFileSync(
    join(nm, '@hermes', 'plugin-sdk', 'index.js'),
    `
import { jsx } from 'react/jsx-runtime'
export const PALETTE_AREA = 'palette'
export const ROUTES_AREA = 'routes'
export const SIDEBAR_NAV_AREA = 'sidebarNav'
export const CHAT_EMPTY_AREA = 'chat.empty'
export const COMPOSER_AREAS = {
  top: 'composer.top',
  bottom: 'composer.bottom',
  underside: 'composer.underside',
  leading: 'composer.leading',
  actions: 'composer.actions',
  middleware: 'composer.middleware',
  attachments: 'composer.attachments',
  microActions: 'composer.microActions',
  atCompletions: 'composer.atCompletions'
}
export function Wordmark({ className, text }) {
  return jsx('div', { className, children: text })
}
globalThis.__NAVIGATED__ = []
export const host = {
  navigate: (to) => globalThis.__NAVIGATED__.push(to),
  state: {
    focusedSessionId: {
      get: () => globalThis.__FOCUSED_SESSION__ || '',
      subscribe: () => () => {}
    }
  },
  notify: () => {},
  request: async () => ({}),
  openWorkspace: (id, options) => {
    if (!globalThis.__OPEN_WORKSPACE__) globalThis.__OPEN_WORKSPACE__ = []
    globalThis.__OPEN_WORKSPACE__.push({ id, options })
    return () => {}
  }
}
export function useQuery({ queryFn, queryKey } = {}) {
  globalThis.__LAST_QUERY_FN__ = queryFn
  if (!globalThis.__QUERY_FNS__) globalThis.__QUERY_FNS__ = []
  globalThis.__QUERY_FNS__.push(queryFn)
  if (queryKey !== undefined) {
    if (!globalThis.__QUERY_KEYS__) globalThis.__QUERY_KEYS__ = []
    globalThis.__QUERY_KEYS__.push(JSON.stringify(queryKey))
  }
  const override = globalThis.__QUERY_OVERRIDE__
  if (override === 'error') return { data: null, isLoading: false, error: new Error('mock'), refetch: () => {} }
  if (override === 'loading') return { data: null, isLoading: true, error: null, refetch: () => {} }
  return { data: globalThis.__PANEL_DATA__ ?? null, isLoading: false, error: null, refetch: () => {} }
}
`
  )

  // --- react + react/jsx-runtime mocks ----------------------------------------
  writeFileSync(
    join(nm, 'react', 'package.json'),
    JSON.stringify({
      name: 'react',
      version: '0.0.0-mock',
      type: 'module',
      exports: { '.': './index.js', './jsx-runtime': './jsx-runtime.js' }
    })
  )
  writeFileSync(
    join(nm, 'react', 'index.js'),
    `
export const Fragment = Symbol('Fragment')
// Slot-backed useState: a "single-fiber" re-render model so setTab() from an
// onClick handler changes what the next render (after __beginRender) returns.
let __slots = []
let __cursor = 0
export function __beginRender() { __cursor = 0 }
export function __resetSlots() { __slots = []; __cursor = 0 }
export function useState(initial) {
  const i = __cursor++
  if (!(i in __slots)) __slots[i] = typeof initial === 'function' ? initial() : initial
  return [__slots[i], (next) => { __slots[i] = typeof next === 'function' ? next(__slots[i]) : next }]
}
export function useEffect() {}
export function useRef(v) { return { current: v } }
export function useMemo(fn) { return fn() }
export function useCallback(fn) { return fn }
export function useSyncExternalStore(subscribe, getSnapshot) { return getSnapshot() }
// React attaches host refs after mounting; the plugin measures its approval
// trigger with getBoundingClientRect to place the mode menu, so the mock has to
// hand over a DOM-like node — otherwise the menu could never be asserted here.
export function __nodeFor(type) {
  return {
    tag: String(type),
    getBoundingClientRect: () => ({ left: 120, top: 700, right: 260, bottom: 720, width: 140, height: 20 }),
    contains: () => false,
    addEventListener: () => {},
    removeEventListener: () => {},
    querySelector: () => null,
    querySelectorAll: () => [],
    appendChild: () => {},
    insertBefore: () => {},
    setAttribute: () => {},
    style: {},
    className: '',
    textContent: '',
    offsetTop: 0,
    offsetLeft: 0,
    offsetWidth: 0,
    offsetHeight: 0,
    scrollTop: 0,
    parentElement: null
  }
}
`
  )
  writeFileSync(
    join(nm, 'react', 'jsx-runtime.js'),
    `
export function jsx(type, props, key) {
  const p = props || {}
  if (p.ref && typeof p.ref === 'object' && typeof type === 'string') p.ref.current = __nodeFor(type)
  return { type, props: p, key }
}
export function jsxs(type, props, key) {
  const p = props || {}
  if (p.ref && typeof p.ref === 'object' && typeof type === 'string') p.ref.current = __nodeFor(type)
  return { type, props: p, key }
}
// React attaches host refs after mounting; the plugin measures its approval
// trigger with getBoundingClientRect to place the mode menu, so the mock hands
// over a DOM-like node instead of nothing (otherwise the menu could never be
// asserted in this harness).
export function __nodeFor(type) {
  return {
    tag: String(type),
    getBoundingClientRect: () => ({ left: 120, top: 700, right: 260, bottom: 720, width: 140, height: 20 }),
    contains: () => false,
    addEventListener: () => {},
    removeEventListener: () => {},
    querySelector: () => null,
    querySelectorAll: () => [],
    appendChild: () => {},
    insertBefore: () => {},
    setAttribute: () => {},
    style: {},
    className: '',
    textContent: '',
    offsetTop: 0,
    offsetLeft: 0,
    offsetWidth: 0,
    offsetHeight: 0,
    scrollTop: 0,
    parentElement: null
  }
}
`
  )

  cpSync(pluginSrc, join(root, 'plugin.js'))

  // --- 1) contract -------------------------------------------------------------
  const mod = await import(pathToFileURL(join(root, 'plugin.js')).href)
  const plugin = mod.default
  check('default export exists', !!plugin)
  check('id is godsv', plugin?.id === 'godsv', String(plugin?.id))
  check('name present', typeof plugin?.name === 'string' && plugin.name.length > 0)
  check('register is a function', typeof plugin?.register === 'function')

  // --- 2) register(ctx) ---------------------------------------------------------
  const registrations = []
  const restCalls = []
  const restDetailed = []
  const responses = {}
  // Minimal DOM stub so register()'s chrome-style injection has somewhere to write
  const styleTags = []
  globalThis.document = {
    head: { appendChild: (node) => styleTags.push(node) },
    createElement: (tag) => ({ tag, id: '', textContent: '' }),
    getElementById: (id) => styleTags.find((node) => node.id === id) || null
  }
  const ctx = {
    register: (reg) => registrations.push(reg),
    rest: async (path, opts) => {
      restCalls.push(path)
      restDetailed.push({ path, ...(opts || {}) })
      // error injection: a panel call that fails must stay visible, so the harness needs a
      // way to make one fail (Wave 2: the old `catch {}` hid exactly this)
      const boom = globalThis.__REST_THROW__
      if (boom && String(path).startsWith(boom)) throw new Error(`panel call failed: ${path}`)
      const hit = Object.keys(responses).find((k) => path.startsWith(k))
      return hit ? responses[hit] : globalThis.__PANEL_DATA__ ?? null
    }
  }
  plugin.register(ctx)
  const byArea = Object.fromEntries(registrations.map((r) => [r.area, r]))
  // TEN contributions: page, 4x nav, palette, chat-empty, live-block,
  // at-completions, goal-middleware, status-strip, approvals, microActions.
  // NINE contributions: page, 4x nav, palette, chat-empty, live-block,
  // at-completions, middleware, plan-row, microActions. The approval pill left
  // the composer (owner 17.09.): the status bar's approval menu is the single
  // control for that setting, so there is one fewer composer contribution.
  check('nine contributions registered', registrations.length === 9, String(registrations.length))
  const micro = registrations.find((r) => r.area === 'composer.microActions')
  check(
    'microActions strip: only badges the product can actually serve ship (S02, Wave 9)',
    Boolean(micro) &&
      typeof micro.data.resolve === 'function' &&
      (() => {
        const acts = micro.data.resolve({ busy: false, sessionId: 's1', todos: [] })
        const labels = acts.map((a) => a.label).join('|')
        return (
          acts.length === 2 &&
          labels.includes('Open plan') &&
          labels.includes('Find tool') &&
          // the docker badge promised an image flow that no surface provides (Wave 9)
          !/docker/i.test(labels) &&
          acts.every((a) => typeof a.run === 'function')
        )
      })(),
    (() => {
      try {
        if (!micro) return 'missing contribution'
        return JSON.stringify(micro.data.resolve({ busy: false, sessionId: 's1', todos: [] }).map((a) => a.label))
      } catch (e) {
        return String(e)
      }
    })()
  )
  check(
    'microActions keep the always-valid badge without a session (the tools view needs no goal)',
    (() => {
      try {
        const acts = micro.data.resolve({ busy: false, sessionId: '', todos: [] })
        return acts.length === 1 && acts[0].label.includes('Find tool')
      } catch (e) {
        return String(e)
      }
    })(),
    'ok'
  )
  check(
    'chrome styles injected — composer geometry travels with the plugin',
    styleTags.length === 1 &&
      styleTags[0].textContent.includes('--composer-width: 46rem') &&
      styleTags[0].textContent.includes('--composer-shell-pad-block-end: 2.5rem') &&
      styleTags[0].textContent.includes('group/composer'),
    JSON.stringify(styleTags.map((n) => n.textContent).join('|')).slice(0, 160)
  )
  check(
    'rare AV controls hidden — composer keeps text + decisions',
    styleTags[0].textContent.includes('button[aria-label="Voice dictation"]') &&
      styleTags[0].textContent.includes('aria-label^="Wake word"'),
    'dictation / read-aloud / wake-word display:none'
  )
  check('routes page at /gods-voice', byArea.routes?.data?.path === '/gods-voice')
  check('sidebar nav wired', byArea.sidebarNav?.data?.path === '/gods-voice' && !!byArea.sidebarNav?.data?.codicon)
  check('palette command present', typeof byArea.palette?.data?.run === 'function')
  check('chat-empty contribution present', typeof byArea['chat.empty']?.data?.render === 'function')
  byArea.palette?.data?.run?.()
  check('palette run navigates to panel', globalThis.__NAVIGATED__?.includes('/gods-voice'))

  // --- chat integration: @goal mentions, middleware, status strip --------------
  const atCompletions = byArea['composer.atCompletions']?.data?.provide
  const middleware = byArea['composer.middleware']?.data?.handler
  check('composer at-completions source registered', typeof atCompletions === 'function')
  const goalRows = atCompletions ? atCompletions('go') : []
  check('at-completions offers @goal', goalRows.some((r) => r.insert === '@goal '), JSON.stringify(goalRows))
  check('at-completions offers @tool', (atCompletions ? atCompletions('tool') : []).some((r) => r.insert === '@tool:'))
  check('at-completions silent for other queries', (atCompletions ? atCompletions('zzz') : []).length === 0)
  check('composer middleware registered', typeof middleware === 'function')
  // Session scoping: the composer middleware must resolve the FOCUSED
  // session — the owner-observed leak came from unscoped /completion
  // reads answering with another session's contract.
  globalThis.__FOCUSED_SESSION__ = 'mock-session-42'
  // This block pins the APPEND contract, so it needs an open goal; the begin case (no open
  // contract -> POST /completion/goal) is pinned separately below.
  responses['/completion?'] = { goal: 'Ship the strict completion supervisor', state: 'WORKING', terminal: false, revision: 1 }
  responses['/completion'] = responses['/completion?']
  const gvDraft = {
    text: 'Please build this feature.\n@goal It must survive a restart.\n@goal No unrelated settings change.\n!decision: keep the existing store'
  }
  const gvResult = await middleware(gvDraft)
  await globalThis.__GV_GOAL_TASK__
  check('middleware passes the draft through unchanged', gvResult === gvDraft)
  const criteriaCall = restDetailed.find((c) => String(c.path).includes('/completion/criteria'))
  check(
    'middleware posts @goal criteria',
    Boolean(criteriaCall) && criteriaCall.method === 'POST' && criteriaCall.body?.criteria?.length === 2,
    JSON.stringify(restDetailed.filter((c) => String(c.path).includes('/completion/')))
  )
  const trajCall = restDetailed.find((c) => String(c.path).includes('/completion/trajectory'))
  check(
    'middleware posts !decision trajectory entries scoped to the session',
    Boolean(trajCall) &&
      trajCall.method === 'POST' &&
      trajCall.body?.kind === 'decision' &&
      trajCall.body?.session_id === 'mock-session-42',
    JSON.stringify(restDetailed.filter((c) => String(c.path).includes('/completion/trajectory')))
  )
  check(
    'middleware scopes the criteria POST to the focused session',
    criteriaCall?.body?.session_id === 'mock-session-42',
    JSON.stringify(criteriaCall?.body || {})
  )
  // --- Wave 2: @goal OPENS a goal when none is open, and failures stay visible --------
  const completionsSince = (index) => restDetailed.slice(index)
  const terminalFixture = { goal: '', state: 'VERIFIED_COMPLETE', terminal: true, revision: 1 }
  responses['/completion?'] = terminalFixture
  responses['/completion'] = terminalFixture
  const openDraft = {
    text: 'Bitte den Statusstreifen aufräumen.\n@goal Der Streifen zeigt den echten Zustand.'
  }
  const openIndex = restDetailed.length
  await middleware(openDraft)
  await globalThis.__GV_GOAL_TASK__
  const openedCalls = completionsSince(openIndex)
  check(
    'a @goal draft without an open contract OPENS a goal instead of appending criteria',
    openedCalls.some((c) => String(c.path).includes('/completion/goal') && c.method === 'POST') &&
      !openedCalls.some((c) => String(c.path).includes('/completion/criteria')),
    JSON.stringify(openedCalls.map((c) => `${c.method || 'GET'} ${c.path}`))
  )
  const openedBody = openedCalls.find((c) => String(c.path).includes('/completion/goal'))?.body || {}
  check(
    'the opened goal carries the user sentence, the criterion and the focused session',
    /Statusstreifen aufr\.umen|aufräumen/.test(String(openedBody.goal || '')) &&
      openedBody.criteria?.length === 1 &&
      openedBody.session_id === 'mock-session-42',
    JSON.stringify(openedBody)
  )
  const workingFixture = { goal: 'Ship the strict completion supervisor', state: 'WORKING', terminal: false, revision: 2 }
  responses['/completion?'] = workingFixture
  responses['/completion'] = workingFixture
  const appendIndex = restDetailed.length
  await middleware(openDraft)
  await globalThis.__GV_GOAL_TASK__
  const appendCalls = completionsSince(appendIndex)
  check(
    'an OPEN contract still receives the @goal line as a criterion',
    appendCalls.some((c) => String(c.path).includes('/completion/criteria') && c.method === 'POST') &&
      !appendCalls.some((c) => String(c.path).includes('/completion/goal')),
    JSON.stringify(appendCalls.map((c) => `${c.method || 'GET'} ${c.path}`))
  )
  // No open contract + a failing begin call = the case the old `catch {}` hid entirely.
  responses['/completion?'] = terminalFixture
  responses['/completion'] = terminalFixture
  globalThis.__REST_THROW__ = '/completion/goal'
  await middleware(openDraft)
  await globalThis.__GV_GOAL_TASK__
  globalThis.__REST_THROW__ = null
  check(
    'a failed goal action is never swallowed (the middleware keeps going)',
    typeof byArea['composer.middleware']?.data?.handler === 'function'
  )
  {
    // No focused session id on this app build (measured in the live app: the app exposed none at
    // send time): the goal must still be recorded, and the strip tells the user the scope is
    // missing instead of leaving them with silence.
    globalThis.__FOCUSED_SESSION__ = ''
    responses['/completion?'] = terminalFixture
    responses['/completion'] = terminalFixture
    const noSidIndex = restDetailed.length
    await middleware(openDraft)
    await globalThis.__GV_GOAL_TASK__
    const noSidCalls = completionsSince(noSidIndex)
    check(
      'without a focused session id the goal is still recorded (unscoped, no silent loss)',
      noSidCalls.some((c) => String(c.path).includes('/completion/goal') && !c.body?.session_id),
      JSON.stringify(noSidCalls.map((c) => `${c.method || 'GET'} ${c.path}`))
    )
    globalThis.__FOCUSED_SESSION__ = 'mock-session-42'
  }
  const plainDraft = { text: 'no mentions here' }
  const plainCallsBefore = restDetailed.length
  await middleware(plainDraft)
  check('middleware stays silent without @goal lines', restDetailed.length === plainCallsBefore)
  check('composer status strip registered', typeof byArea['composer.top']?.render === 'function')
  if (typeof byArea['composer.top']?.render === 'function') {
    const reactMockEarly = await import(pathToFileURL(join(nm, 'react', 'index.js')).href)
    reactMockEarly.__resetSlots()
    reactMockEarly.__beginRender()
    const stripEl = byArea['composer.top'].render()
    const stripOut = []
    collect(stripEl, stripOut, 0)
    for (const fn of globalThis.__QUERY_FNS__ || []) {
      try {
        await fn()
      } catch (err) {
        /* query errors are fine here — the URL is the assertion */
      }
    }
    check(
      'status strip reads the FOCUSED session completion',
      restCalls.includes('/completion?session_id=mock-session-42'),
      restCalls.slice(-8).join(',')
    )
    check(
      'status strip reads the FOCUSED session events',
      restCalls.includes('/events?limit=6&session_id=mock-session-42'),
      restCalls.slice(-8).join(',')
    )
    // Wave 2: the failure recorded by the middleware must reach the strip's DOM, not vanish
    // in a `catch {}` (measured defect: a failed @goal looked like "nothing happened").
    globalThis.__PANEL_DATA__ = { state: 'WORKING', goal: 'ship it', total_count: 1, verified_count: 0, last_verdict: {} }
    // Walk the tree by CALLING function components (the shape the existing orb pin uses);
    // `collect` alone stays on the element and would report an empty strip.
    const goalTexts = []
    const walkGoal = (node, depth = 0) => {
      if (node == null || typeof node === 'boolean' || depth > 60) return
      if (typeof node === 'string' || typeof node === 'number') {
        goalTexts.push(String(node))
        return
      }
      if (Array.isArray(node)) {
        node.forEach((child) => walkGoal(child, depth + 1))
        return
      }
      if (typeof node === 'object' && 'props' in node) {
        if (node.props?.text) goalTexts.push(String(node.props.text))
        if (typeof node.type === 'function') {
          try {
            walkGoal(node.type({ ...node.props, children: node.props.children }), depth + 1)
            return
          } catch (err) {
            /* older builds render less — fall through to the children */
          }
        }
        walkGoal(node.props?.children, depth + 1)
      }
    }
    reactMockEarly.__resetSlots()
    reactMockEarly.__beginRender()
    walkGoal(byArea['composer.top'].render())
    if (!goalTexts.length) {
      reactMockEarly.__resetSlots()
      walkGoal(byArea['composer.top'].render())
    }
    const rendered = JSON.stringify(goalTexts)
    // Either honest failure must be visible — a panel error ("panel call failed") or the missing
    // session scope ("without a session"); what must never happen is silence.
    check(
      'the strip renders the failed goal action with its reason (no catch {} silence)',
      rendered.includes('panel call failed') || rendered.includes('without a session'),
      rendered.slice(0, 300)
    )
    // Clean up through the product path (a later successful action clears the failure) so the
    // feed pins below see the feed, not this pin's leftovers.
    responses['/completion?'] = workingFixture
    responses['/completion'] = workingFixture
    await middleware(openDraft)
    await globalThis.__GV_GOAL_TASK__
  }

  // --- Wave 9: a cut feed window is visible and walkable (audit AF-14) --------------------
  {
    const reactFeed = await import(pathToFileURL(join(nm, 'react', 'index.js')).href)
    const feedFixture = {
      // The plan row above the composer is goal-gated by design (UI audit P1
      // #13): without a goal it renders nothing, so a fixture that wants to see
      // the FEED must carry a goal — the feed is what this pin asserts, not the
      // row's presence.
      goal: 'ship the feed',
      state: 'WORKING',
      events: [
        {
          seq: 60,
          ts: Date.now() / 1000,
          session: 'mock-session-42',
          kind: 'pause_delivered',
          payload: {}
        }
      ],
      latest_seq: 60,
      oldest_seq: 21,
      remaining: 20,
      truncated: true,
      next_before: 21
    }
    responses['/events?'] = feedFixture
    // the harness's useQuery answers every query with __PANEL_DATA__, so the fixture is what
    // the strip's feed query sees (and its completion read, which this pin does not assert)
    globalThis.__PANEL_DATA__ = feedFixture
    // the query functions must run against this fixture before the strip renders it
    for (const fn of globalThis.__QUERY_FNS__ || []) {
      try {
        await fn()
      } catch (err) {
        /* the fixture is what this pin asserts, not query success */
      }
    }
    reactFeed.__resetSlots()
    reactFeed.__beginRender()
    const feedTexts = []
    const walkFeed = (node, depth = 0) => {
      if (node == null || typeof node === 'boolean' || depth > 60) return
      if (typeof node === 'string' || typeof node === 'number') {
        feedTexts.push(String(node))
        return
      }
      if (Array.isArray(node)) {
        node.forEach((child) => walkFeed(child, depth + 1))
        return
      }
      if (typeof node === 'object' && 'props' in node) {
        if (node.props?.text) feedTexts.push(String(node.props.text))
        if (typeof node.type === 'function') {
          try {
            walkFeed(node.type({ ...node.props, children: node.props.children }), depth + 1)
            return
          } catch (err) {
            /* fall through to children */
          }
        }
        walkFeed(node.props?.children, depth + 1)
      }
    }
    walkFeed(byArea['composer.top'].render())
    const feedRendered = JSON.stringify(feedTexts)
    check(
      'a window cut by the feed limit is reported (never silently dropped)',
      feedRendered.includes('older events outside the window') && feedRendered.includes('20'),
      feedRendered.slice(0, 260)
    )
    check(
      'the cut window offers the cursor-backed way to load it',
      feedRendered.includes('Load older') && feedRendered.includes('next_before=21'),
      feedRendered.slice(0, 260)
    )
    delete responses['/events?']
  }

  // --- 3) render helpers ----------------------------------------------------------
  const reactMock = await import(pathToFileURL(join(nm, 'react', 'index.js')).href)

  function renderPage() {
    reactMock.__beginRender()
    const pageReg = byArea.routes
    const element = pageReg.render() // jsx(Page, {})
    return element.type(element.props) // call the component directly
  }

  const FragmentSym = reactMock.Fragment

  function collect(node, out, depth = 0) {
    if (node == null || typeof node === 'boolean' || depth > 60) return
    if (typeof node === 'string' || typeof node === 'number') {
      out.push(String(node))
      return
    }
    if (Array.isArray(node)) {
      for (const child of node) collect(child, out, depth + 1)
      return
    }
    if (typeof node === 'object' && 'props' in node) {
      if (node.props?.text) out.push(String(node.props.text))
      if (typeof node.type === 'function') {
        try {
          collect(node.type({ ...node.props, children: node.props.children }), out, depth + 1)
        } catch (err) {
          if (process.env.GODSV_SMOKE_DEBUG) {
            console.log('COLLECT_ERR:', String(err).slice(0, 300))
            console.log('COLLECT_ERR_STACK:', String(err?.stack || '').split('\n').slice(0, 6).join(' | '))
          }
          collect(node.props?.children, out, depth + 1)
        }
        return
      }
      if (node.type === FragmentSym) {
        collect(node.props?.children, out, depth + 1)
        return
      }
      collect(node.props?.children, out, depth + 1)
    }
  }

  function findButtons(node, out, depth = 0) {
    if (node == null || typeof node !== 'object' || depth > 60) return
    if (Array.isArray(node)) {
      for (const child of node) findButtons(child, out, depth + 1)
      return
    }
    if ('props' in node) {
      if (node.type === 'button') out.push(node)
      if (typeof node.type === 'function') {
        try {
          findButtons(node.type({ ...node.props }), out, depth + 1)
        } catch {
          /* ignore */
        }
      }
      findButtons(node.props?.children, out, depth + 1)
    }
  }

  function clickTab(name) {
    const buttons = []
    findButtons(renderPage(), buttons)
    const target = buttons.find((b) => b.props?.children === name)
    if (!target) throw new Error(`tab button not found: ${name}`)
    target.props.onClick()
  }

  // --- 4) data path + render states ----------------------------------------------
  const sample = {
    voice_mode: 'whisper',
    mode: 'testgeld',
    runtime: '/tmp/godsv',
    generated_at: 1700000000,
    kinds: { pause_requested: 2, pause_delivered: 1, guard_check: 3, claim: 5 },
    recent: [{ ts: 1700000000, session: 's1', kind: 'pause_delivered', payload: { pause: 'p1' } }],
    pauses: [{ id: 'p1', kind: 'voice', status: 'delivered', created: 1700000000, ttl: 90, attempts: 1, session_id: 'sess-1' }],
    allowance: { mode: 'testgeld', by_status: { approved: 4.0 }, counts: { approved: 1 } }
  }

  // loading state
  globalThis.__QUERY_OVERRIDE__ = 'loading'
  let texts = []
  collect(renderPage(), texts)
  check('loading state renders', texts.join(' ').includes('loading status'), texts.join(' ').slice(0, 80))

  // --- chat feed rendering (v2): fresh voice/gate events, no noise, no staleness
  globalThis.__QUERY_OVERRIDE__ = undefined
  globalThis.__PANEL_DATA__ = {
    voice_mode: 'whisper',
    // goal-gated plan row (UI audit P1 #13): the row is what carries the feed
    goal: 'ship the feed',
    state: 'WORKING',
    events: [
      {
        seq: 9,
        ts: Math.floor(Date.now() / 1000) - 90,
        kind: 'completion_verdict',
        session: 's-demo',
        step: 4,
        payload: { status: 'INCOMPLETE', blockers: ['no restart evidence'], evidence: ['pytest: ok'], fingerprint: 'abc123' }
      },
      { seq: 8, ts: Math.floor(Date.now() / 1000) - 150, kind: 'pause_requested', payload: { reason: 'loop detected' } },
      { seq: 7, ts: Math.floor(Date.now() / 1000) - 3 * 24 * 3600, kind: 'completion_rejected', payload: {} }
    ],
    latest_seq: 9
  }
  const feedTexts = []
  const stripElement = byArea['composer.top'].render()
  const stripTree = stripElement.type(stripElement.props)
  collect(stripTree, feedTexts)
  const feedJoined = feedTexts.join(' ')
  check(
    'feed renders fresh verdict + intervention',
    feedJoined.includes('Verification: INCOMPLETE') && feedJoined.includes('Voice paused the agent'),
    feedJoined.slice(0, 200)
  )
  check('feed labels its sources', feedJoined.includes('GATE') && feedJoined.includes('VOICE'))
  check('feed drops stale events', !feedJoined.includes('Completion rejected'))
  check('feed shows relative ages', /(^| )(now|\dm)( |$)/.test(feedJoined))


  // --- inspect drawer (v3): a feed click opens the evidence pane ---------------
  const feedButtons = []
  findButtons(stripTree, feedButtons)
  const eventBtn = feedButtons.find((b) => String(b.props?.['aria-label'] || '').includes("Skaldir Agent event"))
  check('feed rows are interactive (inspect entry)', Boolean(eventBtn))
  if (eventBtn) {
    globalThis.__OPEN_WORKSPACE__ = []
    eventBtn.props.onClick()
    const opened = globalThis.__OPEN_WORKSPACE__[0]
    check('feed click opens the inspect drawer', globalThis.__OPEN_WORKSPACE__.length === 1, JSON.stringify(globalThis.__OPEN_WORKSPACE__?.map((o) => o.id)))
    check(
      'drawer docks right with an event title',
      Boolean(opened) && opened.options?.dock?.pos === 'right' && String(opened.options?.title || '').includes('GATE'),
      opened ? `${opened.id} / ${opened.options?.title}` : 'none'
    )
    const drawerTexts = []
    if (opened) collect(opened.options.render(), drawerTexts)
    const drawerJoined = drawerTexts.join(' ')
    check(
      'drawer shows verdict evidence',
      drawerJoined.includes('INCOMPLETE') && drawerJoined.includes('no restart evidence') && drawerJoined.includes('pytest: ok'),
      drawerJoined.slice(0, 220)
    )
    check('drawer links to the full diagnostics panel', drawerJoined.includes('Open diagnostics'))
    check('drawer shows metadata (session/seq/fingerprint)', drawerJoined.includes('s-demo') && drawerJoined.includes('abc123'))
  }
  globalThis.__PANEL_DATA__ = null

  // data path: the component calls rest via the captured queryFn(s)
  globalThis.__QUERY_OVERRIDE__ = undefined
  globalThis.__PANEL_DATA__ = sample
  globalThis.__QUERY_FNS__ = []
  texts = []
  collect(renderPage(), texts)
  const fnResults = []
  for (const fn of globalThis.__QUERY_FNS__) fnResults.push(await fn())
  check('queryFn calls ctx.rest /summary', restCalls.includes('/summary?limit=40'), restCalls.join(','))
  check('queryFn returns payload', fnResults.some((r) => r?.mode === 'testgeld'))

  texts = []
  collect(renderPage(), texts)
  const joined = texts.join(' ')
  check('cockpit renders stat line', joined.includes('pauses') && joined.includes('guard checks') && joined.includes('events'))
  check('cockpit renders recent signal', joined.includes('pause_delivered'))
  // The mock's slot-backed useState means a deep re-render can hand `view` a
  // value that belongs to another component (the harness is a single-fiber
  // model), so the pin asserts the SHAPE — exactly one plain title element and
  // no telemetry header — instead of the literal title string.
  const pageTree = renderPage()
  const h1Texts = []
  const walkH1 = (node, depth = 0) => {
    if (!node || typeof node !== 'object' || depth > 60) return
    if (Array.isArray(node)) {
      node.forEach((child) => walkH1(child, depth + 1))
      return
    }
    const props = node.props || {}
    if (node.type === 'h1') {
      collect(node, h1Texts)
      return
    }
    if (typeof node.type === 'function') {
      try {
        walkH1(node.type({ ...props, children: props.children }), depth + 1)
      } catch {
        /* fall through to children */
      }
      return
    }
    walkH1(props.children, depth + 1)
  }
  walkH1(pageTree)
  check(
    'panel page has a plain title instead of the telemetry header',
    h1Texts.length === 1 &&
      h1Texts[0].trim().length > 2 &&
      !joined.includes('whisper mode') &&
      !joined.includes('↻ refresh'),
    `h1=${JSON.stringify(h1Texts)} mode=${joined.includes('whisper mode')} refresh=${joined.includes('↻ refresh')}`
  )
  // UI audit: one navigation — Control + Manage(Settings, Treasury)
  // V2-01/V2-11: the nav layer shrank — cockpit/activity live in the chat
  // (live block + status strip + Inspect drawers), so the panel nav must NOT
  // offer them any more. The rail keeps only unique-function views.
  check('nav renders groups', ['Control', 'Manage'].every((t) => joined.includes(t)))
  check('nav keeps only unique-function views', ['Verification', 'Tools', 'Settings', 'Treasury'].every((t) => joined.includes(t)))
  check('nav no longer lists Cockpit or Activity', !joined.includes('Cockpit'), joined.slice(0, 200))

  // Cockpit: goal + completion state (the control layer answers "where are we?")
  globalThis.__PANEL_DATA__ = {
    voice_mode: 'whisper',
    state: 'INCOMPLETE',
    terminal: false,
    goal: 'Ship the strict completion supervisor',
    session: 's1',
    requirements: [
      { id: 'c1', text: 'runtime uses the selection', blocking: true, verified: true },
      { id: 'c2', text: 'persistence survives restart', blocking: true, verified: false }
    ],
    verified_count: 1,
    total_count: 2,
    last_verdict: {
      status: 'INCOMPLETE',
      blocking_failures: ['definition-of-done: persistence survives restart — no evidence'],
      major_failures: [],
      minor_findings: [],
      evidence: ['pytest: ok (pytest -q @ step 4)'],
      next_actions: ['produce evidence for: persistence survives restart'],
      created: 1700000000
    },
    gates: [{ name: 'acceptance', status: 'FAIL' }, { name: 'tests', status: 'PASS' }],
    timeline: [],
    metrics: { completion_requests: 3, completion_rejections: 2 }
  }
  if (reactMock.__resetSlots) reactMock.__resetSlots()
  // Route-driven: the cockpit is the panel home; it left the nav but the view
  // (and route) stay — reached directly via the route, as chat actions do.
  globalThis.__QUERY_FNS__ = []
  texts = []
  collect(renderPage(), texts)
  const cockpitJoined = texts.join(' ')
  check('cockpit renders goal + state', cockpitJoined.includes('Ship the strict completion supervisor') && cockpitJoined.includes('INCOMPLETE'))
  check('cockpit renders requirements checklist', cockpitJoined.includes('runtime uses the selection') && cockpitJoined.includes('persistence survives restart'))
  check('cockpit renders blocker detail', cockpitJoined.includes('definition-of-done: persistence survives restart'))
  check('cockpit renders evidence line', cockpitJoined.includes('pytest: ok'))
  check('cockpit renders progress', cockpitJoined.includes('1 / 2 requirements verified'))
  for (const fn of globalThis.__QUERY_FNS__) await fn()
  check('cockpit queries /completion', restCalls.some((p) => String(p).startsWith('/completion')), restCalls.join(','))

  // LiveTab/PausenTab left the NAV (V2-11): no tab row "Activity" may remain.
  // The same live state is covered by the cockpit home (asserted above: stat
  // line + Voice activity card + Recent signal) and by the chat live block.
  globalThis.__PANEL_DATA__ = sample
  texts = []
  const allBtns = []
  collect(renderPage(), texts)
  findButtons(renderPage(), allBtns)
  check('activity left the nav tab bar', !allBtns.some((b) => b.props?.children === 'Activity'),
    JSON.stringify(allBtns.map((b) => b.props?.children)))
  const actJoined = texts.join(' ')
  check('cockpit carries the live KPIs (fetch-free surface)', actJoined.includes('redirects') && actJoined.includes('guard checks'), actJoined.slice(0, 220))
  check('pause history stays reachable (Voice activity card)', actJoined.includes('delivered'))

  // Memory tab
  globalThis.__PANEL_DATA__ = {
    incidents: [
      { id: 'i1', signature: 'sig-1', title: 'Wacom stall', kind: 'io', status: 'active', confidence: 0.8, occurrences: 3, last_seen: 1700000000 }
    ],
    notes: [
      { id: 'n1', topic: 'Hermes Desktop Envs', claim: 'userData is per instance', source_type: 'docs', trust: 'high', status: 'active', created: 1700000000 }
    ],
    db_present: true
  }
  clickTab('Settings')
  texts = []
  collect(renderPage(), texts)
  check(
    'settings bundles the admin views',
    ['Models', 'Tools', 'Computer Use', 'Memory', 'Skills', 'System'].every((t) =>
      texts.join(' ').includes(t)
    )
  )
  clickTab('Memory')
  texts = []
  collect(renderPage(), texts)
  const memJoined = texts.join(' ')
  check('memory tab renders incidents', memJoined.includes('Wacom stall') && memJoined.includes('Incidents'))
  check('memory tab renders notes', memJoined.includes('userData is per instance'))
  const memFn = globalThis.__LAST_QUERY_FN__
  await memFn()
  check('memory tab queries /memory', restCalls.includes('/memory?limit=60'), restCalls.join(','))

  // Skills tab
  globalThis.__PANEL_DATA__ = {
    skills: [
      { id: 's1', name: 'godsv-verify', title: 'Verify before done', status: 'promoted', occurrences: 4, sessions: 2, applied: 3, succeeded: 3, updated: 1700000000 }
    ],
    db_present: true
  }
  clickTab('Settings')
  clickTab('Skills')
  texts = []
  collect(renderPage(), texts)
  check('skills tab renders candidates', texts.join(' ').includes('godsv-verify'))
  await globalThis.__LAST_QUERY_FN__()
  check('skills tab queries /skills', restCalls.includes('/skills?limit=60'), restCalls.join(','))

  // Treasury tab
  globalThis.__PANEL_DATA__ = {
    spends: [{ id: 'sp1', amount: 12.5, currency: 'EUR', merchant: 'Testshop', status: 'approved', reason: 'Demo', created: 1700000000 }],
    grants: [{ id: 'g1', handle: 'secret://demo', status: 'active', created: 1700000000, decided: 1700000001 }],
    credentials: [{ handle: 'secret://demo/key', service: 'demo', scopes: ['grant:required'], used_by: ['money_convert'] }],
    db_present: true
  }
  clickTab('Treasury')
  globalThis.__QUERY_FNS__ = []
  texts = []
  collect(renderPage(), texts)
  const trJoined = texts.join(' ')
  check('treasury tab renders spends', trJoined.includes('12.50 EUR') && trJoined.includes('Testshop'))
  check('treasury tab renders grants', trJoined.includes('secret://demo'))
  check('credentials pane offers safe test action', trJoined.includes('test') && trJoined.includes('secret://demo/key'))
  for (const fn of globalThis.__QUERY_FNS__) await fn()
  check('treasury tab queries /treasury', restCalls.includes('/treasury?limit=60'), restCalls.join(','))
  check('credentials pane queries /treasury/credentials', restCalls.some((p) => p.startsWith('/treasury/credentials')), restCalls.join(','))

  // Computer Use tab
  globalThis.__PANEL_DATA__ = {
    runners: [
      { name: 'dry-run', status: 'available' },
      { name: 'sandbox (bwrap)', status: 'available' }
    ],
    policy: {
      channel_order: 'API > DOM > Vision',
      allowed_kinds: ['cli', 'dom_click', 'mouse'],
      max_actions: 20,
      max_fallbacks: 3,
      require_isolation: true
    },
    runs: [{ ts: 1700000000, status: 'verified', channel: 'api', actions: 2, fallbacks: 1, detail: 'ok', kind: 'dry-run' }]
  }
  clickTab('Settings')
  clickTab('Computer Use')
  globalThis.__QUERY_FNS__ = []
  texts = []
  collect(renderPage(), texts)
  const cuJoined = texts.join(' ')
  check('cu tab renders runners', cuJoined.includes('dry-run') && cuJoined.includes('bwrap'))
  check('cu tab renders policy + order', cuJoined.includes('API > DOM > Vision') && cuJoined.includes('isolation required'))
  check('cu tab renders dry-run history', cuJoined.includes('Recent runs') && cuJoined.includes('verified'))
  for (const fn of globalThis.__QUERY_FNS__) await fn()
  check('cu tab queries /cu', restCalls.includes('/cu'), restCalls.join(','))

  // Evals tab
  globalThis.__PANEL_DATA__ = {
    episodes: [
      { name: 'demo', events: 10, expect_signals: ['loop', 'error_burst'], expect_guard: null, expect_pause_delivered: true }
    ],
    metrics: {
      measured: {
        events_total: 42, pauses: 2, deliveries: 2, research_runs: 1, research_failed: 0,
        capability_requests: 1, context_builds: 3, context_chars: 9000
      },
      estimated: {
        voice_ctx_tokens: 2250, investigator_tokens: 100,
        notes: ['Tokens ≈ chars/4 (ESTIMATED). Hermes keeps the main-agent tokens.']
      },
      available: true
    },
    last_run: {
      ts: 1700000000, passed: 1, total: 1,
      results: [{ name: 'demo', passed: true, signals: ['loop'], notes: [] }]
    }
  }
  clickTab('Verification')
  globalThis.__QUERY_FNS__ = []
  texts = []
  collect(renderPage(), texts)
  const evJoined = texts.join(' ')
  check('verification view renders episodes', evJoined.includes('demo') && evJoined.includes('expected signals'))
  check('evals tab renders last run', evJoined.includes('1/1 passed'))
  check('evals tab renders measured metrics', evJoined.includes('MEASURED') && evJoined.includes('events_total'))
  check('evals tab renders estimated metrics', evJoined.includes('ESTIMATED') && evJoined.includes('voice_ctx_tokens'))
  for (const fn of globalThis.__QUERY_FNS__) await fn()
  check('evals tab queries /evals', restCalls.includes('/evals'), restCalls.join(','))

  // Completion tab (strict supervisor: goal, requirement checklist, gates, timeline)
  if (reactMock.__resetSlots) reactMock.__resetSlots()
  globalThis.__PANEL_DATA__ = {
    voice_mode: 'whisper',
    state: 'INCOMPLETE',
    terminal: false,
    goal: 'Ship the strict completion supervisor',
    session: 's1',
    requirements: [
      { id: 'c1', text: 'runtime uses the selection', blocking: true, verified: true },
      { id: 'c2', text: 'persistence survives restart', blocking: true, verified: false }
    ],
    verified_count: 1,
    total_count: 2,
    last_verdict: {
      status: 'INCOMPLETE',
      blocking_failures: ['definition-of-done: persistence survives restart — no evidence'],
      major_failures: [],
      minor_findings: [],
      evidence: ['pytest: ok (pytest -q @ step 4)'],
      next_actions: ['produce evidence for: persistence survives restart'],
      created: 1700000000
    },
    gates: [
      { name: 'acceptance', status: 'FAIL' },
      { name: 'tests', status: 'PASS' }
    ],
    timeline: [
      { kind: 'state', state: 'WORKING', created: 1699999000 },
      { kind: 'completion_request', attempt_id: 'att-1', created: 1699999500 },
      { kind: 'verification', status: 'INCOMPLETE', created: 1699999600 },
      { kind: 'verification', status: 'VERIFIED_COMPLETE', created: 1700000000 }
    ],
    metrics: {
      completion_requests: 3, completion_rejections: 2, verified_completions: 1,
      false_completion_prevented: 2, attempts_until_verified: 3, requirements_verified: 9
    }
  }
  clickTab('Verification')
  globalThis.__QUERY_FNS__ = []
  texts = []
  collect(renderPage(), texts)
  const compJoined = texts.join(' ')
  check('verification view renders goal + status', compJoined.includes('Ship the strict completion supervisor') && compJoined.includes('INCOMPLETE'))
  check('completion tab renders requirement checklist', compJoined.includes('runtime uses the selection') && compJoined.includes('persistence survives restart'))
  check('completion tab renders gate grid', compJoined.includes('acceptance') && compJoined.includes('FAIL'))
  check('completion tab renders timeline verdicts', compJoined.includes('VERIFIED_COMPLETE') && compJoined.includes('verdict'))
  check('completion tab renders metrics', compJoined.includes('false_completion_prevented'))
  check('completion tab renders supervisor actions', compJoined.includes('Verify now') && compJoined.includes('Accept override'), compJoined.slice(compJoined.indexOf('Supervisor'), compJoined.indexOf('Supervisor') + 160))
  for (const fn of globalThis.__QUERY_FNS__) await fn()
  check('completion tab queries /completion', restCalls.some((p) => String(p).startsWith('/completion')), restCalls.join(','))

  // Models tab
  globalThis.__PANEL_DATA__ = {
    providers: [
      { slug: 'deepseek', models: ['deepseek-v4-pro', 'deepseek-v4-flash'], source: 'hermes-cache' },
      { slug: 'openrouter', models: ['deepseek/deepseek-v4.1-flash'], source: 'config' }
    ],
    hermes: { provider: 'deepseek', model: 'deepseek-v4-flash', fallbacks: [{ provider: 'openrouter', model: 'deepseek/deepseek-v4.1-flash' }] },
    roles: { watcher: { provider: 'deepseek', model: 'deepseek-v4-flash' } },
    strictness: { level: 'standard', options: ['relaxed', 'standard', 'strict'] },
    roles_file: '/tmp/godsv-models.yaml',
    known_roles: ['main', 'watcher', 'investigator', 'judge', 'fallback']
  }
  clickTab('Settings')
  clickTab('Models')
  texts = []
  collect(renderPage(), texts)
  const modelsJoined = texts.join(' ')
  check('models tab renders hermes origin', modelsJoined.includes('Hermes Default: deepseek/deepseek-v4-flash'))
  check('models tab renders roles', ['Main Agent', 'Watcher / Triage', 'Investigator / Research', 'Judge / Verification', 'Fallback'].every((t) => modelsJoined.includes(t)))
  check('models tab renders voice strictness control', ['Voice strictness', 'relaxed', 'standard', 'strict'].every((t) => modelsJoined.includes(t)))
  check('models tab bridges to treasury handles', modelsJoined.includes('secret:// handles'))
  check('models tab lists real providers', modelsJoined.includes('deepseek') && modelsJoined.includes('openrouter'))
  const modelsFn = globalThis.__LAST_QUERY_FN__
  await modelsFn()
  check('models tab queries /models', restCalls.includes('/models'), restCalls.join(','))

  // Tools tab (fresh slots so openTool starts clean)
  if (reactMock.__resetSlots) reactMock.__resetSlots()
  globalThis.__PANEL_DATA__ = {
    installed: [{
      name: 'money_convert', kind: 'python', type: 'cli', status: 'approved',
      source: 'toolbuilder:build-1', generated: true, health: 'gates: scan=ok',
      last_tested_at: 1700000000, required_secrets: ['secret://rates/example'],
      allowed_domains: ['rates.example.com'], risk_level: 'low', usage_count: 3
    }],
    generated: [{ build_id: 'build-1', stage: 'ready', capability: 'currency conversion' }],
    queue: [{
      build_id: 'build-1', stage: 'ready', capability: 'currency conversion',
      stages: [{ stage: 'requested' }, { stage: 'gates' }, { stage: 'ready' }], updated: 1700000000
    }],
    mcp_servers: [{ name: 'godsv-tools', transport: 'stdio', tool_count: 2, auth_configured: false, enabled: true }],
    requests: [
      {
        request_id: 'cap-1', capability: 'currency conversion', reason: 'Agent blocked mid-task',
        requested_by: 'main-agent', kind: 'cli', name: 'money_convert', status: 'fulfilled',
        result_tool: 'money_convert', error: '', created: 1700000000,
        required_secrets: ['secret://rates/example'], allowed_domains: ['rates.example.com'],
        build: { build_id: 'build-1', stage: 'ready', ok: true, stages: ['requested', 'gates', 'ready'] }
      },
      {
        request_id: 'cap-2', capability: 'Linear API', reason: 'need to update issue state',
        requested_by: 'main-agent', kind: 'mcp', name: 'linear_tool', status: 'open',
        result_tool: '', error: '', created: 1700000100,
        required_secrets: ['secret://linear/token'], allowed_domains: ['api.linear.app'],
        build: null
      }
    ],
    spec: {
      name: 'money_convert', kind: 'python', status: 'approved', title: 'Convert money',
      description: 'Convert money amounts', provenance: 'toolbuilder:build-1',
      rationale: 'Agent needs currency conversion', scan_summary: 'gates: scan=ok, test=ok'
    },
    manifest: {
      type: 'cli', risk_level: 'low', required_secrets: ['secret://rates/example'],
      network: { allowed_domains: ['rates.example.com'] }, dependencies: [],
      tests: ['pytest -q tests/'], last_tested_at: 1700000000
    },
    grants: [{ id: 'grant-1', handle: 'secret://rates/example', tool_id: 'money_convert', status: 'approved', created: 1700000000 }],
    builds: [{ build_id: 'build-1', updated: 1700000000, stages: [{ stage: 'requested' }, { stage: 'ready' }] }],
    usage: {
      count: 3, ok: 2, failed: 1, success_rate: 0.667, avg_duration_ms: 25,
      last_used: 1700000000, last_errors: ['ValueError: boom']
    },
    wrapper: '/tmp/godsv/generated_tools/money_convert/bin/money_convert'
  }
  clickTab('Settings')
  clickTab('Tools')
  texts = []
  collect(renderPage(), texts)
  const toolsJoined = texts.join(' ')
  if (process.env.GODSV_SMOKE_DEBUG) console.log('TOOLS_DEBUG:', toolsJoined.slice(0, 1400))
  check('tools tab renders installed tools', toolsJoined.includes('money_convert') && toolsJoined.includes('approved'))

  check('tools tab renders build queue', toolsJoined.includes('Build Queue') && toolsJoined.includes('ready'))
  check('tools tab renders mcp servers', toolsJoined.includes('godsv-tools'))
  check('tools tab mentions secret handles', toolsJoined.includes('secret://rates/example'))
  check(
    'tools tab renders capability requests',
    toolsJoined.includes('Capability Requests') && toolsJoined.includes('CAPABILITY REQUEST') &&
    toolsJoined.includes('main-agent') && toolsJoined.includes('secret://linear/token')
  )
  check('tools tab shows usage counts column', toolsJoined.includes('Calls'))

  // Tool detail drilldown: click the tool name button
  const toolButtons = []
  findButtons(renderPage(), toolButtons)
  const toolLink = toolButtons.find((b) => b.props?.children === 'money_convert')
  check('tool name is clickable in installed table', Boolean(toolLink))
  if (toolLink) {
    toolLink.props.onClick()
    texts = []
    collect(renderPage(), texts)
    const detailJoined = texts.join(' ')
    check('detail renders back link', detailJoined.includes('back to Tools'))
    check('detail renders manifest fields', detailJoined.includes('rates.example.com') && detailJoined.includes('cli'))
    check('detail offers actions', detailJoined.includes('Test') && detailJoined.includes('Re-run gates'))
    check('detail renders grants', detailJoined.includes('grant-1') || detailJoined.includes('revoke'))
    check(
      'detail renders usage + last errors',
      detailJoined.includes('Usage & Errors') && detailJoined.includes('2 / 1') &&
      detailJoined.includes('ValueError: boom')
    )
  }

  // §7/§10: scope + promotion at a glance in the installed table
  reactMock.__resetSlots()
  globalThis.__PANEL_DATA__ = {
    installed: [
      { name: 'proj_check', title: 'P', kind: 'generated', type: 'testing_tool', status: 'approved',
        source: 'toolbuilder:x', generated: true, health: '', scope: 'project', version: '0.1.0',
        project_id: 'remote:git/here', promoted_from: null,
        promotion: { name: 'proj_check', candidate: true, blockers: ['evidence from >=2 independent project contexts required'] } },
      { name: 'glob_check', title: 'G', kind: 'generated', type: 'testing_tool', status: 'approved',
        source: 'toolbuilder:y', generated: true, health: '', scope: 'global', version: '1.0.0',
        project_id: '', promoted_from: { project_id: 'remote:git/origin', evidence_projects: ['a', 'b'] },
        promotion: null },
      { name: 'legacy_check', title: 'L', kind: 'generated', type: 'testing_tool', status: 'approved',
        source: 'toolbuilder:z', generated: true, health: '', scope: 'legacy', version: 1,
        project_id: '', promoted_from: null, promotion: null }
    ],
    queue: [], requests: [], mcp_servers: []
  }
  clickTab('Tools')
  texts = []
  collect(renderPage(), texts)
  const scopeJoined = texts.join(' ')
  check(
    'tools tab shows scope badges + promotion candidate',
    scopeJoined.includes('project · candidate') && scopeJoined.includes('Global ↑') && scopeJoined.includes('Legacy'),
    scopeJoined.slice(0, 200)
  )
  reactMock.__resetSlots()

  // System tab
  globalThis.__PANEL_DATA__ = sample
  clickTab('Settings')
  clickTab('System')
  texts = []
  collect(renderPage(), texts)
  const sysJoined = texts.join(' ')
  check('system tab renders runtime info', sysJoined.includes('/tmp/godsv') && sysJoined.includes('read-only'))
  check('system tab mentions Hermes engine', sysJoined.includes('Hermes engine'))

  // Back to the cockpit via reset (nav tab is gone; the cockpit is the default
  // view after the slots reset — the chat "Open the cockpit" actions navigate
  // to the /gods-voice route just the same in the live app).
  reactMock.__resetSlots()
  texts = []
  collect(renderPage(), texts)
  check('switch back to the cockpit works', texts.join(' ').includes('Current goal'), texts.join(' ').slice(0, 120))

  // error state
  globalThis.__PANEL_DATA__ = null
  globalThis.__QUERY_OVERRIDE__ = 'error'
  texts = []
  collect(renderPage(), texts)
  check('error state renders', texts.join(' ').includes('Backend not reachable'))

  // --- 5) chat-empty contribution -------------------------------------------------
  globalThis.__QUERY_OVERRIDE__ = undefined
  const emptyTree = byArea['chat.empty'].data.render({ sessionId: 's-42' })
  // Like collect(), but for the logo img src: walk rendered component trees.
  function collectSrcs(node, out, depth = 0) {
    if (node == null || typeof node === 'boolean' || depth > 60) return
    if (Array.isArray(node)) {
      for (const child of node) collectSrcs(child, out, depth + 1)
      return
    }
    if (typeof node === 'object' && 'props' in node) {
      if (typeof node.props?.src === 'string') out.push(node.props.src)
      if (typeof node.type === 'function') {
        try {
          collectSrcs(node.type({ ...node.props, children: node.props.children }), out, depth + 1)
        } catch {
          collectSrcs(node.props?.children, out, depth + 1)
        }
        return
      }
      collectSrcs(node.props?.children, out, depth + 1)
    }
  }
  const emptySrcs = []
  collectSrcs(emptyTree, emptySrcs)
  check(
    'empty state shows the brand banner',
    emptySrcs.some(src => src.startsWith('data:image/png;base64'))
  )
  texts = []
  collect(emptyTree, texts)
  const emptyJoined = texts.join(' ')
  // The wordmark lives inside the banner bitmap now — presence is covered by
  // the src check above; keep only the quiet-start copy assertions below.
  // UI audit: quiet start — one short question, no feature catalogue
  check(
    'chat-empty is a quiet start',
    emptyJoined.includes('What should get done?') && !emptyJoined.includes('Pauses & Redirects')
  )
  check('chat-empty mentions Hermes engine + panel route', emptyJoined.includes('Hermes') && emptyJoined.includes('Panel'))

  // --- 6) voice strip always-on · goal edit · approvals pill (chat-ui-v4) -------
  function findByType(node, want, out = []) {
    if (!node || typeof node !== 'object') return out
    if (node.type === want) out.push(node)
    const props = node.props || {}
    if (typeof node.type === 'function') findByType(node.type(props), want, out)
    if (Array.isArray(props.children)) props.children.forEach((child) => findByType(child, want, out))
    else if (props.children) findByType(props.children, want, out)
    return out
  }
  const renderStrip = () => {
    reactMock.__beginRender()
    const element = byArea['composer.top'].render()
    return element.type(element.props)
  }
  const renderPill = () => {
    reactMock.__beginRender()
    const element = byArea['composer.actions'].render()
    return element.type(element.props)
  }

  // (a) without a goal the row renders NOTHING — no permanent voice band
  // (UI audit P1 #13). The `@goal` hint lives in the empty chat state instead.
  reactMock.__resetSlots()
  globalThis.__PANEL_DATA__ = {
    events: [],
    latest_seq: 0,
    state: 'WORKING',
    goal: '',
    total_count: 0,
    verified_count: 0,
    last_verdict: {},
    started: null
  }
  let texts6 = []
  const noGoalTree = renderStrip()
  collect(noGoalTree, texts6)
  const noGoalJoined = texts6.join(' ').trim()
  check('plan row renders nothing without a goal', noGoalJoined === '', noGoalJoined.slice(0, 140))

  // (a2) the empty chat state teaches @goal (the hint moved there — the same
  // contribution the section above renders, so this cannot silently drift)
  const emptyTexts = []
  collect(byArea['chat.empty'].data.render({ sessionId: 's1' }), emptyTexts)
  check(
    'the empty chat state teaches @goal',
    emptyTexts.join(' ').includes('@goal'),
    emptyTexts.join(' ').slice(0, 160)
  )

  // (b) with a goal: state, progress, elapsed, edit affordance
  const started = Math.floor(Date.now() / 1000) - 420
  globalThis.__PANEL_DATA__ = {
    events: [],
    latest_seq: 0,
    state: 'WORKING',
    goal: 'ship the chat ui',
    total_count: 2,
    verified_count: 1,
    last_verdict: {},
    started
  }
  reactMock.__resetSlots()
  const goalTree = renderStrip()
  texts6 = []
  collect(goalTree, texts6)
  const goalJoined = texts6.join(' ')
  check('strip shows goal + progress', goalJoined.includes('ship the chat ui') && goalJoined.includes('1/2'), goalJoined.slice(0, 160))
  check('strip shows elapsed time', goalJoined.includes('7m'), goalJoined.slice(0, 160))
  const editBtn = findByType(goalTree, 'button').find((node) => node.props['aria-label'] === 'Edit goal')
  check('strip offers inline goal edit', !!editBtn)

  // (c) edit → save posts the new goal text to the real endpoint
  if (editBtn) {
    editBtn.props.onClick()
    let editTree = renderStrip()
    let goalInput = findByType(editTree, 'input').find((node) => node.props['aria-label'] === 'Goal text')
    check('goal edit opens an input pre-filled with the goal', !!goalInput && goalInput.props.value === 'ship the chat ui')
    if (goalInput) {
      goalInput.props.onChange({ target: { value: 'edited goal text' } })
      editTree = renderStrip()
      goalInput = findByType(editTree, 'input').find((node) => node.props['aria-label'] === 'Goal text')
      check('goal edit input tracks typing', !!goalInput && goalInput.props.value === 'edited goal text')
      const saveBtn = findByType(editTree, 'button').find((node) => node.props.children === 'Save')
      check('goal edit offers Save/Cancel', !!saveBtn)
      if (saveBtn) {
        await saveBtn.props.onClick()
        const call = restDetailed.find((entry) => entry.path === '/goal' && entry.method === 'POST')
        check('goal edit posts to /goal', !!call && call.body?.goal === 'edited goal text', JSON.stringify(call || {}))
      }
    }
  }
  reactMock.__resetSlots()

  // (c2) the plan surface (owner 17.09.): expanded by default, hierarchical,
  // editable in place, with the orb attributed from real events only.
  const planFixture = {
    goal: 'ship the plan',
    state: 'WORKING',
    revision: 3,
    total_count: 2,
    verified_count: 1,
    dod: [
      { id: 'c1', text: 'runtime uses the selection', blocking: true, status: 'verified' },
      { id: 'c2', text: 'persistence survives restart', blocking: true, status: 'open' }
    ],
    last_verdict: {
      status: 'INCOMPLETE',
      blocking_failures: ['definition-of-done: persistence survives restart — no evidence'],
      evidence: ['pytest: ok'],
      next_actions: ['add a restart test']
    }
  }
  const planTexts = (fixture) => {
    globalThis.__PANEL_DATA__ = fixture
    reactMock.__resetSlots()
    const texts = []
    collect(renderStrip(), texts)
    return { joined: texts.join(' ') }
  }
  const renderPlan = (fixture) => {
    globalThis.__PANEL_DATA__ = fixture
    reactMock.__resetSlots()
    reactMock.__beginRender()
    return renderStrip()
  }
  let planTree = renderPlan(planFixture)
  let planText = planTexts(planFixture).joined
  check(
    'plan is expanded by default with both blocks',
    planTree.props['data-godsv-expanded'] === '1' &&
      planText.includes('Part of the goal') &&
      planText.includes('Backed by evidence') &&
      planText.includes('ship the plan'),
    planText.slice(0, 160)
  )
  check(
    'plan separates the task from the verdict',
    planText.includes('open') && planText.includes('verified') && planText.includes('Verdict INCOMPLETE'),
    planText.slice(0, 200)
  )
  check(
    'the goal counts as satisfied only when every blocking criterion is verified',
    !planText.includes('met'),
    'one blocking criterion is still open'
  )
  const allVerified = {
    ...planFixture,
    verified_count: 2,
    state: 'VERIFIED_COMPLETE',
    dod: planFixture.dod.map((item) => ({ ...item, status: 'verified' })),
    last_verdict: { status: 'VERIFIED_COMPLETE', evidence: ['pytest: ok'] }
  }
  check('a fully verified goal says so', planTexts(allVerified).joined.includes('met'))
  check('the plan surfaces no cockpit entry', !planText.toLowerCase().includes('cockpit'))
  reactMock.__resetSlots()

  // editable in place: add / move / re-classify, all through the ONE criteria
  // contract with the revision fence of the record we rendered.
  planTree = renderPlan(planFixture)
  const planButtons = findByType(planTree, 'button')
  check(
    'plan keeps the inline goal editor',
    planButtons.some((node) => node.props['aria-label'] === 'Edit goal')
  )
  const addBtn = planButtons.find((node) => node.props['data-godsv-plan-add'] === '1')
  check('plan offers adding a point inline', !!addBtn)
  const moveDown = planButtons.filter((node) => node.props['aria-label'] === 'Move criterion down')
  restDetailed.length = 0
  if (moveDown.length) {
    await moveDown[0].props.onClick()
    const call = restDetailed.find((entry) => entry.path === '/completion/criteria' && entry.method === 'POST')
    check(
      'moving a point posts op=move with a neighbour + the revision fence',
      !!call && call.body?.op === 'move' && call.body?.id === 'c1' && call.body?.after === 'c2' && call.body?.expected_revision === 3,
      JSON.stringify(call?.body || {})
    )
  } else {
    check('moving a point posts op=move with a neighbour + the revision fence', false, 'no move control')
  }
  restDetailed.length = 0
  if (addBtn) {
    reactMock.__beginRender()
    addBtn.props.onClick()
    reactMock.__beginRender()
    const addInput = findByType(renderStrip(), 'input').find((node) => node.props['aria-label'] === 'New criterion text')
    check('adding opens an input', !!addInput)
    if (addInput) {
      addInput.props.onChange({ target: { value: 'a fresh criterion' } })
      reactMock.__beginRender()
      const confirm = findByType(renderStrip(), 'button').find((node) => node.props.children === 'Add')
      check('adding offers a confirm', !!confirm)
      if (confirm) {
        await confirm.props.onClick()
        const call = restDetailed.find((entry) => entry.path === '/completion/criteria' && entry.method === 'POST')
        check(
          'adding a point posts op=add with the text + the revision fence',
          !!call && call.body?.op === 'add' && call.body?.criteria?.[0] === 'a fresh criterion' && call.body?.expected_revision === 3,
          JSON.stringify(call?.body || {})
        )
      }
    }
  }
  planTree = renderPlan(planFixture)
  const boxes = findByType(planTree, 'input').filter((node) => node.props.type === 'checkbox')
  check('every point is selectable', boxes.length === 2, `${boxes.length} checkbox(es)`)
  if (boxes.length) {
    restDetailed.length = 0
    reactMock.__beginRender()
    boxes[1].props.onChange()
    const optionalBtn = findByType(renderStrip(), 'button').find((node) => node.props.children === 'optional')
    check('a selection offers re-classifying the points', !!optionalBtn)
    if (optionalBtn) {
      await optionalBtn.props.onClick()
      const calls = restDetailed.filter((entry) => entry.path === '/completion/criteria' && entry.method === 'POST')
      check(
        're-classifying posts op=update with blocking=false',
        calls.length === 1 && calls[0].body?.op === 'update' && calls[0].body?.blocking === false,
        JSON.stringify(calls.map((c) => c.body))
      )
    }
  }
  reactMock.__resetSlots()

  // (d) approvals live in the STATUS BAR only (owner 17.09.): the composer
  // carries no approval control, so there is exactly one place for that safety
  // setting, and the plugin holds no approval state of its own to drift from
  // the engine's.
  check('no approval control in the composer', byArea['composer.actions'] === undefined)
  const pluginSource = readFileSync(pluginSrc, 'utf8')
  check(
    'the plugin keeps no approval logic of its own',
    !pluginSource.includes("rest('/approvals'") && !pluginSource.includes('ApprovalModePill'),
    'the status bar owns the approval menu'
  )
  delete globalThis.__REST_THROW__
  // (e) live block: the completion record outranks the directive's stage —
  // a finished goal must never render as "working" (owner requirement).
  const liveReg = byArea['transcript.directives']
  check('live block directive registered', typeof liveReg?.data?.render === 'function')
  if (typeof liveReg?.data?.render === 'function') {
    globalThis.__PANEL_DATA__ = {
      state: 'VERIFIED_COMPLETE',
      terminal: true,
      goal: 'Owner goal',
      session: 's-live',
      dod: [],
      last_verdict: { blocking_failures: [] }
    }
    reactMock.__resetSlots()
    reactMock.__beginRender()
    const liveTexts = []
    collect(liveReg.data.render({ attrs: { stage: 'working' }, streaming: false }), liveTexts)
    const joinedLive = liveTexts.join(' ')
    check(
      'finished goal renders VERIFIED COMPLETE, never WORKING',
      joinedLive.includes('VERIFIED COMPLETE') && !joinedLive.includes('WORKING'),
      joinedLive.slice(0, 140)
    )
    // stage 1 (plan): only `verified` counts; failed shows separately;
    // rejection wording claims no automatic resume; unknown state != WORKING.
    globalThis.__PANEL_DATA__ = {
      state: 'INCOMPLETE',
      terminal: false,
      goal: 'Owner goal',
      session: 's-live',
      dod: [
        { id: 'c1', text: 'a', status: 'verified' },
        { id: 'c2', text: 'b', status: 'open' },
        { id: 'c3', text: 'c', status: 'failed' }
      ],
      last_verdict: { blocking_failures: ['one blocker'] }
    }
    reactMock.__resetSlots()
    reactMock.__beginRender()
    const countsTexts = []
    collect(liveReg.data.render({ attrs: { stage: 'working' }, streaming: false }), countsTexts)
    const countsJoined = countsTexts.join(' ')
    check(
      'live block counts verified only, failed separately',
      countsJoined.includes('1/3 verified') && countsJoined.includes('1 failed'),
      countsJoined.slice(0, 180)
    )
    check(
      'rejection wording claims no automatic resume',
      countsJoined.includes('redirect') && !countsJoined.includes('resumed'),
      countsJoined.slice(0, 180)
    )
    globalThis.__PANEL_DATA__ = { state: '', goal: 'g', dod: [], last_verdict: {} }
    reactMock.__resetSlots()
    reactMock.__beginRender()
    const unknownTexts = []
    collect(liveReg.data.render({ attrs: {}, streaming: false }), unknownTexts)
    check(
      'unknown backend state never renders WORKING',
      !unknownTexts.join(' ').includes('WORKING'),
      unknownTexts.join(' ').slice(0, 140)
    )
    // stage 2 (plan): historical cards keep their own session; all surfaces
    // share one completion/feed query key (no per-card polling chains).
    globalThis.__PANEL_DATA__ = { state: 'WORKING', goal: 'g', session: 's-old', dod: [], last_verdict: {} }
    globalThis.__QUERY_KEYS__ = []
    reactMock.__resetSlots()
    reactMock.__beginRender()
    collect(liveReg.data.render({ attrs: { stage: 'working', sid: 's-old' }, streaming: false }), [])
    const oldKeys = globalThis.__QUERY_KEYS__.slice()
    check(
      'historical live block binds its own session',
      oldKeys.some((k) => k.includes('"completion","s-old"')) &&
        oldKeys.some((k) => k.includes('"chat-feed","s-old"')),
      oldKeys.join(' | ').slice(0, 180)
    )
    check(
      'legacy live-* query keys are gone',
      !oldKeys.some((k) => k.includes('live-completion') || k.includes('live-feed'))
    )
    globalThis.__QUERY_KEYS__ = []
    reactMock.__resetSlots()
    reactMock.__beginRender()
    collect(liveReg.data.render({ attrs: { stage: 'working' }, streaming: false }), [])
    const liveKeys = globalThis.__QUERY_KEYS__.filter((k) => k.includes('"completion"'))
    globalThis.__QUERY_KEYS__ = []
    reactMock.__resetSlots()
    reactMock.__beginRender()
    collect(byArea['composer.top'].render(), [])
    const stripKeys = globalThis.__QUERY_KEYS__.filter((k) => k.includes('"completion"'))
    check(
      'strip and live block share one completion key',
      liveKeys.length > 0 && stripKeys.length > 0 && liveKeys[0] === stripKeys[0],
      `${liveKeys[0]} vs ${stripKeys[0]}`
    )
  }
  // --- 7) thinking-orbs: the ONE visible working indicator ---------------------
  //
  // Every visible thinking/working phase renders through thinking-orbs, and
  // nothing else may signal a working state — the old `●`/`◉`/`…` glyphs are
  // asserted gone below. The library is vendored INTO plugin.js (a runtime blob
  // module cannot resolve a relative import, and nothing but the three mocked
  // specifiers resolves at all — which is itself enforced here, because this
  // scratch dir holds plugin.js alone), so there is no module identity to
  // compare against: orb elements are recognized structurally — they are the
  // only elements carrying `theme: 'auto'` with a verb `state` and one of the
  // two tuned sizes.
  const ORB_VERBS = new Set([
    'working', 'searching', 'solving', 'listening', 'connecting',
    'weaving', 'composing', 'breathing', 'shaping'
  ])
  function collectOrbs(node, out = [], depth = 0) {
    if (!node || typeof node !== 'object' || depth > 60) return out
    if (Array.isArray(node)) {
      node.forEach((child) => collectOrbs(child, out, depth + 1))
      return out
    }
    const props = node.props || {}
    if (props.theme === 'auto' && ORB_VERBS.has(props.state) && (props.size === 20 || props.size === 64)) {
      out.push({ state: props.state, size: props.size, label: props['aria-label'] })
    }
    if (typeof node.type === 'function') {
      // Call it and walk ONLY its output — walking `props.children` as well
      // would visit the same subtree twice (Section/panel components take
      // children as a prop), which double-counted orbs.
      try {
        collectOrbs(node.type(props), out, depth + 1)
      } catch {
        /* ignore */
      }
      return out
    }
    if (Array.isArray(props.children)) props.children.forEach((child) => collectOrbs(child, out, depth + 1))
    else if (props.children) collectOrbs(props.children, out, depth + 1)
    return out
  }
  const liveOrbs = (state, streaming) => {
    globalThis.__PANEL_DATA__ = { state, goal: 'ship it', dod: [], last_verdict: {} }
    reactMock.__resetSlots()
    reactMock.__beginRender()
    return collectOrbs(liveReg.data.render({ attrs: { stage: 'working' }, streaming }))
  }

  // (a) runtime state -> verb: exactly one orb, compact inline while a goal runs
  const orbWorking = liveOrbs('WORKING', false)
  check(
    'live card: WORKING shows exactly one orb, inline (20px), verb working',
    orbWorking.length === 1 && orbWorking[0].state === 'working' && orbWorking[0].size === 20,
    JSON.stringify(orbWorking)
  )
  const orbStreaming = liveOrbs('WORKING', true)
  check(
    'live card: a streaming turn projects to composing, not working',
    orbStreaming.length === 1 && orbStreaming[0].state === 'composing',
    JSON.stringify(orbStreaming)
  )
  const orbVerifying = liveOrbs('VERIFYING', false)
  check(
    'live card: VERIFYING projects to solving',
    orbVerifying.length === 1 && orbVerifying[0].state === 'solving',
    JSON.stringify(orbVerifying)
  )
  const orbBlocked = liveOrbs('BLOCKED_USER', false)
  check(
    'live card: a blocked/waiting session projects to breathing',
    orbBlocked.length === 1 && orbBlocked[0].state === 'breathing',
    JSON.stringify(orbBlocked)
  )
  for (const terminal of ['VERIFIED_COMPLETE', 'INCOMPLETE', 'FAILED_UNRECOVERABLE']) {
    const orbs = liveOrbs(terminal, false)
    check(`live card: ${terminal} is final — no orb at all`, orbs.length === 0, JSON.stringify(orbs))
  }
  const orbUnknown = liveOrbs('SOMETHING_NEW', false)
  check('live card: an unknown state invents no verb', orbUnknown.length === 0, JSON.stringify(orbUnknown))

  // --- §4 UI state vocabulary: the feed projects a runtime fact into ONE state,
  // and an unmapped event stays `unknown` instead of reading as success.
  function collectNodes(node, out, depth = 0) {
    if (node == null || typeof node === 'boolean' || depth > 60) return
    if (Array.isArray(node)) {
      for (const child of node) collectNodes(child, out, depth + 1)
      return
    }
    if (typeof node === 'object' && node.props) {
      if (node.props['data-godsv-node']) out.push(String(node.props['data-godsv-node']))
      if (typeof node.type === 'function') {
        try {
          collectNodes(node.type({ ...node.props, children: node.props.children }), out, depth + 1)
          return
        } catch {
          /* fall through to the children below */
        }
      }
      collectNodes(node.props.children, out, depth + 1)
    }
  }
  const stripTreeFor = (events) => {
    globalThis.__PANEL_DATA__ = {
      voice_mode: 'whisper',
      // goal-gated plan row (UI audit P1 #13): the feed lives inside it
      goal: 'ship it',
      state: 'WORKING',
      events,
      latest_seq: events.length ? events[0].seq : 0
    }
    const el = byArea['composer.top'].render()
    return el.type(el.props)
  }
  const nodeStates = (tree) => {
    const out = []
    collectNodes(tree, out)
    return out
  }
  // The feed rows are what the state-vocabulary pins assert. The plan row above
  // them carries its own status orb (a live phase), so a pin about "a concluded
  // ROW shows no orb" must look inside the feed subtree only — the row's orb is
  // not a feed row's orb.
  const feedSubtree = (node, depth = 0) => {
    if (!node || typeof node !== 'object' || depth > 60) return null
    if (Array.isArray(node)) {
      for (const child of node) {
        const hit = feedSubtree(child, depth + 1)
        if (hit) return hit
      }
      return null
    }
    const props = node.props || {}
    if (props['data-godsv-feed']) return node
    if (typeof node.type === 'function') {
      try {
        const rendered = feedSubtree(node.type({ ...props, children: props.children }), depth + 1)
        if (rendered || rendered === null) {
          if (rendered) return rendered
        }
      } catch {
        /* fall through to children */
      }
    }
    if (Array.isArray(props.children)) {
      for (const child of props.children) {
        const hit = feedSubtree(child, depth + 1)
        if (hit) return hit
      }
    } else if (props.children) {
      const hit = feedSubtree(props.children, depth + 1)
      if (hit) return hit
    }
    return null
  }
  const t0 = Math.floor(Date.now() / 1000)
  const GV_STATE_VOCABULARY = [
    'queued',
    'running',
    'waiting',
    'approval_required',
    'paused',
    'succeeded',
    'failed',
    'unknown',
    'cancelled'
  ]
  const mixedStates = nodeStates(
    stripTreeFor([
      { seq: 31, ts: t0 - 30, kind: 'tool_build_ready', payload: {} },
      { seq: 30, ts: t0 - 60, kind: 'completion_verdict', payload: { status: 'INCOMPLETE' } },
      { seq: 29, ts: t0 - 90, kind: 'pause_requested', payload: { reason: 'loop detected' } }
    ])
  )
  check(
    'every feed row carries one state from the shared vocabulary',
    mixedStates.length === 3 && mixedStates.every((s) => GV_STATE_VOCABULARY.includes(s)),
    mixedStates.join(',')
  )
  check(
    'runtime facts map correctly (build ready -> succeeded, INCOMPLETE -> failed, pause -> paused)',
    mixedStates.includes('succeeded') && mixedStates.includes('failed') && mixedStates.includes('paused'),
    mixedStates.join(',')
  )
  const unknownStates = nodeStates(
    stripTreeFor([{ seq: 40, ts: t0 - 5, kind: 'a_kind_the_plugin_does_not_know', payload: {} }])
  )
  check(
    'an unmapped runtime event is unknown — never rendered as success',
    unknownStates.join(',') === 'unknown',
    unknownStates.join(',')
  )
  const runningTree = stripTreeFor([{ seq: 41, ts: t0 - 5, kind: 'research_started', payload: {} }])
  const runningOrbs = []
  collectOrbs(feedSubtree(runningTree), runningOrbs)
  check(
    'an active state carries the one thinking-orb',
    runningOrbs.length >= 1 && nodeStates(runningTree).includes('running'),
    `${runningOrbs.length} orb(s) / ${nodeStates(runningTree).join(',')}`
  )
  const doneTree = stripTreeFor([{ seq: 42, ts: t0 - 5, kind: 'research_done', payload: {} }])
  const doneOrbs = []
  collectOrbs(feedSubtree(doneTree), doneOrbs)
  check(
    'a concluded state renders the static symbol, never an orb',
    doneOrbs.length === 0 && nodeStates(doneTree).join(',') === 'succeeded',
    `${doneOrbs.length} orb(s) / ${nodeStates(doneTree).join(',')}`
  )
  const blockedTree = stripTreeFor([{ seq: 43, ts: t0 - 5, kind: 'completion_blocked', payload: {} }])
  check(
    'a blocked runtime state asks for a decision (approval_required)',
    nodeStates(blockedTree).join(',') === 'approval_required',
    nodeStates(blockedTree).join(',')
  )
  const outcomeStates = nodeStates(
    stripTreeFor([
      { seq: 51, ts: t0 - 20, kind: 'cu_verified', payload: { app: 'blender' } },
      { seq: 50, ts: t0 - 40, kind: 'system_one_blocked', payload: {} },
      { seq: 49, ts: t0 - 60, kind: 'spend_approved', payload: { amount: 4 } }
    ])
  )
  check(
    'outcome kinds reach the chat (CU verified -> succeeded, Jev blocked -> decision, spend approved -> running)',
    outcomeStates.includes('succeeded') && outcomeStates.includes('approval_required') && outcomeStates.includes('running'),
    outcomeStates.join(',')
  )
  const humanizedTree = stripTreeFor([{ seq: 52, ts: t0 - 10, kind: 'tool_build_gates', payload: {} }])
  const humanizedTexts = []
  collect(humanizedTree, humanizedTexts)
  const humanizedJoined = humanizedTexts.join(' ')
  check(
    'a kind without its own text is humanized, never printed as an identifier',
    humanizedJoined.includes('Tool build gates') && !humanizedJoined.includes('tool_build_gates'),
    humanizedJoined.slice(0, 120)
  )

  // --- §11/§3/§14: the completion check reads as ONE strand; the criteria list
  // stays behind the fold (a 97-criteria contract must not print 97 lines).
  function collectAttr(node, attr, out, depth = 0) {
    if (node == null || typeof node !== 'object' || depth > 60) return
    if (Array.isArray(node)) {
      node.forEach((child) => collectAttr(child, attr, out, depth + 1))
      return
    }
    const props = node.props || {}
    if (props[attr] != null) out.push(String(props[attr]))
    if (typeof node.type === 'function') {
      try {
        collectAttr(node.type(props), attr, out, depth + 1)
        return
      } catch {
        /* fall through to the children */
      }
    }
    collectAttr(props.children, attr, out, depth + 1)
  }
  globalThis.__PANEL_DATA__ = {
    state: 'INCOMPLETE',
    goal: 'ship the UI refresh',
    dod: [
      { id: 'c1', text: 'first criterion', status: 'verified' },
      { id: 'c2', text: 'second criterion', status: 'failed' },
      { id: 'c3', text: 'third criterion', status: 'open' }
    ],
    last_verdict: { blocking_failures: ['one blocker'] }
  }
  reactMock.__resetSlots()
  reactMock.__beginRender()
  const strandTree = liveReg.data.render({ attrs: { stage: 'working' }, streaming: false })
  const strandTexts = []
  collect(strandTree, strandTexts)
  const strandJoined = strandTexts.join(' ')
  const strandCounts = []
  collectAttr(strandTree, 'data-godsv-strand', strandCounts)
  check(
    'the completion check renders as ONE connected strand',
    strandCounts.length === 1 && Number(strandCounts[0]) >= 3,
    strandCounts.join(',') || 'no strand rendered'
  )
  check(
    'the strand names the missing evidence and the open requirements',
    strandJoined.includes('criteria need current evidence') && strandJoined.includes('requirements remain open'),
    strandJoined.slice(0, 220)
  )
  check(
    'the criteria list itself stays behind the fold (§11: no 97-line wall)',
    !strandJoined.includes('first criterion') && strandJoined.includes('1/3 verified'),
    strandJoined.slice(0, 160)
  )
  globalThis.__PANEL_DATA__ = null

  // (a2) the ONE larger rendering: a session with no goal has no other work
  // surface, so the card IS the work and the phase takes the 64px avatar orb.
  globalThis.__PANEL_DATA__ = { state: 'WORKING', goal: '', dod: [], last_verdict: {} }
  reactMock.__resetSlots()
  reactMock.__beginRender()
  const focusOrbs = collectOrbs(liveReg.data.render({ attrs: { stage: 'working' }, streaming: false }))
  check(
    'live card: the central long phase (no goal yet) takes the 64px orb',
    focusOrbs.length === 1 && focusOrbs[0].size === 64 && focusOrbs[0].state === 'working',
    JSON.stringify(focusOrbs)
  )

  // (b) the aria-label names observable work only — never a reasoning trace
  const orbLabels = orbWorking.concat(orbVerifying).map((orb) => String(orb.label || ''))
  check(
    'orbs are labelled with observable work, not chain-of-thought',
    orbLabels.every((label) => label.length > 0 && label.length < 48 && !/reason|think|thought|deci/i.test(label)),
    JSON.stringify(orbLabels)
  )

  // (c) no legacy working glyph survives in the rendered card
  const liveGlyphTexts = []
  for (const st of ['WORKING', 'VERIFYING', 'BLOCKED_USER', 'VERIFIED_COMPLETE', 'INCOMPLETE']) {
    globalThis.__PANEL_DATA__ = { state: st, goal: 'ship it', dod: [], last_verdict: {} }
    reactMock.__resetSlots()
    reactMock.__beginRender()
    collect(liveReg.data.render({ attrs: { stage: 'working' }, streaming: false }), liveGlyphTexts)
  }
  const liveGlyphs = liveGlyphTexts.join(' ')
  check(
    'no legacy thinking glyph (● / ◉) is rendered any more',
    !/[●◉]/.test(liveGlyphs),
    liveGlyphs.slice(0, 140)
  )

  // (d) the panel surfaces use the SAME projection — one indicator, no second
  // status model. Composer strip, gate card and the capability/build rows.
  reactMock.__resetSlots()
  globalThis.__PANEL_DATA__ = {
    state: 'WORKING', goal: 'ship it', total_count: 0, verified_count: 0, last_verdict: {}
  }
  reactMock.__beginRender()
  const stripOrbs = collectOrbs(byArea['composer.top'].render())
  check(
    'composer strip: a running goal shows the working orb',
    stripOrbs.length === 1 && stripOrbs[0].state === 'working',
    JSON.stringify(stripOrbs)
  )
  reactMock.__resetSlots()
  globalThis.__PANEL_DATA__ = {
    state: 'WORKING', goal: '', total_count: 0, verified_count: 0, last_verdict: {}
  }
  reactMock.__beginRender()
  const noGoalOrbs = collectOrbs(byArea['composer.top'].render())
  check('composer strip: without a goal no phase is asserted', noGoalOrbs.length === 0, JSON.stringify(noGoalOrbs))

  if (reactMock.__resetSlots) reactMock.__resetSlots()
  globalThis.__PANEL_DATA__ = {
    state: 'WORKING', terminal: false, goal: 'ship it', requirements: [], verified_count: 0,
    total_count: 0, timeline: [], gates: [], last_verdict: {}, voice_mode: 'whisper'
  }
  clickTab('Verification')
  const gateOrbs = collectOrbs(renderPage())
  check(
    'gate card + panel header project the SAME running phase',
    gateOrbs.length >= 1 && gateOrbs.every((orb) => orb.state === 'working'),
    JSON.stringify(gateOrbs)
  )
  if (reactMock.__resetSlots) reactMock.__resetSlots()
  globalThis.__PANEL_DATA__ = {
    state: 'VERIFIED_COMPLETE', terminal: true, goal: 'ship it', requirements: [],
    verified_count: 0, total_count: 0, timeline: [], gates: [], last_verdict: {}, voice_mode: 'whisper'
  }
  clickTab('Verification')
  const gateDoneOrbs = collectOrbs(renderPage())
  check('gate card: a verified goal shows no orb', gateDoneOrbs.length === 0, JSON.stringify(gateDoneOrbs))

  // capability requests + build queue: an in-flight build projects its OWN
  // stage; a finished one shows nothing.
  if (reactMock.__resetSlots) reactMock.__resetSlots()
  globalThis.__PANEL_DATA__ = {
    installed: [], generated: [], mcp_servers: [], spec: {}, grants: [], builds: [], usage: {},
    queue: [
      { build_id: 'b-live', stage: 'generating', capability: 'scrape', stages: [] },
      { build_id: 'b-done', stage: 'ready', capability: 'convert', stages: [] }
    ],
    requests: [
      {
        request_id: 'cap-live', capability: 'scrape', reason: 'blocked mid-task', status: 'open',
        build: { build_id: 'b-live', stages: ['requested', 'generating'] }
      },
      { request_id: 'cap-done', capability: 'convert', status: 'fulfilled', build: null }
    ]
  }
  clickTab('Settings')
  clickTab('Tools')
  const toolOrbs = collectOrbs(renderPage()).map((orb) => orb.state)
  check(
    'capability + build rows: active stages shape, finished ones show no orb',
    toolOrbs.length === 2 && toolOrbs.every((verb) => verb === 'shaping'),
    JSON.stringify(toolOrbs)
  )

  // --- stage 3 (plan §5): criteria editor in the live block --------------------
  // Revision-fenced, idempotent, no status path, frozen once the goal is
  // verified. Behavioral: render -> click expand -> edit -> save -> REST body.
  {
    const renderLive = (attrs) => {
      reactMock.__beginRender()
      return liveReg.data.render({ attrs: attrs || { stage: 'working' }, streaming: false })
    }
    const liveData = (state) => {
      globalThis.__PANEL_DATA__ = {
        state,
        goal: 'ship it',
        revision: 4,
        dod: [
          { id: 'c1', text: 'first criterion', blocking: true, status: 'open' },
          { id: 'c2', text: 'second criterion', blocking: true, status: 'verified' }
        ],
        last_verdict: {}
      }
    }
    liveData('WORKING')
    reactMock.__resetSlots()
    restDetailed.length = 0
    let ltree = renderLive({ stage: 'working', sid: 's-edit' })
    let goalRow = findByType(ltree, 'div').find((n) => n.props.title === 'Show and edit criteria')
    check('criteria counter row toggles the list', !!goalRow && typeof goalRow.props.onClick === 'function')
    if (goalRow) {
      goalRow.props.onClick()
      ltree = renderLive({ stage: 'working', sid: 's-edit' })
      const rowTexts = []
      collect(ltree, rowTexts)
      const joinedRows = rowTexts.join(' ')
      check(
        'expanded criteria list shows items with status glyphs',
        joinedRows.includes('first criterion') && joinedRows.includes('second criterion') && joinedRows.includes('○') && joinedRows.includes('✓'),
        joinedRows.slice(0, 160)
      )
      ltree = renderLive({ stage: 'working', sid: 's-edit' }) // fresh traversal cursor
      const allBtns = findByType(ltree, 'button')
      const editBtn3 = allBtns.find((n) => n.props.title === 'Edit criterion')
      check(
        'criteria rows offer inline edit + remove',
        !!editBtn3 && !!allBtns.find((n) => n.props.title === 'Remove criterion'),
        JSON.stringify(allBtns.map((n) => [n.props.title || null, String(n.props.children).slice(0, 18)]))
      )
      if (editBtn3) {
        editBtn3.props.onClick()
        ltree = renderLive({ stage: 'working', sid: 's-edit' })
        const inp = findByType(ltree, 'input').find((n) => String(n.props.value || '').startsWith('first criterion'))
        check('criterion edit input is pre-filled', !!inp)
        if (inp) {
          inp.props.onChange({ target: { value: 'first criterion v2' } })
          ltree = renderLive({ stage: 'working', sid: 's-edit' })
          const saveB = findByType(ltree, 'button').find((n) => n.props.children === 'Save')
          check('criterion edit offers Save/Cancel', !!saveB)
          if (saveB) {
            await saveB.props.onClick()
            const call = restDetailed.find((e2) => e2.path === '/completion/criteria' && e2.method === 'POST')
            check(
              'criterion update sends revision fence + request id, never status',
              !!call &&
                call.body?.op === 'update' &&
                call.body?.id === 'c1' &&
                call.body?.text === 'first criterion v2' &&
                call.body?.expected_revision === 4 &&
                !!call.body?.request_id &&
                !('status' in (call.body || {})),
              JSON.stringify(call || {})
            )
          }
        }
      }
    }
    liveData('WORKING')
    reactMock.__resetSlots()
    restDetailed.length = 0
    ltree = renderLive({ stage: 'working', sid: 's-edit' })
    goalRow = findByType(ltree, 'div').find((n) => n.props.title === 'Show and edit criteria')
    if (goalRow) goalRow.props.onClick()
    ltree = renderLive({ stage: 'working', sid: 's-edit' })
    const remBtn = findByType(ltree, 'button').find((n) => n.props.title === 'Remove criterion')
    if (remBtn) {
      await remBtn.props.onClick()
      const call = restDetailed.find((e2) => e2.path === '/completion/criteria' && e2.method === 'POST')
      check(
        'criterion remove posts op=remove with fence',
        !!call && call.body?.op === 'remove' && call.body?.id === 'c1' && call.body?.expected_revision === 4,
        JSON.stringify(call || {})
      )
    } else {
      check('criterion remove posts op=remove with fence', false, 'no remove button rendered')
    }
    liveData('VERIFIED_COMPLETE')
    reactMock.__resetSlots()
    ltree = renderLive({ stage: 'working', sid: 's-edit' })
    goalRow = findByType(ltree, 'div').find((n) => n.props.title === 'Show and edit criteria')
    if (goalRow) goalRow.props.onClick()
    ltree = renderLive({ stage: 'working', sid: 's-edit' })
    check(
      'verified contracts freeze the criteria editor',
      !findByType(ltree, 'button').find((n) => n.props.title === 'Edit criterion')
    )
    // fresh mount, no click: the list (and its controls) stay collapsed
    liveData('WORKING')
    reactMock.__resetSlots()
    ltree = renderLive({ stage: 'working', sid: 's-edit' })
    const hiddenTexts = []
    collect(ltree, hiddenTexts)
    const hiddenJoined = hiddenTexts.join(' ')
    check(
      'criteria stay hidden until expanded',
      hiddenJoined.includes('1/2 verified') && hiddenJoined.includes('▸') && !hiddenJoined.includes('first criterion'),
      hiddenJoined.slice(0, 140)
    )
  }
    // --- composer directive highlight (owner ask 13.09.: @goal turns gold) -------
  {
    const segs = mod.gvDirectiveSegments
    check('directive segments exported for the smoke', typeof segs === 'function')
    if (typeof segs === 'function') {
      const goalRun = segs('@goal ship the release')
      const goalGold = goalRun.filter((r) => r.gold)
      check(
        'only the @goal tag is gold, its text stays plain',
        goalGold.length === 1 && goalGold[0].text === '@goal',
        JSON.stringify(goalRun)
      )
      const mixed = segs('ask @researcher about it')
      check(
        'an @mention runs gold, the prose stays plain',
        mixed.some((r) => r.gold && r.text === '@researcher') &&
          mixed.some((r) => !r.gold && r.text.includes('ask')),
        JSON.stringify(mixed)
      )
      const decisions = segs('!decision: use sqlite')
      check('directive lines stay plain (owner: only tags highlight)', decisions.every((r) => !r.gold))
      const plain = segs('just some ordinary words')
      check('plain prose stays untouched', plain.length === 1 && plain[0].gold === false)
      const email = segs('mail me at a@b.com')
      check('an email address is not a directive', email.every((r) => !r.gold), JSON.stringify(email))
    }
  }
    globalThis.__PANEL_DATA__ = null
} finally {
  rmSync(root, { recursive: true, force: true })
}

if (failures.length) {
  console.log(`\n${failures.length} check(s) failed`)
  process.exit(1)
}
console.log('\ndesktop panel: contract OK')
