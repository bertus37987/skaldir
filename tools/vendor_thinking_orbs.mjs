#!/usr/bin/env node
/**
 * Vendor `thinking-orbs` into the desktop plugin (one file, no build step).
 *
 * WHY INLINE. `plugin.js` is loaded two ways: the app's runtime plugin loader
 * (`apps/desktop/src/contrib/runtime-loader.ts`) rewrites ONLY the mapped bare
 * specifiers (`@hermes/plugin-sdk`, `react`, `react/jsx-runtime`) to live shim
 * blobs and then `import()`s the source as a blob URL; the GV app build
 * additionally bundles it. A blob URL is not a hierarchical base, so a
 * RELATIVE import cannot resolve — which is why this plugin is a single
 * self-contained file. npm resolution is not available in the renderer either.
 * A sibling vendor module would therefore break the runtime door, so the
 * library's two ESM files are inlined between markers instead.
 *
 * Both dist files declare a top-level `function _`; a plain concatenation
 * collides. Each therefore gets its own IIFE scope, and its imports become
 * explicit parameters. Nothing in the block is hand-written: it is derived
 * from the installed package, and the react identifiers it needs must already
 * exist in plugin.js's own (bare) react imports, which are checked here.
 *
 * Usage:
 *   npm install && node tools/vendor_thinking_orbs.mjs
 *   node tools/vendor_thinking_orbs.mjs --check   # fail when the copy is stale
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const repoRoot = join(here, '..')
const PLUGIN = join(repoRoot, 'integrations', 'hermes', 'godsv_plugin', 'desktop', 'plugin.js')
const PACKAGE = join(repoRoot, 'node_modules', 'thinking-orbs')

const BEGIN = '// >>> BEGIN thinking-orbs (vendored — regenerate: node tools/vendor_thinking_orbs.mjs)'
const END = '// <<< END thinking-orbs'
const ANCHOR = "\nconst ID = 'godsv'"
const IMPORT_RE = /^import\s*\{[^}]*\}\s*from\s*(['"])[^'"]+\1\s*;?$/gm

/** `export { A as B }` → the body without it plus a `{ B: A }` literal. */
function splitExports(source) {
  const trimmed = source.trimEnd()
  const match = /export\s*\{([^}]*)\}\s*;?$/.exec(trimmed)
  if (!match) throw new Error('no trailing export list found')
  const literal = match[1]
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const [local, exported] = part.split(/\s+as\s+/).map((chunk) => chunk.trim())
      return `${exported || local}: ${local}`
    })
    .join(', ')
  return { body: trimmed.slice(0, match.index).trimEnd(), literal }
}

/** `import { a as b } from 'x'` → `{ from: 'x', names: [{ orig, local }] }`. */
function readImport(statement) {
  const match = /^import\s*\{([^}]*)\}\s*from\s*(['"])([^'"]+)\2\s*;?$/.exec(statement.trim())
  if (!match) return null
  return {
    from: match[3],
    names: match[1]
      .split(',')
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => {
        const [orig, local] = part.split(/\s+as\s+/).map((chunk) => chunk.trim())
        return { orig, local: local || orig }
      })
  }
}

const version = JSON.parse(readFileSync(join(PACKAGE, 'package.json'), 'utf8')).version
const engine = splitExports(readFileSync(join(PACKAGE, 'dist', 'engine.es.js'), 'utf8'))
const index = splitExports(readFileSync(join(PACKAGE, 'dist', 'index.es.js'), 'utf8'))

const engineExports = new Set(engine.literal.split(',').map((pair) => pair.split(':')[0].trim()))
const params = []
const args = []
const reactNames = new Set()
let indexBody = index.body
for (const statement of index.body.match(IMPORT_RE) || []) {
  const spec = readImport(statement)
  if (!spec) continue
  indexBody = indexBody.replace(statement, '').replace(/\n{3,}/g, '\n\n').trim()
  for (const name of spec.names) {
    params.push(name.local)
    if (spec.from === './engine.es.js') {
      if (!engineExports.has(name.orig)) {
        throw new Error(`index.es.js imports \`${name.orig}\`, which engine.es.js does not export`)
      }
      args.push(`${name.local}: __gvOrbsEngine.${name.orig}`)
    } else {
      // The react specifier stays BARE on purpose: the runtime loader rewrites
      // it to the live shim blob, so ONE react instance is shared with the plugin.
      reactNames.add(name.orig)
      args.push(`${name.local}: ${name.orig}`)
    }
  }
}

const pluginSource = readFileSync(PLUGIN, 'utf8')
for (const name of reactNames) {
  const imported = new RegExp(`import\\s*\\{[^}]*\\b${name}\\b[^}]*\\}\\s*from`)
  if (!imported.test(pluginSource)) {
    throw new Error(
      `plugin.js must import \`${name}\` from \`react\`/\`react/jsx-runtime\` before vendoring — ` +
        'add it to the react import at the top of the file.'
    )
  }
}

const block = [
  BEGIN,
  `// thinking-orbs v${version} (MIT (c) Jakub Antalik) — inlined, not imported: the`,
  '// runtime plugin loader evaluates plugin.js as a blob module and a blob URL is not',
  '// a hierarchical base, so a RELATIVE import cannot resolve (the reason this plugin',
  '// is one file); npm is not reachable from the renderer either. Both dist files',
  '// declare a top-level `function _`, so each keeps its own IIFE scope and only the',
  '// parameters the components need cross the boundary. Nothing here is hand-edited —',
  '// regenerate with `npm install && node tools/vendor_thinking_orbs.mjs`.',
  'const __gvOrbsEngine = (() => {',
  engine.body,
  `  return { ${engine.literal} }`,
  '})()',
  '',
  `const __gvThinkingOrbs = (({ ${params.join(', ')} }) => {`,
  indexBody,
  `  return { ${index.literal} }`,
  '})({',
  `  ${args.join(', ')}`,
  '})',
  '',
  'const ThinkingOrb = __gvThinkingOrbs.ThinkingOrb',
  END
].join('\n')

const beginAt = pluginSource.indexOf(BEGIN)
const endAt = pluginSource.indexOf(END)
let next
if (beginAt !== -1 && endAt > beginAt) {
  next = pluginSource.slice(0, beginAt) + block + pluginSource.slice(endAt + END.length)
} else if (beginAt === -1 && endAt === -1) {
  const anchorAt = pluginSource.indexOf(ANCHOR)
  if (anchorAt === -1) throw new Error(`insertion anchor not found: ${JSON.stringify(ANCHOR)}`)
  next = pluginSource.slice(0, anchorAt) + '\n\n' + block + '\n' + pluginSource.slice(anchorAt)
} else {
  throw new Error('exactly one vendor marker is present — plugin.js is inconsistent')
}

if (process.argv.includes('--check')) {
  if (next !== pluginSource) {
    console.error(
      'thinking-orbs in plugin.js is stale — run: npm install && node tools/vendor_thinking_orbs.mjs'
    )
    process.exit(1)
  }
  console.log(`thinking-orbs v${version}: vendored copy is current`)
} else if (next === pluginSource) {
  console.log(`thinking-orbs v${version}: already current`)
} else {
  writeFileSync(PLUGIN, next)
  console.log(`thinking-orbs v${version}: plugin.js updated`)
}
