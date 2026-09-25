# Skaldir CLI (`godsvoice`) — generated command reference

<!-- inventory-sha256: cf03ab0ea03dbea057ad1b678216e8b4b54f68dff50cc106e30f80a2fca1fa81 -->

> **Generated file — do not edit by hand.** Every command, argument, JSON schema,
> exit code and limitation sentence below is copied from the CLI's own contract dump.

| Fact | Value |
|---|---|
| Generator | `tools/gv_cli_reference.py` |
| Regenerate | `.venv/bin/python tools/gv_cli_reference.py --out docs/CLI-REFERENCE.md` |
| Staleness gate | `.venv/bin/python tools/gv_cli_reference.py --check` |
| Inventory fingerprint | `sha256:cf03ab0ea03dbea057ad1b678216e8b4b54f68dff50cc106e30f80a2fca1fa81` |
| Source command | `/home/willy/projects/gods-voice/.venv/bin/godsvoice contract dump --json` (exit 0) |
| `godsvoice_version` | 1.5.0 |
| contract `schema_version` | 1.0 |
| Generated at (UTC) | 2026-09-21T17:38:07Z |
| Inventory | 65 root · 271 leaf commands · 325 parser nodes |
| Unregistered parser nodes | 0 |
| Cross-check `cli_contract.COMMANDS` | 271 entries — matches the dump |
| Exit-code vocabulary | read from `godsv.cli_contract.EXIT_NAMES` / `.EXIT_ERROR_CODES` |

The dump is the GUI's source of truth as well: the desktop panel renders forms and tables from the same records, so this file and the GUI cannot disagree about a command's arguments.

## 1. The envelope

`godsvoice contract dump` is itself an ordinary command: its `--json` output *is* the envelope. The field list below is the top-level key list of the dump that produced this file, with the JSON type observed in that document.

| Field | Type in this dump |
|---|---|
| `schema_version` | string |
| `ok` | boolean |
| `command` | string |
| `godsvoice_version` | string |
| `started_at` | string |
| `duration_ms` | number |
| `exit_code` | number |
| `data` | object |
| `error` | null |
| `evidence` | array (1) |
| `warnings` | array (0) |
| `next_actions` | array (0) |

Rules the module `src/godsv/cli_contract.py` *enforces* rather than documents (its
own header, lines 18–24):

- a success envelope without evidence is downgraded to exit `7` — "no command reports
  success it cannot back" (`build_envelope`);
- `--json` never mixes text and JSON on stdout (human lines move to stderr);
- `selftest` reports a layer that is not wired as `ok: false` with a `blocked_reason` —
  never as reachable.

Beyond the four flags every command gets for free (`--json`, `--format {text,json,jsonl}`, `--quiet`, `--no-color`), a command declares its own arguments; `GODSV_FORMAT` is the environment fallback for `--format`.

## 2. Exit codes

Vocabulary source: read from `godsv.cli_contract.EXIT_NAMES` / `.EXIT_ERROR_CODES`. One meaning per code, one `error.code` per code.

| Code | Name | `error.code` | Declared by (commands) |
|---|---|---|---|
| `0` | OK | `ok` | 271 |
| `1` | USAGE | `usage` | 201 |
| `2` | VERIFICATION_FAILED | `verification_failed` | 59 |
| `3` | BLOCKED | `blocked` | 125 |
| `4` | NEEDS_CREDENTIALS | `needs_credentials` | 18 |
| `5` | NOT_FOUND | `not_found` | 133 |
| `6` | CONFLICT | `conflict` | 33 |
| `7` | INTERNAL | `internal` | 266 |
| `8` | UNKNOWN | `unknown` | 13 |

- **`0` OK** — `act.check`, `act.run`, `act.subtool.learn`, `act.subtool.list`, `act.subtool.run`, `act.subtool.save`, `act.subtool.show`, `adversarial.plan`, `adversarial.run`, `adversarial.selftest`, `agent.cancel`, `agent.collect`, `agent.list`, `agent.retry`, `agent.run`, `agent.show`, `agent.spawn`, `agent.transcript`, `approval.allow`, `approval.check`, `approval.deny`, `approval.list`, `approval.run`, `art.json`, `art.preview`, `chat`, `checkpoint.create`, `checkpoint.list`, `checkpoint.prune`, `checkpoint.restore`, `checkpoint.resume`, `checkpoint.show`, `code.blame`, `code.cache`, `code.changed`, `code.diagnostics`, `code.explain`, `code.map`, `code.pack`, `code.run`, `code.search`, `code.symbols`, `code.tools`, `coding.complete`, `coding.events.run`, `coding.events.show`, `coding.expand`, `coding.init`, `coding.model.clear`, `coding.model.set`, `coding.model.show`, `coding.plan.begin`, `coding.plan.check`, `coding.plan.close`, `coding.plan.probe`, `coding.plan.propose`, `coding.plan.show`, `coding.status`, `coding.summary`, `coding.testfix`, `coding.turn.begin`, `coding.turn.end`, `coding.turn.list`, `coding.turn.record`, `coding.turn.undo`, `completion.begin`, `completion.history`, `completion.override`, `completion.status`, `compose.candidates`, `compose.create`, `compose.run`, `compose.show`, `compose.validate`, `context.compact`, `context.explain`, `context.fingerprint`, `context.show`, `context.usage`, `contract.dump`, `contract.list`, `contract.show`, `cu.check`, `cu.demo`, `daemon.serve`, `daemon.status`, `demo`, `desktop.build-app`, `desktop.install`, `desktop.start`, `desktop.status`, `desktop.uninstall`, `docker.ensure`, `doctor`, `edit.apply`, `edit.dry-run`, `edit.show`, `edit.undo`, `eval`, `goal.criteria`, `goal.show`, `host.cron`, `host.doctor`, `host.memory.search`, `host.sessions`, `host.skills`, `host.status`, `host.tools`, `image.guard`, `image.inspect`, `image.layers`, `image.list`, `image.pin`, `image.resolve`, `image.verify`, `init`, `jev.decide`, `jev.history`, `jev.kinds`, `jev.packs`, `jev.selftest`, `ledger-show`, `mcp.call`, `mcp.map`, `mcp.probe`, `mcp.register`, `mcp.serve`, `mcp.test`, `memory-audit`, `memory-retrieval-check`, `memory-search`, `memory-stats`, `memory.consolidate`, `memory.explain`, `memory.forget`, `memory.recall`, `memory.remember`, `memory.scope`, `metrics`, `model`, `nightshift`, `pact-show`, `permission.explain`, `permission.set`, `permission.show`, `prompt.build`, `prompt.cache.plan`, `prompt.cache.status`, `prompt.explain`, `prompt.fingerprint`, `prompts.list`, `provider.add`, `provider.catalog`, `provider.default`, `provider.doctor`, `provider.list`, `provider.remove`, `provider.test`, `provider.tiers`, `registry.migrate`, `registry.status`, `run`, `runs.checkpoint`, `runs.resume`, `runs.start`, `runs.status`, `scan`, `security.audit`, `security.coverage`, `selftest`, `serve`, `session.append`, `session.archive`, `session.export`, `session.fork`, `session.list`, `session.new`, `session.policy`, `session.purge`, `session.resume`, `session.rotate`, `session.search`, `session.show`, `session.status`, `session.verify`, `setup`, `shell`, `skills.demote`, `skills.export`, `skills.install`, `skills.list`, `skills.mine`, `skills.promote`, `skills.show`, `skills.validate`, `spend.allowance`, `spend.approve`, `spend.complete`, `spend.deny`, `spend.list`, `spend.request`, `stream.measure`, `stream.policy`, `stream.replay`, `stream.selfcheck`, `style.logo`, `style.palette`, `style.preview`, `telemetry.export`, `telemetry.live`, `telemetry.show`, `tools-core.call`, `tools-core.health`, `tools-core.list`, `tools-core.policy`, `tools-core.register`, `tools-core.show`, `tools-core.smoke`, `tools-core.unregister`, `tools-hub`, `tools.adapter.show`, `tools.api.call`, `tools.api.discover`, `tools.api.health`, `tools.api.inspect`, `tools.api.register`, `tools.api.rollback`, `tools.api.show`, `tools.approve`, `tools.build`, `tools.builds`, `tools.call`, `tools.candidates`, `tools.compose`, `tools.fulfill`, `tools.health`, `tools.list`, `tools.mine`, `tools.promote`, `tools.promote-global`, `tools.propose`, `tools.register`, `tools.request`, `tools.revoke`, `tools.rollback`, `tools.scan`, `tools.show`, `vault.add`, `vault.describe`, `vault.list`, `vault.revoke`, `verify.evidence`, `verify.flow`, `verify.flows`, `verify.plan`, `verify.run`, `verify.status`, `vision.capabilities`, `vision.describe`, `vision.ocr`, `vision.screenshot`, `voice.check`, `voice.explain`, `voice.status`, `voice.triage`, `warm.start`, `warm.status`, `warm.stop`, `workflows.list`, `workflows.run`, `workflows.show`
- **`1` USAGE** — `act.check`, `act.run`, `act.subtool.run`, `act.subtool.save`, `act.subtool.show`, `adversarial.plan`, `adversarial.run`, `agent.cancel`, `agent.collect`, `agent.list`, `agent.retry`, `agent.run`, `agent.show`, `agent.spawn`, `agent.transcript`, `approval.allow`, `approval.check`, `approval.deny`, `approval.list`, `approval.run`, `art.json`, `art.preview`, `chat`, `checkpoint.create`, `checkpoint.list`, `checkpoint.prune`, `checkpoint.restore`, `checkpoint.resume`, `checkpoint.show`, `code.explain`, `code.map`, `code.pack`, `code.run`, `code.search`, `code.tools`, `coding.complete`, `coding.events.run`, `coding.events.show`, `coding.expand`, `coding.model.clear`, `coding.model.set`, `coding.model.show`, `coding.plan.begin`, `coding.plan.check`, `coding.plan.close`, `coding.summary`, `coding.testfix`, `coding.turn.begin`, `coding.turn.end`, `coding.turn.record`, `coding.turn.undo`, `completion.history`, `completion.override`, `completion.status`, `compose.candidates`, `compose.create`, `compose.run`, `context.compact`, `context.explain`, `context.fingerprint`, `context.show`, `context.usage`, `contract.dump`, `contract.list`, `cu.check`, `cu.demo`, `daemon.serve`, `demo`, `desktop.install`, `desktop.uninstall`, `docker.ensure`, `edit.apply`, `edit.dry-run`, `edit.show`, `edit.undo`, `goal.criteria`, `goal.show`, `host.cron`, `host.doctor`, `host.memory.search`, `host.sessions`, `host.skills`, `host.status`, `host.tools`, `image.guard`, `image.inspect`, `image.layers`, `image.list`, `image.pin`, `image.resolve`, `image.verify`, `init`, `jev.decide`, `jev.history`, `jev.kinds`, `jev.packs`, `jev.selftest`, `ledger-show`, `mcp.map`, `memory-audit`, `memory-retrieval-check`, `memory-search`, `memory-stats`, `memory.explain`, `memory.forget`, `memory.recall`, `memory.remember`, `metrics`, `model`, `nightshift`, `pact-show`, `permission.explain`, `permission.set`, `permission.show`, `prompt.build`, `prompt.cache.plan`, `prompt.cache.status`, `prompt.explain`, `prompt.fingerprint`, `provider.add`, `provider.catalog`, `provider.default`, `provider.doctor`, `provider.list`, `provider.remove`, `provider.test`, `provider.tiers`, `registry.status`, `run`, `runs.checkpoint`, `runs.resume`, `runs.start`, `runs.status`, `scan`, `security.audit`, `serve`, `session.append`, `session.archive`, `session.export`, `session.fork`, `session.new`, `session.purge`, `session.rotate`, `session.search`, `setup`, `shell`, `skills.list`, `skills.promote`, `skills.show`, `spend.allowance`, `spend.list`, `stream.measure`, `stream.policy`, `stream.replay`, `stream.selfcheck`, `style.logo`, `style.palette`, `style.preview`, `telemetry.export`, `telemetry.live`, `telemetry.show`, `tools-core.call`, `tools-core.list`, `tools-core.policy`, `tools-hub`, `tools.api.discover`, `tools.api.inspect`, `tools.builds`, `tools.call`, `tools.candidates`, `tools.compose`, `tools.list`, `tools.mine`, `tools.promote-global`, `tools.propose`, `tools.request`, `tools.revoke`, `tools.scan`, `tools.show`, `vault.add`, `vault.describe`, `vault.list`, `vault.revoke`, `verify.evidence`, `verify.flow`, `verify.flows`, `verify.plan`, `verify.run`, `verify.status`, `vision.capabilities`, `vision.describe`, `vision.ocr`, `vision.screenshot`, `voice.check`, `voice.explain`, `voice.status`, `voice.triage`, `warm.start`, `warm.status`, `warm.stop`, `workflows.list`
- **`2` VERIFICATION_FAILED** — `act.run`, `act.subtool.run`, `adversarial.run`, `adversarial.selftest`, `agent.collect`, `agent.retry`, `agent.spawn`, `approval.run`, `checkpoint.restore`, `checkpoint.resume`, `code.run`, `coding.events.run`, `coding.testfix`, `compose.create`, `compose.run`, `compose.validate`, `doctor`, `edit.apply`, `edit.dry-run`, `eval`, `goal.criteria`, `image.guard`, `image.verify`, `mcp.call`, `mcp.register`, `mcp.test`, `memory.consolidate`, `model`, `prompt.build`, `prompt.explain`, `provider.test`, `run`, `runs.resume`, `security.audit`, `selftest`, `session.export`, `session.resume`, `session.show`, `session.verify`, `setup`, `skills.export`, `skills.validate`, `spend.request`, `tools-core.smoke`, `tools.api.call`, `tools.api.discover`, `tools.api.register`, `tools.approve`, `tools.build`, `tools.fulfill`, `tools.mine`, `tools.promote`, `tools.register`, `verify.flow`, `verify.run`, `vision.capabilities`, `voice.check`, `voice.triage`, `workflows.run`
- **`3` BLOCKED** — `act.check`, `act.run`, `act.subtool.run`, `adversarial.plan`, `adversarial.run`, `adversarial.selftest`, `agent.retry`, `agent.run`, `agent.spawn`, `approval.allow`, `approval.check`, `approval.deny`, `approval.run`, `chat`, `checkpoint.create`, `checkpoint.prune`, `checkpoint.restore`, `checkpoint.resume`, `checkpoint.show`, `code.blame`, `code.changed`, `code.diagnostics`, `code.explain`, `code.pack`, `code.run`, `code.search`, `code.symbols`, `coding.events.run`, `coding.expand`, `coding.plan.check`, `coding.plan.propose`, `coding.testfix`, `completion.begin`, `compose.run`, `context.compact`, `context.explain`, `context.fingerprint`, `context.show`, `context.usage`, `daemon.serve`, `desktop.start`, `docker.ensure`, `edit.apply`, `edit.dry-run`, `edit.undo`, `host.cron`, `host.doctor`, `host.memory.search`, `host.sessions`, `host.skills`, `host.status`, `host.tools`, `image.guard`, `image.inspect`, `image.layers`, `image.pin`, `image.resolve`, `jev.decide`, `mcp.call`, `mcp.map`, `mcp.probe`, `mcp.register`, `mcp.test`, `model`, `permission.explain`, `permission.set`, `permission.show`, `prompt.build`, `prompt.cache.plan`, `prompt.cache.status`, `prompt.explain`, `prompt.fingerprint`, `provider.add`, `provider.catalog`, `provider.doctor`, `provider.list`, `provider.test`, `run`, `runs.checkpoint`, `runs.resume`, `runs.start`, `security.audit`, `security.coverage`, `serve`, `session.append`, `session.archive`, `session.fork`, `session.new`, `session.purge`, `session.rotate`, `setup`, `spend.approve`, `spend.complete`, `spend.deny`, `stream.selfcheck`, `telemetry.export`, `telemetry.live`, `telemetry.show`, `tools-core.call`, `tools-core.smoke`, `tools.adapter.show`, `tools.api.call`, `tools.api.discover`, `tools.api.health`, `tools.api.inspect`, `tools.api.rollback`, `tools.api.show`, `tools.build`, `tools.call`, `tools.fulfill`, `tools.health`, `tools.mine`, `tools.rollback`, `verify.flow`, `verify.plan`, `verify.run`, `vision.capabilities`, `vision.describe`, `vision.ocr`, `vision.screenshot`, `voice.check`, `voice.explain`, `voice.triage`, `warm.status`, `workflows.run`
- **`4` NEEDS_CREDENTIALS** — `agent.run`, `host.cron`, `host.doctor`, `host.memory.search`, `host.sessions`, `host.skills`, `host.status`, `host.tools`, `jev.decide`, `model`, `provider.test`, `run`, `setup`, `tools.build`, `vision.capabilities`, `vision.describe`, `vision.ocr`, `vision.screenshot`
- **`5` NOT_FOUND** — `act.check`, `act.subtool.learn`, `act.subtool.run`, `act.subtool.show`, `agent.cancel`, `agent.collect`, `agent.retry`, `agent.show`, `agent.transcript`, `checkpoint.create`, `checkpoint.restore`, `checkpoint.resume`, `checkpoint.show`, `code.blame`, `code.diagnostics`, `code.run`, `code.search`, `code.symbols`, `coding.events.show`, `coding.init`, `coding.plan.close`, `coding.plan.probe`, `coding.plan.propose`, `coding.plan.show`, `coding.turn.end`, `coding.turn.record`, `coding.turn.undo`, `compose.run`, `compose.show`, `compose.validate`, `context.compact`, `context.explain`, `context.fingerprint`, `context.show`, `context.usage`, `contract.show`, `daemon.status`, `desktop.status`, `edit.apply`, `edit.dry-run`, `edit.show`, `edit.undo`, `goal.criteria`, `goal.show`, `image.guard`, `image.inspect`, `image.layers`, `image.pin`, `image.resolve`, `image.verify`, `ledger-show`, `mcp.call`, `mcp.register`, `mcp.serve`, `mcp.test`, `memory.consolidate`, `memory.explain`, `memory.forget`, `memory.recall`, `memory.scope`, `pact-show`, `prompts.list`, `provider.add`, `provider.catalog`, `provider.default`, `provider.remove`, `provider.test`, `provider.tiers`, `run`, `runs.checkpoint`, `runs.resume`, `runs.start`, `runs.status`, `scan`, `session.append`, `session.archive`, `session.export`, `session.fork`, `session.list`, `session.new`, `session.purge`, `session.resume`, `session.rotate`, `session.search`, `session.show`, `session.status`, `session.verify`, `skills.demote`, `skills.export`, `skills.install`, `skills.mine`, `skills.promote`, `skills.validate`, `spend.approve`, `spend.complete`, `spend.deny`, `telemetry.export`, `telemetry.live`, `telemetry.show`, `tools-core.call`, `tools-core.show`, `tools-core.smoke`, `tools-core.unregister`, `tools.adapter.show`, `tools.api.call`, `tools.api.discover`, `tools.api.health`, `tools.api.register`, `tools.api.rollback`, `tools.api.show`, `tools.approve`, `tools.call`, `tools.fulfill`, `tools.health`, `tools.mine`, `tools.promote`, `tools.promote-global`, `tools.register`, `tools.rollback`, `vault.describe`, `vault.revoke`, `verify.evidence`, `verify.flow`, `verify.flows`, `verify.status`, `vision.describe`, `vision.ocr`, `vision.screenshot`, `voice.status`, `warm.status`, `warm.stop`, `workflows.run`, `workflows.show`
- **`6` CONFLICT** — `act.subtool.save`, `checkpoint.restore`, `checkpoint.resume`, `coding.init`, `coding.plan.begin`, `coding.turn.undo`, `compose.create`, `edit.apply`, `edit.dry-run`, `edit.undo`, `goal.criteria`, `image.guard`, `image.pin`, `image.verify`, `prompt.build`, `prompt.fingerprint`, `provider.add`, `registry.migrate`, `run`, `runs.start`, `session.append`, `session.archive`, `session.fork`, `session.new`, `session.purge`, `session.rotate`, `tools-core.call`, `tools-core.register`, `tools.api.discover`, `tools.build`, `tools.mine`, `warm.status`, `warm.stop`
- **`7` INTERNAL** — `act.check`, `act.run`, `act.subtool.learn`, `act.subtool.list`, `act.subtool.run`, `act.subtool.save`, `act.subtool.show`, `adversarial.plan`, `adversarial.run`, `adversarial.selftest`, `agent.cancel`, `agent.collect`, `agent.list`, `agent.retry`, `agent.run`, `agent.show`, `agent.spawn`, `agent.transcript`, `approval.allow`, `approval.check`, `approval.deny`, `approval.list`, `approval.run`, `art.json`, `art.preview`, `chat`, `checkpoint.create`, `checkpoint.list`, `checkpoint.prune`, `checkpoint.restore`, `checkpoint.resume`, `checkpoint.show`, `code.blame`, `code.cache`, `code.changed`, `code.diagnostics`, `code.explain`, `code.map`, `code.pack`, `code.run`, `code.search`, `code.symbols`, `code.tools`, `coding.complete`, `coding.events.run`, `coding.expand`, `coding.init`, `coding.model.clear`, `coding.model.set`, `coding.model.show`, `coding.plan.begin`, `coding.plan.close`, `coding.plan.probe`, `coding.plan.propose`, `coding.status`, `coding.summary`, `coding.testfix`, `coding.turn.begin`, `coding.turn.end`, `coding.turn.list`, `coding.turn.record`, `coding.turn.undo`, `completion.begin`, `completion.history`, `completion.override`, `completion.status`, `compose.candidates`, `compose.create`, `compose.run`, `compose.show`, `compose.validate`, `context.compact`, `context.explain`, `context.fingerprint`, `context.show`, `context.usage`, `contract.dump`, `contract.list`, `contract.show`, `cu.check`, `cu.demo`, `daemon.serve`, `daemon.status`, `demo`, `desktop.build-app`, `desktop.install`, `desktop.start`, `desktop.status`, `desktop.uninstall`, `docker.ensure`, `doctor`, `edit.apply`, `edit.dry-run`, `edit.show`, `edit.undo`, `eval`, `goal.criteria`, `goal.show`, `host.cron`, `host.doctor`, `host.memory.search`, `host.sessions`, `host.skills`, `host.status`, `host.tools`, `image.guard`, `image.inspect`, `image.layers`, `image.list`, `image.pin`, `image.resolve`, `image.verify`, `init`, `jev.decide`, `jev.history`, `jev.kinds`, `jev.packs`, `jev.selftest`, `ledger-show`, `mcp.call`, `mcp.map`, `mcp.probe`, `mcp.register`, `mcp.serve`, `mcp.test`, `memory-audit`, `memory-retrieval-check`, `memory-search`, `memory-stats`, `memory.consolidate`, `memory.explain`, `memory.forget`, `memory.recall`, `memory.remember`, `memory.scope`, `metrics`, `model`, `nightshift`, `pact-show`, `permission.explain`, `permission.set`, `permission.show`, `prompt.build`, `prompt.cache.status`, `prompt.explain`, `prompt.fingerprint`, `prompts.list`, `provider.add`, `provider.catalog`, `provider.default`, `provider.doctor`, `provider.list`, `provider.remove`, `provider.test`, `provider.tiers`, `registry.migrate`, `registry.status`, `run`, `runs.checkpoint`, `runs.resume`, `runs.start`, `runs.status`, `scan`, `security.audit`, `selftest`, `serve`, `session.append`, `session.archive`, `session.export`, `session.fork`, `session.list`, `session.new`, `session.policy`, `session.purge`, `session.resume`, `session.rotate`, `session.search`, `session.show`, `session.status`, `session.verify`, `setup`, `shell`, `skills.demote`, `skills.export`, `skills.install`, `skills.list`, `skills.mine`, `skills.promote`, `skills.show`, `skills.validate`, `spend.allowance`, `spend.approve`, `spend.complete`, `spend.deny`, `spend.list`, `spend.request`, `stream.measure`, `stream.policy`, `stream.replay`, `stream.selfcheck`, `style.logo`, `style.palette`, `style.preview`, `telemetry.export`, `telemetry.live`, `telemetry.show`, `tools-core.call`, `tools-core.health`, `tools-core.list`, `tools-core.policy`, `tools-core.register`, `tools-core.show`, `tools-core.smoke`, `tools-core.unregister`, `tools-hub`, `tools.adapter.show`, `tools.api.call`, `tools.api.discover`, `tools.api.health`, `tools.api.inspect`, `tools.api.register`, `tools.api.rollback`, `tools.api.show`, `tools.approve`, `tools.build`, `tools.builds`, `tools.call`, `tools.candidates`, `tools.compose`, `tools.fulfill`, `tools.health`, `tools.list`, `tools.mine`, `tools.promote`, `tools.promote-global`, `tools.propose`, `tools.register`, `tools.request`, `tools.revoke`, `tools.rollback`, `tools.scan`, `tools.show`, `vault.add`, `vault.describe`, `vault.list`, `vault.revoke`, `verify.evidence`, `verify.flow`, `verify.flows`, `verify.plan`, `verify.run`, `verify.status`, `vision.capabilities`, `vision.describe`, `vision.ocr`, `vision.screenshot`, `voice.check`, `voice.explain`, `voice.status`, `voice.triage`, `warm.start`, `warm.status`, `warm.stop`, `workflows.list`, `workflows.run`, `workflows.show`
- **`8` UNKNOWN** — `agent.collect`, `agent.retry`, `code.blame`, `code.diagnostics`, `code.run`, `coding.testfix`, `model`, `provider.test`, `setup`, `tools-core.call`, `vision.describe`, `vision.ocr`, `vision.screenshot`

argparse usage errors exit `1` (`ContractArgumentParser.error`), not `2`. A code that a command does not list is not part of that command's contract.

## 3. The eight layers and the command that proves each one

Source: `/home/willy/projects/gods-voice/.venv/bin/godsvoice selftest --json` (exit 0) — runtime `/home/willy/.hermes/godsv`, overall `ok: true`.

| Layer | Reachable | Exit | CLI commands that prove it | `blocked_reason` (verbatim) |
|---|---|---|---|---|
| EXPAND | reachable (`ok: true`) | `0` | `code.cache`, `code.map`, `code.pack`, `code.search`, `code.symbols`, `code.tools`, `coding.complete`, `coding.expand`, `coding.init`, `coding.plan.probe`, `docker.ensure`, `image.guard`, `image.inspect`, `image.layers`, `image.list`, `image.pin`, `image.resolve`, `image.verify`, `mcp.call`, `mcp.map`, `mcp.probe`, `mcp.register`, `mcp.test`, `registry.migrate`, `registry.status`, `tools-core.call`, `tools-core.health`, `tools-core.list`, `tools-core.policy`, `tools-core.register`, `tools-core.show`, `tools-core.smoke`, `tools-core.unregister`, `tools-hub`, `tools.adapter.show`, `tools.api.call`, `tools.api.discover`, `tools.api.health`, `tools.api.inspect`, `tools.api.register`, `tools.api.rollback`, `tools.api.show`, `tools.approve`, `tools.build`, `tools.builds`, `tools.call`, `tools.candidates`, `tools.fulfill`, `tools.health`, `tools.list`, `tools.mine`, `tools.promote`, `tools.promote-global`, `tools.propose`, `tools.register`, `tools.request`, `tools.revoke`, `tools.rollback`, `tools.scan`, `tools.show`, `vision.capabilities`, `vision.describe`, `vision.ocr` | — |
| ACT | reachable (`ok: true`) | `0` | `act.check`, `act.run`, `act.subtool.learn`, `act.subtool.list`, `act.subtool.run`, `act.subtool.save`, `act.subtool.show`, `cu.check`, `cu.demo`, `vision.screenshot` | — |
| COMPOSE | reachable (`ok: true`) | `0` | `art.json`, `art.preview`, `code.explain`, `code.run`, `compose.candidates`, `compose.create`, `compose.run`, `compose.show`, `compose.validate`, `skills.demote`, `skills.export`, `skills.install`, `skills.list`, `skills.mine`, `skills.promote`, `skills.show`, `skills.validate`, `stream.policy`, `tools.compose` | — |
| ROUTE | reachable (`ok: true`) | `0` | `coding.model.clear`, `coding.model.set`, `coding.model.show`, `context.compact`, `context.usage`, `jev.decide`, `jev.history`, `jev.kinds`, `jev.packs`, `jev.selftest` | — |
| SUPERVISE | reachable (`ok: true`) | `0` | `coding.events.run`, `coding.events.show`, `voice.check`, `voice.explain`, `voice.status`, `voice.triage` | — |
| ENFORCE | reachable (`ok: true`) | `0` | `agent.run`, `agent.transcript`, `approval.allow`, `approval.check`, `approval.deny`, `approval.list`, `approval.run`, `coding.plan.begin`, `coding.plan.check`, `coding.plan.close`, `coding.plan.propose`, `coding.plan.show`, `completion.begin`, `completion.history`, `completion.override`, `completion.status`, `context.explain`, `context.fingerprint`, `context.show`, `edit.apply`, `edit.dry-run`, `permission.explain`, `permission.set`, `permission.show`, `run`, `security.audit`, `security.coverage` | — |
| VERIFY | reachable (`ok: true`) | `0` | `adversarial.plan`, `adversarial.run`, `adversarial.selftest`, `code.diagnostics`, `coding.testfix`, `eval`, `verify.evidence`, `verify.flow`, `verify.flows`, `verify.plan`, `verify.run`, `verify.status` | — |
| PERSIST | reachable (`ok: true`) | `0` | `agent.cancel`, `agent.collect`, `agent.list`, `agent.retry`, `agent.show`, `agent.spawn`, `checkpoint.create`, `checkpoint.list`, `checkpoint.prune`, `checkpoint.restore`, `checkpoint.resume`, `checkpoint.show`, `code.blame`, `code.changed`, `coding.status`, `coding.summary`, `coding.turn.begin`, `coding.turn.end`, `coding.turn.list`, `coding.turn.record`, `coding.turn.undo`, `edit.show`, `edit.undo`, `goal.criteria`, `goal.show`, `memory-audit`, `memory-retrieval-check`, `memory-search`, `memory-stats`, `memory.consolidate`, `memory.explain`, `memory.forget`, `memory.recall`, `memory.remember`, `memory.scope`, `metrics`, `nightshift`, `prompt.build`, `prompt.cache.plan`, `prompt.cache.status`, `prompt.explain`, `prompt.fingerprint`, `runs.checkpoint`, `runs.resume`, `runs.start`, `runs.status`, `session.append`, `session.archive`, `session.export`, `session.fork`, `session.list`, `session.new`, `session.policy`, `session.purge`, `session.resume`, `session.rotate`, `session.search`, `session.show`, `session.status`, `session.verify`, `stream.measure`, `stream.replay`, `stream.selfcheck`, `telemetry.export`, `telemetry.live`, `telemetry.show` | — |

Per-layer probes (the inner checks behind each verdict):

- **EXPAND**
  - `registry.resolve` — reachable — runtime=/home/willy/.hermes/godsv tools_db=/home/willy/.hermes/godsv/tools.db (`src/skaldir/registry.py:123`, 54 ms)
  - `capability.load_tool_entries` — reachable — 10 generated tool(s) with manifest facts (`src/skaldir/capability.py:536`, 54 ms)
  - `capability_health.all_states` — reachable — 16 tool(s) with a health state (`src/skaldir/capability_health.py:265`, 54 ms)
  - `tools_db` — reachable — tools db present (`/home/willy/.hermes/godsv/tools.db`, 54 ms)
  - `container_runtime.ensure` — reachable — docker 29.6.1 reachable via probe (storage driver overlayfs) (`src/skaldir/docker_ensure.py`, 54 ms)
- **ACT**
  - `runner_availability` — reachable — isolation levels: {'dry_run': True, 'sandbox_bwrap': True, 'container_podman': True} (`src/skaldir/computer_use.py:464`, 187 ms)
  - `executor_in_process` — reachable — in-process executor verdict: verified (`src/skaldir/computer_use.py:357`, 187 ms)
  - `live_desktop` — reachable — live desktop refused without the opt-in: `--allow-live-desktop --live-target <window>` — verdict=isolated-only via container, container-gui, dry-run, host-session, sandbox; observe.screenshot=available, host.session.screenshot=available (`src/skaldir/act_cli.py:889`, 187 ms)
- **COMPOSE**
  - `compose_candidates` — reachable — compose_candidates returned 1 ranked candidate(s) (`src/skaldir/tools.py:469`, 133 ms)
  - `compose_lifecycle` — reachable — composite lifecycle wired (create/validate/run/show over 5 CLI verb(s)); 4 executable step kind(s): api, docker, gui, tool; 4 runnable here (`src/skaldir/compose_service.py:807`, 133 ms)
- **ROUTE**
  - `system_one_flags` — reachable — flags loaded: provider=llm_system_one_adapter shadow=False (`/home/willy/.hermes/godsv/settings.yaml`, 9 ms)
  - `advise` — reachable — advise() answered 22 of 22 question(s) through the voice_triage pack, offline on the replay fixture: provider_mode=llm_system_one_adapter — the mode the settings resolve to; the live answer is `skaldir jev status`'s measurement, never the reachability selftest's (`src/skaldir/system_one_service.py:165`, 9 ms)
- **SUPERVISE**
  - `read_trajectory_health` — reachable — 55 session(s); newest=run-20260921T135929Z-baef83 window_steps=0 (`src/skaldir/voice.py:264`, 6 ms)
  - `guard` — reachable — guard wired (evaluate_completion + EvidenceIndex importable) (`src/skaldir/guard.py:266`, 6 ms)
  - `delegation_surface` — reachable — run surface offers spawn_subagents with a provider client (roles: explore, worker, verifier; max_children=3, max_depth=1, mode=explore: read-only, worker: read-write+tests, verifier: read-only+tests) and refuses it by name without one (`src/skaldir/goal_run.py:291`, 6 ms)
  - `pause_store` — reachable — pause store readable: 61 lease(s) {'abandoned': 4, 'delivered': 56, 'expired': 1}, strictness=standard (`/home/willy/.hermes/godsv/pauses.db`, 6 ms)
- **ENFORCE**
  - `enforcement_primitives` — reachable — mechanism=bwrap+landlock coverage=degraded [no_new_privs=enforced, landlock=enforced, namespaces=enforced, seccomp=enforced, pids_cgroup=degraded, rlimits=degraded] — pids_cgroup: degraded (no cap declared (caps.max_processes = 0), or linux_security.pids_cgroup = 'off': nothing caps the process tree) \| rlimits: degraded (no memory cap declared (caps.max_memory_bytes = 0): nothing bounds the child's address space) (`src/skaldir/security_linux.py:platform_coverage`, 2 ms)
  - `completion_store` — reachable — state=WORKING goal=recorded criteria=1 requests=0 (`src/skaldir/completion.py:385`, 2 ms)
  - `terminal_states` — reachable — 6 terminal states known (`src/skaldir/completion.py:496`, 2 ms)
- **VERIFY**
  - `plan_verification` — reachable — plan_verification('the test suite passes') -> pytest (`src/skaldir/completion.py:334`, 0 ms)
  - `gate_runner` — reachable — gate runner present (execute_check + run_requirement_checks importable) (`src/skaldir/completion.py:3507`, 0 ms)
  - `real_world_runner` — reachable — `skaldir verify` runs the real-workspace checks (verify.evidence, verify.flow, verify.flows, verify.plan, verify.run, verify.status); the verdict is PASS/FAIL/UNKNOWN and UNKNOWN exits 3 — never PASS (`src/skaldir/verify_cli.py:627`, 0 ms)
- **PERSIST**
  - `events` — reachable — 3733 event(s) in 55 session(s) (`/home/willy/.hermes/godsv/events.db`, 5 ms)
  - `memory` — reachable — 0 incident(s), 0 note(s) (`/home/willy/.hermes/godsv/memory.db`, 5 ms)
  - `run_store` — reachable — 3 run(s), ok=False (`/home/willy/.hermes/godsv/runs.db`, 5 ms)
  - `session_store` — reachable — 2 session(s), 0 message(s), 0 purged; search fts5 (`/home/willy/.hermes/godsv/sessions.db`, 5 ms)

### Commands per layer (from the contract dump)

| Layer | Commands |
|---|---|
| EXPAND | `code.cache`, `code.map`, `code.pack`, `code.search`, `code.symbols`, `code.tools`, `coding.complete`, `coding.expand`, `coding.init`, `coding.plan.probe`, `docker.ensure`, `image.guard`, `image.inspect`, `image.layers`, `image.list`, `image.pin`, `image.resolve`, `image.verify`, `mcp.call`, `mcp.map`, `mcp.probe`, `mcp.register`, `mcp.test`, `registry.migrate`, `registry.status`, `tools-core.call`, `tools-core.health`, `tools-core.list`, `tools-core.policy`, `tools-core.register`, `tools-core.show`, `tools-core.smoke`, `tools-core.unregister`, `tools-hub`, `tools.adapter.show`, `tools.api.call`, `tools.api.discover`, `tools.api.health`, `tools.api.inspect`, `tools.api.register`, `tools.api.rollback`, `tools.api.show`, `tools.approve`, `tools.build`, `tools.builds`, `tools.call`, `tools.candidates`, `tools.fulfill`, `tools.health`, `tools.list`, `tools.mine`, `tools.promote`, `tools.promote-global`, `tools.propose`, `tools.register`, `tools.request`, `tools.revoke`, `tools.rollback`, `tools.scan`, `tools.show`, `vision.capabilities`, `vision.describe`, `vision.ocr` |
| ACT | `act.check`, `act.run`, `act.subtool.learn`, `act.subtool.list`, `act.subtool.run`, `act.subtool.save`, `act.subtool.show`, `cu.check`, `cu.demo`, `vision.screenshot` |
| COMPOSE | `art.json`, `art.preview`, `code.explain`, `code.run`, `compose.candidates`, `compose.create`, `compose.run`, `compose.show`, `compose.validate`, `skills.demote`, `skills.export`, `skills.install`, `skills.list`, `skills.mine`, `skills.promote`, `skills.show`, `skills.validate`, `stream.policy`, `tools.compose` |
| ROUTE | `coding.model.clear`, `coding.model.set`, `coding.model.show`, `context.compact`, `context.usage`, `jev.decide`, `jev.history`, `jev.kinds`, `jev.packs`, `jev.selftest` |
| SUPERVISE | `coding.events.run`, `coding.events.show`, `voice.check`, `voice.explain`, `voice.status`, `voice.triage` |
| ENFORCE | `agent.run`, `agent.transcript`, `approval.allow`, `approval.check`, `approval.deny`, `approval.list`, `approval.run`, `coding.plan.begin`, `coding.plan.check`, `coding.plan.close`, `coding.plan.propose`, `coding.plan.show`, `completion.begin`, `completion.history`, `completion.override`, `completion.status`, `context.explain`, `context.fingerprint`, `context.show`, `edit.apply`, `edit.dry-run`, `permission.explain`, `permission.set`, `permission.show`, `run`, `security.audit`, `security.coverage` |
| VERIFY | `adversarial.plan`, `adversarial.run`, `adversarial.selftest`, `code.diagnostics`, `coding.testfix`, `eval`, `verify.evidence`, `verify.flow`, `verify.flows`, `verify.plan`, `verify.run`, `verify.status` |
| PERSIST | `agent.cancel`, `agent.collect`, `agent.list`, `agent.retry`, `agent.show`, `agent.spawn`, `checkpoint.create`, `checkpoint.list`, `checkpoint.prune`, `checkpoint.restore`, `checkpoint.resume`, `checkpoint.show`, `code.blame`, `code.changed`, `coding.status`, `coding.summary`, `coding.turn.begin`, `coding.turn.end`, `coding.turn.list`, `coding.turn.record`, `coding.turn.undo`, `edit.show`, `edit.undo`, `goal.criteria`, `goal.show`, `memory-audit`, `memory-retrieval-check`, `memory-search`, `memory-stats`, `memory.consolidate`, `memory.explain`, `memory.forget`, `memory.recall`, `memory.remember`, `memory.scope`, `metrics`, `nightshift`, `prompt.build`, `prompt.cache.plan`, `prompt.cache.status`, `prompt.explain`, `prompt.fingerprint`, `runs.checkpoint`, `runs.resume`, `runs.start`, `runs.status`, `session.append`, `session.archive`, `session.export`, `session.fork`, `session.list`, `session.new`, `session.policy`, `session.purge`, `session.resume`, `session.rotate`, `session.search`, `session.show`, `session.status`, `session.verify`, `stream.measure`, `stream.replay`, `stream.selfcheck`, `telemetry.export`, `telemetry.live`, `telemetry.show` |
| kernel | `chat`, `contract.dump`, `contract.list`, `contract.show`, `daemon.serve`, `daemon.status`, `demo`, `desktop.build-app`, `desktop.install`, `desktop.start`, `desktop.status`, `desktop.uninstall`, `doctor`, `host.cron`, `host.doctor`, `host.memory.search`, `host.sessions`, `host.skills`, `host.status`, `host.tools`, `init`, `ledger-show`, `mcp.serve`, `model`, `pact-show`, `prompts.list`, `provider.add`, `provider.catalog`, `provider.default`, `provider.doctor`, `provider.list`, `provider.remove`, `provider.test`, `provider.tiers`, `scan`, `selftest`, `serve`, `setup`, `shell`, `spend.allowance`, `spend.approve`, `spend.complete`, `spend.deny`, `spend.list`, `spend.request`, `style.logo`, `style.palette`, `style.preview`, `vault.add`, `vault.describe`, `vault.list`, `vault.revoke`, `warm.start`, `warm.status`, `warm.stop`, `workflows.list`, `workflows.run`, `workflows.show` |

## 4. Command index

271 leaf commands in 54 groups. `write` mutates runtime state;
`read` does not. Every command has `--json`.

### `docker` — layer EXPAND (1 command)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`docker.ensure`](#docker-ensure) | read | 0, 1, 3, 7 | no | yes | ensure a container runtime is up (password-free) or report why it is not |

### `image` — layer EXPAND (7 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`image.guard`](#image-guard) | read | 0, 1, 2, 3, 5, 6, 7 | no | yes | the same check at the surface a running capability meets (health / invoke / rollback) |
| [`image.inspect`](#image-inspect) | read | 0, 1, 3, 5, 7 | no | yes | shell-free image inspection + candidate classification |
| [`image.layers`](#image-layers) | read | 0, 1, 3, 5, 7 | no | yes | layer/executable/library listing from `docker save` |
| [`image.list`](#image-list) | read | 0, 1, 7 | no | yes | the recorded pins |
| [`image.pin`](#image-pin) | write | 0, 1, 3, 5, 6, 7 | no | yes | resolve, then record the digest (atomic store) |
| [`image.resolve`](#image-resolve) | read | 0, 1, 3, 5, 7 | no | yes | what the registry says the reference resolves to (index + per-platform manifest digest); --offline reads the daemon's own record |
| [`image.verify`](#image-verify) | read | 0, 1, 2, 5, 6, 7 | no | yes | re-verify a recorded pin against the local image (--remote also against the registry) |

### `registry` — layer EXPAND (2 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`registry.migrate`](#registry-migrate) | write | 0, 6, 7 | no | yes | merge legacy tool DBs |
| [`registry.status`](#registry-status) | read | 0, 1, 7 | no | yes | resolved runtime/db/project context |

### `tools-core` — layer EXPAND (8 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`tools-core.call`](#tools-core-call) | write | 0, 1, 3, 5, 6, 7, 8 | no | yes | run one core tool for real and return its typed result |
| [`tools-core.health`](#tools-core-health) | read | 0, 7 | no | yes | the capability-health view of the core tools |
| [`tools-core.list`](#tools-core-list) | read | 0, 1, 7 | no | yes | the core toolset with kind, permissions, timeout and truncation |
| [`tools-core.policy`](#tools-core-policy) | read | 0, 1, 7 | no | yes | the declared filesystem/network/subprocess surface of the toolset |
| [`tools-core.register`](#tools-core-register) | write | 0, 6, 7 | no | yes | publish the core tools into the runtime registry |
| [`tools-core.show`](#tools-core-show) | read | 0, 5, 7 | no | yes | one core tool's typed contract and registry facts |
| [`tools-core.smoke`](#tools-core-smoke) | write | 0, 2, 3, 5, 7 | no | yes | run every core tool's declared smoke case and record the health fact |
| [`tools-core.unregister`](#tools-core-unregister) | write | 0, 5, 7 | no | yes | take the core toolset back out (manifest, descriptor, health retire) |

### `tools.adapter` — layer EXPAND (1 command)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`tools.adapter.show`](#tools-adapter-show) | read | 0, 3, 5, 7 | no | yes | the typed container adapter, its permissions and its test evidence |

### `tools.api` — layer EXPAND (7 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`tools.api.call`](#tools-api-call) | read | 0, 2, 3, 5, 7 | no | yes | call the registered HTTP capability and report the real request's status |
| [`tools.api.discover`](#tools-api-discover) | write | 0, 1, 2, 3, 5, 6, 7 | no | yes | discover a documented operation, test it against the real service with an independent local oracle, stage it (does NOT register) |
| [`tools.api.health`](#tools-api-health) | read | 0, 3, 5, 7 | no | yes | the live health probe of a registered HTTP capability |
| [`tools.api.inspect`](#tools-api-inspect) | read | 0, 1, 3, 7 | no | yes | which documented HTTP surface a service offers (read-only) |
| [`tools.api.register`](#tools-api-register) | write | 0, 2, 5, 7 | no | yes | register a TESTED HTTP adapter live |
| [`tools.api.rollback`](#tools-api-rollback) | write | 0, 3, 5, 7 | no | yes | retire a registered HTTP capability (and sweep only OUR containers) |
| [`tools.api.show`](#tools-api-show) | read | 0, 3, 5, 7 | no | yes | one staged HTTP adapter with its evidence |

### `act` — layer ACT (2 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`act.check`](#act-check) | read | 0, 1, 3, 5, 7 | no | yes | what this host can really do (window/click/key/scroll) and what is missing |
| [`act.run`](#act-run) | write | 0, 1, 2, 3, 7 | no | yes | run a declared action list in an isolated runner and verify the end state |

### `act.subtool` — layer ACT (5 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`act.subtool.learn`](#act-subtool-learn) | write | 0, 5, 7 | no | yes | learn a draft GUI sub-tool from one recorded session's repeated sequence |
| [`act.subtool.list`](#act-subtool-list) | read | 0, 7 | no | yes | the saved GUI sub-tools plus the five built-ins |
| [`act.subtool.run`](#act-subtool-run) | write | 0, 1, 2, 3, 5, 7 | no | yes | run one GUI sub-tool: observe → anchors → actions → end state |
| [`act.subtool.save`](#act-subtool-save) | write | 0, 1, 6, 7 | no | yes | store a GUI sub-tool (preconditions + anchors + actions + end state) |
| [`act.subtool.show`](#act-subtool-show) | read | 0, 1, 5, 7 | no | yes | one GUI sub-tool exactly as stored (the schema the owner specified) |

### `cu` — layer ACT (2 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`cu.check`](#cu-check) | read | 0, 1, 7 | no | yes | probe the available runner isolations and bridges |
| [`cu.demo`](#cu-demo) | read | 0, 1, 7 | no | no | deterministic channel-ordering demo (in-process fixture) |

### `art` — layer COMPOSE (2 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`art.json`](#art-json) | read | 0, 1, 7 | no | yes | the activity vocabulary as JSON (marks, frames, widths) |
| [`art.preview`](#art-preview) | read | 0, 1, 7 | no | yes | every activity mark, its frame timing and its width |

### `compose` — layer COMPOSE (5 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`compose.candidates`](#compose-candidates) | read | 0, 1, 7 | no | yes | buildable chains from the real registry (+ stored composites) |
| [`compose.create`](#compose-create) | write | 0, 1, 2, 6, 7 | no | yes | store a composite definition + its registry row |
| [`compose.run`](#compose-run) | write | 0, 1, 2, 3, 5, 7 | no | yes | execute an approved composite through the runtime's real runners |
| [`compose.show`](#compose-show) | read | 0, 5, 7 | no | yes | one composite (or all) with per-step executability |
| [`compose.validate`](#compose-validate) | write | 0, 2, 5, 7 | no | yes | validate a composite; --approve flips the registry row when clean |

### `skills` — layer COMPOSE (8 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`skills.demote`](#skills-demote) | write | 0, 5, 7 | no | no | demote for review |
| [`skills.export`](#skills-export) | write | 0, 2, 5, 7 | no | no | write the SKILL.md out |
| [`skills.install`](#skills-install) | write | 0, 5, 7 | no | no | install a builtin skill or from a GitHub repo |
| [`skills.list`](#skills-list) | read | 0, 1, 7 | no | no | list skill candidates |
| [`skills.mine`](#skills-mine) | write | 0, 5, 7 | no | no | mine repeated tool patterns |
| [`skills.promote`](#skills-promote) | write | 0, 1, 5, 7 | no | no | promote after validation |
| [`skills.show`](#skills-show) | read | 0, 1, 7 | no | no | print the SKILL.md draft |
| [`skills.validate`](#skills-validate) | read | 0, 2, 5, 7 | no | no | re-verify a candidate |

### `coding.model` — layer ROUTE (3 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`coding.model.clear`](#coding-model-clear) | write | 0, 1, 7 | no | yes | drop the session override so the environment/config layer wins again |
| [`coding.model.set`](#coding-model-set) | write | 0, 1, 7 | no | yes | set the model/reasoning effort for THIS session (mid-session switch) |
| [`coding.model.show`](#coding-model-show) | read | 0, 1, 7 | no | yes | the effective model/reasoning effort with the layer that supplied it |

### `jev` — layer ROUTE (5 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`jev.decide`](#jev-decide) | write | 0, 1, 3, 4, 7 | required | yes | one decision: request JSON in, exactly one advisory decision out |
| [`jev.history`](#jev-history) | read | 0, 1, 7 | no | yes | the recorded decisions (what Jev decided, which fallback fired) |
| [`jev.kinds`](#jev-kinds) | read | 0, 1, 7 | no | yes | the six decision kinds with their pack ref, risk class and flag |
| [`jev.packs`](#jev-packs) | read | 0, 1, 7 | no | yes | the question-registry manifest (substrate packs + the five Jev packs) |
| [`jev.selftest`](#jev-selftest) | read | 0, 1, 7 | no | yes | the R1-R4 table plus per-kind reachability (exit 1 if untested) |

### `coding.events` — layer SUPERVISE (2 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`coding.events.run`](#coding-events-run) | write | 0, 1, 2, 3, 7 | no | yes | run one command and emit run.started / item.started / item.updated / item.completed / run.completed as JSONL |
| [`coding.events.show`](#coding-events-show) | read | 0, 1, 5 | no | yes | validate one stream file and count its items per terminal status |

### `voice` — layer SUPERVISE (4 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`voice.check`](#voice-check) | read | 0, 1, 2, 3, 7 | no | yes | one pass over a stored trajectory: six finding categories, each with evidence |
| [`voice.explain`](#voice-explain) | read | 0, 1, 3, 7 | no | yes | why the supervisor would or would not intervene for a trajectory |
| [`voice.status`](#voice-status) | read | 0, 1, 5, 7 | no | yes | what the supervisor is watching: sessions, leases, runs, policy thresholds |
| [`voice.triage`](#voice-triage) | read | 0, 1, 2, 3, 7 | no | yes | should the Voice be woken at all? deterministic pre-filter + Jev advisory |

### `approval` — layer ENFORCE (5 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`approval.allow`](#approval-allow) | write | 0, 1, 3, 7 | no | yes | answer a prompt with yes: turn scope, session scope, or a rule |
| [`approval.check`](#approval-check) | read | 0, 1, 3, 7 | no | yes | would this command be allowed, prompted or forbidden? |
| [`approval.deny`](#approval-deny) | write | 0, 1, 3, 7 | no | yes | answer a prompt with no, plus a reason the agent can act on |
| [`approval.list`](#approval-list) | read | 0, 1, 7 | no | yes | rules, banned prefixes, live grants and the evidence tail |
| [`approval.run`](#approval-run) | write | 0, 1, 2, 3, 7 | no | yes | judge the command, then run it inside the profile when allowed |

### `completion` — layer ENFORCE (4 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`completion.begin`](#completion-begin) | write | 0, 3, 7 | no | yes | record a new goal (no execution loop is wired yet) |
| [`completion.history`](#completion-history) | read | 0, 1, 7 | no | yes | the completion timeline |
| [`completion.override`](#completion-override) | write | 0, 1, 7 | no | no | user override (never VERIFIED_COMPLETE) |
| [`completion.status`](#completion-status) | read | 0, 1, 7 | no | yes | goal state and metrics |

### `permission` — layer ENFORCE (3 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`permission.explain`](#permission-explain) | read | 0, 1, 3, 7 | no | yes | per-field source, the exact argv, and both hashes side by side |
| [`permission.set`](#permission-set) | write | 0, 1, 3, 7 | no | yes | change one profile field and report the identity hash it moved |
| [`permission.show`](#permission-show) | read | 0, 1, 3, 7 | no | yes | the effective permission profile, its hash and the prompt block |

### `security` — layer ENFORCE (2 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`security.audit`](#security-audit) | read | 0, 1, 2, 3, 7 | no | yes | run the declared defect-class rules over the tree and report every finding with its fix |
| [`security.coverage`](#security-coverage) | read | 0, 3 | no | yes | report measured enforcement coverage per primitive without hiding degradation |

### `adversarial` — layer VERIFY (3 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`adversarial.plan`](#adversarial-plan) | read | 0, 1, 3, 7 | no | yes | plan the adversarial cases for the offered surfaces (Jev or deterministic) without running them |
| [`adversarial.run`](#adversarial-run) | write | 0, 1, 2, 3, 7 | no | yes | run every planned case in parallel, judge each with its declared oracle and report PASS/FAIL/UNKNOWN with the defects |
| [`adversarial.selftest`](#adversarial-selftest) | read | 0, 2, 3, 7 | no | yes | prove the suite bites: the cases must FAIL on the canary with known defects and PASS on the fixed one |

### `verify` — layer VERIFY (6 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`verify.evidence`](#verify-evidence) | read | 0, 1, 5, 7 | no | yes | the recorded evidence records, filterable by run and criterion |
| [`verify.flow`](#verify-flow) | write | 0, 1, 2, 3, 5, 7 | no | yes | drive a declared user flow (start state, actions, end-state check) against a real browser or a real isolated desktop and answer PASS/FAIL/UNKNOWN, keeping the screenshots as evidence |
| [`verify.flows`](#verify-flows) | read | 0, 1, 5, 7 | no | yes | the recorded user-flow runs and the evidence each one produced (screenshots, hashes, timings) |
| [`verify.plan`](#verify-plan) | read | 0, 1, 3, 7 | no | yes | derive the real check that would certify each goal criterion |
| [`verify.run`](#verify-run) | write | 0, 1, 2, 3, 7 | no | yes | execute the checks against the real workspace and print PASS/FAIL/UNKNOWN |
| [`verify.status`](#verify-status) | read | 0, 1, 5, 7 | no | yes | the recorded verification runs and whether the latest verdict is still fresh for the workspace it was produced in |

### `checkpoint` — layer PERSIST (6 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`checkpoint.create`](#checkpoint-create) | write | 0, 1, 3, 5, 7 | no | yes | snapshot the files a run touched and its conversation into one checkpoint |
| [`checkpoint.list`](#checkpoint-list) | read | 0, 1, 7 | no | yes | the checkpoints with their cost, plus the directories an interrupted create left |
| [`checkpoint.prune`](#checkpoint-prune) | write | 0, 1, 3, 7 | no | yes | reclaim the blob store: remove the blobs no checkpoint references |
| [`checkpoint.restore`](#checkpoint-restore) | write | 0, 1, 2, 3, 5, 6, 7 | no | yes | restore the declared files and the conversation, byte-identically, or refuse |
| [`checkpoint.resume`](#checkpoint-resume) | write | 0, 1, 2, 3, 5, 6, 7 | no | yes | the long-run protocol's step 0: restore the point and hand back the continuation |
| [`checkpoint.show`](#checkpoint-show) | read | 0, 1, 3, 5, 7 | no | yes | one checkpoint with the exact restore preview (per-file diff, conversation drift) |

### `coding.turn` — layer PERSIST (5 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`coding.turn.begin`](#coding-turn-begin) | write | 0, 1, 7 | no | yes | open a turn in the journal (the unit `coding turn undo` reverts) |
| [`coding.turn.end`](#coding-turn-end) | write | 0, 1, 5, 7 | no | yes | close the open turn, recording each file's post-turn sha256 |
| [`coding.turn.list`](#coding-turn-list) | read | 0, 7 | no | yes | every recorded turn with its file set, its open/closed state and its undo state |
| [`coding.turn.record`](#coding-turn-record) | write | 0, 1, 5, 7 | no | yes | snapshot one file (bytes + sha256) before the turn changes it |
| [`coding.turn.undo`](#coding-turn-undo) | write | 0, 1, 5, 6, 7 | no | yes | undo the last N completed turns, restoring every file byte-identically |

### `goal` — layer PERSIST (2 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`goal.criteria`](#goal-criteria) | write | 0, 1, 2, 5, 6, 7 | no | yes | the criteria table; --verify flips one from evidence only |
| [`goal.show`](#goal-show) | read | 0, 1, 5, 7 | no | yes | the durable goal record: revision, state, decisions, criteria, operations, budget, owners |

### `memory` — layer PERSIST (6 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`memory.consolidate`](#memory-consolidate) | write | 0, 2, 5, 7 | no | yes | merge duplicates and resolve contradictions (dry run unless --apply) |
| [`memory.explain`](#memory-explain) | read | 0, 1, 5, 7 | no | yes | one memory in full: provenance, decay, conflicts, journal, optional ranking |
| [`memory.forget`](#memory-forget) | write | 0, 1, 5, 7 | no | yes | retract a memory (soft by default; --hard deletes; always journaled) |
| [`memory.recall`](#memory-recall) | read | 0, 1, 5, 7 | no | yes | rank memories for a query and explain every hit |
| [`memory.remember`](#memory-remember) | write | 0, 1, 7 | no | yes | store one typed memory (fact/preference/decision/incident/project/entity) |
| [`memory.scope`](#memory-scope) | read | 0, 5, 7 | no | yes | scopes in use, kinds per scope, decay policy and journal integrity |

### `prompt` — layer PERSIST (3 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`prompt.build`](#prompt-build) | read | 0, 1, 2, 3, 6, 7 | no | yes | compose the agent system prompt from the real inputs and print it |
| [`prompt.explain`](#prompt-explain) | read | 0, 1, 2, 3, 7 | no | yes | section by section: size, budget, hash and source of each block |
| [`prompt.fingerprint`](#prompt-fingerprint) | read | 0, 1, 3, 6, 7 | no | yes | the sha256 of the composed prompt, against the frozen one |

### `prompt.cache` — layer PERSIST (2 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`prompt.cache.plan`](#prompt-cache-plan) | read | 0, 1, 3 | no | yes | where cache_control would land on a prompt (never sent) |
| [`prompt.cache.status`](#prompt-cache-status) | read | 0, 1, 3, 7 | no | yes | the prompt-cache surface of the configured provider |

### `runs` — layer PERSIST (4 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`runs.checkpoint`](#runs-checkpoint) | write | 0, 1, 3, 5, 7 | no | yes | the self-sufficient snapshot (file + index row + cursor) |
| [`runs.resume`](#runs-resume) | write | 0, 1, 2, 3, 5, 7 | no | yes | the R0-R3 resume protocol: writer lease, classify every in-flight operation, park what cannot be proven |
| [`runs.start`](#runs-start) | write | 0, 1, 3, 5, 6, 7 | no | yes | create the goal record + criteria + budget rows + the first checkpoint |
| [`runs.status`](#runs-status) | read | 0, 1, 5, 7 | no | yes | the store, the goal records, the operations, the budget and the run supervisor's snapshot (read-only) |

### `session` — layer PERSIST (14 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`session.append`](#session-append) | write | 0, 1, 3, 5, 6, 7 | no | yes | append one message; two same-role turns in a row are refused with exit 6 |
| [`session.archive`](#session-archive) | write | 0, 1, 3, 5, 6, 7 | no | yes | archive or unarchive a session (read-only afterwards, nothing deleted) |
| [`session.export`](#session-export) | write | 0, 1, 2, 5, 7 | no | yes | export a session as json / jsonl / markdown / text (stdout or --out) |
| [`session.fork`](#session-fork) | write | 0, 1, 3, 5, 6, 7 | no | yes | branch a session into a linked child, copying its prefix when asked |
| [`session.list`](#session-list) | read | 0, 5, 7 | no | yes | list sessions: title, message count, last activity, lineage (bounded) |
| [`session.new`](#session-new) | write | 0, 1, 3, 5, 6, 7 | no | yes | create an empty session (or fork one with --parent) |
| [`session.policy`](#session-policy) | read | 0, 7 | no | yes | the retention / delete / rotate policy and the four invariants, as data |
| [`session.purge`](#session-purge) | write | 0, 1, 3, 5, 6, 7 | no | yes | archive / truncate / delete a session's bodies — dry run unless --apply |
| [`session.resume`](#session-resume) | read | 0, 2, 5, 7 | no | yes | reopen a session: prior history + hash check + the next legal role to continue with |
| [`session.rotate`](#session-rotate) | write | 0, 1, 3, 5, 6, 7 | no | yes | roll the newest --keep-last messages into a successor session (nothing deleted) |
| [`session.search`](#session-search) | read | 0, 1, 5, 7 | no | yes | search message content across sessions (bounded, ranked, FTS5) |
| [`session.show`](#session-show) | read | 0, 2, 5, 7 | no | yes | one session: header, a bounded message window, the hash check, optional audit |
| [`session.status`](#session-status) | read | 0, 5, 7 | no | yes | read-only store snapshot: counts, schema, search engine, writer lease |
| [`session.verify`](#session-verify) | read | 0, 2, 5, 7 | no | yes | recompute the history hash and compare it with what the session recorded |

### `telemetry` — layer PERSIST (3 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`telemetry.export`](#telemetry-export) | write | 0, 1, 3, 5, 7 | no | yes | write the record as json, jsonl or csv |
| [`telemetry.live`](#telemetry-live) | read | 0, 1, 3, 5, 7 | no | yes | the per-turn lines and the shell footer of a run, live or replayed |
| [`telemetry.show`](#telemetry-show) | read | 0, 1, 3, 5, 7 | no | yes | one run's telemetry record (rebuilt from its transcript, then filed beside it) |

### `contract` — layer kernel (3 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`contract.dump`](#contract-dump) | read | 0, 1, 7 | no | yes | the self-describing inventory with JSON Schemas |
| [`contract.list`](#contract-list) | read | 0, 1, 7 | no | yes | every command, its layer and its JSON flag |
| [`contract.show`](#contract-show) | read | 0, 5, 7 | no | yes | one command's full contract record |

### `daemon` — layer kernel (2 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`daemon.serve`](#daemon-serve) | read | 0, 1, 3, 7 | no | no | the `daemon` spelling of `skaldir serve` (MVP/02 §5.10) |
| [`daemon.status`](#daemon-status) | read | 0, 5, 7 | no | yes | report whether a Skaldir API server answers on a host:port |

### `desktop` — layer kernel (5 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`desktop.build-app`](#desktop-build-app) | write | 0, 7 | no | no | build the app natively |
| [`desktop.install`](#desktop-install) | write | 0, 1, 7 | no | no | desktop entry + icons |
| [`desktop.start`](#desktop-start) | read | 0, 3, 7 | no | no | verify, then start the app |
| [`desktop.status`](#desktop-status) | read | 0, 5, 7 | no | no | deployment check |
| [`desktop.uninstall`](#desktop-uninstall) | write | 0, 1, 7 | no | no | remove launcher + icons |

### `host` — layer kernel (6 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`host.cron`](#host-cron) | read | 0, 1, 3, 4, 7 | no | yes | the host's scheduled jobs |
| [`host.doctor`](#host-doctor) | read | 0, 1, 3, 4, 7 | no | yes | what of the host is reachable from here, route by route |
| [`host.sessions`](#host-sessions) | read | 0, 1, 3, 4, 7 | no | yes | the host's session store, read-only |
| [`host.skills`](#host-skills) | read | 0, 1, 3, 4, 7 | no | yes | the host's installed skills |
| [`host.status`](#host-status) | read | 0, 1, 3, 4, 7 | no | yes | is a host installed, is one running, which version, which home |
| [`host.tools`](#host-tools) | read | 0, 1, 3, 4, 7 | no | yes | the host's toolsets and their tools |

### `host.memory` — layer kernel (1 command)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`host.memory.search`](#host-memory-search) | read | 0, 1, 3, 4, 7 | no | yes | search the host's own index (FTS5 over its message store) |

### `prompts` — layer kernel (1 command)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`prompts.list`](#prompts-list) | read | 0, 5, 7 | no | no | list prompts and check required ids |

### `provider` — layer kernel (8 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`provider.add`](#provider-add) | write | 0, 1, 3, 5, 6, 7 | no | yes | add (or replace) one provider and project it into godsv.llm |
| [`provider.catalog`](#provider-catalog) | read | 0, 1, 3, 5, 7 | no | yes | the verified multi-provider catalog: base URLs, auth, streaming, structured output, vision — each claim with its verification status |
| [`provider.default`](#provider-default) | write | 0, 1, 5, 7 | no | yes | which provider the runtime uses (mirrored into godsv.llm) |
| [`provider.doctor`](#provider-doctor) | read | 0, 1, 3, 7 | no | yes | what is missing for each configured provider (offline) |
| [`provider.list`](#provider-list) | read | 0, 1, 3, 7 | no | yes | configured providers, redacted (default marked) |
| [`provider.remove`](#provider-remove) | write | 0, 1, 5, 7 | no | yes | remove one provider (a removed default clears the projection) |
| [`provider.test`](#provider-test) | read | 0, 1, 2, 3, 4, 5, 7, 8 | required | yes | one real call: latency, model echo, answer (redacted); --structured asks for a schema-constrained answer |
| [`provider.tiers`](#provider-tiers) | write | 0, 1, 5, 7 | no | yes | the small/mid/main → model mapping (show, or set with --small/--main) |

### `spend` — layer kernel (6 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`spend.allowance`](#spend-allowance) | read | 0, 1, 7 | no | yes | test-money allowance |
| [`spend.approve`](#spend-approve) | write | 0, 3, 5, 7 | no | no | approve a request |
| [`spend.complete`](#spend-complete) | write | 0, 3, 5, 7 | no | no | mark a spend executed |
| [`spend.deny`](#spend-deny) | write | 0, 3, 5, 7 | no | no | deny a request |
| [`spend.list`](#spend-list) | read | 0, 1, 7 | no | no | list open spend requests |
| [`spend.request`](#spend-request) | write | 0, 2, 7 | no | no | request a spend |

### `style` — layer kernel (3 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`style.logo`](#style-logo) | read | 0, 1, 7 | no | yes | the Skaldir CLI art (full banner or compact mark) |
| [`style.palette`](#style-palette) | read | 0, 1, 7 | no | yes | the grey ramp, the tokens and the status marks |
| [`style.preview`](#style-preview) | read | 0, 1, 7 | no | yes | every helper of the visual language, one screen |

### `vault` — layer kernel (4 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`vault.add`](#vault-add) | write | 0, 1, 7 | required | no | add a secret |
| [`vault.describe`](#vault-describe) | read | 0, 1, 5, 7 | required | no | describe one handle |
| [`vault.list`](#vault-list) | read | 0, 1, 7 | required | no | list credential handles |
| [`vault.revoke`](#vault-revoke) | write | 0, 1, 5, 7 | required | no | revoke a handle |

### `warm` — layer kernel (3 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`warm.start`](#warm-start) | write | 0, 1, 7 | no | no | start the warm CLI session (one long-lived process behind a 0600 unix socket) |
| [`warm.status`](#warm-status) | read | 0, 1, 3, 5, 6, 7 | no | no | report the warm session: pid, socket, uptime, served commands, memory, env match |
| [`warm.stop`](#warm-stop) | write | 0, 1, 5, 6, 7 | no | no | stop the warm CLI session (and say when there was nothing to stop) |

### `workflows` — layer kernel (3 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`workflows.list`](#workflows-list) | read | 0, 1, 7 | no | yes | workflow contracts |
| [`workflows.run`](#workflows-run) | write | 0, 2, 3, 5, 7 | no | yes | start a workflow |
| [`workflows.show`](#workflows-show) | read | 0, 5, 7 | no | yes | one workflow contract |

### `(top level)` — layer ENFORCE, EXPAND, PERSIST, VERIFY, kernel (21 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`chat`](#chat) | write | 0, 1, 3, 7 | no | yes | the chat bar: a framed input line, a slash-first palette, the session loop |
| [`demo`](#demo) | write | 0, 1, 7 | no | no | deterministic V0 pipeline demo |
| [`doctor`](#doctor) | read | 0, 2, 7 | no | yes | release health check |
| [`eval`](#eval) | read | 0, 2, 7 | no | yes | run the in-process eval episodes (not real-world verification) |
| [`init`](#init) | write | 0, 1, 7 | no | no | create .godsv/ with a pact + ledger |
| [`ledger-show`](#ledger-show) | read | 0, 1, 5, 7 | no | no | print the rendered ledger |
| [`memory-audit`](#memory-audit) | read | 0, 1, 7 | no | yes | event duplicates + retention |
| [`memory-retrieval-check`](#memory-retrieval-check) | read | 0, 1, 7 | no | no | retrieve → applicability → use/reject |
| [`memory-search`](#memory-search) | read | 0, 1, 7 | no | yes | evidence view over memory |
| [`memory-stats`](#memory-stats) | read | 0, 1, 7 | no | yes | incident/note counts of a memory db |
| [`metrics`](#metrics) | read | 0, 1, 7 | no | yes | operational counters |
| [`model`](#model) | write | 0, 1, 2, 3, 4, 7, 8 | required | yes | pick the provider and the model behind one of the two keys |
| [`nightshift`](#nightshift) | write | 0, 1, 7 | no | no | consolidate a session |
| [`pact-show`](#pact-show) | read | 0, 1, 5, 7 | no | no | print the pact |
| [`run`](#run) | write | 0, 1, 2, 3, 4, 5, 6, 7 | required | yes | drive one goal to verification: plan, work through the agent loop, run the declared checks, enforce the completion gate, continue on failure — exit 0 only on VERIFY PASS |
| [`scan`](#scan) | read | 0, 1, 5, 7 | no | yes | scan a file (or -) for secrets |
| [`selftest`](#selftest) | read | 0, 2, 7 | no | yes | probe all eight layers and report reachability |
| [`serve`](#serve) | read | 0, 1, 3, 7 | no | no | serve the HTTP API (one route per CLI command) with an OpenAPI document |
| [`setup`](#setup) | write | 0, 1, 2, 3, 4, 7, 8 | required | yes | first-run setup: the two mandatory keys, each proved by a real call |
| [`shell`](#shell) | write | 0, 1, 7 | no | yes | interactive shell: session state, slash palette, completion, streaming |
| [`tools-hub`](#tools-hub) | read | 0, 1, 7 | no | yes | discover tools/skills/MCP servers next to the runtime |

### `agent` — layer ENFORCE, PERSIST (8 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`agent.cancel`](#agent-cancel) | write | 0, 1, 5, 7 | no | yes | cancel a live run from another process (or reconcile a dead one) |
| [`agent.collect`](#agent-collect) | read | 0, 1, 2, 5, 7, 8 | no | yes | the collector: one structured result per child (--wait blocks on a live run) |
| [`agent.list`](#agent-list) | read | 0, 1, 7 | no | yes | list the runs in the subagent state directory, newest first |
| [`agent.retry`](#agent-retry) | write | 0, 1, 2, 3, 5, 7, 8 | no | yes | re-run the children of an earlier run that did not deliver (one new run, linked back with retried_from) |
| [`agent.run`](#agent-run) | write | 0, 1, 3, 4, 7 | no | yes | run one task through the loop (model-driven tool calling) |
| [`agent.show`](#agent-show) | read | 0, 1, 5, 7 | no | yes | show one run: its children, per-turn transcripts and the streamed events |
| [`agent.spawn`](#agent-spawn) | write | 0, 1, 2, 3, 7 | no | yes | spawn N children in parallel (one shared provider client per run) and collect a structured result per child |
| [`agent.transcript`](#agent-transcript) | read | 0, 1, 5, 7 | no | yes | read one run back (requests, answers, tool calls, digests) |

### `code` — layer COMPOSE, EXPAND, PERSIST, VERIFY (11 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`code.blame`](#code-blame) | read | 0, 3, 5, 7, 8 | no | yes | one line's author, commit and text through git blame |
| [`code.cache`](#code-cache) | write | 0, 7 | no | yes | what the repo-map cache holds right now |
| [`code.changed`](#code-changed) | read | 0, 3, 7 | no | yes | changed and untracked files through git status |
| [`code.diagnostics`](#code-diagnostics) | read | 0, 3, 5, 7, 8 | no | yes | real diagnostics from ruff, or an honest blocked answer naming the missing tool |
| [`code.explain`](#code-explain) | read | 0, 1, 3, 7 | no | yes | the sandbox policy: limits, the allowed tool list and why, the credential scrub, the kill rule and the limits that are not claimed |
| [`code.map`](#code-map) | write | 0, 1, 7 | no | yes | the ranked repo map (paths, languages, lines, symbols, sizes) under a token budget |
| [`code.pack`](#code-pack) | write | 0, 1, 3, 7 | no | yes | the bounded, ranked context block for a task description |
| [`code.run`](#code-run) | write | 0, 1, 2, 3, 5, 7, 8 | no | yes | run one snippet in a sandboxed process; the tool API inside composes tool calls and the run returns stdout/stderr, a structured value and every call |
| [`code.search`](#code-search) | read | 0, 1, 3, 5, 7 | no | yes | regex content search or file-glob search, with caps, context lines and gitignore rules |
| [`code.symbols`](#code-symbols) | read | 0, 3, 5, 7 | no | yes | definitions per file: python through ast, other languages through ctags |
| [`code.tools`](#code-tools) | read | 0, 1, 7 | no | yes | the declared surface: schemas, permissions, timeouts and truncation per verb |

### `coding` — layer EXPAND, PERSIST, VERIFY (6 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`coding.complete`](#coding-complete) | read | 0, 1, 7 | no | yes | fuzzy @-style file completion with match indices and scores |
| [`coding.expand`](#coding-expand) | read | 0, 1, 3, 7 | no | yes | resolve every @mention of a prompt to a real path inside the workspace |
| [`coding.init`](#coding-init) | write | 0, 5, 6, 7 | no | yes | write an AGENTS.md whose headings project_context injects, from measured facts |
| [`coding.status`](#coding-status) | read | 0, 7 | no | yes | the session summary: messages by role, token estimate, turns, open plan, model |
| [`coding.summary`](#coding-summary) | write | 0, 1, 7 | no | yes | the handoff summary (progress, constraints, what remains, references) as markdown |
| [`coding.testfix`](#coding-testfix) | write | 0, 1, 2, 3, 7, 8 | no | yes | run the declared tests, parse the failures, hand them to a fixer and run again |

### `coding.plan` — layer ENFORCE, EXPAND (6 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`coding.plan.begin`](#coding-plan-begin) | write | 0, 1, 6, 7 | no | yes | open plan mode for a task (refuses while another plan is open) |
| [`coding.plan.check`](#coding-plan-check) | read | 0, 1, 3 | no | yes | classify one action as read_only or mutating and say whether it may run now |
| [`coding.plan.close`](#coding-plan-close) | write | 0, 1, 5, 7 | no | yes | close plan mode with --apply (the licence to mutate) or --drop |
| [`coding.plan.probe`](#coding-plan-probe) | read | 0, 5, 7 | no | yes | the non-mutating exploration a plan is grounded on (bounded walk + declared commands) |
| [`coding.plan.propose`](#coding-plan-propose) | write | 0, 3, 5, 7 | no | yes | render the decision-complete <proposed_plan> for the open plan |
| [`coding.plan.show`](#coding-plan-show) | read | 0, 5 | no | yes | the current plan: status, guarded actions and the rendered proposal |

### `context` — layer ENFORCE, ROUTE (5 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`context.compact`](#context-compact) | read | 0, 1, 3, 5, 7 | no | yes | judge the tool history of a run per call (keep / shorten / remove) and report what would change; --dry-run applies nothing |
| [`context.explain`](#context-explain) | read | 0, 1, 3, 5, 7 | no | yes | show what would be injected and why: precedence, budget, refusals, overrides |
| [`context.fingerprint`](#context-fingerprint) | read | 0, 1, 3, 5, 7 | no | yes | print the block fingerprint and the per-file digests (no block) |
| [`context.show`](#context-show) | read | 0, 1, 3, 5, 7 | no | yes | print the exact block that would be injected (stdout), provenance to stderr |
| [`context.usage`](#context-usage) | read | 0, 1, 3, 5, 7 | no | yes | the budget state of a transcript: measured facts, labelled estimates, trigger |

### `edit` — layer ENFORCE, PERSIST (4 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`edit.apply`](#edit-apply) | write | 0, 1, 2, 3, 5, 6, 7 | no | yes | plan and publish an atomic multi-file edit batch, then run the declared check |
| [`edit.dry-run`](#edit-dry-run) | read | 0, 1, 2, 3, 5, 6, 7 | no | yes | resolve every edit, print the diffs and the exact failure reason, write nothing |
| [`edit.show`](#edit-show) | read | 0, 1, 5, 7 | no | yes | one transaction record (verdict, diffs, verification, snapshot, undo) or the list |
| [`edit.undo`](#edit-undo) | write | 0, 1, 3, 5, 6, 7 | no | yes | restore every file of a transaction from its snapshot, byte-identically |

### `mcp` — layer EXPAND, kernel (6 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`mcp.call`](#mcp-call) | read | 0, 2, 3, 5, 7 | no | yes | call the registered MCP capability through its adapter (--via wrapper runs the generated CLI wrapper) |
| [`mcp.map`](#mcp-map) | write | 0, 1, 3, 7 | no | yes | map a server's tools to typed adapters; an untypable schema is refused with its reason |
| [`mcp.probe`](#mcp-probe) | read | 0, 3, 7 | no | yes | handshake + tools/list with the raw transcript |
| [`mcp.register`](#mcp-register) | write | 0, 2, 3, 5, 7 | no | yes | register a TESTED MCP adapter live |
| [`mcp.serve`](#mcp-serve) | read | 0, 5, 7 | no | no | run the stdio MCP server |
| [`mcp.test`](#mcp-test) | write | 0, 2, 3, 5, 7 | no | yes | run a real tools/call against the server and check the declared outputs |

### `stream` — layer COMPOSE, PERSIST (4 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`stream.measure`](#stream-measure) | read | 0, 1, 7 | no | yes | measure a streamed answer with and without coalescing |
| [`stream.policy`](#stream-policy) | read | 0, 1, 7 | no | yes | the parallel-tool grouping a run would use, call by call |
| [`stream.replay`](#stream-replay) | read | 0, 1, 7 | no | yes | replay a real file as a chunked stream (the measurement's source) |
| [`stream.selfcheck`](#stream-selfcheck) | read | 0, 1, 3, 7 | no | yes | prove the render bound holds (byte-identical, no dropped bytes) |

### `tools` — layer COMPOSE, EXPAND (19 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`tools.approve`](#tools-approve) | write | 0, 2, 5, 7 | no | no | run gates, then approve |
| [`tools.build`](#tools-build) | write | 0, 2, 3, 4, 6, 7 | no | yes | capability → gated tool (or a container tool with --image) |
| [`tools.builds`](#tools-builds) | read | 0, 1, 7 | no | no | the tool-build queue |
| [`tools.call`](#tools-call) | write | 0, 1, 3, 5, 7 | no | yes | call one approved registry tool by name and report result + sha256 |
| [`tools.candidates`](#tools-candidates) | read | 0, 1, 7 | no | no | list mined candidates |
| [`tools.compose`](#tools-compose) | read | 0, 1, 7 | no | yes | list composition candidates for a need |
| [`tools.fulfill`](#tools-fulfill) | write | 0, 2, 3, 5, 7 | no | no | run the build pipeline |
| [`tools.health`](#tools-health) | read | 0, 3, 5, 7 | no | yes | health of a container capability (ledger state + live container facts) |
| [`tools.list`](#tools-list) | read | 0, 1, 7 | no | yes | list tool specs from the runtime DB |
| [`tools.mine`](#tools-mine) | write | 0, 1, 2, 3, 5, 6, 7 | no | yes | scan for candidates, or mine one from a container image (--image) |
| [`tools.promote`](#tools-promote) | write | 0, 2, 5, 7 | no | no | candidate → build request |
| [`tools.promote-global`](#tools-promote-global) | write | 0, 1, 5, 7 | no | yes | promote a validated project tool to global |
| [`tools.propose`](#tools-propose) | write | 0, 1, 7 | no | no | propose a tool spec |
| [`tools.register`](#tools-register) | write | 0, 2, 5, 7 | no | yes | register a TESTED container adapter live (version/health/rollback snapshot) |
| [`tools.request`](#tools-request) | write | 0, 1, 7 | no | yes | record a missing capability |
| [`tools.revoke`](#tools-revoke) | write | 0, 1, 7 | no | no | revoke a tool |
| [`tools.rollback`](#tools-rollback) | write | 0, 3, 5, 7 | no | yes | take a container capability back (registry/files/health/containers) |
| [`tools.scan`](#tools-scan) | read | 0, 1, 7 | no | no | static security scan |
| [`tools.show`](#tools-show) | read | 0, 1, 7 | no | yes | one tool spec as JSON |

### `vision` — layer ACT, EXPAND (4 commands)

| Command | Access | Exit codes | Credentials | JSON payload | Description |
|---|---|---|---|---|---|
| [`vision.capabilities`](#vision-capabilities) | read | 0, 1, 2, 3, 4, 7 | required | yes | measure whether the configured model accepts an image part |
| [`vision.describe`](#vision-describe) | read | 0, 1, 3, 4, 5, 7, 8 | required | yes | send one image (file path or http(s) URL) to the configured model |
| [`vision.ocr`](#vision-ocr) | read | 0, 1, 3, 4, 5, 7, 8 | required | yes | read the text in an image (the describe call with an OCR instruction) |
| [`vision.screenshot`](#vision-screenshot) | read | 0, 1, 3, 4, 5, 7, 8 | required | yes | capture the screen (or one window) through Computer Use, then describe it |

## 5. Command reference

A command marked **declared limitation** carries a sentence in its own contract record that says the feature does not do the whole thing yet. Section 6 quotes those sentences verbatim.

### Group `docker`

#### `docker.ensure` — EXPAND

ensure a container runtime is up (password-free) or report why it is not

| | |
|---|---|
| Group | `docker` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |
| `--timeout-s` | number | no | no | — | `5.0` | wall-clock bound per probe (default 5.0, clamped to 0.5…30.0) |
| `--ready-s` | number | no | no | — | `5.0` | how long a just-activated daemon may take to answer (default 5.0) |
| `--no-start` | flag | no | no | — | `false` | read-only: probe the runtimes, attempt no start path |
| `--no-fallback` | flag | no | no | — | `false` | docker only: declare no start path at all (the 'is docker up?' question) |
| `--path` | choice | no | yes | socket-activation, rootless-podman | `—` | a start path to try, in the order given (repeatable; default: both) |
| `--docker-bin` | string | no | no | — | `—` | the docker binary to probe |
| `--podman-bin` | string | no | no | — | `—` | the podman binary to probe |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "state": {
      "type": "string"
    },
    "ok": {
      "type": "boolean"
    },
    "exit_code": {
      "type": "integer"
    },
    "provider": {
      "type": "string"
    },
    "answered_by": {
      "type": "string"
    },
    "server_version": {
      "type": "string"
    },
    "storage_driver": {
      "type": "string"
    },
    "reason": {
      "type": "string"
    },
    "exact_command": {
      "type": "string"
    },
    "attempts": {
      "type": "array"
    },
    "commands": {
      "type": "array"
    },
    "container_ops_attempted": {
      "type": "integer"
    },
    "socket": {
      "type": "string"
    },
    "probed_at": {
      "type": "string"
    }
  },
  "required": [
    "state",
    "ok",
    "exit_code",
    "provider",
    "server_version",
    "storage_driver",
    "reason",
    "exact_command"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/docker_ensure.py` — probe → socket activation → rootless podman → blocked
- `code`: `src/skaldir/tools_core/docker.py` — the same capability as the built-in agent tool docker.ensure

**Contract note (verbatim)**

> exit 3 (blocked) is the honest failure: nothing answered and every password-free path was tried; the payload's `exact_command` is the literal one-liner for the owner (this command never runs a privileged start). `mutates` is false on purpose: no file, no container and no registry is written — when the socket-activation path wakes docker.service, the privileged start is systemd's action in response to one connect, and this capability never takes it itself

### Group `image`

#### `image.guard` — EXPAND

the same check at the surface a running capability meets (health / invoke / rollback)

| | |
|---|---|
| Group | `image` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `reference` | string (positional) | yes | no | — | `—` | — |
| `--surface` | choice | yes | no | health, invoke, rollback | `—` | health (exit 2) \| invoke (exit 6) \| rollback (exit 3) |
| `--capability` | string | no | no | — | `—` | the capability being guarded |
| `--version` | string | no | no | — | `—` | the version being invoked or rolled back to |
| `--remote` | flag | no | no | — | `false` | also re-resolve the tag against the registry |
| `--registry` | string | no | no | — | `—` | resolve a Hub reference via this pull-through mirror (e.g. a regional mirror host) instead of the Hub |
| `--state` | string | no | no | — | `—` | pin store: a directory or a .json file (default: <runtime>/image-pins.json) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME) |
| `--timeout` | number | no | no | — | `0.0` | per-call timeout in seconds (default 60) |
| `--docker-bin` | string | no | no | — | `—` | the docker binary to drive (default: the one on PATH) |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/image_pin.py`

**Contract note (verbatim)**

> exit 2 (health) · 6 (invoke) · 3 (rollback to --version); every guard carries the digest into its evidence

#### `image.inspect` — EXPAND

shell-free image inspection + candidate classification

| | |
|---|---|
| Group | `image` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--image` | string | yes | no | — | `—` | image reference or digest to inspect (e.g. hello-world) |
| `--digest` | string | no | no | — | `—` | the pinned sha256:<64 hex> digest (default: the local image id) |
| `--capability` | string | no | no | — | `—` | hand the inspected surface to the adapter generator for this capability (e.g. 'hash a string with sha256') |
| `--name` | string | no | no | — | `—` | tool name for the generated adapter |
| `--no-exec` | flag | no | no | — | `false` | container-free: metadata + layers only, no exec probe |
| `--exec-probes` | integer | no | no | — | `3` | how many candidates get a direct `docker run` exec probe |
| `--exec-timeout` | number | no | no | — | `15.0` | seconds per exec probe |
| `--timeout` | number | no | no | — | `60.0` | seconds for one docker call (inspect / save stream) |
| `--max-save-mb` | integer | no | no | — | `256` | cap for the spooled `docker save` stream in MiB |
| `--spool-dir` | string | no | no | — | `—` | where the temporary image tar is written (default: system temp) |
| `--candidates` | integer | no | no | — | `10` | how many filesystem executables are sampled as candidates |
| `--compare-shell` | flag | no | no | — | `false` | also run the existing sh -c inspector and report what it says |
| `--text` | flag | no | no | — | `false` | human-readable output instead of the JSON report |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/image_inspect.py`

**Contract note (verbatim)**

> a produced classification exits 0 even when every candidate is 'impossible' — that is the answer, not a refusal

#### `image.layers` — EXPAND

layer/executable/library listing from `docker save`

| | |
|---|---|
| Group | `image` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--image` | string | yes | no | — | `—` | image reference or digest |
| `--digest` | string | no | no | — | `—` | the pinned sha256:<64 hex> digest |
| `--limit` | integer | no | no | — | `12` | how many executables/libraries/directories to list |
| `--timeout` | number | no | no | — | `60.0` | — |
| `--max-save-mb` | integer | no | no | — | `256` | — |
| `--spool-dir` | string | no | no | — | `—` | — |
| `--text` | flag | no | no | — | `false` | human-readable output instead of the JSON report |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/image_inspect.py`

**Contract note (verbatim)**

> never starts a container (metadata + layers only)

#### `image.list` — EXPAND

the recorded pins

| | |
|---|---|
| Group | `image` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--state` | string | no | no | — | `—` | pin store: a directory or a .json file (default: <runtime>/image-pins.json) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME) |
| `--timeout` | number | no | no | — | `0.0` | per-call timeout in seconds (default 60) |
| `--docker-bin` | string | no | no | — | `—` | the docker binary to drive (default: the one on PATH) |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/image_pin.py`

#### `image.pin` — EXPAND

resolve, then record the digest (atomic store)

| | |
|---|---|
| Group | `image` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `reference` | string (positional) | yes | no | — | `—` | image reference, e.g. busybox:1.36 |
| `--platform` | string | no | no | — | `—` | OCI platform to pin (default: the host, linux/amd64) |
| `--registry` | string | no | no | — | `—` | resolve a Hub reference via this pull-through mirror (e.g. a regional mirror host) instead of the Hub |
| `--capability` | string | no | no | — | `—` | the capability this artifact belongs to |
| `--version` | string | no | no | — | `—` | the capability version this digest is registered as |
| `--force` | flag | no | no | — | `false` | replace a pin that records a different digest (a decision) |
| `--offline` | flag | no | no | — | `false` | record the digest the daemon recorded for the pulled image when the registry cannot be reached (marked unverified) |
| `--note` | string | no | no | — | `—` | a short human note for the pin |
| `--state` | string | no | no | — | `—` | pin store: a directory or a .json file (default: <runtime>/image-pins.json) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME) |
| `--timeout` | number | no | no | — | `0.0` | per-call timeout in seconds (default 60) |
| `--docker-bin` | string | no | no | — | `—` | the docker binary to drive (default: the one on PATH) |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/image_pin.py`

**Contract note (verbatim)**

> a different digest for an already-pinned reference needs --force: a re-pin is a decision, never a side effect of a moved tag

#### `image.resolve` — EXPAND

what the registry says the reference resolves to (index + per-platform manifest digest); --offline reads the daemon's own record

| | |
|---|---|
| Group | `image` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `reference` | string (positional) | yes | no | — | `—` | image reference, e.g. busybox:1.36 |
| `--platform` | string | no | no | — | `—` | OCI platform to pin (default: the host, linux/amd64) |
| `--registry` | string | no | no | — | `—` | resolve a Hub reference via this pull-through mirror (e.g. a regional mirror host) instead of the Hub |
| `--offline` | flag | no | no | — | `false` | do not touch the network: use the digest the daemon recorded for the pulled image (marked unverified) |
| `--state` | string | no | no | — | `—` | pin store: a directory or a .json file (default: <runtime>/image-pins.json) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME) |
| `--timeout` | number | no | no | — | `0.0` | per-call timeout in seconds (default 60) |
| `--docker-bin` | string | no | no | — | `—` | the docker binary to drive (default: the one on PATH) |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/image_pin.py`

**Contract note (verbatim)**

> read-only: nothing is pulled and nothing is written

#### `image.verify` — EXPAND

re-verify a recorded pin against the local image (--remote also against the registry)

| | |
|---|---|
| Group | `image` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `reference` | string (positional) | yes | no | — | `—` | — |
| `--capability` | string | no | no | — | `—` | the capability that must be bound to this pin |
| `--version` | string | no | no | — | `—` | the capability version the pin must cover |
| `--remote` | flag | no | no | — | `false` | also re-resolve the tag against the registry (needs network) |
| `--registry` | string | no | no | — | `—` | resolve a Hub reference via this pull-through mirror (e.g. a regional mirror host) instead of the Hub |
| `--state` | string | no | no | — | `—` | pin store: a directory or a .json file (default: <runtime>/image-pins.json) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME) |
| `--timeout` | number | no | no | — | `0.0` | per-call timeout in seconds (default 60) |
| `--docker-bin` | string | no | no | — | `—` | the docker binary to drive (default: the one on PATH) |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/image_pin.py`

**Contract note (verbatim)**

> a digest that no longer matches is refused, exit 6

### Group `registry`

#### `registry.migrate` — EXPAND

merge legacy tool DBs

| | |
|---|---|
| Group | `registry` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--dry-run` | flag | no | no | — | `false` | report only (default) |
| `--apply` | flag | no | no | — | `false` | back up source and target, then merge |
| `--runtime` | string | no | no | — | `—` | runtime dir override |
| `--db` | string | no | no | — | `—` | explicit tools DB override |
| `--project` | string | no | no | — | `—` | project path (default: cwd) |
| `--json` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/registry.py`

#### `registry.status` — EXPAND

resolved runtime/db/project context

| | |
|---|---|
| Group | `registry` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | yes |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir override |
| `--db` | string | no | no | — | `—` | explicit tools DB override |
| `--project` | string | no | no | — | `—` | project path for the stable project id (default: cwd) |
| `--json` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/registry.py:172`

**Contract note (verbatim)**

> compat_top_level_keys: the pre-contract payload is read at top level by tests/test_registry_context.py

### Group `tools-core`

#### `tools-core.call` — EXPAND

run one core tool for real and return its typed result

| | |
|---|---|
| Group | `tools-core` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL), `8` (UNKNOWN) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | — |
| `--input` | string | no | no | — | `—` | arguments as a JSON object |
| `--input-file` | string | no | no | — | `—` | arguments read from a JSON file |
| `--workspace` | string | no | no | — | `—` | sandbox root (default: the current dir) |
| `--isolate` | flag | no | no | — | `false` | escalate to bubblewrap when usable |
| `--offline` | flag | no | no | — | `false` | refuse every network tool |
| `--allow-unregistered` | flag | no | no | — | `false` | call even when the registry has no row for the tool |
| `--fail-on-nonzero` | flag | no | no | — | `false` | exit with the observed process status when it is non-zero |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: the product runtime) |
| `--db` | string | no | no | — | `—` | explicit tools DB override |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "tool": {
      "type": "string"
    },
    "ok": {
      "type": "boolean"
    },
    "error_kind": {
      "type": "string"
    },
    "exit_code": {
      "type": "integer"
    },
    "output": {
      "type": "object"
    }
  },
  "required": [
    "tool",
    "ok",
    "output"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/tools_core/registry.py`

**Contract note (verbatim)**

> a command that exits non-zero is a successful observation (exit 0 here); timeouts/network failures exit 8, refusals exit 3, unregistered tools exit 5

#### `tools-core.health` — EXPAND

the capability-health view of the core tools

| | |
|---|---|
| Group | `tools-core` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir (default: the product runtime) |
| `--db` | string | no | no | — | `—` | explicit tools DB override |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/tools_core/registry.py`

**Contract note (verbatim)**

> never fails on an unverified tool — it reports the state

#### `tools-core.list` — EXPAND

the core toolset with kind, permissions, timeout and truncation

| | |
|---|---|
| Group | `tools-core` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir (default: the product runtime) |
| `--db` | string | no | no | — | `—` | explicit tools DB override |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "tools": {
      "type": "array"
    },
    "count": {
      "type": "integer"
    },
    "registered": {
      "type": "integer"
    }
  },
  "required": [
    "tools",
    "count",
    "registered"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/tools_core/registry.py`

#### `tools-core.policy` — EXPAND

the declared filesystem/network/subprocess surface of the toolset

| | |
|---|---|
| Group | `tools-core` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/tools_core/contract.py`

**Contract note (verbatim)**

> read-only; the isolation probe result is included

#### `tools-core.register` — EXPAND

publish the core tools into the runtime registry

| | |
|---|---|
| Group | `tools-core` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir (default: the product runtime) |
| `--db` | string | no | no | — | `—` | explicit tools DB override |
| `--no-network-probe` | flag | no | no | — | `false` | do not probe the key-free search backends while registering |
| `--no-manifests` | flag | no | no | — | `false` | skip the scope manifests (the tools then count as 'legacy' and are not auto-exposed) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/tools_core/registry.py`

**Contract note (verbatim)**

> exit 6 when a core tool name is already taken by a foreign kind; the operation is idempotent (re-running re-publishes the same version)

#### `tools-core.show` — EXPAND

one core tool's typed contract and registry facts

| | |
|---|---|
| Group | `tools-core` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | — |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: the product runtime) |
| `--db` | string | no | no | — | `—` | explicit tools DB override |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/tools_core/contract.py`

**Contract note (verbatim)**

> an unknown tool name exits 5

#### `tools-core.smoke` — EXPAND

run every core tool's declared smoke case and record the health fact

| | |
|---|---|
| Group | `tools-core` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--name` | string | no | no | — | `—` | only this tool |
| `--network` | flag | no | no | — | `false` | include the network tools |
| `--workspace` | string | no | no | — | `—` | smoke workspace (default: a temp dir) |
| `--no-record` | flag | no | no | — | `false` | do not write health facts |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: the product runtime) |
| `--db` | string | no | no | — | `—` | explicit tools DB override |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/tools_core/registry.py`

**Contract note (verbatim)**

> exit 2 when a check really failed, 3 when nothing was measured (all smoke cases skipped), 5 for an unknown --name

#### `tools-core.unregister` — EXPAND

take the core toolset back out (manifest, descriptor, health retire)

| | |
|---|---|
| Group | `tools-core` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | yes | — | `—` | only these tools (default: all) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: the product runtime) |
| `--db` | string | no | no | — | `—` | explicit tools DB override |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/tools_core/registry.py`

**Contract note (verbatim)**

> nothing is deleted silently: a retire fact is recorded when a ledger row exists

### Group `tools.adapter`

#### `tools.adapter.show` — EXPAND

the typed container adapter, its permissions and its test evidence

| | |
|---|---|
| Group | `tools.adapter` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | — |
| `--runtime` | string | no | no | — | `—` | runtime dir |
| `--db` | string | no | no | — | `—` | explicit tools DB override |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/toolbuilder.py ContainerToolBuilder.container_adapter`
- `code`: `src/skaldir/toolbuilder_cli.py _cmd_tools_adapter_show`

**Contract note (verbatim)**

> a staged or registered container tool is required; an unknown name exits 5

### Group `tools.api`

#### `tools.api.call` — EXPAND

call the registered HTTP capability and report the real request's status

| | |
|---|---|
| Group | `tools.api` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | — |
| `payload` | string (positional) | yes | no | — | `—` | the inputs as JSON, e.g. '{"text": "skaldir"}' |
| `--input` | string | no | no | — | `—` | the inputs as JSON (alternative form) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--db` | string | no | no | — | `—` | explicit tools DB override |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/adapter_http.py`

**Contract note (verbatim)**

> exit 3 = the pinned surface changed or the service is down

#### `tools.api.discover` — EXPAND

discover a documented operation, test it against the real service with an independent local oracle, stage it (does NOT register)

| | |
|---|---|
| Group | `tools.api` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--capability` | string | no | no | — | `—` | what the capability must do |
| `--base-url` | string | no | no | — | `—` | a running service to adapt |
| `--image` | string | no | no | — | `—` | a container image whose API must be adapted (started by us) |
| `--port` | integer | no | no | — | `0` | the port the API listens on inside that container |
| `--service-argv` | string | no | no | — | `—` | command that starts the API inside the container (e.g. "httpd -f -p 8080 -h /www") |
| `--document` | string | no | no | — | `—` | an OpenAPI/Swagger document or endpoint list (path or URL) |
| `--operation-id` | string | no | no | — | `—` | pin the documented operation (no guessing) |
| `--oracle` | string | no | no | — | `—` | the independent local oracle (sha256-text, echo-text, …) |
| `--name` | string | no | no | — | `—` | tool name for the mined capability |
| `--test-input` | string | no | no | — | `—` | JSON inputs for the adapter test |
| `--expect` | string | no | no | — | `—` | the value the test must observe |
| `--allow-mutating` | flag | no | no | — | `false` | acknowledge a documented POST/PUT/PATCH/DELETE operation |
| `--digest` | string | no | no | — | `—` | pin the image by sha256 digest |
| `--pull` | flag | no | no | — | `false` | pull the image when not local |
| `--session` | string | no | no | — | `—` | session id recorded with the build |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--db` | string | no | no | — | `—` | explicit tools DB override |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/adapter_http.py`

#### `tools.api.health` — EXPAND

the live health probe of a registered HTTP capability

| | |
|---|---|
| Group | `tools.api` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | — |
| `--probe` | flag | no | no | — | `false` | call the capability once (a real request) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--db` | string | no | no | — | `—` | explicit tools DB override |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/adapter_http.py`
- `code`: `src/skaldir/capability_health.py`

#### `tools.api.inspect` — EXPAND

which documented HTTP surface a service offers (read-only)

| | |
|---|---|
| Group | `tools.api` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--base-url` | string | no | no | — | `—` | a running service, e.g. http://127.0.0.1:8080 |
| `--document` | string | no | no | — | `—` | an OpenAPI/Swagger document or endpoint list (path or URL) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--db` | string | no | no | — | `—` | explicit tools DB override |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/adapter_http.py`
- `code`: `src/skaldir/adapter_http_cli.py`

**Contract note (verbatim)**

> read-only: which documented surface answered + every probe

#### `tools.api.register` — EXPAND

register a TESTED HTTP adapter live

| | |
|---|---|
| Group | `tools.api` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | the staged capability to register |
| `--version` | string | no | no | — | `—` | explicit version (default 0.1.0; re-registering bumps the patch) |
| `--project-id` | string | no | no | — | `—` | project id recorded in the manifest |
| `--requested-by` | string | no | no | — | `cli` | — |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--db` | string | no | no | — | `—` | explicit tools DB override |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/adapter_http.py`
- `code`: `src/skaldir/tools.py`
- `code`: `src/skaldir/capability_health.py`

**Contract note (verbatim)**

> exit 2 = the adapter is not (still) proven: no registration

#### `tools.api.rollback` — EXPAND

retire a registered HTTP capability (and sweep only OUR containers)

| | |
|---|---|
| Group | `tools.api` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | — |
| `--reason` | string | no | no | — | `—` | — |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--db` | string | no | no | — | `—` | explicit tools DB override |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/toolbuilder.py`

#### `tools.api.show` — EXPAND

one staged HTTP adapter with its evidence

| | |
|---|---|
| Group | `tools.api` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | — |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--db` | string | no | no | — | `—` | explicit tools DB override |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/adapter_http.py`

### Group `act`

#### `act.check` — ACT

what this host can really do (window/click/key/scroll) and what is missing

| | |
|---|---|
| Group | `act` |
| Layer | ACT |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--require` | string | no | yes | — | `[]` | exit 3 when this capability is unavailable here (repeatable) |
| `--probe-image` | flag | no | no | — | `false` | start the isolated display once and verify the driver tools inside the image |
| `--json` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "host": {
      "type": "object"
    },
    "bridges": {
      "type": "array"
    },
    "capabilities": {
      "type": "array"
    },
    "capability_names": {
      "type": "array"
    },
    "policy": {
      "type": "object"
    },
    "live_desktop": {
      "type": "boolean"
    },
    "live_desktop_allowed": {
      "type": "boolean"
    },
    "live_desktop_optin": {
      "type": "object"
    },
    "verdict": {
      "type": "string"
    },
    "summary": {
      "type": "object"
    }
  },
  "required": [
    "bridges",
    "capabilities",
    "live_desktop",
    "verdict"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/computer_use.py:464` — runner_availability() — the real isolation levels of this host
- `code`: `src/skaldir/act_service.py:483` — capability_report() — bridge/driver/verification per capability

**Contract note (verbatim)**

> exit 0 is the report itself; --require NAME exits 3 when that capability is unavailable here, and the record names the missing piece. `bridges` carries the `host-session` bridge (a display this run owns) and `live-desktop` (the opted-in one named window); `live_desktop`/`live_desktop_allowed` are the measured two-fact opt-in (switch + named target), never a literal

#### `act.run` — ACT · **declared limitation**

run a declared action list in an isolated runner and verify the end state

| | |
|---|---|
| Group | `act` |
| Layer | ACT |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--actions` | string | no | no | — | `—` | JSON request file: {actions: [...], end_state: {...}, goal: '...'} or a bare list |
| `--end-state` | string | no | no | — | `—` | JSON file with the end-state check (overrides the request's end_state) |
| `--expect-end-state` | string | no | no | — | `—` | shortcut: the string the end-state observation must contain |
| `--runner` | choice | no | no | auto, container-gui, container, host-session, live-desktop, sandbox, dry-run | `auto` | which bridge to run on (auto = strongest real one; never the fixture) |
| `--screenshot` | flag | no | no | — | `—` | capture the end state from the runner and record its path + sha256 (named after the observed window); also implied by --screenshot-dir |
| `--screenshot-dir` | string | no | no | — | `—` | where the end-state screenshots go (default: <runtime>/act-screenshots) |
| `--allow-live-desktop` | flag | no | no | — | `false` | opt in to driving a real host display — requires --live-target naming exactly one window; without both, the live desktop stays refused |
| `--live-target` | string | no | no | — | `—` | the one window (id or exact title) a live-desktop run may touch; never the whole desktop |
| `--workdir` | string | no | no | — | `—` | working directory of the sandbox runner |
| `--timeout` | number | no | no | — | `90.0` | per-action timeout in seconds |
| `--state` | string | no | no | — | `—` | runtime directory (default: SKALDIR_HOME) |
| `--json` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "status": {
      "type": "string"
    },
    "verified": {
      "type": "boolean"
    },
    "reason": {
      "type": "string"
    },
    "goal": {
      "type": "string"
    },
    "declared": {
      "type": "integer"
    },
    "executed": {
      "type": "integer"
    },
    "actions": {
      "type": "array"
    },
    "end_state": {
      "type": [
        "object",
        "null"
      ]
    },
    "observation": {
      "type": [
        "object",
        "null"
      ]
    },
    "runner": {
      "type": "object"
    },
    "policy": {
      "type": "object"
    },
    "scope": {
      "type": "string"
    },
    "live": {
      "type": "boolean"
    },
    "record": {
      "type": "string"
    },
    "missing_capability": {
      "type": "string"
    },
    "screenshot": {
      "type": [
        "object",
        "null"
      ]
    },
    "cleanup": {
      "type": [
        "object",
        "null"
      ]
    }
  },
  "required": [
    "status",
    "verified",
    "reason",
    "actions",
    "runner"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/computer_use.py:357` — CUExecutor — act → observe → verify → audit, isolation enforced
- `code`: `src/skaldir/act_service.py:938` — run_action_list() — the declared list plus the end-state check

**Contract note (verbatim)**

> exit 2 when an action or the end state is not verified (an action the runner REFUSED is never 'verified' — the run stops instead of publishing evidence for something that did not run); exit 3 when no real bridge can carry the run; --runner dry-run declares data.scope='in-process fixture' and is never chosen by 'auto'. `runner.bridge` is `host-session` (a display this run owns, reported in data.cleanup with its display/closed/still_present) or `live-desktop` (one named window of the user's session, only with the opt-in); with --screenshot, data.screenshot carries {path, sha256, size_bytes, width, height, scope, label} of a real capture, and a capture that could not be taken is reported as absent (a warning), never as an empty file

### Group `act.subtool`

#### `act.subtool.learn` — ACT

learn a draft GUI sub-tool from one recorded session's repeated sequence

| | |
|---|---|
| Group | `act.subtool` |
| Layer | ACT |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--session` | string | yes | no | — | `—` | the recorded session id to replay |
| `--name` | string | no | no | — | `—` | name for the learned draft (default: learned_<session>) |
| `--min-repeats` | integer | no | no | — | `2` | how many times a sequence must repeat (default 2) |
| `--force` | flag | no | no | — | `false` | replace an existing learned draft |
| `--state` | string | no | no | — | `—` | runtime directory (default: SKALDIR_HOME) |
| `--json` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "learned": {
      "type": "boolean"
    },
    "subtool": {
      "type": "object"
    },
    "session_id": {
      "type": "string"
    },
    "pattern": {
      "type": "array"
    },
    "repeats": {
      "type": "integer"
    },
    "steps_seen": {
      "type": "integer"
    }
  },
  "required": [
    "learned"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/act_service.py:1517` — subtool_errors() — the stored schema is validated, never assumed

**Contract note (verbatim)**

> exit 5 when nothing is repeated to learn (or the session has no recorded computer-use events) — the reason is stated, never an invented draft; the learn happens only, the draft is NOT executed

#### `act.subtool.list` — ACT

the saved GUI sub-tools plus the five built-ins

| | |
|---|---|
| Group | `act.subtool` |
| Layer | ACT |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--state` | string | no | no | — | `—` | runtime directory (default: SKALDIR_HOME) |
| `--json` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "subtools": {
      "type": "array"
    },
    "count": {
      "type": "integer"
    },
    "directory": {
      "type": "string"
    },
    "builtin": {
      "type": "array"
    }
  },
  "required": [
    "subtools",
    "count",
    "directory"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/act_service.py:1605` — list_subtools() — saved sub-tools plus the five built-ins

#### `act.subtool.run` — ACT

run one GUI sub-tool: observe → anchors → actions → end state

| | |
|---|---|
| Group | `act.subtool` |
| Layer | ACT |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | sub-tool name |
| `--runner` | choice | no | no | auto, container-gui, container, host-session, live-desktop, sandbox, dry-run | `auto` | which bridge to run on (auto = strongest real one; never the fixture) |
| `--workdir` | string | no | no | — | `—` | working directory of the sandbox runner |
| `--app-version` | string | no | no | — | `—` | the target app's version (applicability is refused when unknown/inapplicable) |
| `--locale` | string | no | no | — | `en` | the target app's locale |
| `--timeout` | number | no | no | — | `90.0` | per-action timeout |
| `--state` | string | no | no | — | `—` | runtime directory (default: SKALDIR_HOME) |
| `--json` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "subtool": {
      "type": "string"
    },
    "origin": {
      "type": "string"
    },
    "status": {
      "type": "string"
    },
    "verified": {
      "type": "boolean"
    },
    "reason": {
      "type": "string"
    },
    "applicability": {
      "type": "object"
    },
    "precondition_check": {
      "type": "object"
    },
    "actions": {
      "type": "array"
    },
    "end_state": {
      "type": [
        "object",
        "null"
      ]
    },
    "runner": {
      "type": "object"
    },
    "missing_capability": {
      "type": "string"
    },
    "missing_anchors": {
      "type": "array"
    }
  },
  "required": [
    "subtool",
    "status",
    "verified",
    "reason"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/system_one_subtools.py:696` — check_blind_replay() — a stored sequence only runs when it is observed
- `code`: `src/skaldir/act_service.py:1819` — run_subtool() — applicability, anchors, actions, end state

**Contract note (verbatim)**

> exit 3 when a declared check has no channel on this host (the missing capability is named); exit 2 when the channel works but the anchor is not observed — nothing is executed then

#### `act.subtool.save` — ACT

store a GUI sub-tool (preconditions + anchors + actions + end state)

| | |
|---|---|
| Group | `act.subtool` |
| Layer | ACT |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--file` | string | no | no | — | `—` | sub-tool JSON file ('-' for stdin) |
| `--force` | flag | no | no | — | `false` | replace an existing sub-tool |
| `--state` | string | no | no | — | `—` | runtime directory (default: SKALDIR_HOME) |
| `--json` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "subtool": {
      "type": "object"
    },
    "stored": {
      "type": "string"
    },
    "replaced": {
      "type": "boolean"
    }
  },
  "required": [
    "subtool",
    "stored"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/act_service.py:1517` — subtool_errors() — the stored schema is validated, never assumed

**Contract note (verbatim)**

> exit 6 without --force when the name is already stored (never a silent overwrite); a declaration that does not validate exits 1 with every reason

#### `act.subtool.show` — ACT

one GUI sub-tool exactly as stored (the schema the owner specified)

| | |
|---|---|
| Group | `act.subtool` |
| Layer | ACT |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | sub-tool name |
| `--state` | string | no | no | — | `—` | runtime directory (default: SKALDIR_HOME) |
| `--json` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "name": {
      "type": "string"
    },
    "origin": {
      "type": "string"
    },
    "path": {
      "type": "string"
    },
    "spec": {
      "type": "object"
    },
    "subtool": {
      "type": "object"
    }
  },
  "required": [
    "name",
    "origin",
    "spec"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/act_service.py:1637` — load_subtool() — one stored sub-tool, with its origin

### Group `cu`

#### `cu.check` — ACT

probe the available runner isolations and bridges

| | |
|---|---|
| Group | `cu` |
| Layer | ACT |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "runners": {
      "type": "object"
    },
    "levels_reported": {
      "type": "array"
    },
    "host": {
      "type": "object"
    },
    "bridges": {
      "type": "array"
    },
    "capabilities": {
      "type": "array"
    },
    "capability_names": {
      "type": "array"
    },
    "verdict": {
      "type": "string"
    },
    "summary": {
      "type": "object"
    },
    "live_desktop": {
      "type": "boolean"
    },
    "live_desktop_optin": {
      "type": "object"
    },
    "policy": {
      "type": "object"
    }
  },
  "required": [
    "runners",
    "live_desktop",
    "bridges",
    "verdict"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/computer_use.py:464`
- `code`: `src/skaldir/act_service.py:610`
- `code`: `src/skaldir/cu_host_session.py:113`

**Contract note (verbatim)**

> the same matrix `act check` reports; `live_desktop` is the measured opt-in (false unless the switch is set AND a target is named), never a literal — no container is started here

#### `cu.demo` — ACT · **declared limitation**

deterministic channel-ordering demo (in-process fixture)

| | |
|---|---|
| Group | `cu` |
| Layer | ACT |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/computer_use.py:151`

**Contract note (verbatim)**

> exit 0 stays (tests/test_computer_use.py::test_cu_demo_cli pins it) but the envelope declares data.scope='in-process fixture' + a warning: no desktop, no window, no real action

### Group `art`

#### `art.json` — COMPOSE

the activity vocabulary as JSON (marks, frames, widths)

| | |
|---|---|
| Group | `art` |
| Layer | COMPOSE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--ascii` | flag | no | no | — | `false` | — |
| `--json` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "activities": {
      "type": "array"
    },
    "frame_ms": {
      "type": "integer"
    },
    "chat_indent": {
      "type": "integer"
    },
    "line_budget": {
      "type": "integer"
    },
    "statuses": {
      "type": "array"
    }
  },
  "required": [
    "activities",
    "line_budget"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/art.py` — the same vocabulary as data

#### `art.preview` — COMPOSE

every activity mark, its frame timing and its width

| | |
|---|---|
| Group | `art` |
| Layer | COMPOSE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--ascii` | flag | no | no | — | `false` | force the plain ASCII rendering |
| `--json` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "activities": {
      "type": "array"
    },
    "frame_ms": {
      "type": "integer"
    },
    "budget": {
      "type": "integer"
    }
  },
  "required": [
    "activities",
    "frame_ms",
    "budget"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/art.py` — the vocabulary: marks, frames, widths, ascii twin
- `test`: `tests/test_art.py` — the ASCII fallback and the width budget

**Contract note (verbatim)**

> the reference render; --ascii forces the plain ASCII mode

### Group `compose`

#### `compose.candidates` — COMPOSE

buildable chains from the real registry (+ stored composites)

| | |
|---|---|
| Group | `compose` |
| Layer | COMPOSE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `need` | string (positional) | yes | no | — | `—` | optional need text (ranks the seed steps) |
| `--tag` | string | no | yes | — | `—` | — |
| `--limit` | integer | no | no | — | `8` | — |
| `--db` | string | no | no | — | `—` | explicit tools DB (default: the runtime DB) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--project` | string | no | no | — | `—` | project path for the project id |
| `--json` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "registry": {
      "type": "string"
    },
    "steps_considered": {
      "type": "integer"
    },
    "chains": {
      "type": "array"
    },
    "composites": {
      "type": "array"
    }
  }
}
```

**Default evidence**

- `code`: `src/skaldir/compose_service.py:1599`

**Contract note (verbatim)**

> a chain candidate is a heuristic proposal, never proof of a join

#### `compose.create` — COMPOSE

store a composite definition + its registry row

| | |
|---|---|
| Group | `compose` |
| Layer | COMPOSE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--spec` | string | no | no | — | `—` | composite JSON file ('-' for stdin) |
| `--replace` | flag | no | no | — | `false` | overwrite an existing composite of that name |
| `--db` | string | no | no | — | `—` | explicit tools DB (default: the runtime DB) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--project` | string | no | no | — | `—` | project path for the project id |
| `--json` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "composite": {
      "type": "string"
    },
    "created": {
      "type": "boolean"
    },
    "path": {
      "type": "string"
    },
    "validation": {
      "type": "object"
    }
  }
}
```

**Default evidence**

- `code`: `src/skaldir/compose_service.py:686`

**Contract note (verbatim)**

> an existing name is exit 6 (conflict) unless --replace; a stored but invalid composite exits 2

#### `compose.run` — COMPOSE

execute an approved composite through the runtime's real runners

| | |
|---|---|
| Group | `compose` |
| Layer | COMPOSE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--name` | string | no | no | — | `—` | — |
| `--input` | string | no | yes | — | `—` | KEY=VALUE (repeatable); VALUE is JSON when it parses |
| `--inputs` | string | no | no | — | `—` | JSON object file ('-' for stdin) |
| `--docker-bin` | string | no | no | — | `—` | docker CLI to use for docker steps |
| `--db` | string | no | no | — | `—` | explicit tools DB (default: the runtime DB) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--project` | string | no | no | — | `—` | project path for the project id |
| `--json` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "composite": {
      "type": "string"
    },
    "status": {
      "type": "string"
    },
    "verified": {
      "type": "boolean"
    },
    "steps": {
      "type": "array"
    },
    "outputs": {
      "type": "object"
    },
    "missing_kinds": {
      "type": "array"
    }
  }
}
```

**Default evidence**

- `code`: `src/skaldir/compose_service.py:1355`

**Contract note (verbatim)**

> a step kind without a runner is exit 3 and names the kind; a step that fails is exit 2; only a held end-state exits 0

#### `compose.show` — COMPOSE

one composite (or all) with per-step executability

| | |
|---|---|
| Group | `compose` |
| Layer | COMPOSE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--name` | string | no | no | — | `—` | — |
| `--db` | string | no | no | — | `—` | explicit tools DB (default: the runtime DB) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--project` | string | no | no | — | `—` | project path for the project id |
| `--json` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "composite": {
      "type": "string"
    },
    "document": {
      "type": "object"
    },
    "validation": {
      "type": "object"
    },
    "registry": {
      "type": "object"
    }
  }
}
```

**Default evidence**

- `code`: `src/skaldir/compose_service.py:807`

#### `compose.validate` — COMPOSE

validate a composite; --approve flips the registry row when clean

| | |
|---|---|
| Group | `compose` |
| Layer | COMPOSE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--name` | string | no | no | — | `—` | — |
| `--approve` | flag | no | no | — | `false` | — |
| `--force` | flag | no | no | — | `false` | approve despite validation problems (recorded, never hidden) |
| `--db` | string | no | no | — | `—` | explicit tools DB (default: the runtime DB) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--project` | string | no | no | — | `—` | project path for the project id |
| `--json` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "composite": {
      "type": "string"
    },
    "validation": {
      "type": "object"
    },
    "approved": {
      "type": "boolean"
    }
  }
}
```

**Default evidence**

- `code`: `src/skaldir/compose_service.py:807`

**Contract note (verbatim)**

> --force records the override; it never makes a missing runner exist

### Group `skills`

#### `skills.demote` — COMPOSE

demote for review

| | |
|---|---|
| Group | `skills` |
| Layer | COMPOSE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | — |
| `--db` | string | no | no | — | `/home/willy/.hermes/godsv/memory.db` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/skills.py`

#### `skills.export` — COMPOSE

write the SKILL.md out

| | |
|---|---|
| Group | `skills` |
| Layer | COMPOSE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | — |
| `--db` | string | no | no | — | `/home/willy/.hermes/godsv/memory.db` | — |
| `--out` | string | no | no | — | `.` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/skills.py`

#### `skills.install` — COMPOSE

install a builtin skill or from a GitHub repo

| | |
|---|---|
| Group | `skills` |
| Layer | COMPOSE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `target` | string (positional) | yes | no | — | `—` | builtin name \| owner/repo \| github URL |
| `--db` | string | no | no | — | `/home/willy/.hermes/godsv/memory.db` | — |
| `--skills-dir` | string | no | no | — | `—` | install directory (default: skills/ under the runtime dir) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/skill_install.py`

**Contract note (verbatim)**

> builtin names are package data (no network); GitHub installs fetch codeload tar.gz/HEAD, only md/json/yaml/toml, 256KB cap, refuse honestly otherwise

#### `skills.list` — COMPOSE

list skill candidates

| | |
|---|---|
| Group | `skills` |
| Layer | COMPOSE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--db` | string | no | no | — | `/home/willy/.hermes/godsv/memory.db` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/skills.py`

#### `skills.mine` — COMPOSE

mine repeated tool patterns

| | |
|---|---|
| Group | `skills` |
| Layer | COMPOSE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--events` | string | no | no | — | `/home/willy/.hermes/godsv/events.db` | — |
| `--db` | string | no | no | — | `/home/willy/.hermes/godsv/memory.db` | — |
| `--min-occurrences` | integer | no | no | — | `3` | — |
| `--min-sessions` | integer | no | no | — | `2` | — |
| `--apply` | flag | no | no | — | `false` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/skills.py`

#### `skills.promote` — COMPOSE

promote after validation

| | |
|---|---|
| Group | `skills` |
| Layer | COMPOSE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | — |
| `--db` | string | no | no | — | `/home/willy/.hermes/godsv/memory.db` | — |
| `--events` | string | no | no | — | `/home/willy/.hermes/godsv/events.db` | — |
| `--force` | flag | no | no | — | `false` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/skills.py`

**Contract note (verbatim)**

> a validation refusal exits 1 (legacy), not 2: tests/test_skills.py::test_promote_refuses_without_events pins 1

#### `skills.show` — COMPOSE

print the SKILL.md draft

| | |
|---|---|
| Group | `skills` |
| Layer | COMPOSE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | — |
| `--db` | string | no | no | — | `/home/willy/.hermes/godsv/memory.db` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/skills.py`

#### `skills.validate` — COMPOSE

re-verify a candidate

| | |
|---|---|
| Group | `skills` |
| Layer | COMPOSE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | — |
| `--db` | string | no | no | — | `/home/willy/.hermes/godsv/memory.db` | — |
| `--events` | string | no | no | — | `/home/willy/.hermes/godsv/events.db` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/skills.py`

### Group `coding.model`

#### `coding.model.clear` — ROUTE

drop the session override so the environment/config layer wins again

| | |
|---|---|
| Group | `coding.model` |
| Layer | ROUTE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--session` | string | no | no | — | `—` | the session id |
| `--workspace` | string | no | no | — | `—` | the directory to work in (default: cwd) |
| `--runtime` | string | no | no | — | `—` | the runtime dir (default: the registry's) |
| `--state` | string | no | no | — | `—` | the state dir (default <runtime>/coding) |
| `--text` | flag | no | no | — | `false` | human-readable output on stderr instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/coding_features.py` — coding model clear

#### `coding.model.set` — ROUTE

set the model/reasoning effort for THIS session (mid-session switch)

| | |
|---|---|
| Group | `coding.model` |
| Layer | ROUTE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--model` | string | no | no | — | `—` | the model name |
| `--effort` | string | no | no | — | `—` | reasoning effort: low\|medium\|high |
| `--provider` | string | no | no | — | `—` | the provider name |
| `--session` | string | no | no | — | `—` | the session id |
| `--workspace` | string | no | no | — | `—` | the directory to work in (default: cwd) |
| `--runtime` | string | no | no | — | `—` | the runtime dir (default: the registry's) |
| `--state` | string | no | no | — | `—` | the state dir (default <runtime>/coding) |
| `--text` | flag | no | no | — | `false` | human-readable output on stderr instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/coding_features.py` — coding model set

**Contract note (verbatim)**

> an effort outside low|medium|high is a usage error (exit 1), never coerced

#### `coding.model.show` — ROUTE

the effective model/reasoning effort with the layer that supplied it

| | |
|---|---|
| Group | `coding.model` |
| Layer | ROUTE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--session` | string | no | no | — | `—` | the session id |
| `--workspace` | string | no | no | — | `—` | the directory to work in (default: cwd) |
| `--runtime` | string | no | no | — | `—` | the runtime dir (default: the registry's) |
| `--state` | string | no | no | — | `—` | the state dir (default <runtime>/coding) |
| `--text` | flag | no | no | — | `false` | human-readable output on stderr instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/coding_features.py` — coding model show

**Contract note (verbatim)**

> read-only; layers are listed in precedence order

### Group `jev`

#### `jev.decide` — ROUTE · **declared limitation**

one decision: request JSON in, exactly one advisory decision out

| | |
|---|---|
| Group | `jev` |
| Layer | ROUTE |
| Runtime effect | mutates state |
| Credentials | required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `4` (NEEDS_CREDENTIALS), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--kind` | choice | no | no | memory_injection, tool_routing, capability_ranking, context_compaction, voice_triage, adversarial_test_planning, semantic_verification | `—` | — |
| `--request` | string | no | no | — | `—` | request JSON file ('-' for stdin) |
| `--transcript` | string | no | no | — | `—` | alias for --request: the transcript-derived request document (compaction) |
| `--apply` | flag | no | no | — | `false` | rebuild the transcript from the decisions (owned by the compaction port) |
| `--out` | string | no | no | — | `—` | also write the decision JSON here |
| `--provider` | choice | no | no | settings, disabled, llm_system_one_adapter, typesafe_jev | `settings` | which provider answers: 'settings' (default) = the runtime's own flags, or the configured decision block when they name none; 'typesafe_jev' = the System One endpoint (needs an authorized TypeSafe credential); 'llm_system_one_adapter' = an LLM standing in for Jev; 'disabled' = refuse, by design |
| `--shadow` | flag | no | no | — | `false` | — |
| `--dry-run` | flag | no | no | — | `false` | do not append to the ledger |
| `--state` | string | no | no | — | `.godsv` | — |
| `--runtime` | string | no | no | — | `—` | the runtime state directory holding settings.yaml (default: the product's) |
| `--config` | string | no | no | — | `—` | the provider config file to resolve (default: the product's) |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true,
  "properties": {
    "decision_id": {
      "type": "string"
    },
    "kind": {
      "type": "string"
    },
    "run_id": {
      "type": "string"
    },
    "goal_id": {
      "type": "string"
    },
    "goal_revision": {
      "type": "integer"
    },
    "authority": {
      "type": "string",
      "enum": [
        "advisory"
      ]
    },
    "status": {
      "type": "string",
      "enum": [
        "ok",
        "blocked",
        "refused"
      ]
    },
    "provider_mode": {
      "type": "string"
    },
    "live_jev": {
      "type": "boolean"
    },
    "question_pack_id": {
      "type": "string"
    },
    "question_pack_version": {
      "type": "string"
    },
    "confidence": {
      "type": "number"
    },
    "answer": {
      "type": "object"
    },
    "refusal_reason": {
      "type": "string"
    },
    "fallback": {
      "type": "string"
    },
    "caution_level": {
      "type": "integer"
    },
    "latency_ms": {
      "type": "integer"
    },
    "attempts": {
      "type": "integer"
    },
    "evidence_refs": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "created_at": {
      "type": "number"
    }
  },
  "required": [
    "decision_id",
    "kind",
    "status",
    "provider_mode",
    "authority",
    "fallback"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/jev/decide.py:1` — one provider edge + the decision
- `code`: `src/skaldir/jev/rules.py` — R1-R4 re-checked at construction
- `code`: `src/skaldir/jev/ledger.py` — the append-only decision ledger

**Contract note (verbatim)**

> the request arrives on stdin or via --request/--transcript FILE; exit 3 = no advisory (provider disabled/stalled), exit 4 = a live-capable provider without an authorized credential (needs_credentials), exit 7 = malformed request, kind mismatch or a hard-rule violation. Every decision is appended to --state/jev_decisions.jsonl unless --dry-run; `--apply` is refused with exit 1 because rebuilding a transcript belongs to the compaction port (src/skaldir/jevcomp). Never a goal PASS, never a permission, never a security exception

#### `jev.history` — ROUTE

the recorded decisions (what Jev decided, which fallback fired)

| | |
|---|---|
| Group | `jev` |
| Layer | ROUTE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--run` | string | no | no | — | `—` | — |
| `--limit` | integer | no | no | — | `20` | — |
| `--state` | string | no | no | — | `.godsv` | — |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "decisions": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": true
      }
    },
    "count": {
      "type": "integer"
    },
    "ledger": {
      "type": "string"
    }
  },
  "required": [
    "decisions",
    "count",
    "ledger"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/jev/ledger.py` — the decision ledger read back

**Contract note (verbatim)**

> an absent ledger is an empty list with exit 0, never a 5 (not_found): the state dir may not have been written yet. --state defaults to the relative '.godsv', so the ledger path in the payload is relative to the cwd it ran in

#### `jev.kinds` — ROUTE

the six decision kinds with their pack ref, risk class and flag

| | |
|---|---|
| Group | `jev` |
| Layer | ROUTE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "kinds": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "kind": {
            "type": "string"
          },
          "pack_ref": {
            "type": "string"
          },
          "owner": {
            "type": "string"
          },
          "risk_class": {
            "type": "string"
          },
          "applicable_states": {
            "type": "array",
            "items": {
              "type": "string"
            }
          },
          "flag": {
            "type": "string"
          },
          "answer_model": {
            "type": "string"
          },
          "request_model": {
            "type": "string"
          }
        },
        "required": [
          "kind",
          "pack_ref",
          "answer_model"
        ]
      }
    },
    "count": {
      "type": "integer"
    }
  },
  "required": [
    "kinds",
    "count"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/jev/kinds.py` — kind -> pack map
- `code`: `src/skaldir/jev/contracts.py` — JEV_REQUESTS/JEV_ANSWERS

**Contract note (verbatim)**

> read-only view; the same rows `skaldir jev selftest` probes

#### `jev.packs` — ROUTE

the question-registry manifest (substrate packs + the five Jev packs)

| | |
|---|---|
| Group | `jev` |
| Layer | ROUTE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "packs": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "ref": {
            "type": "string"
          },
          "id": {
            "type": "string"
          },
          "version": {
            "type": "string"
          },
          "owner": {
            "type": "string"
          },
          "risk_class": {
            "type": "string"
          },
          "purpose": {
            "type": "string"
          },
          "fallback_policy": {
            "type": "string"
          },
          "calibration_dataset_version": {
            "type": "string"
          },
          "questions": {
            "type": "array",
            "items": {
              "type": "string"
            }
          }
        },
        "required": [
          "ref",
          "id",
          "owner",
          "questions"
        ]
      }
    },
    "count": {
      "type": "integer"
    }
  },
  "required": [
    "packs",
    "count"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/jev/packs.py` — pack definitions
- `code`: `src/skaldir/system_one_questions.py` — the registry the view is built from (voice_triage_v1 is reused)

**Contract note (verbatim)**

> 12 packs live: the substrate's registry plus memory_injection_v1, routing_v1, capability_ranking_v1, compaction_v1, adversarial_test_v1

#### `jev.selftest` — ROUTE · **declared limitation**

the R1-R4 table plus per-kind reachability (exit 1 if untested)

| | |
|---|---|
| Group | `jev` |
| Layer | ROUTE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "selftest": {
      "type": "object",
      "properties": {
        "ok": {
          "type": "boolean"
        },
        "reachable": {
          "type": "boolean"
        },
        "blocked_path_ok": {
          "type": "boolean"
        },
        "kinds": {
          "type": "array"
        },
        "rules": {
          "type": "array"
        },
        "untested_rules": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "rule_count": {
          "type": "integer"
        },
        "contract_scan": {
          "type": "object"
        },
        "packs": {
          "type": "array"
        },
        "registry_refs": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "exit_code": {
          "type": "integer"
        }
      },
      "required": [
        "ok",
        "kinds",
        "rules",
        "untested_rules",
        "exit_code"
      ]
    }
  },
  "required": [
    "selftest"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/jev/selftest.py` — R1-R4 table + kind reachability
- `test`: `tests/test_jev_facade_rules.py` — the pinned test for every hard rule the report names

**Contract note (verbatim)**

> exit 1 = a hard rule lost its tests or a kind is not reachable — the façade's own vocabulary, not the designed 2 (verification_failed): tests/test_jev_facade_cli.py pins 1. A kind that is not wired reports reachable=false with its blocked_path, never a fabricated answer. Wired, the report is nested under data.selftest; standalone (`python -m skaldir.jev`) it is the top level

### Group `coding.events`

#### `coding.events.run` — SUPERVISE

run one command and emit run.started / item.started / item.updated / item.completed / run.completed as JSONL

| | |
|---|---|
| Group | `coding.events` |
| Layer | SUPERVISE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `command` | string (positional) | yes | no | — | `—` | the command to run (after --) |
| `--jsonl` | string | no | no | — | `—` | write the stream to this file (JSONL) |
| `--progress-every` | integer | no | no | — | `1` | emit an update every N lines |
| `--timeout` | number | no | no | — | `120.0` | kill the command after N seconds |
| `--run-id` | string | no | no | — | `—` | the run id the events carry |
| `--plan-guard` | flag | no | no | — | `false` | decline a mutating command while plan mode is open |
| `--workspace` | string | no | no | — | `—` | the directory to work in (default: cwd) |
| `--runtime` | string | no | no | — | `—` | the runtime dir (default: the registry's) |
| `--state` | string | no | no | — | `—` | the state dir (default <runtime>/coding) |
| `--text` | flag | no | no | — | `false` | human-readable output on stderr instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "status": {
      "type": "string"
    },
    "exit_code": {
      "type": [
        "integer",
        "null"
      ]
    },
    "lines": {
      "type": "integer"
    },
    "events": {
      "type": "integer"
    }
  },
  "required": [
    "status",
    "lines",
    "events"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/coding_features.py` — coding events run

**Contract note (verbatim)**

> the command's own exit code travels in data; the CLI reports 2 for a failed run and 3 for a declined one (--plan-guard); an item is never both

#### `coding.events.show` — SUPERVISE

validate one stream file and count its items per terminal status

| | |
|---|---|
| Group | `coding.events` |
| Layer | SUPERVISE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `stream` | string (positional) | yes | no | — | `—` | the JSONL stream file |
| `--text` | flag | no | no | — | `false` | human-readable output on stderr instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/coding_features.py` — coding events show

**Contract note (verbatim)**

> the invariant checked is one terminal event per started item; an orphan is reported, never ignored

### Group `voice`

#### `voice.check` — SUPERVISE

one pass over a stored trajectory: six finding categories, each with evidence

| | |
|---|---|
| Group | `voice` |
| Layer | SUPERVISE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME) |
| `--events-db` | string | no | no | — | `—` | event store (default: <runtime>/events.db) |
| `--session` | string | no | no | — | `—` | session id (default: the newest one) |
| `--step` | integer | no | no | — | `0` | read the trajectory up to this step (default: all of it) |
| `--window` | integer | no | no | — | `12` | how many steps the health reading looks back |
| `--goal` | string | no | no | — | `—` | the goal to judge drift against (default: the recorded one) |
| `--no-recorded-goal` | flag | no | no | — | `false` | do not use the stored contract/goal for the drift reading |
| `--no-incidents` | flag | no | no | — | `false` | do not match errors against the incident memory |
| `--no-jev` | flag | no | no | — | `false` | skip the Jev advisory (deterministic reading only) |
| `--provider` | string | no | no | — | `—` | override the System-One provider Jev may use (default: this runtime's system_one.provider — 'disabled' when it names none) |
| `--pact` | string | no | no | — | `—` | pact file (YAML/JSON) for scope checks |
| `--limit` | integer | no | no | — | `200` | max findings carried in the payload (0 = all) |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/monitor.py`
- `code`: `src/skaldir/patterns.py`
- `code`: `src/skaldir/guard.py`
- `code`: `src/skaldir/voice.py`

#### `voice.explain` — SUPERVISE

why the supervisor would or would not intervene for a trajectory

| | |
|---|---|
| Group | `voice` |
| Layer | SUPERVISE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME) |
| `--events-db` | string | no | no | — | `—` | event store (default: <runtime>/events.db) |
| `--session` | string | no | no | — | `—` | session id (default: the newest one) |
| `--step` | integer | no | no | — | `0` | read the trajectory up to this step (default: all of it) |
| `--window` | integer | no | no | — | `12` | how many steps the health reading looks back |
| `--goal` | string | no | no | — | `—` | the goal to judge drift against (default: the recorded one) |
| `--no-recorded-goal` | flag | no | no | — | `false` | do not use the stored contract/goal for the drift reading |
| `--no-incidents` | flag | no | no | — | `false` | do not match errors against the incident memory |
| `--no-jev` | flag | no | no | — | `false` | skip the Jev advisory (deterministic reading only) |
| `--provider` | string | no | no | — | `—` | override the System-One provider Jev may use (default: this runtime's system_one.provider — 'disabled' when it names none) |
| `--pact` | string | no | no | — | `—` | pact file (YAML/JSON) for scope checks |
| `--limit` | integer | no | no | — | `200` | max findings carried in the payload (0 = all) |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--record-advisory` | flag | no | no | — | `false` | also write the Jev advisory into the runtime's event store as a jev_decision row (default: this read-only verb never writes into the store it inspects) |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/pause.py`
- `code`: `src/skaldir/voice.py`

#### `voice.status` — SUPERVISE

what the supervisor is watching: sessions, leases, runs, policy thresholds

| | |
|---|---|
| Group | `voice` |
| Layer | SUPERVISE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | — |
| `--limit` | integer | no | no | — | `20` | sessions listed |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/voice_service.py`
- `code`: `src/skaldir/events.py`
- `code`: `src/skaldir/pause.py`

**Contract note (verbatim)**

> status reads the live stores read-only

#### `voice.triage` — SUPERVISE

should the Voice be woken at all? deterministic pre-filter + Jev advisory

| | |
|---|---|
| Group | `voice` |
| Layer | SUPERVISE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME) |
| `--events-db` | string | no | no | — | `—` | event store (default: <runtime>/events.db) |
| `--session` | string | no | no | — | `—` | session id (default: the newest one) |
| `--step` | integer | no | no | — | `0` | read the trajectory up to this step (default: all of it) |
| `--window` | integer | no | no | — | `12` | how many steps the health reading looks back |
| `--goal` | string | no | no | — | `—` | the goal to judge drift against (default: the recorded one) |
| `--no-recorded-goal` | flag | no | no | — | `false` | do not use the stored contract/goal for the drift reading |
| `--no-incidents` | flag | no | no | — | `false` | do not match errors against the incident memory |
| `--no-jev` | flag | no | no | — | `false` | skip the Jev advisory (deterministic reading only) |
| `--provider` | string | no | no | — | `—` | override the System-One provider Jev may use (default: this runtime's system_one.provider — 'disabled' when it names none) |
| `--pact` | string | no | no | — | `—` | pact file (YAML/JSON) for scope checks |
| `--limit` | integer | no | no | — | `200` | max findings carried in the payload (0 = all) |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--record-advisory` | flag | no | no | — | `false` | also write the Jev advisory into the runtime's event store as a jev_decision row (default: this read-only verb never writes into the store it inspects) |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/voice_service.py`
- `code`: `src/skaldir/jev/decide.py`
- `code`: `src/skaldir/pause.py`

### Group `approval`

#### `approval.allow` — ENFORCE

answer a prompt with yes: turn scope, session scope, or a rule

| | |
|---|---|
| Group | `approval` |
| Layer | ENFORCE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME) |
| `--profile` | string | no | no | — | `default` | profile name (default: default) |
| `--workspace` | string | no | no | — | `—` | workspace the profile is rooted at |
| `--json` | flag | no | no | — | `false` | — |
| `--session` | string | no | no | — | `—` | session id grants are filed under (default: default) |
| `--policy` | choice | no | no | on_request, unless_trusted, never | `on_request` | on_request \| unless_trusted (persisted rules only) \| never (no prompt is possible) |
| `--headless` | flag | no | no | — | `false` | no interactive user (the default): a prompt returns blocked, never a hang |
| `--interactive` | flag | no | no | — | `false` | an interactive user is present and would be shown the prompt |
| `--no-persist` | flag | no | no | — | `false` | do not write any state |
| `command` | string (positional) | yes | no | — | `—` | the command or prefix being approved (options come first) |
| `--scope` | choice | no | no | turn, session | `turn` | turn = allow once (default); session = for this session id only |
| `--rule` | flag | no | no | — | `false` | persist a prefix rule instead of a grant |
| `--reason` | string | no | no | — | `—` | why it is allowed (recorded) |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "command": {
      "type": "string"
    },
    "decision": {
      "type": "string",
      "enum": [
        "allow",
        "prompt",
        "forbid"
      ]
    },
    "reason_kind": {
      "type": "string"
    },
    "reason": {
      "type": "string"
    },
    "rule_id": {
      "type": "string"
    },
    "rule_text": {
      "type": "string"
    },
    "blocked": {
      "type": "boolean"
    },
    "exit_code": {
      "type": "integer"
    },
    "would_allow": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "profile": {
      "type": "object"
    }
  },
  "required": [
    "command",
    "decision",
    "blocked",
    "exit_code"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/approval.py` — ApprovalEngine.approve
- `file`: `<runtime>/approval/events.jsonl` — the recorded decision

**Contract note (verbatim)**

> A banned prefix is refused with exit 3 in every scope — that is what the banned list means. A session grant is persisted per session id and is not inherited by a new one.

#### `approval.check` — ENFORCE · **declared limitation**

would this command be allowed, prompted or forbidden?

| | |
|---|---|
| Group | `approval` |
| Layer | ENFORCE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME) |
| `--profile` | string | no | no | — | `default` | profile name (default: default) |
| `--workspace` | string | no | no | — | `—` | workspace the profile is rooted at |
| `--json` | flag | no | no | — | `false` | — |
| `--session` | string | no | no | — | `—` | session id grants are filed under (default: default) |
| `--policy` | choice | no | no | on_request, unless_trusted, never | `on_request` | on_request \| unless_trusted (persisted rules only) \| never (no prompt is possible) |
| `--headless` | flag | no | no | — | `false` | no interactive user (the default): a prompt returns blocked, never a hang |
| `--interactive` | flag | no | no | — | `false` | an interactive user is present and would be shown the prompt |
| `--no-persist` | flag | no | no | — | `false` | do not write any state |
| `command` | string (positional) | yes | no | — | `—` | the command to judge; nothing runs (options come first) |
| `--dry-run` | flag | no | no | — | `false` | answer 'allowed / prompted / forbidden' without running anything (the default) |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "command": {
      "type": "string"
    },
    "decision": {
      "type": "string",
      "enum": [
        "allow",
        "prompt",
        "forbid"
      ]
    },
    "reason_kind": {
      "type": "string"
    },
    "reason": {
      "type": "string"
    },
    "rule_id": {
      "type": "string"
    },
    "rule_text": {
      "type": "string"
    },
    "blocked": {
      "type": "boolean"
    },
    "exit_code": {
      "type": "integer"
    },
    "would_allow": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "profile": {
      "type": "object"
    }
  },
  "required": [
    "command",
    "decision",
    "blocked",
    "exit_code"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/approval.py` — ApprovalEngine.judge
- `code`: `src/skaldir/approval.py` — decompose

**Contract note (verbatim)**

> --dry-run: nothing is executed. Exit 0 when it would be allowed, 3 when it would be prompted or forbidden — with the deciding rule and what the user would need to allow.

#### `approval.deny` — ENFORCE

answer a prompt with no, plus a reason the agent can act on

| | |
|---|---|
| Group | `approval` |
| Layer | ENFORCE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME) |
| `--profile` | string | no | no | — | `default` | profile name (default: default) |
| `--workspace` | string | no | no | — | `—` | workspace the profile is rooted at |
| `--json` | flag | no | no | — | `false` | — |
| `--session` | string | no | no | — | `—` | session id grants are filed under (default: default) |
| `--policy` | choice | no | no | on_request, unless_trusted, never | `on_request` | on_request \| unless_trusted (persisted rules only) \| never (no prompt is possible) |
| `--headless` | flag | no | no | — | `false` | no interactive user (the default): a prompt returns blocked, never a hang |
| `--interactive` | flag | no | no | — | `false` | an interactive user is present and would be shown the prompt |
| `--no-persist` | flag | no | no | — | `false` | do not write any state |
| `command` | string (positional) | yes | no | — | `—` | the command being denied (options come first) |
| `--reason` | string | no | no | — | `—` | what the agent should do instead |
| `--rule` | flag | no | no | — | `false` | persist a forbid rule |
| `--abort` | flag | no | no | — | `false` | stop the turn instead of continuing |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "command": {
      "type": "string"
    },
    "decision": {
      "type": "string",
      "enum": [
        "allow",
        "prompt",
        "forbid"
      ]
    },
    "reason_kind": {
      "type": "string"
    },
    "reason": {
      "type": "string"
    },
    "rule_id": {
      "type": "string"
    },
    "rule_text": {
      "type": "string"
    },
    "blocked": {
      "type": "boolean"
    },
    "exit_code": {
      "type": "integer"
    },
    "would_allow": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "profile": {
      "type": "object"
    }
  },
  "required": [
    "command",
    "decision",
    "blocked",
    "exit_code"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/approval.py` — ApprovalEngine.deny
- `file`: `<runtime>/approval/events.jsonl` — the recorded denial

**Contract note (verbatim)**

> Denied continues the turn with the reason (deny-and-teach-back); --abort stops the turn instead. --rule persists a forbid rule.

#### `approval.list` — ENFORCE

rules, banned prefixes, live grants and the evidence tail

| | |
|---|---|
| Group | `approval` |
| Layer | ENFORCE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME) |
| `--profile` | string | no | no | — | `default` | profile name (default: default) |
| `--workspace` | string | no | no | — | `—` | workspace the profile is rooted at |
| `--json` | flag | no | no | — | `false` | — |
| `--session` | string | no | no | — | `—` | session id grants are filed under (default: default) |
| `--policy` | choice | no | no | on_request, unless_trusted, never | `on_request` | on_request \| unless_trusted (persisted rules only) \| never (no prompt is possible) |
| `--headless` | flag | no | no | — | `false` | no interactive user (the default): a prompt returns blocked, never a hang |
| `--interactive` | flag | no | no | — | `false` | an interactive user is present and would be shown the prompt |
| `--no-persist` | flag | no | no | — | `false` | do not write any state |
| `--tail` | integer | no | no | — | `10` | how many evidence events to show |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "policy": {
      "type": "object"
    },
    "grants": {
      "type": "object"
    },
    "events": {
      "type": "object"
    }
  },
  "required": [
    "policy",
    "grants"
  ]
}
```

**Default evidence**

- `file`: `<runtime>/approval/policy.json` — the persisted prefix rules
- `file`: `<runtime>/approval/grants/<session>.json` — session grants
- `file`: `<runtime>/approval/events.jsonl` — the evidence ledger

**Contract note (verbatim)**

> Read-only; the banned list is printed in full so it cannot be forgotten.

#### `approval.run` — ENFORCE

judge the command, then run it inside the profile when allowed

| | |
|---|---|
| Group | `approval` |
| Layer | ENFORCE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME) |
| `--profile` | string | no | no | — | `default` | profile name (default: default) |
| `--workspace` | string | no | no | — | `—` | workspace the profile is rooted at |
| `--json` | flag | no | no | — | `false` | — |
| `--session` | string | no | no | — | `—` | session id grants are filed under (default: default) |
| `--policy` | choice | no | no | on_request, unless_trusted, never | `on_request` | on_request \| unless_trusted (persisted rules only) \| never (no prompt is possible) |
| `--headless` | flag | no | no | — | `false` | no interactive user (the default): a prompt returns blocked, never a hang |
| `--interactive` | flag | no | no | — | `false` | an interactive user is present and would be shown the prompt |
| `--no-persist` | flag | no | no | — | `false` | do not write any state |
| `command` | string (positional) | yes | no | — | `—` | the command to judge and run (options come first) |
| `--dry-run` | flag | no | no | — | `false` | judge only, never run |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "command": {
      "type": "string"
    },
    "decision": {
      "type": "string",
      "enum": [
        "allow",
        "prompt",
        "forbid"
      ]
    },
    "reason_kind": {
      "type": "string"
    },
    "reason": {
      "type": "string"
    },
    "rule_id": {
      "type": "string"
    },
    "rule_text": {
      "type": "string"
    },
    "blocked": {
      "type": "boolean"
    },
    "exit_code": {
      "type": "integer"
    },
    "would_allow": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "profile": {
      "type": "object"
    }
  },
  "required": [
    "command",
    "decision",
    "blocked",
    "exit_code"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/approval.py` — ApprovalEngine.decide
- `code`: `src/skaldir/permission_profile.py` — run_in_sandbox

**Contract note (verbatim)**

> The enforcement path: exit 3 blocked (no user / forbidden / no sandbox), 2 when the command ran and returned non-zero, 0 when it ran clean. The argv that ran carries the same profile hash as the prompt block.

### Group `completion`

#### `completion.begin` — ENFORCE · **declared limitation**

record a new goal (no execution loop is wired yet)

| | |
|---|---|
| Group | `completion` |
| Layer | ENFORCE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--goal` | string | yes | no | — | `—` | — |
| `--criterion` | string | no | yes | — | `[]` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "goal": {
      "type": "string"
    },
    "state": {
      "type": "string"
    },
    "criteria": {
      "type": "array"
    },
    "started_at": {
      "type": "string"
    },
    "loop_wired": {
      "type": "boolean"
    }
  },
  "required": [
    "goal",
    "state",
    "loop_wired"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/completion.py:541`

**Contract note (verbatim)**

> exit 3 while no loop is wired: the goal is recorded, nothing continues after the command returns

#### `completion.history` — ENFORCE

the completion timeline

| | |
|---|---|
| Group | `completion` |
| Layer | ENFORCE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--limit` | integer | no | no | — | `40` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "rows": {
      "type": "array"
    },
    "count": {
      "type": "integer"
    }
  },
  "required": [
    "rows",
    "count"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/completion.py:966`

#### `completion.override` — ENFORCE

user override (never VERIFIED_COMPLETE)

| | |
|---|---|
| Group | `completion` |
| Layer | ENFORCE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--reason` | string | yes | no | — | `—` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/completion.py:496`

#### `completion.status` — ENFORCE

goal state and metrics

| | |
|---|---|
| Group | `completion` |
| Layer | ENFORCE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "state": {
      "type": "string"
    },
    "goal": {
      "type": [
        "string",
        "null"
      ]
    },
    "revision": {
      "type": "integer"
    },
    "terminal": {
      "type": "boolean"
    },
    "verified": {
      "type": "integer"
    },
    "total": {
      "type": "integer"
    },
    "last_verdict": {
      "type": [
        "object",
        "null"
      ]
    },
    "metrics": {
      "type": "object"
    }
  },
  "required": [
    "state",
    "verified",
    "total",
    "metrics"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/completion.py:385`

**Contract note (verbatim)**

> state/goal/last_verdict stay identical to the text output

### Group `permission`

#### `permission.explain` — ENFORCE

per-field source, the exact argv, and both hashes side by side

| | |
|---|---|
| Group | `permission` |
| Layer | ENFORCE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME) |
| `--profile` | string | no | no | — | `default` | profile name (default: default) |
| `--workspace` | string | no | no | — | `—` | workspace the profile is rooted at |
| `--json` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "type": "object"
    },
    "identity_sha256": {
      "type": "string"
    },
    "hash_in_argv": {
      "type": "string"
    },
    "hash_in_prompt": {
      "type": "string"
    },
    "hashes_agree": {
      "type": "boolean"
    },
    "enforcement": {
      "type": "array",
      "items": {
        "type": "object"
      }
    },
    "unenforced": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "sandbox_argv": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "prompt_block": {
      "type": "string"
    },
    "sources": {
      "type": "object"
    }
  },
  "required": [
    "profile",
    "identity_sha256",
    "hashes_agree",
    "prompt_block"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/permission_profile.py` — enforcement_matrix / sandbox_argv / prompt_block
- `code`: `src/skaldir/approval_cli.py` — this handler

**Contract note (verbatim)**

> Also probes the sandbox for real (bwrap/podman) and names every field that is *not* enforced on this host instead of implying that it is.

#### `permission.set` — ENFORCE

change one profile field and report the identity hash it moved

| | |
|---|---|
| Group | `permission` |
| Layer | ENFORCE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME) |
| `--profile` | string | no | no | — | `default` | profile name (default: default) |
| `--workspace` | string | no | no | — | `—` | workspace the profile is rooted at |
| `--json` | flag | no | no | — | `false` | — |
| `--set` | string | no | yes | — | `[]` | repeatable; append with KEY+=VALUE. keys: name, workspace, network, read-policy, writable, readable, protected, write-deny-glob, allow-command, timeout, output-max-bytes, max-memory-bytes, max-processes, sandbox-backend |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "type": "object"
    },
    "identity_sha256": {
      "type": "string"
    },
    "hash_in_argv": {
      "type": "string"
    },
    "hash_in_prompt": {
      "type": "string"
    },
    "hashes_agree": {
      "type": "boolean"
    },
    "enforcement": {
      "type": "array",
      "items": {
        "type": "object"
      }
    },
    "unenforced": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "sandbox_argv": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "prompt_block": {
      "type": "string"
    },
    "sources": {
      "type": "object"
    }
  },
  "required": [
    "profile",
    "identity_sha256",
    "hashes_agree",
    "prompt_block"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/permission_profile.py` — save_profile
- `file`: `<runtime>/permissions/<name>.json` — the stored profile

**Contract note (verbatim)**

> Writes the named profile. A change that does not move the hash (an identical value) is reported as a warning, never as a change.

#### `permission.show` — ENFORCE

the effective permission profile, its hash and the prompt block

| | |
|---|---|
| Group | `permission` |
| Layer | ENFORCE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME) |
| `--profile` | string | no | no | — | `default` | profile name (default: default) |
| `--workspace` | string | no | no | — | `—` | workspace the profile is rooted at |
| `--json` | flag | no | no | — | `false` | — |
| `--probe` | flag | no | no | — | `false` | also probe the sandbox for real |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "type": "object"
    },
    "identity_sha256": {
      "type": "string"
    },
    "hash_in_argv": {
      "type": "string"
    },
    "hash_in_prompt": {
      "type": "string"
    },
    "hashes_agree": {
      "type": "boolean"
    },
    "enforcement": {
      "type": "array",
      "items": {
        "type": "object"
      }
    },
    "unenforced": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "sandbox_argv": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "prompt_block": {
      "type": "string"
    },
    "sources": {
      "type": "object"
    }
  },
  "required": [
    "profile",
    "identity_sha256",
    "hashes_agree",
    "prompt_block"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/permission_profile.py` — the one profile object
- `code`: `src/skaldir/approval_cli.py` — this handler

**Contract note (verbatim)**

> Read-only. Prints the fields the sandbox argv is built from and the block the agent is told, both carrying the same identity_sha256.

### Group `security`

#### `security.audit` — ENFORCE

run the declared defect-class rules over the tree and report every finding with its fix

| | |
|---|---|
| Group | `security` |
| Layer | ENFORCE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--path` | string | no | yes | — | `[]` | a directory (or file) to scan; repeatable (default: src, integrations) |
| `--root` | string | no | no | — | `—` | the repository root (default: this tree) |
| `--class` | string | no | yes | — | `[]` | only report this class; repeatable (command_injection, path_traversal, secrets, unsafe_deserialisation, permissions, toctou, resource_limits, error_leakage) |
| `--min-severity` | choice | no | no | info, low, medium, high, critical | `info` | drop findings below this severity (default: info) |
| `--fail-on` | choice | no | no | info, low, medium, high, critical, none | `high` | exit 2 when a finding at or above this severity is reported (default: high) |
| `--runtime` | string | no | no | — | `—` | runtime dir for the live checks |
| `--include-tests` | flag | no | no | — | `false` | also scan tests/ (their fixtures carry fake secrets) |
| `--no-live` | flag | no | no | — | `false` | skip the live runtime checks (permissions, leftovers, manifests) |
| `--max-file-bytes` | integer | no | no | — | `1500000` | skip files larger than this (default: 1500000) |
| `--list-rules` | flag | no | no | — | `false` | print the rule catalogue with the fix behind every rule |
| `--json` | flag | no | no | — | `false` | machine-readable envelope |
| `--text` | flag | no | no | — | `false` | human text only |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/security_audit.py`

**Contract note (verbatim)**

> a scan that could not run exits blocked, never ok — nothing is reported as clean that was not scanned

#### `security.coverage` — ENFORCE

report measured enforcement coverage per primitive without hiding degradation

| | |
|---|---|
| Group | `security` |
| Layer | ENFORCE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `3` (BLOCKED) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/security_linux.py`

### Group `adversarial`

#### `adversarial.plan` — VERIFY

plan the adversarial cases for the offered surfaces (Jev or deterministic) without running them

| | |
|---|---|
| Group | `adversarial` |
| Layer | VERIFY |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--ui-url` | string | no | no | — | `—` | a running web UI to attack |
| `--canary` | choice | no | no | , broken, fixed | `—` | start the built-in canary target ('broken' has known defects, 'fixed' has none) |
| `--api-url` | string | no | no | — | `—` | a running HTTP API to attack |
| `--with-api` | flag | no | no | — | `false` | start the real Skaldir API in its own process (throwaway runtime) and attack it |
| `--slots` | integer | no | no | — | `6` | parallel cases (isolated contexts) |
| `--timeout` | integer | no | no | — | `45` | per-case timeout in seconds |
| `--runtime` | string | no | no | — | `—` | runtime whose settings/config name the planning provider and whose event store receives the decision rows (default: the installation's runtime) |
| `--planner` | choice | no | no | auto, jev, deterministic | `auto` | who plans the cases: the Jev façade, the fallback, or either |
| `--offline-planner` | flag | no | no | — | `false` | answer the planning questions from the declared catalogue instead of asking the configured provider (deterministic, no network; the plan says answer_source=declared_space) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "planner": {
      "type": "string"
    },
    "advisor": {
      "type": "string"
    },
    "stop_reason": {
      "type": "string"
    },
    "case_count": {
      "type": "integer"
    },
    "offered_surfaces": {
      "type": "array"
    },
    "cases": {
      "type": "array"
    },
    "notes": {
      "type": "array"
    }
  },
  "required": [
    "planner",
    "case_count",
    "cases"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/adversarial.py` — the declared action space + planner
- `code`: `src/skaldir/jev/decide.py` — adversarial_test_planning

**Contract note (verbatim)**

> no case runs here; a plan without a target exits 3 instead of pretending there is nothing to test

#### `adversarial.run` — VERIFY

run every planned case in parallel, judge each with its declared oracle and report PASS/FAIL/UNKNOWN with the defects

| | |
|---|---|
| Group | `adversarial` |
| Layer | VERIFY |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--ui-url` | string | no | no | — | `—` | a running web UI to attack |
| `--canary` | choice | no | no | , broken, fixed | `—` | start the built-in canary target ('broken' has known defects, 'fixed' has none) |
| `--api-url` | string | no | no | — | `—` | a running HTTP API to attack |
| `--with-api` | flag | no | no | — | `false` | start the real Skaldir API in its own process (throwaway runtime) and attack it |
| `--slots` | integer | no | no | — | `6` | parallel cases (isolated contexts) |
| `--timeout` | integer | no | no | — | `45` | per-case timeout in seconds |
| `--runtime` | string | no | no | — | `—` | runtime whose settings/config name the planning provider and whose event store receives the decision rows (default: the installation's runtime) |
| `--planner` | choice | no | no | auto, jev, deterministic | `auto` | who plans the cases: the Jev façade, the fallback, or either |
| `--offline-planner` | flag | no | no | — | `false` | answer the planning questions from the declared catalogue instead of asking the configured provider (deterministic, no network; the plan says answer_source=declared_space) |
| `--block-release` | flag | no | no | — | `false` | the exit code is the release decision: 0 only for PASS, no UNKNOWN waiver |
| `--allow-unknown` | flag | no | no | — | `false` | report mode only: exit 0 for an UNKNOWN suite (never allowed with --block-release) |
| `--state` | string | no | no | — | `—` | where the run report is written (default: <runtime>/adversarial) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "suite_version": {
      "type": "string"
    },
    "run_id": {
      "type": "string"
    },
    "verdict": {
      "type": "string"
    },
    "blocks_release": {
      "type": "boolean"
    },
    "release_gate": {
      "type": "boolean"
    },
    "waived_unknown": {
      "type": "boolean"
    },
    "counts": {
      "type": "object"
    },
    "defects": {
      "type": "array"
    },
    "defect_classes": {
      "type": "array"
    },
    "cases": {
      "type": "array"
    },
    "record": {
      "type": "string"
    }
  },
  "required": [
    "verdict",
    "counts",
    "cases"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/adversarial.py` — cases, isolated contexts, oracles, verdict
- `code`: `src/skaldir/adversarial.py` — the run report record

**Contract note (verbatim)**

> UNKNOWN is never PASS (a case that cannot be verified is not certified); --block-release refuses the --allow-unknown waiver and exits 2 on FAIL, 3 on UNKNOWN

#### `adversarial.selftest` — VERIFY

prove the suite bites: the cases must FAIL on the canary with known defects and PASS on the fixed one

| | |
|---|---|
| Group | `adversarial` |
| Layer | VERIFY |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--slots` | integer | no | no | — | `6` | — |
| `--timeout` | integer | no | no | — | `45` | — |
| `--with-api` | flag | no | no | — | `false` | also run the API cases against a real api_server |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "ok": {
      "type": "boolean"
    },
    "broken_verdict": {
      "type": "string"
    },
    "fixed_verdict": {
      "type": "string"
    },
    "detected": {
      "type": "array"
    },
    "missed": {
      "type": "array"
    },
    "false_positives": {
      "type": "array"
    },
    "detail": {
      "type": "string"
    }
  },
  "required": [
    "ok",
    "detail"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/adversarial.py` — the canary target and the detectors
- `test`: `tests/test_adversarial_browser.py` — the same proof in the suite

**Contract note (verbatim)**

> a detector that never fired is decoration: this verb exits 2 when a case cannot tell a broken target from a fixed one, and 3 when the browser driver is not installed (nothing is attempted, so nothing is blamed on the detectors)

### Group `verify`

#### `verify.evidence` — VERIFY

the recorded evidence records, filterable by run and criterion

| | |
|---|---|
| Group | `verify` |
| Layer | VERIFY |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--state` | string | no | no | — | `—` | — |
| `--runtime` | string | no | no | — | `—` | — |
| `--run-id` | string | no | no | — | `—` | — |
| `--criterion` | string | no | no | — | `—` | filter by criterion text or id |
| `--limit` | integer | no | no | — | `50` | — |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/verify_service.py`

#### `verify.flow` — VERIFY

drive a declared user flow (start state, actions, end-state check) against a real browser or a real isolated desktop and answer PASS/FAIL/UNKNOWN, keeping the screenshots as evidence

| | |
|---|---|
| Group | `verify` |
| Layer | VERIFY |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--workspace` | string | no | no | — | `—` | the workspace (default: cwd) |
| `--flows` | string | no | no | — | `—` | the declaration file (default: <workspace>/.godsv/flows.json) |
| `--flow` | string | no | yes | — | `[]` | run only this declared flow id (repeatable) |
| `--base-url` | string | no | no | — | `—` | rewrite loopback target URLs onto this base URL |
| `--screenshots` | string | no | no | — | `—` | where captures are written (default: <state>/screenshots) |
| `--headed` | flag | no | no | — | `false` | show the browser window instead of running headless |
| `--desktop-timeout` | number | no | no | — | `120.0` | per-action timeout for a desktop flow, in seconds |
| `--state` | string | no | no | — | `—` | state dir (default: <runtime>/verify) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME) |
| `--list` | flag | no | no | — | `false` | show what is declared, run nothing |
| `--template` | flag | no | no | — | `false` | print the declaration shape a workspace can copy |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/verify_flows.py` — the declared flow, the real browser and the isolated desktop
- `code`: `src/skaldir/computer_use.py` — the isolated container runner a desktop flow is driven by

**Contract note (verbatim)**

> UNKNOWN is never PASS: a flow that could not be driven here (no browser, no isolated desktop runner, an unreachable target, an action the verifier does not implement) exits 3; a flow whose anchors did not hold exits 2

#### `verify.flows` — VERIFY

the recorded user-flow runs and the evidence each one produced (screenshots, hashes, timings)

| | |
|---|---|
| Group | `verify` |
| Layer | VERIFY |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--state` | string | no | no | — | `—` | — |
| `--runtime` | string | no | no | — | `—` | — |
| `--run-id` | string | no | no | — | `—` | a specific flow run (default: the latest) |
| `--limit` | integer | no | no | — | `20` | — |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/verify_flows.py` — the recorded flow runs

#### `verify.plan` — VERIFY

derive the real check that would certify each goal criterion

| | |
|---|---|
| Group | `verify` |
| Layer | VERIFY |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--workspace` | string | no | no | — | `—` | the real workspace (default: cwd) |
| `--goal` | string | no | no | — | `—` | the goal the criteria belong to |
| `--criterion` | string | no | yes | — | `[]` | one criterion text (repeatable) |
| `--plan` | string | no | no | — | `—` | plan file (default: <workspace>/.godsv/verify.json) |
| `--from-contract` | flag | no | no | — | `false` | plan the goal recorded in the completion contract |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME) |
| `--session` | string | no | no | — | `—` | completion session id |
| `--revision` | integer | no | no | — | `0` | goal revision the evidence binds to |
| `--state` | string | no | no | — | `—` | state dir (default: <runtime>/verify) |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/verify_service.py`

**Contract note (verbatim)**

> exit 3 when a criterion has no certifiable check here, so a plan never looks stronger than it is

#### `verify.run` — VERIFY

execute the checks against the real workspace and print PASS/FAIL/UNKNOWN

| | |
|---|---|
| Group | `verify` |
| Layer | VERIFY |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--workspace` | string | no | no | — | `—` | the real workspace (default: cwd) |
| `--goal` | string | no | no | — | `—` | the goal the criteria belong to |
| `--criterion` | string | no | yes | — | `[]` | one criterion text (repeatable) |
| `--plan` | string | no | no | — | `—` | plan file (default: <workspace>/.godsv/verify.json) |
| `--from-contract` | flag | no | no | — | `false` | plan the goal recorded in the completion contract |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME) |
| `--session` | string | no | no | — | `—` | completion session id |
| `--revision` | integer | no | no | — | `0` | goal revision the evidence binds to |
| `--state` | string | no | no | — | `—` | state dir (default: <runtime>/verify) |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--timeout` | number | no | no | — | `600.0` | per-check timeout in seconds |
| `--dry-run` | flag | no | no | — | `false` | run the checks but record nothing |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/verify_service.py`

**Contract note (verbatim)**

> UNKNOWN is never PASS: the verdict comes from executed results only and exits 3

#### `verify.status` — VERIFY

the recorded verification runs and whether the latest verdict is still fresh for the workspace it was produced in

| | |
|---|---|
| Group | `verify` |
| Layer | VERIFY |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--state` | string | no | no | — | `—` | — |
| `--runtime` | string | no | no | — | `—` | — |
| `--run-id` | string | no | no | — | `—` | a specific run (default: the latest) |
| `--limit` | integer | no | no | — | `20` | — |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/verify_service.py`

### Group `checkpoint`

#### `checkpoint.create` — PERSIST

snapshot the files a run touched and its conversation into one checkpoint

| | |
|---|---|
| Group | `checkpoint` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--state` | string | no | no | — | `—` | checkpoint state dir (default: <runtime>/checkpoint) |
| `--runtime` | string | no | no | — | `—` | the product runtime dir |
| `--session-store` | string | no | no | — | `—` | sessions.db to use |
| `--json` | flag | no | no | — | `false` | print the JSON payload |
| `--text` | flag | no | no | — | `false` | print the human view |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |
| `--workspace` | string | no | no | — | `—` | the tree to snapshot (default: cwd) |
| `--session` | string | no | no | — | `—` | the session id to fold in (or 'last') |
| `--edit-state` | string | no | no | — | `—` | the edit_tx state dir (default: <runtime>/edit) |
| `--tx` | string | no | yes | — | `[]` | name a transaction (repeatable; 'last' allowed) |
| `--file` | string | no | yes | — | `[]` | declare an extra relative path (repeatable) |
| `--label` | string | no | no | — | `—` | a label for this point |
| `--no-applied` | flag | no | no | — | `false` | do not fold in the transactions in the tree |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "checkpoint_id": {
      "type": "string",
      "description": "the checkpoint this payload answers"
    },
    "verdict": {
      "type": "string",
      "description": "created / list / preview / restored / refused_drift"
    },
    "workspace": {
      "type": "string",
      "description": "the tree the plan or restore targets"
    },
    "files": {
      "type": "array",
      "description": "the declared file set with its sha256s"
    },
    "conversation": {
      "type": "object",
      "description": "the conversation half: hashes, never bodies"
    },
    "preview": {
      "type": "object",
      "description": "what a restore would write, delete, skip"
    },
    "drift": {
      "type": "object",
      "description": "what changed since the checkpoint"
    },
    "written": {
      "type": "array",
      "description": "per-file results with byte_identical"
    },
    "resume": {
      "type": "object",
      "description": "the long-run continuation (step 0)"
    },
    "stats": {
      "type": "object",
      "description": "measured cost: ms and bytes"
    }
  },
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/checkpoints.py`
- `code`: `src/skaldir/checkpoints_cli.py`

**Contract note (verbatim)**

> content-addressed blobs (an unchanged file costs nothing) and a manifest published atomically, last; exit 3 on a cap hit or an unsafe declared path — nothing partial is published; every cap is declared in data.caps and overridable through SKALDIR_CHECKPOINT_MAX_FILES / _MAX_MESSAGES / _MAX_FILE_BYTES / _MAX_BYTES / _MAX_TRANSACTIONS / _MAX_DIFF_BYTES / _KEEP / _PRUNE_MAX_BLOBS (the legacy GODSV_* spelling is accepted)

#### `checkpoint.list` — PERSIST

the checkpoints with their cost, plus the directories an interrupted create left

| | |
|---|---|
| Group | `checkpoint` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--state` | string | no | no | — | `—` | checkpoint state dir (default: <runtime>/checkpoint) |
| `--runtime` | string | no | no | — | `—` | the product runtime dir |
| `--session-store` | string | no | no | — | `—` | sessions.db to use |
| `--json` | flag | no | no | — | `false` | print the JSON payload |
| `--text` | flag | no | no | — | `false` | print the human view |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |
| `--limit` | integer | no | no | — | `50` | how many rows to return |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "checkpoint_id": {
      "type": "string",
      "description": "the checkpoint this payload answers"
    },
    "verdict": {
      "type": "string",
      "description": "created / list / preview / restored / refused_drift"
    },
    "workspace": {
      "type": "string",
      "description": "the tree the plan or restore targets"
    },
    "files": {
      "type": "array",
      "description": "the declared file set with its sha256s"
    },
    "conversation": {
      "type": "object",
      "description": "the conversation half: hashes, never bodies"
    },
    "preview": {
      "type": "object",
      "description": "what a restore would write, delete, skip"
    },
    "drift": {
      "type": "object",
      "description": "what changed since the checkpoint"
    },
    "written": {
      "type": "array",
      "description": "per-file results with byte_identical"
    },
    "resume": {
      "type": "object",
      "description": "the long-run continuation (step 0)"
    },
    "stats": {
      "type": "object",
      "description": "measured cost: ms and bytes"
    }
  },
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/checkpoints.py`
- `code`: `src/skaldir/checkpoints_cli.py`

**Contract note (verbatim)**

> read-only; data.interrupted names every directory without a manifest (the shape a killed create leaves)

#### `checkpoint.prune` — PERSIST · **declared limitation**

reclaim the blob store: remove the blobs no checkpoint references

| | |
|---|---|
| Group | `checkpoint` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--state` | string | no | no | — | `—` | checkpoint state dir (default: <runtime>/checkpoint) |
| `--runtime` | string | no | no | — | `—` | the product runtime dir |
| `--session-store` | string | no | no | — | `—` | sessions.db to use |
| `--json` | flag | no | no | — | `false` | print the JSON payload |
| `--text` | flag | no | no | — | `false` | print the human view |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |
| `--yes` | flag | no | no | — | `false` | really remove the orphan blobs (without it this is a dry run) |
| `--force` | flag | no | no | — | `false` | also reclaim from a directory without a manifest (an interrupted create) |
| `--limit` | integer | no | no | — | `0` | remove at most N orphan blobs (0 = the cap) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "checkpoint_id": {
      "type": "string",
      "description": "the checkpoint this payload answers"
    },
    "verdict": {
      "type": "string",
      "description": "created / list / preview / restored / refused_drift"
    },
    "workspace": {
      "type": "string",
      "description": "the tree the plan or restore targets"
    },
    "files": {
      "type": "array",
      "description": "the declared file set with its sha256s"
    },
    "conversation": {
      "type": "object",
      "description": "the conversation half: hashes, never bodies"
    },
    "preview": {
      "type": "object",
      "description": "what a restore would write, delete, skip"
    },
    "drift": {
      "type": "object",
      "description": "what changed since the checkpoint"
    },
    "written": {
      "type": "array",
      "description": "per-file results with byte_identical"
    },
    "resume": {
      "type": "object",
      "description": "the long-run continuation (step 0)"
    },
    "stats": {
      "type": "object",
      "description": "measured cost: ms and bytes"
    }
  },
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/checkpoints.py`
- `code`: `src/skaldir/checkpoints_cli.py`
- `code`: `src/skaldir/checkpoints.py::prune_checkpoints` — the reference set is built from every readable manifest before anything is removed

**Contract note (verbatim)**

> a dry run unless --yes; every readable manifest is read first and a blob one of them names is never a candidate; a manifest that cannot be read blocks the sweep (exit 3) and a directory without a manifest blocks it too unless --force; at most caps.prune_max_blobs blobs per call, the rest reported as truncated

#### `checkpoint.restore` — PERSIST

restore the declared files and the conversation, byte-identically, or refuse

| | |
|---|---|
| Group | `checkpoint` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--state` | string | no | no | — | `—` | checkpoint state dir (default: <runtime>/checkpoint) |
| `--runtime` | string | no | no | — | `—` | the product runtime dir |
| `--session-store` | string | no | no | — | `—` | sessions.db to use |
| `--json` | flag | no | no | — | `false` | print the JSON payload |
| `--text` | flag | no | no | — | `false` | print the human view |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |
| `checkpoint_id` | string (positional) | yes | no | — | `—` | a checkpoint id, or 'last' |
| `--workspace` | string | no | no | — | `—` | restore into this tree |
| `--yes` | flag | no | no | — | `false` | confirm: without it nothing is overwritten |
| `--force` | flag | no | no | — | `false` | restore over drift and record it |
| `--dry-run` | flag | no | no | — | `false` | preview only |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "checkpoint_id": {
      "type": "string",
      "description": "the checkpoint this payload answers"
    },
    "verdict": {
      "type": "string",
      "description": "created / list / preview / restored / refused_drift"
    },
    "workspace": {
      "type": "string",
      "description": "the tree the plan or restore targets"
    },
    "files": {
      "type": "array",
      "description": "the declared file set with its sha256s"
    },
    "conversation": {
      "type": "object",
      "description": "the conversation half: hashes, never bodies"
    },
    "preview": {
      "type": "object",
      "description": "what a restore would write, delete, skip"
    },
    "drift": {
      "type": "object",
      "description": "what changed since the checkpoint"
    },
    "written": {
      "type": "array",
      "description": "per-file results with byte_identical"
    },
    "resume": {
      "type": "object",
      "description": "the long-run continuation (step 0)"
    },
    "stats": {
      "type": "object",
      "description": "measured cost: ms and bytes"
    }
  },
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/checkpoints.py`
- `code`: `src/skaldir/checkpoints_cli.py`
- `code`: `src/skaldir/checkpoints.py::plan_restore` — the per-file sha256 check and the conversation prefix check behind every refusal

**Contract note (verbatim)**

> without --yes nothing is written (exit 1, confirmation_required); drift since the checkpoint exits 6 unless --force, which records the drift; a store whose bodies no longer match the recorded hashes exits 2; every written file carries byte_identical + its sha256s

#### `checkpoint.resume` — PERSIST

the long-run protocol's step 0: restore the point and hand back the continuation

| | |
|---|---|
| Group | `checkpoint` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--state` | string | no | no | — | `—` | checkpoint state dir (default: <runtime>/checkpoint) |
| `--runtime` | string | no | no | — | `—` | the product runtime dir |
| `--session-store` | string | no | no | — | `—` | sessions.db to use |
| `--json` | flag | no | no | — | `false` | print the JSON payload |
| `--text` | flag | no | no | — | `false` | print the human view |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |
| `checkpoint_id` | string (positional) | yes | no | — | `—` | a checkpoint id, or 'last' |
| `--workspace` | string | no | no | — | `—` | restore into this tree |
| `--yes` | flag | no | no | — | `false` | confirm: without it nothing is overwritten |
| `--force` | flag | no | no | — | `false` | restore over drift and record it |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "checkpoint_id": {
      "type": "string",
      "description": "the checkpoint this payload answers"
    },
    "verdict": {
      "type": "string",
      "description": "created / list / preview / restored / refused_drift"
    },
    "workspace": {
      "type": "string",
      "description": "the tree the plan or restore targets"
    },
    "files": {
      "type": "array",
      "description": "the declared file set with its sha256s"
    },
    "conversation": {
      "type": "object",
      "description": "the conversation half: hashes, never bodies"
    },
    "preview": {
      "type": "object",
      "description": "what a restore would write, delete, skip"
    },
    "drift": {
      "type": "object",
      "description": "what changed since the checkpoint"
    },
    "written": {
      "type": "array",
      "description": "per-file results with byte_identical"
    },
    "resume": {
      "type": "object",
      "description": "the long-run continuation (step 0)"
    },
    "stats": {
      "type": "object",
      "description": "measured cost: ms and bytes"
    }
  },
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/checkpoints.py`
- `code`: `src/skaldir/checkpoints_cli.py`

**Contract note (verbatim)**

> the same restore as checkpoint.restore; data.resume carries next_role, next_seq and the continuation command taken from SessionStore.resume

#### `checkpoint.show` — PERSIST

one checkpoint with the exact restore preview (per-file diff, conversation drift)

| | |
|---|---|
| Group | `checkpoint` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--state` | string | no | no | — | `—` | checkpoint state dir (default: <runtime>/checkpoint) |
| `--runtime` | string | no | no | — | `—` | the product runtime dir |
| `--session-store` | string | no | no | — | `—` | sessions.db to use |
| `--json` | flag | no | no | — | `false` | print the JSON payload |
| `--text` | flag | no | no | — | `false` | print the human view |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |
| `checkpoint_id` | string (positional) | yes | no | — | `—` | a checkpoint id, or 'last' |
| `--workspace` | string | no | no | — | `—` | preview against another tree |
| `--no-diff` | flag | no | no | — | `false` | skip the per-file diffs |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "checkpoint_id": {
      "type": "string",
      "description": "the checkpoint this payload answers"
    },
    "verdict": {
      "type": "string",
      "description": "created / list / preview / restored / refused_drift"
    },
    "workspace": {
      "type": "string",
      "description": "the tree the plan or restore targets"
    },
    "files": {
      "type": "array",
      "description": "the declared file set with its sha256s"
    },
    "conversation": {
      "type": "object",
      "description": "the conversation half: hashes, never bodies"
    },
    "preview": {
      "type": "object",
      "description": "what a restore would write, delete, skip"
    },
    "drift": {
      "type": "object",
      "description": "what changed since the checkpoint"
    },
    "written": {
      "type": "array",
      "description": "per-file results with byte_identical"
    },
    "resume": {
      "type": "object",
      "description": "the long-run continuation (step 0)"
    },
    "stats": {
      "type": "object",
      "description": "measured cost: ms and bytes"
    }
  },
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/checkpoints.py`
- `code`: `src/skaldir/checkpoints_cli.py`
- `code`: `src/skaldir/checkpoints.py::plan_restore` — the per-file sha256 check and the conversation prefix check behind every refusal

**Contract note (verbatim)**

> writes nothing: it is the plan a restore would run; exit 5 for an unknown id

### Group `coding.turn`

#### `coding.turn.begin` — PERSIST

open a turn in the journal (the unit `coding turn undo` reverts)

| | |
|---|---|
| Group | `coding.turn` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--label` | string | no | no | — | `—` | what this turn is |
| `--workspace` | string | no | no | — | `—` | the directory to work in (default: cwd) |
| `--runtime` | string | no | no | — | `—` | the runtime dir (default: the registry's) |
| `--state` | string | no | no | — | `—` | the state dir (default <runtime>/coding) |
| `--text` | flag | no | no | — | `false` | human-readable output on stderr instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/coding_features.py` — coding turn begin

**Contract note (verbatim)**

> writes <runtime>/coding/turns.jsonl only

#### `coding.turn.end` — PERSIST

close the open turn, recording each file's post-turn sha256

| | |
|---|---|
| Group | `coding.turn` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--workspace` | string | no | no | — | `—` | the directory to work in (default: cwd) |
| `--runtime` | string | no | no | — | `—` | the runtime dir (default: the registry's) |
| `--state` | string | no | no | — | `—` | the state dir (default <runtime>/coding) |
| `--text` | flag | no | no | — | `false` | human-readable output on stderr instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/coding_features.py` — coding turn end

**Contract note (verbatim)**

> undo compares against these hashes, which is how it detects a file that moved since the turn

#### `coding.turn.list` — PERSIST

every recorded turn with its file set, its open/closed state and its undo state

| | |
|---|---|
| Group | `coding.turn` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--workspace` | string | no | no | — | `—` | the directory to work in (default: cwd) |
| `--runtime` | string | no | no | — | `—` | the runtime dir (default: the registry's) |
| `--state` | string | no | no | — | `—` | the state dir (default <runtime>/coding) |
| `--text` | flag | no | no | — | `false` | human-readable output on stderr instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/coding_features.py` — coding turn list

**Contract note (verbatim)**

> read-only

#### `coding.turn.record` — PERSIST

snapshot one file (bytes + sha256) before the turn changes it

| | |
|---|---|
| Group | `coding.turn` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `path` | string (positional) | yes | no | — | `—` | the file the turn is about to change |
| `--new` | flag | no | no | — | `false` | the turn CREATES this file |
| `--workspace` | string | no | no | — | `—` | the directory to work in (default: cwd) |
| `--runtime` | string | no | no | — | `—` | the runtime dir (default: the registry's) |
| `--state` | string | no | no | — | `—` | the state dir (default <runtime>/coding) |
| `--text` | flag | no | no | — | `false` | human-readable output on stderr instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/coding_features.py` — coding turn record

**Contract note (verbatim)**

> a snapshot is taken BEFORE the change; --new declares a file the turn creates

#### `coding.turn.undo` — PERSIST

undo the last N completed turns, restoring every file byte-identically

| | |
|---|---|
| Group | `coding.turn` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--turns` | integer | no | no | — | `1` | how many turns to undo |
| `--workspace` | string | no | no | — | `—` | the directory to work in (default: cwd) |
| `--runtime` | string | no | no | — | `—` | the runtime dir (default: the registry's) |
| `--state` | string | no | no | — | `—` | the state dir (default <runtime>/coding) |
| `--text` | flag | no | no | — | `false` | human-readable output on stderr instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/coding_features.py` — coding turn undo

**Contract note (verbatim)**

> a file that changed since the turn refuses the WHOLE turn (exit 6) — a partial undo is worse than none; a created file is deleted

### Group `goal`

#### `goal.criteria` — PERSIST

the criteria table; --verify flips one from evidence only

| | |
|---|---|
| Group | `goal` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir holding runs.db (default: $SKALDIR_HOME/godsv) |
| `--store` | string | no | no | — | `—` | explicit runs.db path (tests/CI) |
| `--owner` | string | no | no | — | `—` | lease owner string (default pid=<pid>:boot=<boot_id>) |
| `--goal-id` | string | no | no | — | `—` | the goal record to use |
| `--session` | string | no | no | — | `—` | pick the session's latest open goal instead of an id |
| `--verify` | string | no | no | — | `—` | criterion id to record an outcome for |
| `--evidence-ref` | string | no | yes | — | `[]` | evidence ref (events:, run:, receipt:, test:, file:) — mandatory with --verify |
| `--check-id` | string | no | no | — | `—` | the check that ran |
| `--cmd` | string | no | no | — | `—` | the command the evidence came from |
| `--exit-code` | integer | no | no | — | `0` | — |
| `--artifact` | string | no | no | — | `—` | artifact the check produced |
| `--workspace` | string | no | no | — | `—` | workspace the check ran in |
| `--workspace-fingerprint` | string | no | no | — | `—` | fingerprint the evidence belongs to (default: the goal's) |
| `--revision` | integer | no | no | — | `—` | revision the evidence was produced under |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/runs_store.py`

**Contract note (verbatim)**

> --verify demands --evidence-ref; a stale revision or a foreign workspace fingerprint is refused and nothing is written

#### `goal.show` — PERSIST

the durable goal record: revision, state, decisions, criteria, operations, budget, owners

| | |
|---|---|
| Group | `goal` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir holding runs.db (default: $SKALDIR_HOME/godsv) |
| `--store` | string | no | no | — | `—` | explicit runs.db path (tests/CI) |
| `--owner` | string | no | no | — | `—` | lease owner string (default pid=<pid>:boot=<boot_id>) |
| `--goal-id` | string | no | no | — | `—` | the goal record to use |
| `--session` | string | no | no | — | `—` | pick the session's latest open goal instead of an id |
| `--all` | flag | no | no | — | `false` | every goal record in the store |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/runs_store.py`

### Group `memory`

#### `memory.consolidate` — PERSIST · **declared limitation**

merge duplicates and resolve contradictions (dry run unless --apply)

| | |
|---|---|
| Group | `memory` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--apply` | flag | no | no | — | `false` | write (default is a dry run) |
| `--near-dup` | number | no | no | — | `0.8` | — |
| `--actor` | string | no | no | — | `—` | — |
| `--db` | string | no | no | — | `—` | typed memory db (default: <runtime>/typed_memory.db) |
| `--runtime` | string | no | no | — | `—` | runtime dir override (used to resolve the default db path) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "dry_run": {
      "type": "boolean"
    },
    "scanned": {
      "type": "integer"
    },
    "merged": {
      "type": "integer"
    },
    "superseded": {
      "type": "integer"
    },
    "flagged": {
      "type": "integer"
    },
    "unresolved_pairs": {
      "type": "integer"
    },
    "before": {
      "type": "object"
    },
    "after": {
      "type": "object"
    },
    "actions": {
      "type": "array"
    },
    "warnings": {
      "type": "array"
    }
  },
  "required": [
    "dry_run",
    "scanned",
    "merged",
    "flagged",
    "unresolved_pairs"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/memory_store.py` — typed store, consolidation rule, ranking

**Contract note (verbatim)**

> exit 2 when a contradiction is left flagged for a human; --apply then writes, it does not resolve the flag

#### `memory.explain` — PERSIST

one memory in full: provenance, decay, conflicts, journal, optional ranking

| | |
|---|---|
| Group | `memory` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `id` | string (positional) | yes | no | — | `—` | — |
| `--query` | string | no | no | — | `—` | also show where it ranks for this query |
| `--db` | string | no | no | — | `—` | typed memory db (default: <runtime>/typed_memory.db) |
| `--runtime` | string | no | no | — | `—` | runtime dir override (used to resolve the default db path) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "memory": {
      "type": "object"
    },
    "expired": {
      "type": "boolean"
    },
    "decay": {
      "type": "object"
    },
    "evidence": {
      "type": "object"
    },
    "conflicts": {
      "type": "array"
    },
    "journal": {
      "type": "array"
    },
    "would_rank": {
      "type": "array"
    }
  },
  "required": [
    "memory",
    "expired",
    "decay",
    "evidence"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/memory_store.py` — typed store, consolidation rule, ranking

#### `memory.forget` — PERSIST

retract a memory (soft by default; --hard deletes; always journaled)

| | |
|---|---|
| Group | `memory` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `id` | string (positional) | yes | no | — | `—` | — |
| `--reason` | string | no | no | — | `—` | — |
| `--hard` | flag | no | no | — | `false` | delete the row (journal stays) |
| `--actor` | string | no | no | — | `—` | — |
| `--dry-run` | flag | no | no | — | `false` | — |
| `--db` | string | no | no | — | `—` | typed memory db (default: <runtime>/typed_memory.db) |
| `--runtime` | string | no | no | — | `—` | runtime dir override (used to resolve the default db path) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "memory": {
      "type": "object"
    },
    "written": {
      "type": "boolean"
    },
    "hard": {
      "type": "boolean"
    },
    "db": {
      "type": "string"
    }
  },
  "required": [
    "memory",
    "written"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/memory_store.py` — typed store, consolidation rule, ranking

#### `memory.recall` — PERSIST

rank memories for a query and explain every hit

| | |
|---|---|
| Group | `memory` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `query` | string (positional) | yes | yes | — | `—` | — |
| `--k` | integer | no | no | — | `3` | — |
| `--scope` | string | no | no | — | `—` | — |
| `--scope-id` | string | no | no | — | `—` | — |
| `--kind` | choice | no | no | fact, preference, decision, incident, project, entity | `—` | — |
| `--strict-scope` | flag | no | no | — | `false` | — |
| `--include-superseded` | flag | no | no | — | `false` | — |
| `--jev` | flag | no | no | — | `false` | also emit the Jev request shape |
| `--measure` | flag | no | no | — | `false` | the measured precision check |
| `--db` | string | no | no | — | `—` | typed memory db (default: <runtime>/typed_memory.db) |
| `--runtime` | string | no | no | — | `—` | runtime dir override (used to resolve the default db path) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string"
    },
    "hits": {
      "type": "array"
    },
    "count": {
      "type": "integer"
    },
    "k": {
      "type": "integer"
    },
    "pool": {
      "type": "integer"
    },
    "measurement": {
      "type": [
        "object",
        "null"
      ]
    },
    "jev_request": {
      "type": "object"
    }
  },
  "required": [
    "query",
    "hits",
    "count"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/memory_store.py` — typed store, consolidation rule, ranking

**Contract note (verbatim)**

> an empty hit list is data with exit 0; --measure runs the synthetic precision check and exits 2 when it drops below the declared floor; --jev also emits the Jev memory_injection request shape (shape only — Jev decides inject_now/defer/drop)

#### `memory.remember` — PERSIST · **declared limitation**

store one typed memory (fact/preference/decision/incident/project/entity)

| | |
|---|---|
| Group | `memory` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `text` | string (positional) | yes | no | — | `—` | — |
| `--kind` | choice | no | no | fact, preference, decision, incident, project, entity | `fact` | — |
| `--scope` | choice | no | no | global, project, session | `global` | — |
| `--scope-id` | string | no | no | — | `—` | — |
| `--subject` | string | no | no | — | `—` | comparison key (else 'subject: text') |
| `--confidence` | number | no | no | — | `0.6` | — |
| `--source` | choice | no | no | owner, review, gate, agent, import, guess | `agent` | where it came from; owner is authoritative in a conflict |
| `--session` | string | no | no | — | `—` | — |
| `--note` | string | no | no | — | `—` | — |
| `--evidence-ref` | string | no | yes | — | `—` | repeatable: a file/run/commit this memory rests on |
| `--ttl` | string | no | no | — | `—` | expiry, e.g. 30d / 12h / 0 (never) |
| `--half-life` | string | no | no | — | `—` | e.g. 180d |
| `--contradicts` | string | no | yes | — | `—` | repeatable id |
| `--actor` | string | no | no | — | `—` | — |
| `--at` | number | no | no | — | `—` | created_at (epoch; tests) |
| `--dry-run` | flag | no | no | — | `false` | — |
| `--db` | string | no | no | — | `—` | typed memory db (default: <runtime>/typed_memory.db) |
| `--runtime` | string | no | no | — | `—` | runtime dir override (used to resolve the default db path) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "memory": {
      "type": "object"
    },
    "written": {
      "type": "boolean"
    },
    "db": {
      "type": "string"
    },
    "journal_seq": {
      "type": [
        "integer",
        "null"
      ]
    }
  },
  "required": [
    "memory",
    "written",
    "db"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/memory_store.py` — typed store, consolidation rule, ranking

**Contract note (verbatim)**

> dry run writes nothing and says written=false; provenance is always recorded (source/refs/session)

#### `memory.scope` — PERSIST

scopes in use, kinds per scope, decay policy and journal integrity

| | |
|---|---|
| Group | `memory` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--scope` | choice | no | no | global, project, session | `—` | — |
| `--db` | string | no | no | — | `—` | typed memory db (default: <runtime>/typed_memory.db) |
| `--runtime` | string | no | no | — | `—` | runtime dir override (used to resolve the default db path) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "db": {
      "type": "string"
    },
    "total": {
      "type": "integer"
    },
    "scopes": {
      "type": "array"
    },
    "decay_policy": {
      "type": "object"
    },
    "journal_rows": {
      "type": "integer"
    },
    "journal_integrity": {
      "type": "object"
    },
    "embedding_provider": {
      "type": "object"
    }
  },
  "required": [
    "total",
    "scopes",
    "decay_policy"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/memory_store.py` — typed store, consolidation rule, ranking

### Group `prompt`

#### `prompt.build` — PERSIST

compose the agent system prompt from the real inputs and print it

| | |
|---|---|
| Group | `prompt` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--project` | string | no | no | — | `—` | project directory the assembly reads (default: the cwd) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME, else ~/.hermes/skaldir) |
| `--memory-db` | string | no | no | — | `—` | memory / skill store (default: <runtime>/memory.db) |
| `--tools-db` | string | no | no | — | `—` | runtime tool registry (default: <runtime>/tools.db) |
| `--prompts-root` | string | no | no | — | `—` | prompts directory for the hard rules (default: the package's) |
| `--budget` | integer | no | no | — | `0` | total token budget (default 12000) |
| `--section-budget` | string | no | yes | — | `[]` | override one section's character budget (repeatable) |
| `--without` | string | no | yes | — | `[]` | omit a section: identity, hard_rules, tools, memory, skills, project_context, runtime_facts (repeatable) |
| `--conversation` | string | no | no | — | `default` | the conversation whose frozen prompt is compared (default 'default') |
| `--freeze` | flag | no | no | — | `false` | record this fingerprint as the frozen prompt of the conversation |
| `--accept-change` | flag | no | no | — | `false` | deliberately accept a reported cache break (re-freezes) |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | print the prompt verbatim (the human default for `build`) |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "fingerprint": {
      "type": "string"
    },
    "chars": {
      "type": "integer"
    },
    "bytes": {
      "type": "integer"
    },
    "tokens_estimate": {
      "type": "integer"
    },
    "estimate_method": {
      "type": "string"
    },
    "budget_tokens": {
      "type": "integer"
    },
    "over_budget": {
      "type": "boolean"
    },
    "text": {
      "type": "string"
    },
    "sections": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "name": {
            "type": "string"
          },
          "title": {
            "type": "string"
          },
          "present": {
            "type": "boolean"
          },
          "chars": {
            "type": "integer"
          },
          "tokens_estimate": {
            "type": "integer"
          },
          "budget_chars": {
            "type": "integer"
          },
          "over_budget": {
            "type": "boolean"
          },
          "truncated": {
            "type": "boolean"
          },
          "sha256": {
            "type": "string"
          },
          "source": {
            "type": "string"
          },
          "detail": {
            "type": "string"
          }
        },
        "required": [
          "name",
          "present",
          "chars",
          "sha256",
          "source"
        ]
      }
    },
    "present": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "omitted": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "cache": {
      "type": "object",
      "properties": {
        "fingerprint": {
          "type": "string"
        },
        "frozen": {
          "type": "boolean"
        },
        "stable": {
          "type": "boolean"
        },
        "cache_break": {
          "type": "boolean"
        },
        "conversation": {
          "type": "string"
        },
        "previous_fingerprint": {
          "type": "string"
        },
        "accepted_from": {
          "type": "string"
        },
        "changed_sections": {
          "type": "array"
        },
        "reason": {
          "type": "string"
        },
        "note": {
          "type": "string"
        }
      },
      "required": [
        "fingerprint",
        "frozen",
        "stable",
        "cache_break",
        "reason"
      ]
    },
    "cache_break_accepted": {
      "type": "boolean"
    },
    "frozen_now": {
      "type": "boolean"
    },
    "conversation": {
      "type": "string"
    },
    "inputs": {
      "type": "object"
    }
  },
  "required": [
    "fingerprint",
    "text",
    "sections",
    "cache",
    "budget_tokens",
    "over_budget"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/prompt_builder.py` — the composer: seven fixed sections, per-section budgets, one fingerprint
- `code`: `src/skaldir/prompt_builder_cli.py` — this handler
- `file`: `<runtime>/prompt-freeze.json` — the frozen fingerprint of the conversation, when one was recorded

**Contract note (verbatim)**

> Prints the composed prompt verbatim on stdout (human view); diagnostics — truncation, budget, a refused cache break — go to stderr, so `skaldir prompt build > prompt.txt` captures exactly the bytes the fingerprint covers. `--freeze` records the fingerprint for the conversation; a later build whose fingerprint differs exits 6 (conflict) until the change is accepted with --accept-change. Exit 2 means the prompt is over --budget.

#### `prompt.explain` — PERSIST

section by section: size, budget, hash and source of each block

| | |
|---|---|
| Group | `prompt` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--project` | string | no | no | — | `—` | project directory the assembly reads (default: the cwd) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME, else ~/.hermes/skaldir) |
| `--memory-db` | string | no | no | — | `—` | memory / skill store (default: <runtime>/memory.db) |
| `--tools-db` | string | no | no | — | `—` | runtime tool registry (default: <runtime>/tools.db) |
| `--prompts-root` | string | no | no | — | `—` | prompts directory for the hard rules (default: the package's) |
| `--budget` | integer | no | no | — | `0` | total token budget (default 12000) |
| `--section-budget` | string | no | yes | — | `[]` | override one section's character budget (repeatable) |
| `--without` | string | no | yes | — | `[]` | omit a section: identity, hard_rules, tools, memory, skills, project_context, runtime_facts (repeatable) |
| `--conversation` | string | no | no | — | `default` | the conversation whose frozen prompt is compared (default 'default') |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "fingerprint": {
      "type": "string"
    },
    "chars": {
      "type": "integer"
    },
    "bytes": {
      "type": "integer"
    },
    "tokens_estimate": {
      "type": "integer"
    },
    "estimate_method": {
      "type": "string"
    },
    "budget_tokens": {
      "type": "integer"
    },
    "over_budget": {
      "type": "boolean"
    },
    "section_count": {
      "type": "integer"
    },
    "sections": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "name": {
            "type": "string"
          },
          "title": {
            "type": "string"
          },
          "present": {
            "type": "boolean"
          },
          "chars": {
            "type": "integer"
          },
          "tokens_estimate": {
            "type": "integer"
          },
          "budget_chars": {
            "type": "integer"
          },
          "over_budget": {
            "type": "boolean"
          },
          "truncated": {
            "type": "boolean"
          },
          "sha256": {
            "type": "string"
          },
          "source": {
            "type": "string"
          },
          "detail": {
            "type": "string"
          }
        },
        "required": [
          "name",
          "present",
          "chars",
          "sha256",
          "source"
        ]
      }
    },
    "present": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "omitted": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "cache": {
      "type": "object",
      "properties": {
        "fingerprint": {
          "type": "string"
        },
        "frozen": {
          "type": "boolean"
        },
        "stable": {
          "type": "boolean"
        },
        "cache_break": {
          "type": "boolean"
        },
        "conversation": {
          "type": "string"
        },
        "previous_fingerprint": {
          "type": "string"
        },
        "accepted_from": {
          "type": "string"
        },
        "changed_sections": {
          "type": "array"
        },
        "reason": {
          "type": "string"
        },
        "note": {
          "type": "string"
        }
      },
      "required": [
        "fingerprint",
        "frozen",
        "stable",
        "cache_break",
        "reason"
      ]
    },
    "conversation": {
      "type": "string"
    },
    "freeze_path": {
      "type": "string"
    },
    "inputs": {
      "type": "object"
    }
  },
  "required": [
    "fingerprint",
    "sections",
    "present",
    "omitted",
    "cache",
    "inputs"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/prompt_builder.py` — prompt_builder.explain
- `code`: `src/skaldir/prompt_builder_cli.py` — this handler

**Contract note (verbatim)**

> The diagnostic view: per section the size, the budget, the clip state, the content hash, the source and the provider's own detail line — omitted sections included, with the reason they are absent. `inputs` names every path the assembly read. Over budget exits 2; a cache break is reported here but not refused (that is `prompt build`).

#### `prompt.fingerprint` — PERSIST

the sha256 of the composed prompt, against the frozen one

| | |
|---|---|
| Group | `prompt` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--project` | string | no | no | — | `—` | project directory the assembly reads (default: the cwd) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME, else ~/.hermes/skaldir) |
| `--memory-db` | string | no | no | — | `—` | memory / skill store (default: <runtime>/memory.db) |
| `--tools-db` | string | no | no | — | `—` | runtime tool registry (default: <runtime>/tools.db) |
| `--prompts-root` | string | no | no | — | `—` | prompts directory for the hard rules (default: the package's) |
| `--budget` | integer | no | no | — | `0` | total token budget (default 12000) |
| `--section-budget` | string | no | yes | — | `[]` | override one section's character budget (repeatable) |
| `--without` | string | no | yes | — | `[]` | omit a section: identity, hard_rules, tools, memory, skills, project_context, runtime_facts (repeatable) |
| `--conversation` | string | no | no | — | `default` | the conversation whose frozen prompt is compared (default 'default') |
| `--freeze` | flag | no | no | — | `false` | record this fingerprint as the frozen prompt of the conversation |
| `--accept-change` | flag | no | no | — | `false` | deliberately accept a reported cache break (re-freezes) |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "fingerprint": {
      "type": "string"
    },
    "chars": {
      "type": "integer"
    },
    "bytes": {
      "type": "integer"
    },
    "tokens_estimate": {
      "type": "integer"
    },
    "estimate_method": {
      "type": "string"
    },
    "budget_tokens": {
      "type": "integer"
    },
    "over_budget": {
      "type": "boolean"
    },
    "sections": {
      "type": "object"
    },
    "present": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "omitted": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "cache": {
      "type": "object",
      "properties": {
        "fingerprint": {
          "type": "string"
        },
        "frozen": {
          "type": "boolean"
        },
        "stable": {
          "type": "boolean"
        },
        "cache_break": {
          "type": "boolean"
        },
        "conversation": {
          "type": "string"
        },
        "previous_fingerprint": {
          "type": "string"
        },
        "accepted_from": {
          "type": "string"
        },
        "changed_sections": {
          "type": "array"
        },
        "reason": {
          "type": "string"
        },
        "note": {
          "type": "string"
        }
      },
      "required": [
        "fingerprint",
        "frozen",
        "stable",
        "cache_break",
        "reason"
      ]
    },
    "cache_break_accepted": {
      "type": "boolean"
    },
    "conversation": {
      "type": "string"
    },
    "freeze_path": {
      "type": "string"
    },
    "inputs": {
      "type": "object"
    }
  },
  "required": [
    "fingerprint",
    "cache",
    "conversation",
    "freeze_path"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/prompt_builder.py` — fingerprint_of + cache_status (the stability contract)
- `code`: `src/skaldir/prompt_builder_cli.py` — this handler
- `file`: `<runtime>/prompt-freeze.json` — the frozen fingerprint

**Contract note (verbatim)**

> The stability check: sha256 over the exact UTF-8 bytes of the composed prompt, compared against the frozen prompt of --conversation. Exits 6 when a frozen prompt exists and the fingerprint moved, unless --accept-change records the new prompt deliberately.

### Group `prompt.cache`

#### `prompt.cache.plan` — PERSIST

where cache_control would land on a prompt (never sent)

| | |
|---|---|
| Group | `prompt.cache` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--prompt` | string | no | no | — | `—` | the system prompt text to plan |
| `--prompt-file` | string | no | no | — | `—` | a file holding the prompt |
| `--tools-file` | string | no | no | — | `—` | a JSON array of tool schemas (the request's tools) |
| `--assembly` | string | no | no | — | `—` | a composer assembly as JSON: {"text": …, "sections": […]} |
| `--provider` | string | no | no | — | `—` | a catalog provider id (deepseek, openai, …) |
| `--base-url` | string | no | no | — | `—` | match the strategy by endpoint host |
| `--model` | string | no | no | — | `—` | match the strategy by model name |
| `--ttl` | string | no | no | — | `—` | cache ttl ['5m', '1h'] (default: unset) |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/prompt_cache.py` — explicit_breakpoints + section_blocks
- `code`: `src/skaldir/prompt_cache_cli.py` — this handler

**Contract note (verbatim)**

> `plan` places `cache_control` markers on the last stable block of the system message, on the last tool, and inside the system prompt at the end of the project-context section when the composer's own section list is supplied (`--assembly`). A target that does not exist is reported in `skipped` with its reason instead of being invented, and the plan is never sent by this command.

#### `prompt.cache.status` — PERSIST

the prompt-cache surface of the configured provider

| | |
|---|---|
| Group | `prompt.cache` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME, else ~/.hermes/skaldir) |
| `--config` | string | no | no | — | `—` | provider config file (default: the resolved godsv.llm block) |
| `--ledger` | string | no | no | — | `—` | the measured-request ledger (default: <runtime>/prompt-cache.jsonl) |
| `--limit` | integer | no | no | — | `0` | consider only the newest N ledger lines (0 = all) |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/prompt_cache.py` — the strategy table and the usage-field reader
- `code`: `src/skaldir/llm_provider.py` — OpenAICompatibleLLM._finish_cache: the per-request verdict
- `file`: `<runtime>/prompt-cache.jsonl` — one line per measured request, when a ledger is enabled

**Contract note (verbatim)**

> `status` answers two different questions in one payload and keeps them apart: `strategy` is what the provider documents (mode, usage fields, reference), `summary`/`last` are what its usage payloads reported. A provider that sent no cache counter counts as `unknown` here, never as a hit — the counter is the only thing that may say `hit`, and it comes from the answer, not from our prefix.

### Group `runs`

#### `runs.checkpoint` — PERSIST

the self-sufficient snapshot (file + index row + cursor)

| | |
|---|---|
| Group | `runs` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir holding runs.db (default: $SKALDIR_HOME/godsv) |
| `--store` | string | no | no | — | `—` | explicit runs.db path (tests/CI) |
| `--owner` | string | no | no | — | `—` | lease owner string (default pid=<pid>:boot=<boot_id>) |
| `--goal-id` | string | no | no | — | `—` | the goal record to use |
| `--session` | string | no | no | — | `—` | pick the session's latest open goal instead of an id |
| `--next-action` | string | no | no | — | `—` | imperative first step after resume |
| `--last-event-seq` | integer | no | no | — | `—` | highest committed event seq this goal consumed |
| `--scan-events` | flag | no | no | — | `false` | take the seq from events.db (MAX(seq)) when --last-event-seq is not given |
| `--directory` | string | no | no | — | `—` | override the payload directory |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/runs_store.py`

#### `runs.resume` — PERSIST

the R0-R3 resume protocol: writer lease, classify every in-flight operation, park what cannot be proven

| | |
|---|---|
| Group | `runs` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir holding runs.db (default: $SKALDIR_HOME/godsv) |
| `--store` | string | no | no | — | `—` | explicit runs.db path (tests/CI) |
| `--owner` | string | no | no | — | `—` | lease owner string (default pid=<pid>:boot=<boot_id>) |
| `--goal-id` | string | no | no | — | `—` | the goal record to use |
| `--session` | string | no | no | — | `—` | pick the session's latest open goal instead of an id |
| `--no-takeover` | flag | no | no | — | `false` | never take over an expired/dead lease (report only) |
| `--no-adopt-host` | flag | no | no | — | `false` | do not adopt orphaned host turn leases |
| `--restore-budget` | flag | no | no | — | `false` | also replay the durable ledger into a live budget.BudgetAccount |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/runs_store.py`

**Contract note (verbatim)**

> exit 3 while a parked operation waits for operator:side_effect_verified; exit 2 for a record that cannot be classified

#### `runs.start` — PERSIST

create the goal record + criteria + budget rows + the first checkpoint

| | |
|---|---|
| Group | `runs` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir holding runs.db (default: $SKALDIR_HOME/godsv) |
| `--store` | string | no | no | — | `—` | explicit runs.db path (tests/CI) |
| `--owner` | string | no | no | — | `—` | lease owner string (default pid=<pid>:boot=<boot_id>) |
| `--goal` | string | yes | no | — | `—` | the owner's goal text |
| `--session` | string | no | no | — | `cli` | session id the goal belongs to |
| `--criterion` | string | no | yes | — | `[]` | acceptance criterion (repeatable) |
| `--criteria-file` | string | no | no | — | `—` | JSON file with a list of {text, blocking, verification_plan} |
| `--task-base` | string | no | no | — | `—` | workspace root |
| `--workspace-fingerprint` | string | no | no | — | `—` | fingerprint evidence must match to certify a criterion |
| `--budget` | string | no | yes | — | `[]` | role=limit_tokens (repeatable) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/runs_store.py`

**Contract note (verbatim)**

> exit 6 when the session already has an open goal — a goal is never silently re-created

#### `runs.status` — PERSIST

the store, the goal records, the operations, the budget and the run supervisor's snapshot (read-only)

| | |
|---|---|
| Group | `runs` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir holding runs.db (default: $SKALDIR_HOME/godsv) |
| `--store` | string | no | no | — | `—` | explicit runs.db path (tests/CI) |
| `--owner` | string | no | no | — | `—` | lease owner string (default pid=<pid>:boot=<boot_id>) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/runs_store.py`

**Contract note (verbatim)**

> exit 5 when runs.db does not exist yet — no store is not a success

### Group `session`

#### `session.append` — PERSIST

append one message; two same-role turns in a row are refused with exit 6

| | |
|---|---|
| Group | `session` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir holding sessions.db (default: $SKALDIR_HOME) |
| `--store` | string | no | no | — | `—` | explicit sessions.db path (tests/CI) |
| `--owner` | string | no | no | — | `—` | lease owner string (default pid=<pid>:boot=<boot_id>) |
| `--session` | string | yes | no | — | `—` | the session id to act on |
| `--role` | choice | yes | no | system, user, assistant, tool | `—` | — |
| `--content` | string | no | no | — | `—` | the message body |
| `--content-file` | string | no | no | — | `—` | read the body from a file instead |
| `--kind` | string | no | no | — | `chat` | a message kind (chat, tool_result, …) |
| `--ref` | string | no | yes | — | `[]` | an evidence/tool ref (repeatable) |
| `--meta` | string | no | no | — | `—` | a JSON object carried with the message |
| `--message-id` | string | no | no | — | `—` | use this id instead of a generated one |
| `--origin-message-id` | string | no | no | — | `—` | the message this one was copied from (provenance) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "session_id": {
      "type": "string"
    },
    "message": {
      "type": "object"
    },
    "seq": {
      "type": "integer"
    },
    "next_role": {
      "type": "string"
    },
    "history_sha256": {
      "type": "string"
    }
  },
  "required": [
    "session_id",
    "message",
    "seq",
    "next_role"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/session_store.py::SessionStore.append` — the one write path
- `code`: `src/skaldir/session_store.py::RoleAlternationError` — the refusal a same-role turn produces (exit 6)

**Contract note (verbatim)**

> strict alternation: the head's role cannot repeat (RoleAlternationError, exit 6)

#### `session.archive` — PERSIST

archive or unarchive a session (read-only afterwards, nothing deleted)

| | |
|---|---|
| Group | `session` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir holding sessions.db (default: $SKALDIR_HOME) |
| `--store` | string | no | no | — | `—` | explicit sessions.db path (tests/CI) |
| `--owner` | string | no | no | — | `—` | lease owner string (default pid=<pid>:boot=<boot_id>) |
| `--session` | string | no | no | — | `—` | the session id to act on |
| `--unarchive` | flag | no | no | — | `false` | make it writable again |
| `--reason` | string | no | no | — | `—` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/session_store.py::SessionStore.archive_session` — the flag

#### `session.export` — PERSIST

export a session as json / jsonl / markdown / text (stdout or --out)

| | |
|---|---|
| Group | `session` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir holding sessions.db (default: $SKALDIR_HOME) |
| `--store` | string | no | no | — | `—` | explicit sessions.db path (tests/CI) |
| `--owner` | string | no | no | — | `—` | lease owner string (default pid=<pid>:boot=<boot_id>) |
| `--session` | string | no | no | — | `—` | the session id to act on |
| `--as` | choice | no | no | json, jsonl, markdown, text | `jsonl` | the export format (--format is the CLI-wide flag) |
| `--limit` | integer | no | no | — | `5000` | — |
| `--out` | string | no | no | — | `—` | write here instead of stdout (atomic) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "session_id": {
      "type": "string"
    },
    "format": {
      "type": "string"
    },
    "messages": {
      "type": "integer"
    },
    "sha256": {
      "type": "string"
    },
    "document": {
      "type": "string"
    },
    "written": {
      "type": "string"
    }
  },
  "required": [
    "session_id",
    "format",
    "document"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/session_store.py::SessionStore.export` — the renderers
- `file`: `--out PATH` — the atomic write of the exported document

**Contract note (verbatim)**

> --as picks the format (--format is the CLI-wide flag); --out writes atomically

#### `session.fork` — PERSIST

branch a session into a linked child, copying its prefix when asked

| | |
|---|---|
| Group | `session` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir holding sessions.db (default: $SKALDIR_HOME) |
| `--store` | string | no | no | — | `—` | explicit sessions.db path (tests/CI) |
| `--owner` | string | no | no | — | `—` | lease owner string (default pid=<pid>:boot=<boot_id>) |
| `--session` | string | no | no | — | `—` | the session id to act on |
| `--at-message-id` | string | no | no | — | `—` | — |
| `--title` | string | no | no | — | `—` | — |
| `--no-copy` | flag | no | no | — | `false` | — |
| `--label` | string | no | yes | — | `[]` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "child": {
      "type": "object"
    },
    "parent": {
      "type": "object"
    },
    "copied_messages": {
      "type": "integer"
    },
    "lineage": {
      "type": "object"
    }
  },
  "required": [
    "child",
    "parent",
    "copied_messages"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/session_store.py::SessionStore.fork` — the fork + copy
- `code`: `src/skaldir/session_store.py::SessionStore.lineage` — the link

#### `session.list` — PERSIST

list sessions: title, message count, last activity, lineage (bounded)

| | |
|---|---|
| Group | `session` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir holding sessions.db (default: $SKALDIR_HOME) |
| `--store` | string | no | no | — | `—` | explicit sessions.db path (tests/CI) |
| `--owner` | string | no | no | — | `—` | lease owner string (default pid=<pid>:boot=<boot_id>) |
| `--limit` | integer | no | no | — | `50` | — |
| `--all` | flag | no | no | — | `false` | include archived sessions |
| `--order` | choice | no | no | recent, created | `recent` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "sessions": {
      "type": "array",
      "items": {
        "type": "object"
      }
    },
    "count": {
      "type": "integer"
    },
    "totals": {
      "type": "object"
    }
  },
  "required": [
    "sessions",
    "count"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/session_store.py` — sessions + messages schema this listing reads
- `code`: `src/skaldir/session_cli.py::cmd_session_list` — the handler

#### `session.new` — PERSIST

create an empty session (or fork one with --parent)

| | |
|---|---|
| Group | `session` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir holding sessions.db (default: $SKALDIR_HOME) |
| `--store` | string | no | no | — | `—` | explicit sessions.db path (tests/CI) |
| `--owner` | string | no | no | — | `—` | lease owner string (default pid=<pid>:boot=<boot_id>) |
| `--title` | string | no | no | — | `—` | a human label for the session |
| `--session-id` | string | no | no | — | `—` | use this id instead of a generated one |
| `--parent` | string | no | no | — | `—` | fork this session instead of creating |
| `--at-message-id` | string | no | no | — | `—` | fork point: copy the parent up to this message |
| `--no-copy` | flag | no | no | — | `false` | fork without copying the parent's prefix |
| `--label` | string | no | yes | — | `[]` | a label (repeatable) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "created": {
      "type": "string"
    },
    "session": {
      "type": "object"
    },
    "lineage": {
      "type": "object"
    },
    "forked": {
      "type": "boolean"
    }
  },
  "required": [
    "created",
    "session"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/session_store.py::SessionStore.create_session` — insert
- `code`: `src/skaldir/session_store.py::SessionStore.fork` — the --parent path

#### `session.policy` — PERSIST

the retention / delete / rotate policy and the four invariants, as data

| | |
|---|---|
| Group | `session` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "retention": {
      "type": "object"
    },
    "invariants": {
      "type": "object"
    },
    "defaults": {
      "type": "object"
    },
    "roles": {
      "type": "array",
      "items": {
        "type": "string"
      }
    }
  },
  "required": [
    "retention",
    "invariants"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/session_store.py::RETENTION_POLICY` — the policy constant this command prints

#### `session.purge` — PERSIST · **declared limitation**

archive / truncate / delete a session's bodies — dry run unless --apply

| | |
|---|---|
| Group | `session` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir holding sessions.db (default: $SKALDIR_HOME) |
| `--store` | string | no | no | — | `—` | explicit sessions.db path (tests/CI) |
| `--owner` | string | no | no | — | `—` | lease owner string (default pid=<pid>:boot=<boot_id>) |
| `--session` | string | no | no | — | `—` | the session id to act on |
| `--mode` | choice | no | no | archive, truncate, delete | `archive` | — |
| `--keep-last` | integer | no | no | — | `20` | — |
| `--reason` | string | no | no | — | `—` | recorded in the audit row / tombstone |
| `--apply` | flag | no | no | — | `false` | perform it; without this flag the command only plans |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "mode": {
      "type": "string"
    },
    "dry_run": {
      "type": "boolean"
    },
    "messages_removed": {
      "type": "integer"
    },
    "messages_kept": {
      "type": "integer"
    },
    "tombstone": {
      "type": "object"
    }
  },
  "required": [
    "mode",
    "dry_run",
    "messages_removed",
    "messages_kept"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/session_store.py::SessionStore.purge` — the purge token + writes
- `code`: `src/skaldir/session_store.py::RETENTION_POLICY` — what this command may remove and what it never touches

**Contract note (verbatim)**

> dry run by default (exit 0, nothing written); --apply performs the purge

#### `session.resume` — PERSIST

reopen a session: prior history + hash check + the next legal role to continue with

| | |
|---|---|
| Group | `session` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir holding sessions.db (default: $SKALDIR_HOME) |
| `--store` | string | no | no | — | `—` | explicit sessions.db path (tests/CI) |
| `--owner` | string | no | no | — | `—` | lease owner string (default pid=<pid>:boot=<boot_id>) |
| `--session` | string | no | no | — | `—` | the session id to act on |
| `--limit` | integer | no | no | — | `200` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "session": {
      "type": "object"
    },
    "messages": {
      "type": "array",
      "items": {
        "type": "object"
      }
    },
    "history": {
      "type": "object"
    },
    "next_role": {
      "type": "string"
    },
    "continuation": {
      "type": "object"
    }
  },
  "required": [
    "session",
    "messages",
    "history",
    "next_role"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/session_store.py` — the history the session was reopened from
- `code`: `src/skaldir/session_store.py::SessionStore.resume` — returns the window, the hash verdict and the next legal role

**Contract note (verbatim)**

> exit 2 when the history does not match: a changed history is not continued

#### `session.rotate` — PERSIST

roll the newest --keep-last messages into a successor session (nothing deleted)

| | |
|---|---|
| Group | `session` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir holding sessions.db (default: $SKALDIR_HOME) |
| `--store` | string | no | no | — | `—` | explicit sessions.db path (tests/CI) |
| `--owner` | string | no | no | — | `—` | lease owner string (default pid=<pid>:boot=<boot_id>) |
| `--session` | string | no | no | — | `—` | the session id to act on |
| `--keep-last` | integer | no | no | — | `20` | — |
| `--title` | string | no | no | — | `—` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "rotated": {
      "type": "boolean"
    },
    "successor": {
      "type": "object"
    },
    "parent": {
      "type": "object"
    },
    "copied_messages": {
      "type": "integer"
    }
  },
  "required": [
    "rotated"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/session_store.py::SessionStore.rotate` — rotate + archive
- `code`: `src/skaldir/session_store.py::SessionStore.archive_session` — the parent

**Contract note (verbatim)**

> the non-destructive answer to a long session; rotated=false is still exit 0

#### `session.search` — PERSIST

search message content across sessions (bounded, ranked, FTS5)

| | |
|---|---|
| Group | `session` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir holding sessions.db (default: $SKALDIR_HOME) |
| `--store` | string | no | no | — | `—` | explicit sessions.db path (tests/CI) |
| `--owner` | string | no | no | — | `—` | lease owner string (default pid=<pid>:boot=<boot_id>) |
| `--session` | string | no | no | — | `—` | the session id to act on |
| `query` | string (positional) | yes | no | — | `—` | the words to look for |
| `--limit` | integer | no | no | — | `20` | — |
| `--role` | string | no | yes | — | `[]` | restrict to a role |
| `--no-archived` | flag | no | no | — | `false` | skip archived sessions |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string"
    },
    "engine": {
      "type": "string"
    },
    "hits": {
      "type": "array",
      "items": {
        "type": "object"
      }
    },
    "returned": {
      "type": "integer"
    },
    "bounded": {
      "type": "boolean"
    },
    "sessions": {
      "type": "array",
      "items": {
        "type": "string"
      }
    }
  },
  "required": [
    "query",
    "engine",
    "hits",
    "returned"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/session_store.py` — messages_fts + bm25 over the message bodies
- `file`: `sessions.db` — the FTS index lives in the same store file

**Contract note (verbatim)**

> keyword/token search (FTS5 unicode61), not semantic; an empty query exits 1

#### `session.show` — PERSIST

one session: header, a bounded message window, the hash check, optional audit

| | |
|---|---|
| Group | `session` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir holding sessions.db (default: $SKALDIR_HOME) |
| `--store` | string | no | no | — | `—` | explicit sessions.db path (tests/CI) |
| `--owner` | string | no | no | — | `—` | lease owner string (default pid=<pid>:boot=<boot_id>) |
| `--session` | string | no | no | — | `—` | the session id to act on |
| `--limit` | integer | no | no | — | `200` | how many messages to return (bounded) |
| `--last` | flag | no | no | — | `false` | return the newest window instead |
| `--no-body` | flag | no | no | — | `false` | omit the message bodies (seq, role, hashes only) |
| `--events` | flag | no | no | — | `false` | include the audit trail |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "session": {
      "type": "object"
    },
    "messages": {
      "type": "array",
      "items": {
        "type": "object"
      }
    },
    "window": {
      "type": "object"
    },
    "history": {
      "type": "object"
    }
  },
  "required": [
    "session",
    "messages",
    "history"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/session_store.py` — the session header and its message window
- `code`: `src/skaldir/session_store.py::SessionStore.verify_history` — the hash check that may fail (exit 2)

**Contract note (verbatim)**

> exit 2 when the recorded history hash does not match the stored messages

#### `session.status` — PERSIST

read-only store snapshot: counts, schema, search engine, writer lease

| | |
|---|---|
| Group | `session` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir holding sessions.db (default: $SKALDIR_HOME) |
| `--store` | string | no | no | — | `—` | explicit sessions.db path (tests/CI) |
| `--owner` | string | no | no | — | `—` | lease owner string (default pid=<pid>:boot=<boot_id>) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "store": {
      "type": "string"
    },
    "schema_version": {
      "type": "integer"
    },
    "counts": {
      "type": "object"
    },
    "search": {
      "type": "object"
    },
    "writer": {
      "type": "object"
    }
  },
  "required": [
    "store",
    "counts"
  ]
}
```

**Default evidence**

- `file`: `sessions.db` — the store this command counts
- `code`: `src/skaldir/session_store.py::SessionStore.status` — the snapshot

#### `session.verify` — PERSIST

recompute the history hash and compare it with what the session recorded

| | |
|---|---|
| Group | `session` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir holding sessions.db (default: $SKALDIR_HOME) |
| `--store` | string | no | no | — | `—` | explicit sessions.db path (tests/CI) |
| `--owner` | string | no | no | — | `—` | lease owner string (default pid=<pid>:boot=<boot_id>) |
| `--session` | string | no | no | — | `—` | the session id to act on |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "checked": {
      "type": "integer"
    },
    "sessions": {
      "type": "array",
      "items": {
        "type": "object"
      }
    },
    "all_match": {
      "type": "boolean"
    },
    "mismatched": {
      "type": "array",
      "items": {
        "type": "string"
      }
    }
  },
  "required": [
    "checked",
    "sessions",
    "all_match"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/session_store.py::SessionStore.verify_history` — recomputes the history hash and every body hash

**Contract note (verbatim)**

> read-only; exit 2 when any checked session does not match

### Group `telemetry`

#### `telemetry.export` — PERSIST

write the record as json, jsonl or csv

| | |
|---|---|
| Group | `telemetry` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `run_id` | string (positional) | yes | no | — | `—` | the run id (`agent run` printed it) |
| `--state-dir` | string | no | no | — | `—` | directory holding runs/ (default: <runtime>/agent) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: the registry's) |
| `--transcript` | string | no | no | — | `—` | read this transcript file instead of resolving a run id |
| `--context-budget` | integer | no | no | — | `—` | declared context window in tokens (default: the layer's SKALDIR_CONTEXT_BUDGET, else 128000) |
| `--trigger-fraction` | number | no | no | — | `—` | compaction fires at this share of the budget (default 0.75) |
| `--price-per-1k-tokens` | number | no | no | — | `—` | declared price in USD per 1000 tokens; without one the cost is null, never guessed |
| `--no-persist` | flag | no | no | — | `false` | do not file the record beside the transcript |
| `--session` | string | no | no | — | `—` | record the evidence under this session id |
| `--as` | choice | no | no | json, jsonl, csv | `json` | export format (default json) |
| `--out` | string | no | no | — | `—` | target file, or - for stdout |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |
| `--text` | flag | no | no | — | `false` | print the human view |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "run_id": {
      "type": "string"
    },
    "duration_ms": {
      "type": [
        "integer",
        "null"
      ]
    },
    "duration": {
      "type": "string"
    },
    "turns": {
      "type": "integer"
    },
    "tool_calls": {
      "type": "object"
    },
    "tokens": {
      "type": "object"
    },
    "tokens_per_second": {
      "type": "object"
    },
    "context": {
      "type": "object"
    },
    "cost": {
      "type": "object"
    },
    "model": {
      "type": [
        "string",
        "null"
      ]
    },
    "provider": {
      "type": "object"
    },
    "footer": {
      "type": "string"
    },
    "estimates": {
      "type": "array"
    },
    "nulls": {
      "type": "array"
    },
    "warnings": {
      "type": "array"
    },
    "format": {
      "type": "string"
    },
    "path": {
      "type": "string"
    },
    "bytes": {
      "type": "integer"
    },
    "content": {
      "type": "string"
    }
  },
  "required": [
    "run_id",
    "format"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/telemetry.py:export_text` — json / jsonl (per turn + run) / csv of the same record

**Contract note (verbatim)**

> writes the export to --out (default: beside the transcript) or to stdout with --out -; in --json mode the export rides in data.content instead, so stdout stays one parseable document

#### `telemetry.live` — PERSIST

the per-turn lines and the shell footer of a run, live or replayed

| | |
|---|---|
| Group | `telemetry` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `run_id` | string (positional) | yes | no | — | `—` | the run id (`agent run` printed it) |
| `--state-dir` | string | no | no | — | `—` | directory holding runs/ (default: <runtime>/agent) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: the registry's) |
| `--transcript` | string | no | no | — | `—` | read this transcript file instead of resolving a run id |
| `--context-budget` | integer | no | no | — | `—` | declared context window in tokens (default: the layer's SKALDIR_CONTEXT_BUDGET, else 128000) |
| `--trigger-fraction` | number | no | no | — | `—` | compaction fires at this share of the budget (default 0.75) |
| `--price-per-1k-tokens` | number | no | no | — | `—` | declared price in USD per 1000 tokens; without one the cost is null, never guessed |
| `--no-persist` | flag | no | no | — | `false` | do not file the record beside the transcript |
| `--session` | string | no | no | — | `—` | record the evidence under this session id |
| `--follow` | flag | no | no | — | `false` | keep reading until the run finishes (or the timeout) |
| `--interval-s` | number | no | no | — | `0.25` | poll interval while following (default 0.25 s) |
| `--timeout-s` | number | no | no | — | `0.0` | stop following after this many seconds (default: wait for the run) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |
| `--text` | flag | no | no | — | `false` | print the human view |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "run_id": {
      "type": "string"
    },
    "duration_ms": {
      "type": [
        "integer",
        "null"
      ]
    },
    "duration": {
      "type": "string"
    },
    "turns": {
      "type": "integer"
    },
    "tool_calls": {
      "type": "object"
    },
    "tokens": {
      "type": "object"
    },
    "tokens_per_second": {
      "type": "object"
    },
    "context": {
      "type": "object"
    },
    "cost": {
      "type": "object"
    },
    "model": {
      "type": [
        "string",
        "null"
      ]
    },
    "provider": {
      "type": "object"
    },
    "footer": {
      "type": "string"
    },
    "estimates": {
      "type": "array"
    },
    "nulls": {
      "type": "array"
    },
    "warnings": {
      "type": "array"
    },
    "lines": {
      "type": "array"
    },
    "followed": {
      "type": "boolean"
    }
  },
  "required": [
    "run_id",
    "lines"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/telemetry.py:TelemetryWatcher` — tails the transcript file by byte offset; the run is never touched
- `code`: `src/skaldir/telemetry.py:footer_line` — the one-line footer: model · ctx · tok/s · duration

**Contract note (verbatim)**

> read-only: the transcript is tailed, never written; a run still open is reported as running with the numbers recorded so far

#### `telemetry.show` — PERSIST

one run's telemetry record (rebuilt from its transcript, then filed beside it)

| | |
|---|---|
| Group | `telemetry` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `run_id` | string (positional) | yes | no | — | `—` | the run id (`agent run` printed it) |
| `--state-dir` | string | no | no | — | `—` | directory holding runs/ (default: <runtime>/agent) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: the registry's) |
| `--transcript` | string | no | no | — | `—` | read this transcript file instead of resolving a run id |
| `--context-budget` | integer | no | no | — | `—` | declared context window in tokens (default: the layer's SKALDIR_CONTEXT_BUDGET, else 128000) |
| `--trigger-fraction` | number | no | no | — | `—` | compaction fires at this share of the budget (default 0.75) |
| `--price-per-1k-tokens` | number | no | no | — | `—` | declared price in USD per 1000 tokens; without one the cost is null, never guessed |
| `--no-persist` | flag | no | no | — | `false` | do not file the record beside the transcript |
| `--session` | string | no | no | — | `—` | record the evidence under this session id |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |
| `--text` | flag | no | no | — | `false` | print the human view |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "run_id": {
      "type": "string"
    },
    "duration_ms": {
      "type": [
        "integer",
        "null"
      ]
    },
    "duration": {
      "type": "string"
    },
    "turns": {
      "type": "integer"
    },
    "tool_calls": {
      "type": "object"
    },
    "tokens": {
      "type": "object"
    },
    "tokens_per_second": {
      "type": "object"
    },
    "context": {
      "type": "object"
    },
    "cost": {
      "type": "object"
    },
    "model": {
      "type": [
        "string",
        "null"
      ]
    },
    "provider": {
      "type": "object"
    },
    "footer": {
      "type": "string"
    },
    "estimates": {
      "type": "array"
    },
    "nulls": {
      "type": "array"
    },
    "warnings": {
      "type": "array"
    }
  },
  "required": [
    "run_id",
    "turns",
    "tokens",
    "tokens_per_second",
    "context"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/telemetry.py:build_record` — the record derived from the run's own transcript (read-only)
- `code`: `src/skaldir/agent_loop.py` — the transcript: run header, request bodies, answers, tool durations

**Contract note (verbatim)**

> the transcript is the source of truth and the record is filed beside it as <run-id>.telemetry.json; an unpriced run reports cost.usd=null with the reason and a labelled estimate says so — `estimates` and `nulls` index both

### Group `contract`

#### `contract.dump` — kernel

the self-describing inventory with JSON Schemas

| | |
|---|---|
| Group | `contract` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--out` | string | no | no | — | `—` | also write the inventory to FILE |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "schema_version": {
      "type": "string"
    },
    "godsvoice_version": {
      "type": "string"
    },
    "root_commands": {
      "type": "integer"
    },
    "leaf_commands": {
      "type": "integer"
    },
    "parser_nodes": {
      "type": "integer"
    },
    "unregistered": {
      "type": "array"
    },
    "commands": {
      "type": "array"
    }
  },
  "required": [
    "schema_version",
    "leaf_commands",
    "commands"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/cli_contract.py`

#### `contract.list` — kernel

every command, its layer and its JSON flag

| | |
|---|---|
| Group | `contract` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--layer` | string | no | no | — | `—` | filter by layer |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "commands": {
      "type": "array"
    },
    "count": {
      "type": "integer"
    }
  },
  "required": [
    "commands",
    "count"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/cli_contract.py`

#### `contract.show` — kernel

one command's full contract record

| | |
|---|---|
| Group | `contract` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `command` | string (positional) | yes | no | — | `—` | dotted command path, e.g. tools.show |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/cli_contract.py`

### Group `daemon`

#### `daemon.serve` — kernel

the `daemon` spelling of `skaldir serve` (MVP/02 §5.10)

| | |
|---|---|
| Group | `daemon` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--http` | flag | no | no | — | `false` | use the HTTP transport (required; it is the only transport implemented today) |
| `--json-http` | flag | no | no | — | `false` | synonym of --http (the spelling MVP/08 §0 uses for the Schemathesis tier) |
| `--port` | integer | no | no | — | `8765` | TCP port (default: 8765) |
| `--host` | string | no | no | — | `127.0.0.1` | bind address (default: 127.0.0.1; loopback unless --allow-remote) |
| `--allow-remote` | flag | no | no | — | `false` | allow a non-loopback bind (explicit opt-in; mutating routes still need a token) |
| `--token-file` | string | no | no | — | `—` | read the API token from FILE instead of generating one (0600 recommended) |
| `--request-timeout` | number | no | no | — | `120.0` | deadline for one command in seconds (default: 120; expiry is exit 8 / HTTP 504) |
| `--max-body-bytes` | integer | no | no | — | `1048576` | maximum request body in bytes (default: 1048576) |
| `--workdir` | string | no | no | — | `—` | working directory the served commands run in (default: the server's own cwd) |
| `--cors-origin` | string | no | yes | — | `[]` | allow this browser origin (repeatable; off by default, since token-free read-only routes must not be reachable from a page) |
| `--openapi` | string | no | no | — | `—` | also write the OpenAPI document to FILE at start (the Schemathesis input) |
| `--transport` | choice | no | no | auto, warm, inprocess, spawn | `auto` | how a command is executed: auto (per-request: in-process for the measured allowlist, else the warm session, else a child process; default), warm (the running warm session only), inprocess (this process only), spawn (a child process per request — the pre-streaming behaviour) |
| `--warm-socket` | string | no | no | — | `—` | unix socket of the warm session to reuse (default: the session's own path, i.e. $SKALDIR_WARM_SOCKET or $XDG_RUNTIME_DIR/<pkg>/warm.sock) |
| `--sse-heartbeat` | number | no | no | — | `10.0` | seconds of silence before an SSE stream writes a keep-alive comment (default: 10) |
| `--keep-alive` | number | no | no | — | `30.0` | seconds an idle HTTP connection is kept open for reuse (default: 30) |
| `--log-level` | choice | no | no | critical, error, warning, info, debug, trace | `info` | uvicorn/log level (default: info) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/api_server.py` — one route per CLI command, generated from cli_contract.COMMANDS + build_parser()
- `file`: `GET /openapi.json` — the OpenAPI 3.1 document served at runtime

**Contract note (verbatim)**

> long-running command: the startup facts — including the API token, which is printed exactly once — go to stderr so stdout stays reserved for the single envelope; the envelope is emitted when the server stops. A missing fastapi/uvicorn, a non-loopback host without --allow-remote, and a taken port all exit 3 (blocked) naming the exact reason

#### `daemon.status` — kernel

report whether a Skaldir API server answers on a host:port

| | |
|---|---|
| Group | `daemon` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--host` | string | no | no | — | `127.0.0.1` | host to probe |
| `--port` | integer | no | no | — | `8765` | port to probe |
| `--json` | flag | no | no | — | `false` | machine-readable output: one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "running": {
      "type": "boolean"
    },
    "endpoint": {
      "type": "string"
    },
    "host": {
      "type": "string"
    },
    "port": {
      "type": "integer"
    },
    "port_open": {
      "type": "boolean",
      "description": "something is listening on the port"
    },
    "skaldir": {
      "type": "boolean",
      "description": "true when GET /health answered"
    },
    "godsvoice_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "schema_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "routes": {
      "type": [
        "integer",
        "null"
      ]
    },
    "mutating_routes": {
      "type": [
        "integer",
        "null"
      ]
    },
    "token_required_for_mutations": {
      "type": [
        "boolean",
        "null"
      ]
    },
    "openapi_path": {
      "type": [
        "string",
        "null"
      ]
    },
    "excluded": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "latency_ms": {
      "type": "integer"
    }
  },
  "required": [
    "running",
    "endpoint",
    "host",
    "port",
    "port_open",
    "skaldir",
    "latency_ms"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/api_server.py` — probe_existing_server + status_for_exit; the handler adds the live endpoint

**Contract note (verbatim)**

> read-only TCP connect + GET /health (0.6 s timeouts); exit 5 when nothing answers, so a GUI can tell 'no server' apart from 'server refused my call'. Reporting here is honest: there is no daemon manager behind this command

### Group `desktop`

#### `desktop.build-app` — kernel

build the app natively

| | |
|---|---|
| Group | `desktop` |
| Layer | kernel |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--force` | flag | no | no | — | `false` | refresh the source mirror from scratch |
| `--perf-probe` | flag | no | no | — | `false` | measurement build: embed the dev-only perf driver (VITE_PERF_PROBE=1) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/appbuild.py`

#### `desktop.install` — kernel

desktop entry + icons

| | |
|---|---|
| Group | `desktop` |
| Layer | kernel |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--no-link` | flag | no | no | — | `false` | skip the ~/.local/bin symlink |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/desktop.py`

#### `desktop.start` — kernel

verify, then start the app

| | |
|---|---|
| Group | `desktop` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--check-only` | flag | no | no | — | `false` | only verify, do not start |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/desktop.py`

#### `desktop.status` — kernel

deployment check

| | |
|---|---|
| Group | `desktop` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/desktop.py`

#### `desktop.uninstall` — kernel

remove launcher + icons

| | |
|---|---|
| Group | `desktop` |
| Layer | kernel |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--remove-plugin` | flag | no | no | — | `false` | full teardown: also remove the $HERMES_HOME/plugins/godsv symlink |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/desktop.py`

### Group `host`

#### `host.cron` — kernel

the host's scheduled jobs

| | |
|---|---|
| Group | `host` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `4` (NEEDS_CREDENTIALS), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--profile` | string | no | no | — | `all` | which profile's jobs (default: all) |
| `--url` | string | no | no | — | `—` | a host backend, e.g. http://127.0.0.1:42703 (default: scan /proc, else $SKALDIR_HOST_URL) |
| `--token` | string | no | no | — | `—` | the backend's loopback session token (default: the backend process's own environment, else its SPA document; $SKALDIR_HOST_TOKEN is the env fallback) |
| `--timeout` | number | no | no | — | `8.0` | per-request seconds (default 8) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "backend": {
      "type": [
        "object",
        "null"
      ]
    },
    "request": {
      "type": "string"
    },
    "profile": {
      "type": "string"
    },
    "source": {
      "type": "string"
    },
    "count": {
      "type": "integer"
    },
    "jobs": {
      "type": "array"
    },
    "transport": {
      "type": "string"
    },
    "fragile": {
      "type": "boolean"
    }
  },
  "required": [
    "count",
    "jobs",
    "source"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/host_bridge.py:910` — cron_jobs() — GET /api/cron/jobs

**Contract note (verbatim)**

> listing only — creating, running or pausing a job is out of scope for this block

#### `host.doctor` — kernel

what of the host is reachable from here, route by route

| | |
|---|---|
| Group | `host` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `4` (NEEDS_CREDENTIALS), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--probe-cli` | flag | no | no | — | `false` | also run `hermes serve --status` and report it as (blind) evidence |
| `--url` | string | no | no | — | `—` | a host backend, e.g. http://127.0.0.1:42703 (default: scan /proc, else $SKALDIR_HOST_URL) |
| `--token` | string | no | no | — | `—` | the backend's loopback session token (default: the backend process's own environment, else its SPA document; $SKALDIR_HOST_TOKEN is the env fallback) |
| `--timeout` | number | no | no | — | `8.0` | per-request seconds (default 8) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "installed": {
      "type": "boolean"
    },
    "running": {
      "type": "boolean"
    },
    "backend": {
      "type": [
        "object",
        "null"
      ]
    },
    "discovery": {
      "type": [
        "object",
        "null"
      ]
    },
    "routes": {
      "type": "array"
    },
    "reachable": {
      "type": "object"
    },
    "blocked": {
      "type": "object"
    },
    "cli_serve_status": {
      "type": "object"
    },
    "notes": {
      "type": "array"
    }
  },
  "required": [
    "installed",
    "running",
    "routes",
    "reachable",
    "blocked"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/host_bridge.py:991` — doctor() — one GET per route, with each verb's own params

**Contract note (verbatim)**

> exit 0 only when every probed route answered; --probe-cli adds `hermes serve --status` as evidence (it cannot see a --port 0 serve)

#### `host.sessions` — kernel

the host's session store, read-only

| | |
|---|---|
| Group | `host` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `4` (NEEDS_CREDENTIALS), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--limit` | integer | no | no | — | `20` | rows to ask for (default 20) |
| `--offset` | integer | no | no | — | `0` | page offset (default 0) |
| `--order` | choice | no | no | recent, created | `recent` | sort order (default recent) |
| `--profile` | string | no | no | — | `default` | which profile's store to read; `-` omits the parameter (default: default) |
| `--via` | choice | no | no | http, cli | `http` | http (the machine-readable API, default) or cli (parses the host CLI's human table — FRAGILE, marked in the payload) |
| `--url` | string | no | no | — | `—` | a host backend, e.g. http://127.0.0.1:42703 (default: scan /proc, else $SKALDIR_HOST_URL) |
| `--token` | string | no | no | — | `—` | the backend's loopback session token (default: the backend process's own environment, else its SPA document; $SKALDIR_HOST_TOKEN is the env fallback) |
| `--timeout` | number | no | no | — | `8.0` | per-request seconds (default 8) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "backend": {
      "type": [
        "object",
        "null"
      ]
    },
    "request": {
      "type": "string"
    },
    "profile": {
      "type": "string"
    },
    "store": {
      "type": "string"
    },
    "total": {
      "type": [
        "integer",
        "null"
      ]
    },
    "count": {
      "type": "integer"
    },
    "pinned": {
      "type": "integer"
    },
    "limit": {
      "type": "integer"
    },
    "offset": {
      "type": "integer"
    },
    "order": {
      "type": "string"
    },
    "note": {
      "type": "string"
    },
    "sessions": {
      "type": "array"
    },
    "transport": {
      "type": "string"
    },
    "fragile": {
      "type": "boolean"
    }
  },
  "required": [
    "profile",
    "count",
    "sessions",
    "transport",
    "fragile"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/host_bridge.py:690` — sessions() — GET /api/sessions with an explicit profile

**Contract note (verbatim)**

> --via http (default) is the machine-readable route; --via cli parses the host CLI's human table and marks the payload fragile:true. The default profile is the machine-level store: the bare route reads the backend process's own (usually empty)

#### `host.skills` — kernel

the host's installed skills

| | |
|---|---|
| Group | `host` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `4` (NEEDS_CREDENTIALS), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--profile` | string | no | no | — | `—` | a profile's skills (default: host's own) |
| `--url` | string | no | no | — | `—` | a host backend, e.g. http://127.0.0.1:42703 (default: scan /proc, else $SKALDIR_HOST_URL) |
| `--token` | string | no | no | — | `—` | the backend's loopback session token (default: the backend process's own environment, else its SPA document; $SKALDIR_HOST_TOKEN is the env fallback) |
| `--timeout` | number | no | no | — | `8.0` | per-request seconds (default 8) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "backend": {
      "type": [
        "object",
        "null"
      ]
    },
    "request": {
      "type": "string"
    },
    "profile": {
      "type": "string"
    },
    "source": {
      "type": "string"
    },
    "count": {
      "type": "integer"
    },
    "enabled": {
      "type": "integer"
    },
    "by_provenance": {
      "type": "object"
    },
    "skills": {
      "type": "array"
    },
    "transport": {
      "type": "string"
    },
    "fragile": {
      "type": "boolean"
    }
  },
  "required": [
    "count",
    "skills",
    "source"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/host_bridge.py:873` — skills() — GET /api/skills

**Contract note (verbatim)**

> the host's own /api/skills catalogue (bundled/hub/agent provenance, enabled flags)

#### `host.status` — kernel

is a host installed, is one running, which version, which home

| | |
|---|---|
| Group | `host` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `4` (NEEDS_CREDENTIALS), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--url` | string | no | no | — | `—` | a host backend, e.g. http://127.0.0.1:42703 (default: scan /proc, else $SKALDIR_HOST_URL) |
| `--token` | string | no | no | — | `—` | the backend's loopback session token (default: the backend process's own environment, else its SPA document; $SKALDIR_HOST_TOKEN is the env fallback) |
| `--timeout` | number | no | no | — | `8.0` | per-request seconds (default 8) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "installed": {
      "type": "boolean"
    },
    "binary": {
      "type": [
        "string",
        "null"
      ]
    },
    "version": {
      "type": [
        "string",
        "null"
      ]
    },
    "version_source": {
      "type": [
        "string",
        "null"
      ]
    },
    "hermes_home": {
      "type": "string"
    },
    "running": {
      "type": "boolean"
    },
    "backend": {
      "type": [
        "object",
        "null"
      ]
    },
    "auth_required": {
      "type": [
        "boolean",
        "null"
      ]
    },
    "gateway": {
      "type": [
        "object",
        "null"
      ]
    },
    "components": {
      "type": [
        "object",
        "null"
      ]
    },
    "discovery": {
      "type": [
        "object",
        "null"
      ]
    },
    "reason": {
      "type": "string"
    }
  },
  "required": [
    "installed",
    "running",
    "hermes_home"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/host_bridge.py:629` — status() — install facts + the live backend's own /api/health and /api/status

**Contract note (verbatim)**

> reads the host's own surfaces only; no token is needed for its two public routes. exit 4 = no host installed, exit 3 = installed but no backend answering

#### `host.tools` — kernel

the host's toolsets and their tools

| | |
|---|---|
| Group | `host` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `4` (NEEDS_CREDENTIALS), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--profile` | string | no | no | — | `—` | a profile's toolsets (default: host's own) |
| `--url` | string | no | no | — | `—` | a host backend, e.g. http://127.0.0.1:42703 (default: scan /proc, else $SKALDIR_HOST_URL) |
| `--token` | string | no | no | — | `—` | the backend's loopback session token (default: the backend process's own environment, else its SPA document; $SKALDIR_HOST_TOKEN is the env fallback) |
| `--timeout` | number | no | no | — | `8.0` | per-request seconds (default 8) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "backend": {
      "type": [
        "object",
        "null"
      ]
    },
    "request": {
      "type": "string"
    },
    "profile": {
      "type": "string"
    },
    "source": {
      "type": "string"
    },
    "count": {
      "type": "integer"
    },
    "tool_count": {
      "type": "integer"
    },
    "toolsets": {
      "type": "array"
    },
    "transport": {
      "type": "string"
    },
    "fragile": {
      "type": "boolean"
    }
  },
  "required": [
    "count",
    "toolsets",
    "source"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/host_bridge.py:943` — toolsets() — GET /api/tools/toolsets

**Contract note (verbatim)**

> what the host has and what is enabled/configured — enabling or disabling is a write

### Group `host.memory`

#### `host.memory.search` — kernel

search the host's own index (FTS5 over its message store)

| | |
|---|---|
| Group | `host.memory` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `4` (NEEDS_CREDENTIALS), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `query` | string (positional) | yes | no | — | `—` | the text to search for |
| `--limit` | integer | no | no | — | `20` | results to ask for (default 20) |
| `--profile` | string | no | no | — | `default` | which profile's store to search (default: default) |
| `--url` | string | no | no | — | `—` | a host backend, e.g. http://127.0.0.1:42703 (default: scan /proc, else $SKALDIR_HOST_URL) |
| `--token` | string | no | no | — | `—` | the backend's loopback session token (default: the backend process's own environment, else its SPA document; $SKALDIR_HOST_TOKEN is the env fallback) |
| `--timeout` | number | no | no | — | `8.0` | per-request seconds (default 8) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "backend": {
      "type": [
        "object",
        "null"
      ]
    },
    "request": {
      "type": "string"
    },
    "query": {
      "type": "string"
    },
    "profile": {
      "type": "string"
    },
    "source": {
      "type": "string"
    },
    "searches": {
      "type": "string"
    },
    "count": {
      "type": "integer"
    },
    "results": {
      "type": "array"
    },
    "transport": {
      "type": "string"
    },
    "fragile": {
      "type": "boolean"
    }
  },
  "required": [
    "query",
    "count",
    "results",
    "fragile"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/host_bridge.py:820` — memory_search() — GET /api/sessions/search (FTS5)

**Contract note (verbatim)**

> read-only search over the host's session/message index — not MEMORY.md

### Group `prompts`

#### `prompts.list` — kernel

list prompts and check required ids

| | |
|---|---|
| Group | `prompts` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/prompts.py`

### Group `provider`

#### `provider.add` — kernel

add (or replace) one provider and project it into godsv.llm

| | |
|---|---|
| Group | `provider` |
| Layer | kernel |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | provider name (lowercase slug) |
| `--base-url` | string | yes | no | — | `—` | endpoint root, e.g. https://<provider-host>/v1 |
| `--model` | string | yes | no | — | `—` | default model for this provider |
| `--api-key-env` | string | no | no | — | `—` | the NAME of the environment variable holding the key (never the key) |
| `--kind` | choice | no | no | openai-compatible | `openai-compatible` | provider kind (default openai-compatible) |
| `--tier-small` | string | no | no | — | `—` | model for the 'small' tier |
| `--tier-mid` | string | no | no | — | `—` | model for the 'mid' tier |
| `--tier-main` | string | no | no | — | `—` | model for the 'main' tier |
| `--timeout` | number | no | no | — | `—` | per-request timeout in seconds |
| `--max-tokens` | integer | no | no | — | `—` | max_tokens sent per request |
| `--default` | flag | no | no | — | `false` | make it the active provider (default: the first one) |
| `--no-default` | flag | no | no | — | `false` | add it without touching the active provider |
| `--force` | flag | no | no | — | `false` | replace an existing provider of that name |
| `--config` | string | no | no | — | `==SUPPRESS==` | the runtime config file (default: $SKALDIR_LLM_CONFIG, else <runtime>/config.yaml — $SKALDIR_HOME, $HERMES_HOME/godsv, ~/.hermes/godsv); the same file the provider layer reads |
| `--json` | flag | no | no | — | `==SUPPRESS==` | print one JSON payload on stdout |
| `--format` | choice | no | no | text, json, jsonl | `==SUPPRESS==` | output format (text\|json\|jsonl) |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "action": {
      "type": "string"
    },
    "name": {
      "type": "string"
    },
    "default": {
      "type": "string"
    },
    "path": {
      "type": "string"
    },
    "backup": {
      "type": [
        "string",
        "null"
      ]
    },
    "provider": {
      "type": "object"
    }
  },
  "required": [
    "action",
    "name",
    "path"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/provider_config.py` — atomic write + backup
- `test`: `tests/test_provider_cli.py` — the secret-refusal and stub-endpoint proofs

**Contract note (verbatim)**

> a literal api_key/token/secret is refused (exit 3); --api-key-env takes a variable NAME only; the previous file is backed up

#### `provider.catalog` — kernel

the verified multi-provider catalog: base URLs, auth, streaming, structured output, vision — each claim with its verification status

| | |
|---|---|
| Group | `provider` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--entry` | string | no | no | — | `—` | one provider in full detail (known: openai, anthropic, google-gemini, google-vertex, xai, deepseek, moonshot, alibaba-qwen, openrouter, zai, xiaomi-mimo, opencode-go, typesafe, llamacpp, vllm, lmstudio, ollama, alibaba-qwen-intl, minimax, mistral, cohere, ai21, groq, cerebras, together, fireworks, deepinfra, huggingface, nvidia, novita, sambanova, siliconflow, nebius, perplexity, opencode-zen, stepfun, hunyuan, qianfan, volcengine, meta, amazon-bedrock, azure-openai, cloudflare) |
| `--verification` | string | no | no | — | `—` | only entries at this status: verified \| documented \| unverified |
| `--probes` | flag | no | no | — | `false` | also print the live probe log (the evidence behind every verified claim) |
| `--text` | flag | no | no | — | `false` | human text even when a JSON default is inherited |
| `--config` | string | no | no | — | `==SUPPRESS==` | the runtime config file (default: $SKALDIR_LLM_CONFIG, else <runtime>/config.yaml — $SKALDIR_HOME, $HERMES_HOME/godsv, ~/.hermes/godsv); the same file the provider layer reads |
| `--json` | flag | no | no | — | `==SUPPRESS==` | print one JSON payload on stdout |
| `--format` | choice | no | no | text, json, jsonl | `==SUPPRESS==` | output format (text\|json\|jsonl) |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "count": {
      "type": "integer"
    },
    "order": {
      "type": "array"
    },
    "verification_counts": {
      "type": "object"
    },
    "probe_counts": {
      "type": "object"
    },
    "probe_date": {
      "type": "string"
    },
    "results": {
      "type": "array"
    },
    "features": {
      "type": "array"
    },
    "drift": {
      "type": "object"
    },
    "probes": {
      "type": "object"
    },
    "chain": {
      "type": "object"
    }
  },
  "required": [
    "count",
    "results",
    "verification_counts"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/provider_registry_data.py` — the breadth registry + the live probe log
- `code`: `src/skaldir/provider_catalog.py` — drift check against the chain catalog

**Contract note (verbatim)**

> verified = a live probe we ran; documented = an official page (the fetched flag says whether we retrieved it); unverified = nobody proved it. --entry names one provider, --verification filters, --probes prints the evidence; an unknown --entry exits 5

#### `provider.default` — kernel

which provider the runtime uses (mirrored into godsv.llm)

| | |
|---|---|
| Group | `provider` |
| Layer | kernel |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | provider name |
| `--config` | string | no | no | — | `==SUPPRESS==` | the runtime config file (default: $SKALDIR_LLM_CONFIG, else <runtime>/config.yaml — $SKALDIR_HOME, $HERMES_HOME/godsv, ~/.hermes/godsv); the same file the provider layer reads |
| `--json` | flag | no | no | — | `==SUPPRESS==` | print one JSON payload on stdout |
| `--format` | choice | no | no | text, json, jsonl | `==SUPPRESS==` | output format (text\|json\|jsonl) |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "action": {
      "type": "string"
    },
    "name": {
      "type": "string"
    },
    "projection": {
      "type": "object"
    }
  },
  "required": [
    "action",
    "name"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/provider_config.py` — projection write
- `code`: `src/skaldir/llm_provider.py` — the reader of godsv.llm

**Contract note (verbatim)**

> the projection is what `skaldir` runs with; the secret is still resolved from the environment

#### `provider.doctor` — kernel

what is missing for each configured provider (offline)

| | |
|---|---|
| Group | `provider` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--config` | string | no | no | — | `==SUPPRESS==` | the runtime config file (default: $SKALDIR_LLM_CONFIG, else <runtime>/config.yaml — $SKALDIR_HOME, $HERMES_HOME/godsv, ~/.hermes/godsv); the same file the provider layer reads |
| `--json` | flag | no | no | — | `==SUPPRESS==` | print one JSON payload on stdout |
| `--format` | choice | no | no | text, json, jsonl | `==SUPPRESS==` | output format (text\|json\|jsonl) |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "config_file": {
      "type": "string"
    },
    "default": {
      "type": "string"
    },
    "count": {
      "type": "integer"
    },
    "ok": {
      "type": "boolean"
    },
    "providers": {
      "type": "array"
    },
    "usable": {
      "type": "array"
    },
    "broken": {
      "type": "array"
    },
    "next_actions": {
      "type": "array"
    }
  },
  "required": [
    "config_file",
    "providers",
    "ok"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/provider_config.py` — offline diagnosis
- `test`: `tests/test_provider_cli.py` — the doctor cases

**Contract note (verbatim)**

> exit 3 when a CONFIGURED provider is unusable; nothing configured exits 0 with ok=false and the command to run; no request is made

#### `provider.list` — kernel

configured providers, redacted (default marked)

| | |
|---|---|
| Group | `provider` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--config` | string | no | no | — | `==SUPPRESS==` | the runtime config file (default: $SKALDIR_LLM_CONFIG, else <runtime>/config.yaml — $SKALDIR_HOME, $HERMES_HOME/godsv, ~/.hermes/godsv); the same file the provider layer reads |
| `--json` | flag | no | no | — | `==SUPPRESS==` | print one JSON payload on stdout |
| `--format` | choice | no | no | text, json, jsonl | `==SUPPRESS==` | output format (text\|json\|jsonl) |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "config_file": {
      "type": "string"
    },
    "default": {
      "type": "string"
    },
    "count": {
      "type": "integer"
    },
    "providers": {
      "type": "array"
    },
    "active": {
      "type": "object"
    },
    "drift": {
      "type": "array"
    }
  },
  "required": [
    "config_file",
    "providers",
    "count"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/provider_config.py` — the store
- `code`: `src/skaldir/llm_provider.py` — the same file/keys the runtime resolves

**Contract note (verbatim)**

> never prints a credential: api_key_env is a NAME, the state is set/unset/not-required

#### `provider.remove` — kernel

remove one provider (a removed default clears the projection)

| | |
|---|---|
| Group | `provider` |
| Layer | kernel |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | provider name |
| `--config` | string | no | no | — | `==SUPPRESS==` | the runtime config file (default: $SKALDIR_LLM_CONFIG, else <runtime>/config.yaml — $SKALDIR_HOME, $HERMES_HOME/godsv, ~/.hermes/godsv); the same file the provider layer reads |
| `--json` | flag | no | no | — | `==SUPPRESS==` | print one JSON payload on stdout |
| `--format` | choice | no | no | text, json, jsonl | `==SUPPRESS==` | output format (text\|json\|jsonl) |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "action": {
      "type": "string"
    },
    "name": {
      "type": "string"
    },
    "remaining": {
      "type": "array"
    },
    "projection_cleared": {
      "type": "boolean"
    }
  },
  "required": [
    "action",
    "name",
    "remaining"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/provider_config.py` — registry write
- `test`: `tests/test_provider_cli.py` — the removal cases

**Contract note (verbatim)**

> never guesses a new default: the runtime stays unconfigured until `provider default` names one

#### `provider.test` — kernel

one real call: latency, model echo, answer (redacted); --structured asks for a schema-constrained answer

| | |
|---|---|
| Group | `provider` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `4` (NEEDS_CREDENTIALS), `5` (NOT_FOUND), `7` (INTERNAL), `8` (UNKNOWN) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | provider (default: the default one) |
| `--tier` | choice | no | no | small, mid, main | `small` | tier to probe (default small) |
| `--prompt` | string | no | no | — | `—` | prompt to send (default: a fixed 'pong' probe) |
| `--timeout` | number | no | no | — | `—` | per-request timeout in seconds |
| `--structured` | flag | no | no | — | `false` | ask for a schema-constrained answer and report which mode answered (native json_schema / json_object / a prompted answer validated locally) |
| `--mode` | choice | no | no | auto, native, json_object, prompted | `—` | which structured mode to use with --structured (default: auto) |
| `--schema-file` | string | no | no | — | `—` | a JSON Schema file for --structured (default: the built-in probe schema) |
| `--config` | string | no | no | — | `==SUPPRESS==` | the runtime config file (default: $SKALDIR_LLM_CONFIG, else <runtime>/config.yaml — $SKALDIR_HOME, $HERMES_HOME/godsv, ~/.hermes/godsv); the same file the provider layer reads |
| `--json` | flag | no | no | — | `==SUPPRESS==` | print one JSON payload on stdout |
| `--format` | choice | no | no | text, json, jsonl | `==SUPPRESS==` | output format (text\|json\|jsonl) |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "name": {
      "type": "string"
    },
    "endpoint": {
      "type": "string"
    },
    "model": {
      "type": "string"
    },
    "model_echo": {
      "type": "string"
    },
    "latency_ms": {
      "type": "integer"
    },
    "answer_preview": {
      "type": "string"
    },
    "credential": {
      "type": "string"
    },
    "client": {
      "type": "string"
    },
    "mode_used": {
      "type": "string"
    },
    "native_json_schema": {
      "type": [
        "boolean",
        "null"
      ]
    },
    "validation": {
      "type": "object"
    },
    "fallback_chain": {
      "type": "object"
    }
  },
  "required": [
    "name",
    "endpoint",
    "model",
    "latency_ms"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/llm_provider.py` — OpenAICompatibleLLM, the real client
- `code`: `src/skaldir/system_one_policy.py` — the retry/timeout policy the call follows
- `code`: `src/skaldir/structured.py` — --structured: the schema-constrained call, its validation and the mode trail
- `code`: `src/skaldir/provider_catalog.py` — --structured: the fallback chain it reports

**Contract note (verbatim)**

> exit 4 when a remote provider has no credential — no request is attempted; 401/403 also exit 4; a timeout exits 8; with --structured an answer that does not satisfy the schema exits 2 and carries the raw text + the validation error (never coerced)

#### `provider.tiers` — kernel

the small/mid/main → model mapping (show, or set with --small/--main)

| | |
|---|---|
| Group | `provider` |
| Layer | kernel |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | provider (default: the default one) |
| `--small` | string | no | no | — | `—` | model for the 'small' tier |
| `--mid` | string | no | no | — | `—` | model for the 'mid' tier |
| `--main` | string | no | no | — | `—` | model for the 'main' tier |
| `--replace` | flag | no | no | — | `false` | replace the whole mapping |
| `--config` | string | no | no | — | `==SUPPRESS==` | the runtime config file (default: $SKALDIR_LLM_CONFIG, else <runtime>/config.yaml — $SKALDIR_HOME, $HERMES_HOME/godsv, ~/.hermes/godsv); the same file the provider layer reads |
| `--json` | flag | no | no | — | `==SUPPRESS==` | print one JSON payload on stdout |
| `--format` | choice | no | no | text, json, jsonl | `==SUPPRESS==` | output format (text\|json\|jsonl) |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "name": {
      "type": "string"
    },
    "tiers": {
      "type": "object"
    },
    "requested": {
      "type": "object"
    }
  },
  "required": [
    "name"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/provider_config.py` — tier mapping
- `code`: `src/skaldir/llm.py` — TIERS: small/mid/main

**Contract note (verbatim)**

> without --small/--mid/--main it only reads; an empty value clears that tier and the provider model stays the fallback

### Group `spend`

#### `spend.allowance` — kernel

test-money allowance

| | |
|---|---|
| Group | `spend` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--tier` | choice | no | no | stufe0, stufe1, stufe2 | `stufe2` | — |
| `--db` | string | no | no | — | `/home/willy/.hermes/godsv/treasury.db` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/treasury.py`

#### `spend.approve` — kernel

approve a request

| | |
|---|---|
| Group | `spend` |
| Layer | kernel |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `request` | string (positional) | yes | no | — | `—` | — |
| `--db` | string | no | no | — | `/home/willy/.hermes/godsv/treasury.db` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/treasury.py`

#### `spend.complete` — kernel

mark a spend executed

| | |
|---|---|
| Group | `spend` |
| Layer | kernel |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `request` | string (positional) | yes | no | — | `—` | — |
| `--db` | string | no | no | — | `/home/willy/.hermes/godsv/treasury.db` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/treasury.py`

#### `spend.deny` — kernel

deny a request

| | |
|---|---|
| Group | `spend` |
| Layer | kernel |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `request` | string (positional) | yes | no | — | `—` | — |
| `--reason` | string | no | no | — | `—` | — |
| `--db` | string | no | no | — | `/home/willy/.hermes/godsv/treasury.db` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/treasury.py`

#### `spend.list` — kernel

list open spend requests

| | |
|---|---|
| Group | `spend` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--db` | string | no | no | — | `/home/willy/.hermes/godsv/treasury.db` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/treasury.py`

#### `spend.request` — kernel

request a spend

| | |
|---|---|
| Group | `spend` |
| Layer | kernel |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `amount` | number (positional) | yes | no | — | `—` | — |
| `--merchant` | string | yes | no | — | `—` | — |
| `--purpose` | string | no | no | — | `—` | — |
| `--currency` | string | no | no | — | `EUR` | — |
| `--tier` | choice | no | no | stufe0, stufe1, stufe2 | `stufe0` | — |
| `--db` | string | no | no | — | `/home/willy/.hermes/godsv/treasury.db` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/treasury.py`

### Group `style`

#### `style.logo` — kernel

the Skaldir CLI art (full banner or compact mark)

| | |
|---|---|
| Group | `style` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `which` | choice (positional) | yes | no | banner, mark, splash | `banner` | which piece of art (default: banner) |
| `--ascii` | flag | no | no | — | `false` | force the plain ASCII rendering |
| `--json` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/theme.py`
- `code`: `src/skaldir/logo.py`

#### `style.palette` — kernel

the grey ramp, the tokens and the status marks

| | |
|---|---|
| Group | `style` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/theme.py`

#### `style.preview` — kernel

every helper of the visual language, one screen

| | |
|---|---|
| Group | `style` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/theme.py`

### Group `vault`

#### `vault.add` — kernel

add a secret

| | |
|---|---|
| Group | `vault` |
| Layer | kernel |
| Runtime effect | mutates state |
| Credentials | required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--service` | string | yes | no | — | `—` | — |
| `--name` | string | yes | no | — | `—` | — |
| `--scopes` | string | no | no | — | `use` | — |
| `--description` | string | no | no | — | `—` | — |
| `--vault` | string | no | no | — | `/home/willy/.hermes/godsv/vault.json` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/treasury.py`

**Contract note (verbatim)**

> a missing vault key exits 1 (see vault.list)

#### `vault.describe` — kernel

describe one handle

| | |
|---|---|
| Group | `vault` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `handle` | string (positional) | yes | no | — | `—` | — |
| `--vault` | string | no | no | — | `/home/willy/.hermes/godsv/vault.json` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/treasury.py`

**Contract note (verbatim)**

> a missing vault key exits 1 (see vault.list)

#### `vault.list` — kernel

list credential handles

| | |
|---|---|
| Group | `vault` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--vault` | string | no | no | — | `/home/willy/.hermes/godsv/vault.json` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/treasury.py`

**Contract note (verbatim)**

> a missing SKALDIR_VAULT_KEY exits 1, not the designed 4 (needs_credentials): tests/test_treasury.py::test_vault_cli_requires_key pins 1; the GUI's provider screen must read error.message until that test is updated

#### `vault.revoke` — kernel

revoke a handle

| | |
|---|---|
| Group | `vault` |
| Layer | kernel |
| Runtime effect | mutates state |
| Credentials | required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `handle` | string (positional) | yes | no | — | `—` | — |
| `--vault` | string | no | no | — | `/home/willy/.hermes/godsv/vault.json` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/treasury.py`

**Contract note (verbatim)**

> a missing vault key exits 1 (see vault.list)

### Group `warm`

#### `warm.start` — kernel

start the warm CLI session (one long-lived process behind a 0600 unix socket)

| | |
|---|---|
| Group | `warm` |
| Layer | kernel |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--socket` | string | no | no | — | `—` | session socket (default: $XDG_RUNTIME_DIR/<app>/warm.sock) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |
| `--idle-timeout` | number | no | no | — | `—` | stop after this many idle seconds (default 900) |
| `--no-parser-cache` | flag | no | no | — | `false` | do not reuse the argparse tree the session built (slower, useful when debugging a default that depends on the environment) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/warm_session.py` — WarmServer.acquire — lock, bind, preload; the client waits for a live ping

**Contract note (verbatim)**

> opt-in and read-only by contract: only commands whose cli_contract entry does not mutate run warm, everything else falls back to the cold path with a note. The session is a unix socket (0600, owned by you, in a 0700 directory), never a TCP port; a stale lock is detected with flock and cleaned up only by `warm start`/`stop`; `warm start` is idempotent: an already running session is reported, not duplicated

#### `warm.status` — kernel

report the warm session: pid, socket, uptime, served commands, memory, env match

| | |
|---|---|
| Group | `warm` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--socket` | string | no | no | — | `—` | session socket (default: $XDG_RUNTIME_DIR/<app>/warm.sock) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/warm_session.py` — status_of — lstat + flock + a status round trip

**Contract note (verbatim)**

> opt-in and read-only by contract: only commands whose cli_contract entry does not mutate run warm, everything else falls back to the cold path with a note. The session is a unix socket (0600, owned by you, in a 0700 directory), never a TCP port; a stale lock is detected with flock and cleaned up only by `warm start`/`stop`; exit 0 running, 5 nothing running, 3 socket refused, 6 lock held without an answer

#### `warm.stop` — kernel

stop the warm CLI session (and say when there was nothing to stop)

| | |
|---|---|
| Group | `warm` |
| Layer | kernel |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--socket` | string | no | no | — | `—` | session socket (default: $XDG_RUNTIME_DIR/<app>/warm.sock) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |
| `--force` | flag | no | no | — | `false` | also SIGTERM a stale lock holder (pid verified against /proc) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/warm_session.py` — stop_session — shutdown op + flock release

**Contract note (verbatim)**

> opt-in and read-only by contract: only commands whose cli_contract entry does not mutate run warm, everything else falls back to the cold path with a note. The session is a unix socket (0600, owned by you, in a 0700 directory), never a TCP port; a stale lock is detected with flock and cleaned up only by `warm start`/`stop`; exit 0 stopped, 5 nothing to stop

### Group `workflows`

#### `workflows.list` — kernel

workflow contracts

| | |
|---|---|
| Group | `workflows` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "workflows": {
      "type": "array"
    },
    "count": {
      "type": "integer"
    },
    "runnable": {
      "type": "array"
    }
  },
  "required": [
    "workflows",
    "count",
    "runnable"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/workflows.py:1044`

#### `workflows.run` — kernel

start a workflow

| | |
|---|---|
| Group | `workflows` |
| Layer | kernel |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `workflow_id` | string (positional) | yes | no | — | `—` | see `skaldir workflows list` |
| `--param` | string | no | yes | — | `[]` | workflow input (repeatable; missing inputs are reported as blocked) |
| `--project` | string | no | no | — | `—` | project path for the stable project id (default: cwd) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "run_id": {
      "type": "string"
    },
    "workflow": {
      "type": "string"
    },
    "status": {
      "type": "string"
    },
    "verified": {
      "type": "boolean"
    },
    "reason": {
      "type": "string"
    },
    "evidence": {
      "type": "string"
    }
  },
  "required": [
    "workflow",
    "status",
    "verified"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/workflows.py:951`

#### `workflows.show` — kernel

one workflow contract

| | |
|---|---|
| Group | `workflows` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `workflow_id` | string (positional) | yes | no | — | `—` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/workflows.py:929`

### Group `(top level)`

#### `chat` — kernel

the chat bar: a framed input line, a slash-first palette, the session loop

| | |
|---|---|
| Group | `(top level)` |
| Layer | kernel |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `verb` | choice (positional) | yes | no | shell, verbs, preview | `shell` | shell (default): run the framed session; verbs: the slash verb mapping; preview: draw the frame as text |
| `--frame` | choice | no | no | auto, on, off | `auto` | draw the two-bar frame: auto (a real terminal gets it), on (ask for it), off (never) |
| `--width` | integer | no | no | — | `0` | columns for `preview` (default: the terminal's) |
| `--runtime` | string | no | no | — | `—` | runtime dir override |
| `--resume` | flag | no | no | — | `false` | restore the previous session's state and print a summary |
| `--limit` | integer | no | no | — | `0` | stop after N lines (0 = no limit) |
| `--transcript` | string | no | no | — | `—` | write the raw session transcript to this path |
| `--color` | flag | no | no | — | `false` | force ANSI colour even when stdout is not a TTY |
| `--no-color` | flag | no | no | — | `false` | never emit colour or unicode (the plain, ASCII look) |
| `--no-probe` | flag | no | no | — | `true` | skip the startup selftest probe |
| `--no-activity` | flag | no | no | — | `false` | do not let the shell draw its own indicator (the frame's hint row carries the clock) |
| `--session` | string | no | no | — | `—` | the session id grants are filed under (default: shell-<pid>) |
| `--attach` | string | no | no | — | `—` | the goal id this chat is the surface of: the band shows *that* run's goal, and /steer + /queue deliver to it |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "runtime": {
      "type": "string",
      "description": "the runtime dir in use"
    },
    "interactive": {
      "type": "boolean",
      "description": "was the session on a terminal"
    },
    "commands": {
      "type": "integer",
      "description": "commands this session ran"
    },
    "failures": {
      "type": "integer",
      "description": "commands that failed"
    },
    "frame": {
      "type": "object",
      "description": "what the frame did",
      "properties": {
        "drawn": {
          "type": "boolean",
          "description": "was the two-bar frame driving the prompt"
        },
        "width": {
          "type": "integer",
          "description": "the frame's width in cells (0 = no frame)"
        },
        "activity": {
          "type": "string",
          "description": "what happened to the shell's indicator"
        },
        "notes": {
          "type": "array",
          "description": "the frame's own notes about this session"
        }
      }
    },
    "verbs": {
      "type": "array",
      "description": "`chat verbs`: the slash verbs with their kind"
    },
    "counts": {
      "type": "object",
      "description": "`chat verbs`: how many are real/builtin/planned"
    },
    "lines": {
      "type": "array",
      "description": "`chat preview`: the frame as lines"
    }
  }
}
```

**Default evidence**

- `code`: `src/skaldir/chatbar.py:1` — ChatBar — the two-bar frame, the keys, the hint row
- `code`: `src/skaldir/slash.py:1` — the palette the frame draws, with planned verbs marked

#### `demo` — kernel

deterministic V0 pipeline demo

| | |
|---|---|
| Group | `(top level)` |
| Layer | kernel |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--db` | string | no | no | — | `—` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/demo.py`

#### `doctor` — kernel

release health check

| | |
|---|---|
| Group | `(top level)` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "checks": {
      "type": "array"
    },
    "ok": {
      "type": "boolean"
    }
  },
  "required": [
    "checks",
    "ok"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/cli.py:2043`

#### `eval` — VERIFY · **declared limitation**

run the in-process eval episodes (not real-world verification)

| | |
|---|---|
| Group | `(top level)` |
| Layer | VERIFY |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "episodes": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "name": {
            "type": "string"
          },
          "passed": {
            "type": "boolean"
          },
          "hints": {
            "type": "integer"
          },
          "signals": {
            "type": "array",
            "items": {
              "type": "string"
            }
          }
        },
        "required": [
          "name",
          "passed"
        ]
      }
    },
    "passed": {
      "type": "integer"
    },
    "total": {
      "type": "integer"
    },
    "scope": {
      "type": "string"
    },
    "report": {
      "type": "string"
    }
  },
  "required": [
    "episodes",
    "passed",
    "total",
    "scope"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/evalharness.py:358`

**Contract note (verbatim)**

> exit 2 when any episode fails; in-process, never a PASS authority

#### `init` — kernel

create .godsv/ with a pact + ledger

| | |
|---|---|
| Group | `(top level)` |
| Layer | kernel |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--dir` | string | no | no | — | `.godsv` | — |
| `--force` | flag | no | no | — | `false` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/cli.py:92`

**Contract note (verbatim)**

> an existing pact exits 1, not the designed 6 (conflict): tests/test_demo_cli.py::test_init_refuses_overwrite pins 1

#### `ledger-show` — kernel

print the rendered ledger

| | |
|---|---|
| Group | `(top level)` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--dir` | string | no | no | — | `.godsv` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/ledger.py`

**Contract note (verbatim)**

> exit 5 (not_found) when there is no .godsv/ledger.yaml

#### `memory-audit` — PERSIST

event duplicates + retention

| | |
|---|---|
| Group | `(top level)` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--events` | string | no | no | — | `/home/willy/.hermes/godsv/events.db` | — |
| `--max-age-days` | number | no | no | — | `30.0` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "total": {
      "type": "integer"
    },
    "sessions": {
      "type": "integer"
    },
    "bytes_on_disk": {
      "type": "integer"
    },
    "over_age": {
      "type": "integer"
    },
    "duplicate_groups": {
      "type": "array"
    },
    "duplicate_rows": {
      "type": "integer"
    },
    "enforcement_gaps": {
      "type": "array"
    },
    "schema_axes": {
      "type": "object"
    }
  },
  "required": [
    "total",
    "sessions",
    "duplicate_groups"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/memory.py`

#### `memory-retrieval-check` — PERSIST

retrieve → applicability → use/reject

| | |
|---|---|
| Group | `(top level)` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `problem` | string (positional) | yes | no | — | `—` | — |
| `--db` | string | no | no | — | `/home/willy/.hermes/godsv/memory.db` | — |
| `--component` | string | no | no | — | `—` | — |
| `--failure-class` | string | no | no | — | `—` | — |
| `--goal-scope` | string | no | no | — | `—` | — |
| `--signature` | string | no | no | — | `—` | — |
| `--limit` | integer | no | no | — | `5` | — |
| `--min-confidence` | number | no | no | — | `0.5` | — |
| `--verbose` | flag | no | no | — | `false` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/memory.py`

#### `memory-search` — PERSIST

evidence view over memory

| | |
|---|---|
| Group | `(top level)` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `query` | string (positional) | yes | no | — | `—` | — |
| `--db` | string | no | no | — | `/home/willy/.hermes/godsv/memory.db` | — |
| `--signature` | string | no | no | — | `—` | — |
| `--limit` | integer | no | no | — | `5` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "evidence": {
      "type": "array"
    },
    "count": {
      "type": "integer"
    },
    "db": {
      "type": "string"
    }
  },
  "required": [
    "evidence",
    "count"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/retrieval.py`

**Contract note (verbatim)**

> an empty result is data.evidence=[] with exit 0; a missing db exits 1 (see memory-stats)

#### `memory-stats` — PERSIST

incident/note counts of a memory db

| | |
|---|---|
| Group | `(top level)` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--db` | string | no | no | — | `/home/willy/.hermes/godsv/memory.db` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "incidents": {
      "type": "integer"
    },
    "notes": {
      "type": "integer"
    },
    "by_status": {
      "type": "object"
    },
    "db": {
      "type": "string"
    }
  },
  "required": [
    "incidents",
    "notes",
    "db"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/incidents.py`
- `code`: `src/skaldir/notes.py`

**Contract note (verbatim)**

> exit 5 (not_found) is planned for a missing db; today exit 1 because tests/test_cli_runtime_paths.py pins it

#### `metrics` — PERSIST

operational counters

| | |
|---|---|
| Group | `(top level)` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--events` | string | no | no | — | `/home/willy/.hermes/godsv/events.db` | — |
| `--memory` | string | no | no | — | `/home/willy/.hermes/godsv/memory.db` | — |
| `--session-id` | string | no | no | — | `—` | add host-measured token usage for this session (from the Hermes state DB) |
| `--session-db` | string | no | no | — | `—` | path to the Hermes state DB (default: $HERMES_HOME/state.db with --session-id) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/metrics.py`

#### `model` — kernel

pick the provider and the model behind one of the two keys

| | |
|---|---|
| Group | `(top level)` |
| Layer | kernel |
| Runtime effect | mutates state |
| Credentials | required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `4` (NEEDS_CREDENTIALS), `7` (INTERNAL), `8` (UNKNOWN) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--role` | string | no | no | — | `—` | which of the two keys to change: typesafe (or jev) · llm (default: the one that is not usable yet) |
| `--provider` | string | no | no | — | `—` | a catalog provider id or row number (supplies the base URL, the model default and the credential variable name) |
| `--model` | string | no | no | — | `—` | a model id (default: the catalog's own) |
| `--base-url` | string | no | no | — | `—` | override the endpoint the provider row would pre-fill |
| `--key-stdin` | flag | no | no | — | `false` | read the key from one line on stdin (there is no --key VALUE: argv is shell history) |
| `--env-file` | string | no | no | — | `—` | where the credential is written (default: .env beside the config) |
| `--no-env-write` | flag | no | no | — | `false` | validate without writing the credential file |
| `--no-write` | flag | no | no | — | `false` | validate the choice and write no config |
| `--no-backup` | flag | no | no | — | `false` | do not back up the config file before replacing it |
| `--check` | flag | no | no | — | `false` | re-validate the configured role(s) and write nothing |
| `--list` | flag | no | no | — | `false` | print the provider rows (name, base URL, model list) and stop |
| `--config` | string | no | no | — | `—` | the runtime config file (default: $SKALDIR_LLM_CONFIG, else <runtime>/config.yaml) |
| `--timeout` | number | no | no | — | `—` | per-request timeout in seconds |
| `--no-streaming-probe` | flag | no | no | — | `false` | skip the streaming measurement (one request fewer) |
| `--yes` | flag | no | no | — | `false` | never prompt: a missing field is reported instead of asked for |
| `--no-input` | flag | no | no | — | `false` | alias of --yes for a headless caller |
| `--json` | flag | no | no | — | `false` | print one JSON payload on stdout |
| `--format` | choice | no | no | text, json | `—` | output format (text\|json) |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "action": {
      "type": "string"
    },
    "plan": {
      "type": "object"
    },
    "key_sources": {
      "type": "object"
    },
    "measure": {
      "type": "object"
    },
    "credentials_file": {
      "type": "object"
    },
    "write": {
      "type": "object"
    },
    "reread": {
      "type": "object"
    },
    "env_file": {
      "type": "string"
    },
    "config_file": {
      "type": "string"
    },
    "roles": {
      "type": "object"
    },
    "checked": {
      "type": "array"
    },
    "provider_rows": {
      "type": "array"
    },
    "asked": {
      "type": "object"
    },
    "ok": {
      "type": "boolean"
    },
    "verdict": {
      "type": "string"
    }
  },
  "required": [
    "action",
    "ok"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/model_picker.py` — the picker: role → provider (base URL pre-filled) → model → hidden key
- `code`: `src/skaldir/setup_flow.py` — the credential store (write_env_file, 0600) and the live probe it reuses
- `code`: `src/skaldir/provider_registry_data.py` — the catalog the rows and their base URLs come from
- `test`: `tests/test_model_picker.py` — the state machine, the fake prober and the pinned exit codes

**Contract note (verbatim)**

> the key is typed at a hidden prompt (or piped with --key-stdin) and written to the .env beside the config with mode 0600; config.yaml names the variable (api_key_env) and never the value. Every provider row pre-fills the base URL, the model comes from the catalog's own list, and the choice is validated live (model echo · latency · streaming · structured) before the config is written atomically with a backup. A remote endpoint with no credential anywhere exits 4 and writes nothing; --check re-validates and writes nothing; in the chat, /model opens the same picker inside the frame

#### `nightshift` — PERSIST

consolidate a session

| | |
|---|---|
| Group | `(top level)` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--events` | string | no | no | — | `/home/willy/.hermes/godsv/events.db` | — |
| `--memory` | string | no | no | — | `/home/willy/.hermes/godsv/memory.db` | — |
| `--session` | string | yes | no | — | `—` | — |
| `--apply` | flag | no | no | — | `false` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/nightshift.py`

#### `pact-show` — kernel

print the pact

| | |
|---|---|
| Group | `(top level)` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--dir` | string | no | no | — | `.godsv` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/pact.py`

**Contract note (verbatim)**

> exit 5 (not_found) when there is no .godsv/pact.yaml — an installed wheel starts empty; the crash-exit 7 it used to report was a missing-file traceback, not an internal error

#### `run` — ENFORCE · **declared limitation**

drive one goal to verification: plan, work through the agent loop, run the declared checks, enforce the completion gate, continue on failure — exit 0 only on VERIFY PASS

| | |
|---|---|
| Group | `(top level)` |
| Layer | ENFORCE |
| Runtime effect | mutates state |
| Credentials | required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `4` (NEEDS_CREDENTIALS), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `goal` | string (positional) | yes | no | — | `—` | the goal, in one sentence — what must be true when done |
| `--resume` | string | no | no | — | `—` | continue this goal id from its durable checkpoint |
| `--criteria` | string | no | no | — | `—` | criteria document (JSON list, or a .godsv/verify.json-shaped object); without it the workspace's declared plan is used |
| `--workspace` | string | no | no | — | `.` | the project to work in |
| `--session` | string | no | no | — | `goal-run` | the contract/session id this goal belongs to |
| `--max-turns` | integer | no | no | — | `6` | hard bound on the turns of this goal (default 6) |
| `--max-steps` | integer | no | no | — | `14` | steps inside one turn (default 14) |
| `--wall-clock-s` | number | no | no | — | `900.0` | wall-clock bound of the whole run in seconds |
| `--check-timeout` | number | no | no | — | `300.0` | per-check timeout in seconds (default 300) |
| `--lease-ttl` | number | no | no | — | `900.0` | writer-lease lifetime of this run in seconds |
| `--no-baseline` | flag | no | no | — | `false` | do not execute the declared checks before the first turn |
| `--no-snapshot` | flag | no | no | — | `false` | do not write a unified workspace checkpoint per rejection |
| `--context-budget` | integer | no | no | — | `0` | context budget in tokens (0 = the manager's own default) |
| `--context-trigger` | number | no | no | — | `0.3` | fraction of the budget that fires compaction (default 0.30) |
| `--preserve-recent` | integer | no | no | — | `1` | messages at the tail of the history compaction never touches |
| `--compaction-asker` | choice | no | no | provider, fixture, off | `provider` | provider = the real model judges the history (default); fixture = an offline simulation, labelled as such; off = no compaction |
| `--probability` | number | no | no | — | `0.05` | the fixture asker's flat keep probability (default 0.05) |
| `--max-result-chars` | integer | no | no | — | `6000` | truncation cap for one tool result fed back to the model |
| `--tier` | string | no | no | — | `main` | the model tier to run as |
| `--base-url` | string | no | no | — | `—` | provider base URL override |
| `--model` | string | no | no | — | `—` | model override |
| `--state` | string | no | no | — | `—` | runtime directory (default: SKALDIR_HOME) |
| `--progress` | flag | no | no | — | `—` | force the live progress lines on (they need a TTY otherwise) |
| `--no-chat` | flag | no | no | — | `false` | never hand the terminal to the chat: keep the foreground run (the default on a pipe, a script, --json or --quiet) |
| `--no-progress` | flag | no | no | — | `—` | no live progress lines, whatever the terminal |
| `--text` | flag | no | no | — | `false` | human output instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "goal_id": {
      "type": "string"
    },
    "goal": {
      "type": "string"
    },
    "workspace": {
      "type": "string"
    },
    "session": {
      "type": "string"
    },
    "resumed": {
      "type": "boolean"
    },
    "turns_run": {
      "type": "integer"
    },
    "accepted_completions": {
      "type": "integer"
    },
    "terminal_state": {
      "type": "string"
    },
    "blocked_reason": {
      "type": "string"
    },
    "exit_code": {
      "type": "integer"
    },
    "report_path": {
      "type": "string"
    },
    "criteria": {
      "type": "array"
    },
    "plan": {
      "type": "object"
    },
    "baseline": {
      "type": "object"
    },
    "turns": {
      "type": "array"
    },
    "completions": {
      "type": "array"
    },
    "checkpoints": {
      "type": "array"
    },
    "compactions": {
      "type": "array"
    },
    "compaction_asker": {
      "type": "object",
      "properties": {
        "source": {
          "type": "string"
        },
        "decision_layer": {
          "type": "string"
        },
        "model": {
          "type": "string"
        },
        "api_key_env": {
          "type": "string"
        },
        "tier": {
          "type": "string"
        },
        "simulation": {
          "type": "boolean"
        }
      },
      "description": "which decision layer judged the compactions — the credential's *name*, never its value; `simulation: true` marks the fixture asker; absent when no asker was built"
    },
    "verify": {
      "type": "object"
    },
    "runtime_errors": {
      "type": "array"
    }
  },
  "required": [
    "goal_id",
    "workspace",
    "turns_run",
    "accepted_completions",
    "terminal_state",
    "verify",
    "exit_code"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/goal_run.py` — the goal-locked engine: plan → turns → gate → verify → continue
- `code`: `src/skaldir/completion.py:2759` — GateRunner.verify — the only thing that may accept a completion
- `code`: `src/skaldir/verify_service.py:1055` — run_plan — the independent verdict behind the exit code
- `code`: `src/skaldir/runs_store.py:916` — start_goal + write_checkpoint: the durable goal, criteria and cursor

**Contract note (verbatim)**

> exit 0 is reserved for a verification PASS; a bound reached with no accepted completion parks the goal BLOCKED_EXTERNAL and exits 3 (the unmet criterion is named); a completion accepted by the gate whose independent pass still does not reach PASS exits 2. --resume continues the stored goal from its durable checkpoint (files, cursor, prior rejections) — the process may die at any point and the goal survives. --compaction-asker fixture marks every compaction as a simulation in the report; 'provider' (the default) uses the real model.

#### `scan` — kernel

scan a file (or -) for secrets

| | |
|---|---|
| Group | `(top level)` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `path` | string (positional) | yes | no | — | `—` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "findings": {
      "type": "array"
    },
    "count": {
      "type": "integer"
    },
    "path": {
      "type": "string"
    }
  },
  "required": [
    "findings",
    "count"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/secretscan.py`

**Contract note (verbatim)**

> findings exit 1, not the designed 2 (verification_failed): tests/test_demo_cli.py::test_scan_command pins 1

#### `selftest` — kernel · **declared limitation**

probe all eight layers and report reachability

| | |
|---|---|
| Group | `(top level)` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--layer` | string | no | yes | — | `[]` | probe only these layers (repeatable; one of EXPAND, ACT, COMPOSE, ROUTE, SUPERVISE, ENFORCE, VERIFY, PERSIST) |
| `--runtime` | string | no | no | — | `—` | runtime dir override |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "runtime": {
      "type": "string"
    },
    "ok": {
      "type": "boolean"
    },
    "probes": {
      "type": "integer"
    },
    "failed": {
      "type": "integer"
    },
    "layers": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "layer": {
            "type": "string"
          },
          "ok": {
            "type": "boolean"
          },
          "status": {
            "type": "string",
            "enum": [
              "reachable",
              "blocked",
              "failed"
            ]
          },
          "exit_code": {
            "type": "integer"
          },
          "blocked_reason": {
            "type": [
              "string",
              "null"
            ]
          },
          "cli_commands": {
            "type": "array",
            "items": {
              "type": "string"
            }
          },
          "evidence": {
            "type": "array"
          },
          "probes": {
            "type": "array"
          }
        },
        "required": [
          "layer",
          "ok",
          "status",
          "probes"
        ]
      }
    }
  },
  "required": [
    "layers",
    "ok",
    "probes",
    "failed"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/cli_contract.py`

**Contract note (verbatim)**

> exit 2 when any layer is not reachable; a layer that is not wired reports ok=false with blocked_reason, never reachable

#### `serve` — kernel

serve the HTTP API (one route per CLI command) with an OpenAPI document

| | |
|---|---|
| Group | `(top level)` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--http` | flag | no | no | — | `false` | use the HTTP transport (required; it is the only transport implemented today) |
| `--json-http` | flag | no | no | — | `false` | synonym of --http (the spelling MVP/08 §0 uses for the Schemathesis tier) |
| `--port` | integer | no | no | — | `8765` | TCP port (default: 8765) |
| `--host` | string | no | no | — | `127.0.0.1` | bind address (default: 127.0.0.1; loopback unless --allow-remote) |
| `--allow-remote` | flag | no | no | — | `false` | allow a non-loopback bind (explicit opt-in; mutating routes still need a token) |
| `--token-file` | string | no | no | — | `—` | read the API token from FILE instead of generating one (0600 recommended) |
| `--request-timeout` | number | no | no | — | `120.0` | deadline for one command in seconds (default: 120; expiry is exit 8 / HTTP 504) |
| `--max-body-bytes` | integer | no | no | — | `1048576` | maximum request body in bytes (default: 1048576) |
| `--workdir` | string | no | no | — | `—` | working directory the served commands run in (default: the server's own cwd) |
| `--cors-origin` | string | no | yes | — | `[]` | allow this browser origin (repeatable; off by default, since token-free read-only routes must not be reachable from a page) |
| `--openapi` | string | no | no | — | `—` | also write the OpenAPI document to FILE at start (the Schemathesis input) |
| `--transport` | choice | no | no | auto, warm, inprocess, spawn | `auto` | how a command is executed: auto (per-request: in-process for the measured allowlist, else the warm session, else a child process; default), warm (the running warm session only), inprocess (this process only), spawn (a child process per request — the pre-streaming behaviour) |
| `--warm-socket` | string | no | no | — | `—` | unix socket of the warm session to reuse (default: the session's own path, i.e. $SKALDIR_WARM_SOCKET or $XDG_RUNTIME_DIR/<pkg>/warm.sock) |
| `--sse-heartbeat` | number | no | no | — | `10.0` | seconds of silence before an SSE stream writes a keep-alive comment (default: 10) |
| `--keep-alive` | number | no | no | — | `30.0` | seconds an idle HTTP connection is kept open for reuse (default: 30) |
| `--log-level` | choice | no | no | critical, error, warning, info, debug, trace | `info` | uvicorn/log level (default: info) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/api_server.py` — one route per CLI command, generated from cli_contract.COMMANDS + build_parser()
- `file`: `GET /openapi.json` — the OpenAPI 3.1 document served at runtime

**Contract note (verbatim)**

> long-running command: the startup facts — including the API token, which is printed exactly once — go to stderr so stdout stays reserved for the single envelope; the envelope is emitted when the server stops. A missing fastapi/uvicorn, a non-loopback host without --allow-remote, and a taken port all exit 3 (blocked) naming the exact reason

#### `setup` — kernel

first-run setup: the two mandatory keys, each proved by a real call

| | |
|---|---|
| Group | `(top level)` |
| Layer | kernel |
| Runtime effect | mutates state |
| Credentials | required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `4` (NEEDS_CREDENTIALS), `7` (INTERNAL), `8` (UNKNOWN) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--jev-provider` | string | no | no | — | `—` | pick the TypeSafe/Jev provider by id (see `skaldir setup --providers`) — supplies the base URL and the key variable name |
| `--jev-base-url` | string | no | no | — | `—` | the TypeSafe/Jev provider's OpenAI-compatible endpoint root |
| `--jev-model` | string | no | no | — | `—` | the model the decision layer evaluates with |
| `--jev-api-key-env` | string | no | no | — | `—` | the NAME of the variable holding the Jev key (never the key) |
| `--jev-key-stdin` | flag | no | no | — | `false` | read the Jev key from one line on stdin — the scripted form of the hidden prompt (a value in argv would be in the shell history) |
| `--llm-provider` | string | no | no | — | `—` | pick the LLM provider by id (deepseek, openrouter, openai, ollama, …) — supplies the base URL, the model and the key name |
| `--llm-base-url` | string | no | no | — | `—` | the general provider's OpenAI-compatible endpoint root |
| `--llm-model` | string | no | no | — | `—` | the general model |
| `--llm-api-key-env` | string | no | no | — | `—` | the NAME of the variable holding the LLM key (never the key) |
| `--llm-key-stdin` | flag | no | no | — | `false` | read the LLM key from one line on stdin — the scripted form of the hidden prompt (a value in argv would be in the shell history) |
| `--llm-catalog` | string | no | no | — | `—` | pick the LLM provider from the catalog instead of spelling it out (deepseek / openrouter / openai / local) |
| `--env-file` | string | no | no | — | `—` | the credential file the key is written to (default: <config dir>/.env, mode 0600) |
| `--no-env-write` | flag | no | no | — | `false` | never write the key into the credential file (the environment keeps it) |
| `--config` | string | no | no | — | `—` | the runtime config file (default: $SKALDIR_LLM_CONFIG, else <runtime>/config.yaml) — the same file the provider layer reads |
| `--runtime` | string | no | no | — | `—` | the runtime directory (default: $SKALDIR_HOME, $HERMES_HOME/skaldir) |
| `--check` | flag | no | no | — | `false` | re-run the validation only and exit 0 only if both keys answer |
| `--state` | flag | no | no | — | `false` | print what is already configured and write nothing |
| `--providers` | flag | no | no | — | `false` | list the pickable providers (name, note, base URL) and stop |
| `--catalog` | flag | no | no | — | `false` | list the LLM providers a first run may pick from |
| `--no-write` | flag | no | no | — | `false` | validate both keys and stop (write nothing) |
| `--no-backup` | flag | no | no | — | `false` | do not back up an existing config file before replacing it |
| `--no-streaming-probe` | flag | no | no | — | `false` | skip the streaming measurement (one request fewer per key) |
| `--timeout` | number | no | no | — | `—` | per-request timeout in seconds |
| `--yes` | flag | no | no | — | `false` | never prompt: missing fields are reported as the flags to pass |
| `--no-input` | flag | no | no | — | `false` | alias of --yes for a headless caller |
| `--json` | flag | no | no | — | `false` | print one JSON payload on stdout |
| `--format` | choice | no | no | text, json | `—` | output format (text\|json) |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "action": {
      "type": "string"
    },
    "config_file": {
      "type": "string"
    },
    "env_file": {
      "type": "string"
    },
    "credentials_file": {
      "type": "object"
    },
    "key_sources": {
      "type": "object"
    },
    "next_steps": {
      "type": "object"
    },
    "asked": {
      "type": "object"
    },
    "providers": {
      "type": "array"
    },
    "mandatory_keys": {
      "type": "array"
    },
    "state": {
      "type": "object"
    },
    "roles": {
      "type": "object"
    },
    "write": {
      "type": "object"
    },
    "verify": {
      "type": "object"
    },
    "reread": {
      "type": "object"
    },
    "ok": {
      "type": "boolean"
    },
    "verdict": {
      "type": "string"
    }
  },
  "required": [
    "action",
    "ok",
    "mandatory_keys"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/setup_flow.py` — the flow: detect, validate, atomic write, verify
- `code`: `src/skaldir/provider_config.py` — the store it writes through (backup + secret refusal)
- `code`: `src/skaldir/jev/runtime.py` — the decision layer the Jev key is verified against
- `test`: `tests/test_setup_flow.py` — the stub-endpoint end-to-end proofs

**Contract note (verbatim)**

> api_key_env is a NAME — the credential itself is typed at a hidden prompt (or piped with --llm-key-stdin/--jev-key-stdin) and written to the .env beside the config with mode 0600; the config never carries a value. The provider picker pre-fills the base URL for all 16 catalog rows (a local server carries its own loopback URL), a key that is already in the environment or in the .env is reused and reported instead of asked for, --check re-validates and writes nothing (it reads the .env, exit 0 only when both keys answer; exit 4 when a remote endpoint has no credential), and the config is written atomically with a backup and then re-read before one real Jev decision and one real completion verify it

#### `shell` — kernel · **declared limitation**

interactive shell: session state, slash palette, completion, streaming

| | |
|---|---|
| Group | `(top level)` |
| Layer | kernel |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir override |
| `--resume` | flag | no | no | — | `false` | restore the previous session's state and print a summary |
| `--transcript` | string | no | no | — | `—` | write the raw session transcript to this path |
| `--limit` | integer | no | no | — | `0` | stop after N commands (0 = no limit; scripted safety) |
| `--color` | flag | no | no | — | `false` | force ANSI colour even when stdout is not a TTY |
| `--no-color` | flag | no | no | — | `false` | never emit colour or unicode (the plain, ASCII look) |
| `--no-probe` | flag | no | no | — | `true` | skip the startup selftest probe (the hint line then says so) |
| `--no-echo` | flag | no | no | — | `—` | do not echo the prompt for lines read from a pipe |
| `--no-footer` | flag | no | no | — | `true` | do not print the session footer line after a turn |
| `--no-activity` | flag | no | no | — | `true` | do not draw the streaming activity indicator |
| `--activity-hz` | number | no | no | — | `—` | how often the activity clock redraws, in hertz (default: every 0.5 s) |
| `--stream-hz` | number | no | no | — | `—` | renders per second for a streamed answer on this terminal (default: 30 on an interactive terminal, never when the output is a pipe; 0 disables pacing) |
| `--no-approval-prompt` | flag | no | no | — | `true` | never ask for approval: a gated command keeps the CLI's headless answer (exit 3) |
| `--no-preview` | flag | no | no | — | `true` | do not preview an edit transaction before it applies |
| `--session` | string | no | no | — | `—` | the session id grants are filed under (default: shell-<pid>) |
| `--attach` | string | no | no | — | `—` | the goal id this session is the surface of: the status line shows *that* run's goal, and /steer + /queue deliver to it |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "runtime": {
      "type": "string"
    },
    "interactive": {
      "type": "boolean"
    },
    "commands": {
      "type": "integer"
    },
    "failures": {
      "type": "integer"
    },
    "cancelled": {
      "type": "integer"
    },
    "last_exit_code": {
      "type": [
        "integer",
        "null"
      ]
    },
    "history_file": {
      "type": "string"
    },
    "transcript": {
      "type": [
        "string",
        "null"
      ]
    },
    "started_at": {
      "type": "string"
    },
    "layers_probed": {
      "type": [
        "integer",
        "null"
      ]
    },
    "layers_not_reachable": {
      "type": [
        "integer",
        "null"
      ]
    },
    "stream_hz": {
      "type": "number",
      "description": "the terminal's render bound in renders/second (0 = this session was not paced, e.g. a pipe)"
    },
    "renders": {
      "type": "integer",
      "description": "writes that reached the terminal while streaming"
    },
    "chunks": {
      "type": "integer",
      "description": "chunks accepted from the commands' output"
    },
    "stream_cpu_ms": {
      "type": "integer",
      "description": "process CPU (user+system) spent inside commands, ms"
    },
    "approvals": {
      "type": "integer",
      "description": "approval prompts this session answered (allow/deny)"
    },
    "approval_session": {
      "type": "string",
      "description": "the session id grants are filed under"
    },
    "last_edit": {
      "type": [
        "string",
        "null"
      ],
      "description": "the newest edit transaction in this runtime (`/diff` shows it), or null when there is none"
    }
  },
  "required": [
    "runtime",
    "commands",
    "history_file"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/repl.py:1` — Shell.run() — the session loop over the real CLI
- `code`: `src/skaldir/slash.py:1` — the slash palette maps onto existing command groups

**Contract note (verbatim)**

> the shell executes whatever is typed through the product CLI: a bare line becomes `skaldir <line>`, a `/…` line is a real command group alias. A command the build does not wire is refused with its reason (never a guessed verb). Ctrl-C cancels the running command (the record marks it cancelled; the session continues). Commands are appended to <runtime>/shell/history.jsonl; --resume restores that state. Two gates run first: `approval run` is judged by the approval ladder and its answer is recorded through `approval allow`/`deny` (allow-once is a one-shot session grant; a non-interactive session never prompts and keeps the CLI's exit 3), and `edit apply` is previewed with a real `edit dry-run` before it applies. `/init` writes a project AGENTS.md; `/diff` renders the last transaction

#### `tools-hub` — EXPAND

discover tools/skills/MCP servers next to the runtime

| | |
|---|---|
| Group | `(top level)` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME or ~/.hermes/godsv — where generated_tools live) |
| `--search` | string | no | no | — | `—` | word-overlap search over name/purpose/type |
| `--show` | string | no | no | — | `—` | detail for one entry |
| `--manifest` | string | no | no | — | `—` | build the shareable hub manifest for a tool |
| `--json` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "array",
  "items": {
    "type": "object"
  },
  "description": "discovered hub entries (unchanged legacy shape)"
}
```

**Default evidence**

- `code`: `src/skaldir/toolhub.py:129`

### Group `agent`

#### `agent.cancel` — PERSIST

cancel a live run from another process (or reconcile a dead one)

| | |
|---|---|
| Group | `agent` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |
| `run_id` | string (positional) | yes | no | — | `—` | the run to cancel |
| `--reason` | string | no | no | — | `—` | recorded with the cancel request |
| `--wait` | number | no | no | — | `0.0` | after signalling, wait this long for the run to go terminal |
| `--state-dir` | string | no | no | — | `—` | — |
| `--runtime` | string | no | no | — | `—` | — |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "run_id": {
      "type": "string"
    },
    "action": {
      "type": "string"
    },
    "status": {
      "type": "string"
    },
    "pid": {
      "type": "integer"
    },
    "pid_alive": {
      "type": "boolean"
    },
    "note": {
      "type": "string"
    }
  }
}
```

**Default evidence**

- `code`: `src/skaldir/subagents.py` — RunStore.request_cancel — cancel.json + the pid/status reconciliation

**Contract note (verbatim)**

> action 'signalled' = a live run will stop its children at their next turn boundary (a thread cannot be interrupted mid-call); 'noop' = already terminal; 'reconciled' = the record said running but its process is gone, so it is recorded as aborted instead of pretending a cancel; a run collected on the main thread also turns SIGINT/SIGTERM into the same cancel (children stop, the record is 'cancelled', never an abandoned 'running')

#### `agent.collect` — PERSIST

the collector: one structured result per child (--wait blocks on a live run)

| | |
|---|---|
| Group | `agent` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `5` (NOT_FOUND), `7` (INTERNAL), `8` (UNKNOWN) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |
| `run_id` | string (positional) | yes | no | — | `—` | the run to collect |
| `--child` | string | no | no | — | `—` | only this child |
| `--wait` | flag | no | no | — | `false` | block until the run is terminal |
| `--wait-timeout` | number | no | no | — | `60.0` | how long --wait blocks before reporting a snapshot (exit 8) |
| `--state-dir` | string | no | no | — | `—` | — |
| `--runtime` | string | no | no | — | `—` | — |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "run_id": {
      "type": "string"
    },
    "status": {
      "type": "string"
    },
    "complete": {
      "type": "boolean"
    },
    "partial": {
      "type": "boolean"
    },
    "delivered": {
      "type": "integer"
    },
    "results": {
      "type": "array"
    },
    "timing": {
      "type": "object"
    }
  }
}
```

**Default evidence**

- `code`: `src/skaldir/subagents.py` — collect_payload + RunStore.wait — the collector view of one run

**Contract note (verbatim)**

> exit 2 = the run is terminal but not every child delivered; exit 8 = --wait hit its deadline while the run is still live (the payload is a snapshot, not the finish); without --wait a live run is reported with complete:false, never as done

#### `agent.list` — PERSIST

list the runs in the subagent state directory, newest first

| | |
|---|---|
| Group | `agent` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |
| `--limit` | integer | no | no | — | `20` | how many runs (newest first) |
| `--status` | string | no | no | — | `—` | only runs in this status |
| `--state-dir` | string | no | no | — | `—` | — |
| `--runtime` | string | no | no | — | `—` | — |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "state_dir": {
      "type": "string"
    },
    "count": {
      "type": "integer"
    },
    "runs": {
      "type": "array"
    }
  }
}
```

**Default evidence**

- `code`: `src/skaldir/subagents.py` — RunStore.list — reads run.json

#### `agent.retry` — PERSIST

re-run the children of an earlier run that did not deliver (one new run, linked back with retried_from)

| | |
|---|---|
| Group | `agent` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL), `8` (UNKNOWN) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |
| `run_id` | string (positional) | yes | no | — | `—` | the earlier run to retry |
| `--child` | string | no | yes | — | `[]` | only this child (repeatable); default: every child that did not deliver |
| `--all` | flag | no | no | — | `false` | re-run the delivered children too (a fresh run, not a retry) |
| `--parallel` | integer | no | no | — | `0` | at most N children at once (0 = all at once) |
| `--model-tier` | choice | no | no | small, main | `—` | override the tier (default: each child keeps its own) |
| `--model` | string | no | no | — | `—` | with a catalog --provider: the model id the children call through (same rule as agent spawn --model) |
| `--max-turns` | integer | no | no | — | `0` | override the turn cap (default: each child keeps its own) |
| `--budget` | integer | no | no | — | `0` | override the per-child token budget (default: its own) |
| `--run-budget` | integer | no | no | — | `0` | estimated token cap for the whole retry (0 = no run cap) |
| `--timeout` | number | no | no | — | `0.0` | override the per-child deadline (default: its own) |
| `--workdir` | string | no | no | — | `—` | parent for the per-child working directories |
| `--context-chars` | integer | no | no | — | `6000` | how much of its own transcript a child carries into the next turn |
| `--provider` | choice | no | no | auto, stub | `auto` | auto = the configured OpenAI-compatible client; stub = deterministic offline provider (no model is called) |
| `--stub-latency` | number | no | no | — | `0.05` | --provider stub: simulated latency per call |
| `--max-depth` | integer | no | no | — | `1` | the retry run's own nesting cap (see `agent spawn --max-depth`) |
| `--stop-grace` | number | no | no | — | `5.0` | how long a cancel waits for the children to stop themselves |
| `--role` | string | no | no | — | `—` | re-role every retried child (default: each child keeps the role it was recorded with) — the child's role ceiling: explore \| worker \| verifier \| main \| unroled (default: the canonical role for the child's kind — a worker) |
| `--stream` | flag | no | no | — | `false` | print every event as it arrives (stderr; JSONL in --json mode) |
| `--new-run-id` | string | no | no | — | `—` | the retry run's own id (default <RUN_ID>-retry) |
| `--state-dir` | string | no | no | — | `—` | — |
| `--runtime` | string | no | no | — | `—` | — |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "run_id": {
      "type": "string"
    },
    "status": {
      "type": "string"
    },
    "retried_from": {
      "type": "string"
    },
    "children_total": {
      "type": "integer"
    },
    "children_delivered": {
      "type": "integer"
    },
    "children_not_delivered": {
      "type": "array"
    },
    "failure_classes": {
      "type": "object"
    },
    "source_run": {
      "type": "object"
    },
    "run_dir": {
      "type": "string"
    },
    "children": {
      "type": "array"
    }
  }
}
```

**Default evidence**

- `code`: `src/skaldir/subagents.py` — retry_briefs — the unfinished children's own briefs, linked by retried_from
- `verification`: `MVP/28-subagent-harness.md` — the measured retry run: only the failed children re-run, the first attempt's transcripts kept as evidence

**Contract note (verbatim)**

> default: only the children whose status was not delivered, with their own task/tier/budget/deadline; --child selects explicitly, --all re-runs the delivered ones too; exit 5 = no such source run; the source run is never modified (a retry is a new run, not a resume) and its transcripts stay on disk as the first attempt's evidence

#### `agent.run` — ENFORCE

run one task through the loop (model-driven tool calling)

| | |
|---|---|
| Group | `agent` |
| Layer | ENFORCE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `4` (NEEDS_CREDENTIALS), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `task` | string (positional) | yes | no | — | `—` | the task, in one argument |
| `--chat-output` | flag | no | no | — | `false` | ==SUPPRESS== |
| `--max-steps` | integer | no | no | — | `8` | maximum number of model calls (default 8) |
| `--token-budget` | integer | no | no | — | `0` | stop after this many tokens (0 = unbounded) |
| `--cost-budget-usd` | number | no | no | — | `0.0` | stop after this many USD (needs --price-per-1k-tokens) |
| `--price-per-1k-tokens` | number | no | no | — | `0.0` | declared price per 1000 tokens; the loop never invents one |
| `--wall-clock-s` | number | no | no | — | `0.0` | stop after this many seconds (0 = unbounded) |
| `--tools` | choice | no | no | builtin, registry, both | `builtin` | tool set to expose: builtin (workspace file tools), registry (approved specs), both |
| `--agent-tools` | flag | no | no | — | `false` | add the layer tools (skill_expand/act/compose/verify — all eight layers reachable, not just file work) |
| `--project-id` | string | no | no | — | `—` | registry scope id the layer tools write under |
| `--db` | string | no | no | — | `—` | explicit tools DB (registry tool set) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: the registry's) |
| `--workspace` | string | no | no | — | `.` | workspace the tools may touch |
| `--tier` | string | no | no | — | `main` | model tier (small\|mid\|main) |
| `--model` | string | no | no | — | `—` | override the model |
| `--base-url` | string | no | no | — | `—` | override the provider base_url |
| `--run-id` | string | no | no | — | `—` | explicit run id (default: generated) |
| `--previous` | string | no | no | — | `—` | continue a conversation: the transcript of the run whose exchange this one should follow (as printed by `agent run`); its messages are carried into this request |
| `--system-prompt` | string | no | no | — | `—` | override the loop's system prompt |
| `--tool-choice` | choice | no | no | auto, none, required | `auto` | what the provider may do with the tools array |
| `--no-policy` | flag | no | no | — | `false` | skip the pre-execution policy gate (records that it was skipped) |
| `--no-jev-turns` | flag | no | no | — | `false` | run with no Jev decision layer on the turn boundaries (the decision rows in the runtime's event store are then absent) |
| `--cancel-file` | string | no | no | — | `—` | if this file appears, the loop stops at the next step (cancelled) |
| `--max-result-chars` | integer | no | no | — | `4000` | how much of one tool result is fed back to the model |
| `--stream` | flag | no | no | — | `—` | stream the model's answer to stdout as it is generated (default: on when stdout is a terminal, off for a pipe, --json or --quiet) |
| `--no-stream` | flag | no | no | — | `—` | wait for the whole answer, exactly as before this flag existed (the answer is printed once, complete) |
| `--stream-hz` | number | no | no | — | `0.0` | upper bound on terminal updates per second while streaming (0 = the 17/s default; a pipe is never paced) |
| `--progress` | flag | no | no | — | `—` | write live progress lines to stderr (default: on when stderr is a terminal; never on stdout, never in --quiet) |
| `--no-progress` | flag | no | no | — | `—` | write no progress lines at all |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "run_id": {
      "type": "string"
    },
    "status": {
      "type": "string"
    },
    "reason": {
      "type": "string"
    },
    "answer": {
      "type": [
        "string",
        "null"
      ]
    },
    "steps": {
      "type": "integer"
    },
    "tool_calls": {
      "type": "integer"
    },
    "executed_calls": {
      "type": "integer"
    },
    "usage": {
      "type": "object"
    },
    "cost_usd": {
      "type": "number"
    },
    "duration_ms": {
      "type": "integer"
    },
    "transcript_path": {
      "type": "string"
    },
    "tools": {
      "type": "array"
    },
    "provider": {
      "type": "object"
    },
    "limits": {
      "type": "object"
    },
    "ok": {
      "type": "boolean"
    },
    "exit_code": {
      "type": "integer"
    },
    "streaming": {
      "type": "object",
      "description": "what the streamed-token path measured: deltas, chars, first_content_ms (provider counts) plus the render side's updates/bytes_written; streamed=false carries the reason it did not stream"
    }
  },
  "required": [
    "run_id",
    "status",
    "answer",
    "steps",
    "exit_code"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/agent_loop.py` — the loop: schema gen, tool_calls, execution, bounds
- `test`: `tests/test_agent_loop.py` — the stub-endpoint proof of loop and bounds

**Contract note (verbatim)**

> a bound (max_steps/token_budget/cost_budget/wall_clock/cancelled) exits 3 with data.answer null — the loop never reports a bound as an answer; exit 4 when no provider is configured or the credential was refused. With --stream (a terminal) the answer is written as the model generates it and data.streaming carries the measured numbers; the token count is an estimate (chars/4) while deltas and millisecond stamps are measured

#### `agent.show` — PERSIST

show one run: its children, per-turn transcripts and the streamed events

| | |
|---|---|
| Group | `agent` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |
| `run_id` | string (positional) | yes | no | — | `—` | the run to show |
| `--child` | string | no | no | — | `—` | only this child |
| `--events` | integer | no | no | — | `25` | how many streamed events to include (default 25) |
| `--transcript` | flag | no | no | — | `false` | also read the children's transcripts straight off disk (this works while the run is still live) |
| `--transcript-tail` | integer | no | no | — | `0` | with --transcript: only the last N turns per child (0 = all) |
| `--state-dir` | string | no | no | — | `—` | — |
| `--runtime` | string | no | no | — | `—` | — |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "run_id": {
      "type": "string"
    },
    "status": {
      "type": "string"
    },
    "run": {
      "type": "object"
    },
    "summary": {
      "type": "object"
    },
    "events": {
      "type": "array"
    }
  }
}
```

**Default evidence**

- `code`: `src/skaldir/subagents.py` — RunStore.load + RunStore.events — the run record and events.jsonl

#### `agent.spawn` — PERSIST

spawn N children in parallel (one shared provider client per run) and collect a structured result per child

| | |
|---|---|
| Group | `agent` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |
| `--task` | string | no | yes | — | `[]` | one child per --task (repeatable) |
| `--task-file` | string | no | no | — | `—` | read tasks from a file: one per line, '#' comments ignored |
| `--model-tier` | choice | no | no | small, main | `small` | which model tier the children call (default small) |
| `--model` | string | no | no | — | `—` | with a catalog --provider: the model id the children call through (must be one that provider's list carries; the first documented model is the default) |
| `--max-turns` | integer | no | no | — | `3` | turns per child (default 3) |
| `--budget` | integer | no | no | — | `20000` | estimated token budget PER CHILD (default 20000; chars/4, a guard not accounting) |
| `--run-budget` | integer | no | no | — | `0` | estimated token cap for the whole run (0 = no run cap) |
| `--parallel` | integer | no | no | — | `0` | at most N children at once (0 = all at once) |
| `--timeout` | number | no | no | — | `120.0` | per-child deadline (default 120s) |
| `--workdir` | string | no | no | — | `—` | parent for the per-child working directories (default: inside the run directory) |
| `--context-chars` | integer | no | no | — | `6000` | how much of its own transcript a child carries into the next turn |
| `--provider` | string | no | no | — | `auto` | auto = the configured OpenAI-compatible client; stub = deterministic offline provider (no model is called); or a provider-catalog id (e.g. deepseek, openai) to give the children their own endpoint (refused by name when unknown) |
| `--stub-latency` | number | no | no | — | `0.05` | --provider stub: simulated latency per call |
| `--max-depth` | integer | no | no | — | `1` | how deep children may nest: 0 = never, 1 (default) = a child of this run may not spawn children, 2 = it may spawn grandchildren which may not nest further (hard ceiling 4) |
| `--stop-grace` | number | no | no | — | `5.0` | after a cancel, how long the collector waits for the children to stop themselves before it settles them (default 5s) |
| `--role` | string | no | no | — | `—` | the child's role ceiling: explore \| worker \| verifier \| main \| unroled (default: the canonical role for the child's kind — a worker) |
| `--stream` | flag | no | no | — | `false` | print every event as it arrives (stderr; JSONL in --json mode) |
| `--run-id` | string | no | no | — | `—` | the run to read (see `agent list`) |
| `--state-dir` | string | no | no | — | `—` | subagent state dir (default <runtime>/subagents) |
| `--runtime` | string | no | no | — | `—` | runtime directory (see the registry) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "run_id": {
      "type": "string"
    },
    "status": {
      "type": "string"
    },
    "partial": {
      "type": "boolean"
    },
    "depth": {
      "type": "integer"
    },
    "max_depth": {
      "type": "integer"
    },
    "queue_max": {
      "type": "integer"
    },
    "children_total": {
      "type": "integer"
    },
    "children_delivered": {
      "type": "integer"
    },
    "children_not_delivered": {
      "type": "array"
    },
    "failure_classes": {
      "type": "object"
    },
    "cost": {
      "type": "object"
    },
    "nesting": {
      "type": "object"
    },
    "duration_ms": {
      "type": "number"
    },
    "timing": {
      "type": "object"
    },
    "provider": {
      "type": "object"
    },
    "run_dir": {
      "type": "string"
    },
    "children": {
      "type": "array"
    }
  }
}
```

**Default evidence**

- `code`: `src/skaldir/subagents.py` — SubagentRun.run — one thread per child, one shared provider client, per-child budget/turn/deadline envelope
- `verification`: `MVP/28-subagent-harness.md` — the measured spawn-latency table (p50/p95) and the process-per-child comparison this command replaces

**Contract note (verbatim)**

> exit 2 means the run finished but at least one child did not deliver — the other children's results are still in data.children; exit 3 means no provider is configured (never a silently faked child); --provider stub runs offline and says so in data.provider.mode and in a warning; --parallel N queues the rest (data.timing.queue_max_concurrent and data.children[].queued report the queue); --max-depth caps nesting (default 1 = children of this run may not spawn children, refusals are in data.nesting.refused)

#### `agent.transcript` — ENFORCE

read one run back (requests, answers, tool calls, digests)

| | |
|---|---|
| Group | `agent` |
| Layer | ENFORCE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `run_id` | string (positional) | yes | no | — | `—` | the run id printed by `agent run` |
| `--db` | string | no | no | — | `—` | explicit tools DB (runtime resolution) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: the registry's) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "run_id": {
      "type": "string"
    },
    "status": {
      "type": "string"
    },
    "reason": {
      "type": "string"
    },
    "answer": {
      "type": [
        "string",
        "null"
      ]
    },
    "task": {
      "type": "string"
    },
    "steps": {
      "type": "integer"
    },
    "tool_calls": {
      "type": "array"
    },
    "tool_call_count": {
      "type": "integer"
    },
    "requests": {
      "type": "array"
    },
    "answers": {
      "type": "array"
    },
    "events": {
      "type": "array"
    },
    "usage": {
      "type": "object"
    },
    "limits": {
      "type": "object"
    },
    "tools": {
      "type": "array"
    },
    "transcript_path": {
      "type": "string"
    }
  },
  "required": [
    "run_id",
    "status",
    "events",
    "tool_calls"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/agent_loop.py` — read_transcript + the JSONL writer
- `code`: `src/skaldir/agent_loop_cli.py` — the group and this entry

**Contract note (verbatim)**

> exit 5 for an unknown run id; the transcript holds every request body (with the tools array) and every tool result digest

### Group `code`

#### `code.blame` — PERSIST

one line's author, commit and text through git blame

| | |
|---|---|
| Group | `code` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL), `8` (UNKNOWN) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `path` | string (positional) | yes | no | — | `—` | — |
| `line` | integer (positional) | yes | no | — | `—` | — |
| `--workspace` | string | no | no | — | `—` | workspace root (default: the git root) |
| `--path` | string | no | no | — | `—` | sub-directory to work in |
| `--state` | string | no | no | — | `—` | map cache directory override |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: the product runtime) |
| `--text` | flag | no | no | — | `false` | human-readable output instead of the JSON payload |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "status": {
      "type": "string"
    },
    "path": {
      "type": "string"
    },
    "line": {
      "type": "integer"
    },
    "author": {
      "type": "string"
    }
  },
  "required": [
    "status",
    "path",
    "line"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/codectx.py` — code.blame

**Contract note (verbatim)**

> an uncommitted line has no blame and exits 5 with git's own reason

#### `code.cache` — EXPAND

what the repo-map cache holds right now

| | |
|---|---|
| Group | `code` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--clear` | flag | no | no | — | `false` | delete the cache files |
| `--workspace` | string | no | no | — | `—` | workspace root (default: the git root) |
| `--path` | string | no | no | — | `—` | sub-directory to work in |
| `--state` | string | no | no | — | `—` | map cache directory override |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: the product runtime) |
| `--text` | flag | no | no | — | `false` | human-readable output instead of the JSON payload |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "cache_path": {
      "type": "string"
    },
    "entries": {
      "type": "integer"
    },
    "exists": {
      "type": "boolean"
    }
  },
  "required": [
    "cache_path",
    "entries",
    "exists"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/codectx_cli.py` — code.cache

**Contract note (verbatim)**

> --clear removes the map cache files only; the workspace is never touched

#### `code.changed` — PERSIST

changed and untracked files through git status

| | |
|---|---|
| Group | `code` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--limit` | integer | no | no | — | `500` | — |
| `--workspace` | string | no | no | — | `—` | workspace root (default: the git root) |
| `--path` | string | no | no | — | `—` | sub-directory to work in |
| `--state` | string | no | no | — | `—` | map cache directory override |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: the product runtime) |
| `--text` | flag | no | no | — | `false` | human-readable output instead of the JSON payload |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "status": {
      "type": "string"
    },
    "files": {
      "type": "array"
    },
    "branch": {
      "type": "string"
    }
  },
  "required": [
    "status",
    "files"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/codectx.py` — code.changed

**Contract note (verbatim)**

> outside a git work tree the status is unavailable (exit 0 with the reason); a missing git exits 3

#### `code.diagnostics` — VERIFY

real diagnostics from ruff, or an honest blocked answer naming the missing tool

| | |
|---|---|
| Group | `code` |
| Layer | VERIFY |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL), `8` (UNKNOWN) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `paths` | string (positional) | yes | yes | — | `—` | files/dirs to check (default: the workspace) |
| `--linter` | choice | no | no | auto, ruff | `auto` | — |
| `--limit` | integer | no | no | — | `200` | — |
| `--workspace` | string | no | no | — | `—` | workspace root (default: the git root) |
| `--path` | string | no | no | — | `—` | sub-directory to work in |
| `--state` | string | no | no | — | `—` | map cache directory override |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: the product runtime) |
| `--text` | flag | no | no | — | `false` | human-readable output instead of the JSON payload |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "status": {
      "type": "string"
    },
    "linter": {
      "type": "object"
    },
    "findings": {
      "type": "array"
    },
    "counts_by_code": {
      "type": "object"
    },
    "needed": {
      "type": "array"
    }
  },
  "required": [
    "status",
    "findings"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/codectx.py` — code.diagnostics

**Contract note (verbatim)**

> no linter reachable exits 3 (never an empty 'all clean'); the probe names the argv it would run

#### `code.explain` — COMPOSE

the sandbox policy: limits, the allowed tool list and why, the credential scrub, the kill rule and the limits that are not claimed

| | |
|---|---|
| Group | `code` |
| Layer | COMPOSE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--workspace` | string | no | no | — | `—` | the workspace the policy would use |
| `--runtime` | string | no | no | — | `—` | the product runtime directory |
| `--timeout` | integer | no | no | — | `10` | the timeout the policy report should describe |
| `--max-tool-calls` | integer | no | no | — | `25` | — |
| `--max-output` | integer | no | no | — | `32000` | — |
| `--max-result` | integer | no | no | — | `64000` | — |
| `--allow-tool` | string | no | yes | — | `[]` | — |
| `--deny-tool` | string | no | yes | — | `[]` | — |
| `--allow-network` | flag | no | no | — | `false` | show the policy with network |
| `--allow-site-packages` | flag | no | no | — | `false` | describe the widened policy (site-packages on the child's sys.path) |
| `--runner` | flag | no | no | — | `false` | also print the child runner's source |
| `--text` | flag | no | no | — | `false` | human-readable output instead of the JSON report |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/code_exec.py:policy_report`
- `code`: `src/skaldir/tools_core/contract.py:TruncationRule`

**Contract note (verbatim)**

> read-only: the report is generated from the same objects the runner enforces, so the documented policy cannot drift from the executed one

#### `code.map` — EXPAND

the ranked repo map (paths, languages, lines, symbols, sizes) under a token budget

| | |
|---|---|
| Group | `code` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--tokens` | integer | no | no | — | `1500` | — |
| `--force` | flag | no | no | — | `false` | rebuild every file fact |
| `--no-ignore` | flag | no | no | — | `false` | do not read .gitignore files |
| `--trust-dir-mtime` | flag | no | no | — | `false` | skip the per-file stat pass when every directory mtime matches (can serve stale facts) |
| `--workspace` | string | no | no | — | `—` | workspace root (default: the git root) |
| `--path` | string | no | no | — | `—` | sub-directory to work in |
| `--state` | string | no | no | — | `—` | map cache directory override |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: the product runtime) |
| `--text` | flag | no | no | — | `false` | human-readable output instead of the JSON payload |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "mode": {
      "type": "string"
    },
    "entries": {
      "type": "array"
    },
    "map": {
      "type": "object"
    },
    "update": {
      "type": "object"
    },
    "map_fingerprint": {
      "type": "string"
    }
  },
  "required": [
    "mode",
    "entries",
    "map",
    "update",
    "map_fingerprint"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/codectx.py` — code.map

**Contract note (verbatim)**

> writes ONLY its cache under <runtime>/codectx (never the workspace); mode reused|incremental|rebuilt names how much work this call did

#### `code.pack` — EXPAND

the bounded, ranked context block for a task description

| | |
|---|---|
| Group | `code` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `task` | string (positional) | yes | no | — | `—` | the task description to pack context for |
| `--tokens` | integer | no | no | — | `4000` | — |
| `--files` | integer | no | no | — | `6` | how many files to excerpt |
| `--no-diagnostics` | flag | no | no | — | `false` | — |
| `--force` | flag | no | no | — | `false` | rebuild the map first |
| `--workspace` | string | no | no | — | `—` | workspace root (default: the git root) |
| `--path` | string | no | no | — | `—` | sub-directory to work in |
| `--state` | string | no | no | — | `—` | map cache directory override |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: the product runtime) |
| `--text` | flag | no | no | — | `false` | human-readable output instead of the JSON payload |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "task": {
      "type": "string"
    },
    "block": {
      "type": "object"
    },
    "sections": {
      "type": "array"
    },
    "fingerprint": {
      "type": "string"
    }
  },
  "required": [
    "task",
    "block",
    "sections",
    "fingerprint"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/codectx.py` — code.pack

**Contract note (verbatim)**

> byte-stable for the same workspace and task (no clock in the render); fingerprint is the digest a prompt builder freezes; --text prints exactly the block

#### `code.run` — COMPOSE

run one snippet in a sandboxed process; the tool API inside composes tool calls and the run returns stdout/stderr, a structured value and every call

| | |
|---|---|
| Group | `code` |
| Layer | COMPOSE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL), `8` (UNKNOWN) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--file` | string | no | no | — | `—` | snippet file to run in the sandbox |
| `--stdin` | flag | no | no | — | `false` | read the snippet from standard input |
| `--workspace` | string | no | no | — | `—` | the sandbox's working directory (default: the cwd) |
| `--runtime` | string | no | no | — | `—` | the product runtime directory |
| `--timeout` | integer | no | no | — | `10` | wall clock in seconds (1..120) before the process group is killed with SIGKILL |
| `--max-output` | integer | no | no | — | `32000` | cap for each of stdout/stderr (head+tail, with a marker) |
| `--max-tool-calls` | integer | no | no | — | `25` | how many tool calls the snippet may attempt (a refusal counts too) |
| `--max-result` | integer | no | no | — | `64000` | cap for the structured result value |
| `--allow-tool` | string | no | yes | — | `[]` | restrict the sandbox to this tool (repeatable; default: the whole core set) |
| `--deny-tool` | string | no | yes | — | `[]` | take this tool out of the sandbox (repeatable) |
| `--allow-network` | flag | no | no | — | `false` | declare network access for the snippet (web_fetch / web_search); off by default |
| `--no-policy-gate` | flag | no | no | — | `false` | skip the pre-execution tool-call policy for this run (not recommended: the call then keeps only the tool's own checks) |
| `--require-registered` | flag | no | no | — | `false` | refuse a tool the runtime registry has no row for |
| `--allow-site-packages` | flag | no | no | — | `false` | WIDENS the sandbox: put site-packages back on the child's sys.path, so the snippet can import installed packages — including skaldir itself, which would let it call a tool without this run's policy, budget or evidence |
| `--keep` | flag | no | no | — | `false` | keep the temporary run directory |
| `--fail-on-error` | flag | no | no | — | `false` | exit 2 when the snippet raised or reported no result |
| `--text` | flag | no | no | — | `false` | human-readable output instead of the JSON report |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "status": {
      "type": "string"
    },
    "ok": {
      "type": "boolean"
    },
    "value": {},
    "value_source": {
      "type": "string"
    },
    "stdout": {
      "type": "string"
    },
    "stderr": {
      "type": "string"
    },
    "tool_calls": {
      "type": "array"
    },
    "tool_budget": {
      "type": "object"
    },
    "workspace_unchanged": {
      "type": "boolean"
    }
  },
  "required": [
    "status",
    "stdout",
    "stderr",
    "tool_calls",
    "tool_budget"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/code_exec.py:run_code`
- `code`: `src/skaldir/tools_core/registry.py:call`
- `code`: `src/skaldir/tools_core/sandbox.py:child_environment`

**Contract note (verbatim)**

> H8: a snippet's tool calls go through tools_core.registry.call plus the pre-execution policy (toolcall.judge_tool_call) in the host process, each recorded with duration and outcome; a measured failure (a raising snippet, a refused call) keeps exit 0 — --fail-on-error turns a non-ok status into 2, and a timeout kill exits 8

#### `code.search` — EXPAND

regex content search or file-glob search, with caps, context lines and gitignore rules

| | |
|---|---|
| Group | `code` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `pattern` | string (positional) | yes | no | — | `—` | — |
| `--target` | choice | no | no | content, files | `content` | — |
| `--glob` | string | no | no | — | `—` | only files whose name matches this glob |
| `--mode` | choice | no | no | content, files_only, count | `content` | — |
| `--context` | integer | no | no | — | `0` | context lines around a match |
| `--limit` | integer | no | no | — | `50` | — |
| `--offset` | integer | no | no | — | `0` | — |
| `--ignore-case` | flag | no | no | — | `false` | — |
| `--no-ignore` | flag | no | no | — | `false` | ignore the .gitignore rules |
| `--workspace` | string | no | no | — | `—` | workspace root (default: the git root) |
| `--path` | string | no | no | — | `—` | sub-directory to work in |
| `--state` | string | no | no | — | `—` | map cache directory override |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: the product runtime) |
| `--text` | flag | no | no | — | `false` | human-readable output instead of the JSON payload |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "pattern": {
      "type": "string"
    },
    "matches": {
      "type": "array"
    },
    "count": {
      "type": "integer"
    },
    "files_scanned": {
      "type": "integer"
    },
    "truncated": {
      "type": "boolean"
    }
  },
  "required": [
    "pattern",
    "matches",
    "count",
    "files_scanned",
    "truncated"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/codectx.py` — code.search

**Contract note (verbatim)**

> no match is data (exit 0 with count 0); an invalid regex exits 1, a path outside the workspace exits 3, a missing path exits 5

#### `code.symbols` — EXPAND

definitions per file: python through ast, other languages through ctags

| | |
|---|---|
| Group | `code` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `path` | string (positional) | yes | no | — | `—` | — |
| `--language` | string | no | no | — | `—` | only this language |
| `--limit` | integer | no | no | — | `200` | — |
| `--no-ctags` | flag | no | no | — | `false` | python files only (stdlib ast) |
| `--workspace` | string | no | no | — | `—` | workspace root (default: the git root) |
| `--path` | string | no | no | — | `—` | sub-directory to work in |
| `--state` | string | no | no | — | `—` | map cache directory override |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: the product runtime) |
| `--text` | flag | no | no | — | `false` | human-readable output instead of the JSON payload |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "status": {
      "type": "string"
    },
    "files": {
      "type": "array"
    },
    "symbols_total": {
      "type": "integer"
    },
    "needed": {
      "type": "array"
    }
  },
  "required": [
    "status",
    "files",
    "symbols_total"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/codectx.py` — code.symbols

**Contract note (verbatim)**

> no index for the requested language exits 3 and names the tool; a guess is never returned

#### `code.tools` — EXPAND

the declared surface: schemas, permissions, timeouts and truncation per verb

| | |
|---|---|
| Group | `code` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--text` | flag | no | no | — | `false` | human-readable output instead of the JSON payload |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/codectx_cli.py` — code.tools

**Contract note (verbatim)**

> read-only self-description; the probes report ruff/ctags/git availability

### Group `coding`

#### `coding.complete` — EXPAND

fuzzy @-style file completion with match indices and scores

| | |
|---|---|
| Group | `coding` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `query` | string (positional) | yes | no | — | `—` | the partial path (a leading @ is allowed) |
| `--limit` | integer | no | no | — | `20` | how many matches to return |
| `--inline` | flag | no | no | — | `false` | add a plain path list for shells |
| `--workspace` | string | no | no | — | `—` | the directory to work in (default: cwd) |
| `--runtime` | string | no | no | — | `—` | the runtime dir (default: the registry's) |
| `--state` | string | no | no | — | `—` | the state dir (default <runtime>/coding) |
| `--text` | flag | no | no | — | `false` | human-readable output on stderr instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/coding_features.py` — coding complete

**Contract note (verbatim)**

> a query that matches nothing exits 0 with count 0 (data, not failure); gitignored paths are never offered

#### `coding.expand` — EXPAND

resolve every @mention of a prompt to a real path inside the workspace

| | |
|---|---|
| Group | `coding` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `text` | string (positional) | yes | no | — | `—` | the prompt text carrying @mentions |
| `--workspace` | string | no | no | — | `—` | the directory to work in (default: cwd) |
| `--runtime` | string | no | no | — | `—` | the runtime dir (default: the registry's) |
| `--state` | string | no | no | — | `—` | the state dir (default <runtime>/coding) |
| `--text` | flag | no | no | — | `false` | human-readable output on stderr instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/coding_features.py` — coding expand

**Contract note (verbatim)**

> an absolute/parent mention or a symlink out of the workspace is REFUSED and listed; nothing outside the workspace is ever read

#### `coding.init` — EXPAND

write an AGENTS.md whose headings project_context injects, from measured facts

| | |
|---|---|
| Group | `coding` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--force` | flag | no | no | — | `false` | replace an existing file |
| `--file` | string | no | no | — | `AGENTS.md` | the file name to write |
| `--name` | string | no | no | — | `—` | the project name in the title |
| `--workspace` | string | no | no | — | `—` | the directory to work in (default: cwd) |
| `--runtime` | string | no | no | — | `—` | the runtime dir (default: the registry's) |
| `--state` | string | no | no | — | `—` | the state dir (default <runtime>/coding) |
| `--text` | flag | no | no | — | `false` | human-readable output on stderr instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/coding_features.py` — coding init

**Contract note (verbatim)**

> refuses to overwrite (exit 6) and returns the existing sha256, so --force is an informed decision; no command is invented

#### `coding.status` — PERSIST

the session summary: messages by role, token estimate, turns, open plan, model

| | |
|---|---|
| Group | `coding` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--session` | string | no | no | — | `—` | the session id to summarise |
| `--workspace` | string | no | no | — | `—` | the directory to work in (default: cwd) |
| `--runtime` | string | no | no | — | `—` | the runtime dir (default: the registry's) |
| `--state` | string | no | no | — | `—` | the state dir (default <runtime>/coding) |
| `--text` | flag | no | no | — | `false` | human-readable output on stderr instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/coding_features.py` — coding status

**Contract note (verbatim)**

> an unreadable session store is reported as available:false with the reason, never as a zero

#### `coding.summary` — PERSIST

the handoff summary (progress, constraints, what remains, references) as markdown

| | |
|---|---|
| Group | `coding` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--session` | string | no | no | — | `—` | the session id to summarise |
| `--write` | flag | no | no | — | `false` | write it to a file |
| `--out` | string | no | no | — | `—` | where to write it (default under the state dir) |
| `--workspace` | string | no | no | — | `—` | the directory to work in (default: cwd) |
| `--runtime` | string | no | no | — | `—` | the runtime dir (default: the registry's) |
| `--state` | string | no | no | — | `—` | the state dir (default <runtime>/coding) |
| `--text` | flag | no | no | — | `false` | human-readable output on stderr instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/coding_features.py` — coding summary

**Contract note (verbatim)**

> built from measured facts only; --write is the only writer (<runtime>/coding/summaries/)

#### `coding.testfix` — VERIFY

run the declared tests, parse the failures, hand them to a fixer and run again

| | |
|---|---|
| Group | `coding` |
| Layer | VERIFY |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `7` (INTERNAL), `8` (UNKNOWN) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `command` | string (positional) | yes | no | — | `—` | the test command (after --) |
| `--fix-cmd` | string | no | no | — | `—` | a shell-free fixer argv (parsed with shlex, no shell) |
| `--max-rounds` | integer | no | no | — | `3` | the hard round bound |
| `--timeout` | number | no | no | — | `900.0` | per test-run timeout |
| `--jsonl` | string | no | no | — | `—` | write the loop's events to this file |
| `--workspace` | string | no | no | — | `—` | the directory to work in (default: cwd) |
| `--runtime` | string | no | no | — | `—` | the runtime dir (default: the registry's) |
| `--state` | string | no | no | — | `—` | the state dir (default <runtime>/coding) |
| `--text` | flag | no | no | — | `false` | human-readable output on stderr instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/coding_features.py` — coding testfix

**Contract note (verbatim)**

> bounded by --max-rounds AND by a repeated failure fingerprint (no_progress); without a fixer the roster is returned and exit is 2 — nothing is claimed

### Group `coding.plan`

#### `coding.plan.begin` — ENFORCE

open plan mode for a task (refuses while another plan is open)

| | |
|---|---|
| Group | `coding.plan` |
| Layer | ENFORCE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `task` | string (positional) | yes | no | — | `—` | what the plan is for |
| `--workspace` | string | no | no | — | `—` | the directory to work in (default: cwd) |
| `--runtime` | string | no | no | — | `—` | the runtime dir (default: the registry's) |
| `--state` | string | no | no | — | `—` | the state dir (default <runtime>/coding) |
| `--text` | flag | no | no | — | `false` | human-readable output on stderr instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/coding_features.py` — coding plan begin

**Contract note (verbatim)**

> plan mode is enforced by coding plan check, not by prompt text

#### `coding.plan.check` — ENFORCE

classify one action as read_only or mutating and say whether it may run now

| | |
|---|---|
| Group | `coding.plan` |
| Layer | ENFORCE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `command` | string (positional) | yes | no | — | `—` | the action (after --) |
| `--action` | string | no | no | — | `—` | the action as one string |
| `--workspace` | string | no | no | — | `—` | the directory to work in (default: cwd) |
| `--runtime` | string | no | no | — | `—` | the runtime dir (default: the registry's) |
| `--state` | string | no | no | — | `—` | the state dir (default <runtime>/coding) |
| `--text` | flag | no | no | — | `false` | human-readable output on stderr instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/coding_features.py` — coding plan check

**Contract note (verbatim)**

> outside plan mode everything is allowed; inside, a mutating action is declined with exit 3 and the reason names the rule

#### `coding.plan.close` — ENFORCE

close plan mode with --apply (the licence to mutate) or --drop

| | |
|---|---|
| Group | `coding.plan` |
| Layer | ENFORCE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--apply` | flag | no | no | — | `false` | the plan is the licence to mutate |
| `--drop` | flag | no | no | — | `false` | abandon the plan |
| `--workspace` | string | no | no | — | `—` | the directory to work in (default: cwd) |
| `--runtime` | string | no | no | — | `—` | the runtime dir (default: the registry's) |
| `--state` | string | no | no | — | `—` | the state dir (default <runtime>/coding) |
| `--text` | flag | no | no | — | `false` | human-readable output on stderr instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/coding_features.py` — coding plan close

**Contract note (verbatim)**

> --apply does not run anything; it ends the refusal

#### `coding.plan.probe` — EXPAND

the non-mutating exploration a plan is grounded on (bounded walk + declared commands)

| | |
|---|---|
| Group | `coding.plan` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--task` | string | no | no | — | `—` | the task (default: the open plan's) |
| `--files` | integer | no | no | — | `40` | how many files to sample |
| `--workspace` | string | no | no | — | `—` | the directory to work in (default: cwd) |
| `--runtime` | string | no | no | — | `—` | the runtime dir (default: the registry's) |
| `--state` | string | no | no | — | `—` | the state dir (default <runtime>/coding) |
| `--text` | flag | no | no | — | `false` | human-readable output on stderr instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/coding_features.py` — coding plan probe

**Contract note (verbatim)**

> reuses codectx.walk_workspace and its ignore rules; a sample that was cut short says so

#### `coding.plan.propose` — ENFORCE

render the decision-complete <proposed_plan> for the open plan

| | |
|---|---|
| Group | `coding.plan` |
| Layer | ENFORCE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--change` | string | no | yes | — | `[]` | one planned change |
| `--test` | string | no | yes | — | `[]` | one test-plan line |
| `--assumption` | string | no | yes | — | `[]` | one assumption |
| `--question` | string | no | yes | — | `[]` | an open question (makes the plan NOT decision complete) |
| `--workspace` | string | no | no | — | `—` | the directory to work in (default: cwd) |
| `--runtime` | string | no | no | — | `—` | the runtime dir (default: the registry's) |
| `--state` | string | no | no | — | `—` | the state dir (default <runtime>/coding) |
| `--text` | flag | no | no | — | `false` | human-readable output on stderr instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/coding_features.py` — coding plan propose

**Contract note (verbatim)**

> an unanswered question makes the plan NOT decision complete and it says so

#### `coding.plan.show` — ENFORCE

the current plan: status, guarded actions and the rendered proposal

| | |
|---|---|
| Group | `coding.plan` |
| Layer | ENFORCE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `5` (NOT_FOUND) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--workspace` | string | no | no | — | `—` | the directory to work in (default: cwd) |
| `--runtime` | string | no | no | — | `—` | the runtime dir (default: the registry's) |
| `--state` | string | no | no | — | `—` | the state dir (default <runtime>/coding) |
| `--text` | flag | no | no | — | `false` | human-readable output on stderr instead of JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/coding_features.py` — coding plan show

**Contract note (verbatim)**

> no plan recorded exits 5; read-only

### Group `context`

#### `context.compact` — ROUTE · **declared limitation**

judge the tool history of a run per call (keep / shorten / remove) and report what would change; --dry-run applies nothing

| | |
|---|---|
| Group | `context` |
| Layer | ROUTE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--transcript` | string | no | no | — | `—` | a message file or a run transcript |
| `--run-id` | string | no | no | — | `—` | a run of this state dir |
| `--state-dir` | string | no | no | — | `—` | — |
| `--goal` | string | no | no | — | `—` | the run's goal (preserved verbatim) |
| `--criterion` | string | no | yes | — | `[]` | an acceptance criterion to preserve (repeatable) |
| `--budget` | integer | no | no | — | `—` | context budget in tokens |
| `--trigger-fraction` | number | no | no | — | `—` | — |
| `--keep-threshold` | number | no | no | — | `—` | — |
| `--preserve-recent` | integer | no | no | — | `—` | — |
| `--truncate-head` | integer | no | no | — | `—` | — |
| `--asker` | choice | no | no | fixture, provider | `fixture` | fixture = an offline simulation (default); provider = the real model |
| `--probability` | number | no | no | — | `—` | the fixture asker's flat keep probability (default 0.9/0.1) |
| `--dry-run` | flag | no | no | — | `false` | — |
| `--force` | flag | no | no | — | `false` | compact below the trigger too |
| `--out` | string | no | no | — | `—` | write the compacted history here |
| `--text` | flag | no | no | — | `false` | human output |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "report": {
      "type": "object"
    },
    "dry_run": {
      "type": "boolean"
    },
    "applied": {
      "type": "boolean"
    },
    "fallback": {
      "type": "boolean"
    },
    "preserved_ok": {
      "type": "boolean"
    },
    "messages_before": {
      "type": "integer"
    },
    "messages_after": {
      "type": "integer"
    },
    "tokens_estimate_before": {
      "type": "integer"
    },
    "tokens_estimate_after": {
      "type": "integer"
    },
    "asker": {
      "type": "string"
    },
    "source": {
      "type": "string"
    }
  },
  "required": [
    "report",
    "dry_run",
    "applied",
    "fallback",
    "preserved_ok"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/jevcomp/compact.py` — the ported upstream algorithm this command drives (keep/shorten/remove)
- `code`: `src/skaldir/context_manager.py` — the preserved set, the widened pinning window and the fallback

**Contract note (verbatim)**

> read-only unless --out is given (then the compacted history is written there, never into the run state); every token number is an estimate and says so; exit 3 when no asker can be built or the transcript records only a digest

#### `context.explain` — ENFORCE

show what would be injected and why: precedence, budget, refusals, overrides

| | |
|---|---|
| Group | `context` |
| Layer | ENFORCE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--allow-empty` | flag | no | no | — | `false` | exit 0 instead of 5 when nothing was found |
| `--dir` | string | no | no | — | `—` | start directory of the walk up (default: the current working directory) |
| `--workspace` | string | no | no | — | `—` | boundary the walk may not leave (default: the git root, else the filesystem root) |
| `--max-chars` | integer | no | no | — | `8000` | total character budget of the injected block (default 8000) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "ladder": {
      "type": "object"
    },
    "budget": {
      "type": "object"
    },
    "levels": {
      "type": "array"
    },
    "sources": {
      "type": "array"
    },
    "refused": {
      "type": "array"
    },
    "overrides": {
      "type": "array"
    },
    "resolutions": {
      "type": "array"
    },
    "warnings": {
      "type": "array"
    }
  },
  "required": [
    "ladder",
    "budget",
    "sources",
    "warnings"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/project_context.py:explain` — the ladder, the budget plan, the per-entry win/lose trail

**Contract note (verbatim)**

> the same build as context.show plus the why: refusals, overrides, truncation

#### `context.fingerprint` — ENFORCE

print the block fingerprint and the per-file digests (no block)

| | |
|---|---|
| Group | `context` |
| Layer | ENFORCE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--allow-empty` | flag | no | no | — | `false` | exit 0 instead of 5 when nothing was found |
| `--dir` | string | no | no | — | `—` | start directory of the walk up (default: the current working directory) |
| `--workspace` | string | no | no | — | `—` | boundary the walk may not leave (default: the git root, else the filesystem root) |
| `--max-chars` | integer | no | no | — | `8000` | total character budget of the injected block (default 8000) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "fingerprint": {
      "type": "string"
    },
    "fingerprint_short": {
      "type": "string"
    },
    "scheme": {
      "type": "string"
    },
    "chars": {
      "type": "integer"
    },
    "sources": {
      "type": "array"
    }
  },
  "required": [
    "fingerprint",
    "fingerprint_short",
    "sources"
  ]
}
```

**Default evidence**

- `file`: `sha256` — block fingerprint + per-file digests, recordable as run evidence

**Contract note (verbatim)**

> stable for the same workspace content — record it in a run's evidence

#### `context.show` — ENFORCE

print the exact block that would be injected (stdout), provenance to stderr

| | |
|---|---|
| Group | `context` |
| Layer | ENFORCE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--allow-empty` | flag | no | no | — | `false` | exit 0 with an empty block instead of 5 when nothing was found |
| `--dir` | string | no | no | — | `—` | start directory of the walk up (default: the current working directory) |
| `--workspace` | string | no | no | — | `—` | boundary the walk may not leave (default: the git root, else the filesystem root) |
| `--max-chars` | integer | no | no | — | `8000` | total character budget of the injected block (default 8000) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "block": {
      "type": "string"
    },
    "fingerprint": {
      "type": "string"
    },
    "fingerprint_short": {
      "type": "string"
    },
    "chars": {
      "type": "integer"
    },
    "max_chars": {
      "type": "integer"
    },
    "truncated": {
      "type": "boolean"
    },
    "empty": {
      "type": "boolean"
    },
    "sources": {
      "type": "array"
    },
    "refused": {
      "type": "array"
    },
    "warnings": {
      "type": "array"
    }
  },
  "required": [
    "block",
    "fingerprint",
    "chars",
    "sources"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/project_context.py` — discovery ladder + precedence ladder + the typed block
- `file`: `AGENTS.md / SOUL.md / SKALDIR.md / CLAUDE.md` — the files this command reads (read-only) — .skaldir/context.md too

**Contract note (verbatim)**

> read-only: the files are inputs, nothing is written; exit 5 when no instruction file was found and --allow-empty was not given, because an empty context is a finding — the block always carries its fingerprint

#### `context.usage` — ROUTE

the budget state of a transcript: measured facts, labelled estimates, trigger

| | |
|---|---|
| Group | `context` |
| Layer | ROUTE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--transcript` | string | no | no | — | `—` | a message file or a run transcript |
| `--run-id` | string | no | no | — | `—` | a run of this state dir |
| `--state-dir` | string | no | no | — | `—` | — |
| `--goal` | string | no | no | — | `—` | — |
| `--criterion` | string | no | yes | — | `[]` | — |
| `--budget` | integer | no | no | — | `—` | context budget in tokens |
| `--trigger-fraction` | number | no | no | — | `—` | — |
| `--preserve-recent` | integer | no | no | — | `—` | — |
| `--history` | flag | no | no | — | `false` | include the last compaction |
| `--text` | flag | no | no | — | `false` | human output |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "accounting": {
      "type": "object"
    },
    "trigger": {
      "type": "object"
    },
    "source": {
      "type": "string"
    },
    "compactions": {
      "type": "array"
    }
  },
  "required": [
    "accounting",
    "trigger",
    "source"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/jevcomp/state.py` — estimate_tokens — the ported estimator behind every token number here
- `code`: `src/skaldir/context_manager.py` — accounting_for — measured counts vs labelled estimates

**Contract note (verbatim)**

> read-only; 'measured' carries exact counts and provider-reported usage, 'estimated' the two labelled token estimates — never mixed

### Group `edit`

#### `edit.apply` — ENFORCE

plan and publish an atomic multi-file edit batch, then run the declared check

| | |
|---|---|
| Group | `edit` |
| Layer | ENFORCE |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--tx` | string | no | no | — | `—` | the transaction document: a file path, or '-' for stdin |
| `--tx-json` | string | no | no | — | `—` | the transaction document inline as JSON (instead of --tx) |
| `--context-lines` | integer | no | no | — | `3` | diff context lines (default 3) |
| `--keep-on-failure` | flag | no | no | — | `false` | keep the change when the verification fails (the transaction is then reported applied_unverified, never verified) |
| `--allow-near-duplicates` | flag | no | no | — | `false` | accept a needle whose whitespace/indentation-normalised form appears elsewhere (strict transactions refuse it) |
| `--verify` | choice | no | no | , syntax, command, tests, none | `—` | the check to run after apply (default: the document's, else 'syntax') |
| `--verify-command` | string | no | no | — | `—` | a command string for --verify command (split without a shell) |
| `--verify-argv` | string | no | no | — | `—` | a JSON list of strings: the argv to run (no shell) |
| `--verify-tests` | string | no | yes | — | `—` | pytest targets, run through this interpreter (kind 'tests') |
| `--verify-timeout` | number | no | no | — | `—` | seconds for the verification command (default 120) |
| `--verify-expect-exit` | integer | no | no | — | `0` | the exit code that means 'verified' (default 0) |
| `--max-file-mb` | integer | no | no | — | `—` | one file, in MiB (default 2) |
| `--max-tx-mb` | integer | no | no | — | `—` | the whole transaction's bytes, in MiB (default 8) |
| `--max-files` | integer | no | no | — | `—` | files in one transaction (default 64) |
| `--max-edits` | integer | no | no | — | `—` | edits in one transaction (default 256) |
| `--max-diff-kb` | integer | no | no | — | `—` | the diff inside the payload, in KiB (default 195) |
| `--workspace` | string | no | no | — | `—` | the root every path in the document is resolved against (default: the current directory) |
| `--state` | string | no | no | — | `—` | where records and snapshots live (default: <runtime>/edit) |
| `--runtime` | string | no | no | — | `—` | the Skaldir runtime directory (default: the installation's) |
| `--text` | flag | no | no | — | `false` | human-readable output instead of the JSON record |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "schema_version": {
      "type": "string"
    },
    "tx_id": {
      "type": "string"
    },
    "created_at": {
      "type": "string"
    },
    "workspace": {
      "type": "string"
    },
    "mode": {
      "type": "string",
      "enum": [
        "apply",
        "dry_run"
      ]
    },
    "verdict": {
      "type": "string",
      "enum": [
        "applicable",
        "not_applicable",
        "applied",
        "blocked",
        "rolled_back",
        "applied_unverified",
        "undone"
      ]
    },
    "applied": {
      "type": "boolean"
    },
    "in_tree": {
      "type": "boolean"
    },
    "verified": {
      "type": [
        "boolean",
        "null"
      ]
    },
    "files": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "path": {
            "type": "string"
          },
          "abs_path": {
            "type": "string"
          },
          "ok": {
            "type": "boolean"
          },
          "reason": {
            "type": "string"
          },
          "message": {
            "type": "string"
          },
          "strategy": {
            "type": "string"
          },
          "score": {
            "type": "number"
          },
          "matches": {
            "type": "integer"
          },
          "occurrence_lines": {
            "type": "array",
            "items": {
              "type": "integer"
            }
          },
          "before_sha256": {
            "type": "string"
          },
          "after_sha256": {
            "type": "string"
          },
          "bytes_before": {
            "type": "integer"
          },
          "bytes_after": {
            "type": "integer"
          },
          "diff": {
            "type": "string"
          },
          "diff_truncated": {
            "type": "boolean"
          }
        },
        "required": [
          "path",
          "ok",
          "reason"
        ]
      }
    },
    "diff": {
      "type": "string"
    },
    "diff_truncated": {
      "type": "boolean"
    },
    "verification": {
      "type": "object"
    },
    "rollback": {
      "type": [
        "object",
        "null"
      ]
    },
    "undo": {
      "type": [
        "object",
        "null"
      ]
    },
    "record_path": {
      "type": "string"
    },
    "caps": {
      "type": "object"
    },
    "caps_hit": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "limits": {
      "type": "array",
      "items": {
        "type": "string"
      }
    }
  },
  "required": [
    "schema_version",
    "verdict",
    "files"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/edit_tx.py`
- `code`: `src/skaldir/edit_cli.py`
- `code`: `src/skaldir/tools_core/patch_tool.py` — the matching ladder this layer runs the edits through

**Contract note (verbatim)**

> all-or-nothing: a refused plan publishes nothing; a failed verification rolls back and exits 2; every cap is declared in data.caps and overridable through SKALDIR_EDIT_MAX_FILE_BYTES / _MAX_TX_BYTES / _MAX_FILES / _MAX_EDITS / _MAX_DIFF_BYTES (the legacy GODSV_* spelling is accepted)

#### `edit.dry-run` — ENFORCE

resolve every edit, print the diffs and the exact failure reason, write nothing

| | |
|---|---|
| Group | `edit` |
| Layer | ENFORCE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--tx` | string | no | no | — | `—` | the transaction document: a file path, or '-' for stdin |
| `--tx-json` | string | no | no | — | `—` | the transaction document inline as JSON (instead of --tx) |
| `--context-lines` | integer | no | no | — | `3` | diff context lines (default 3) |
| `--keep-on-failure` | flag | no | no | — | `false` | keep the change when the verification fails (the transaction is then reported applied_unverified, never verified) |
| `--allow-near-duplicates` | flag | no | no | — | `false` | accept a needle whose whitespace/indentation-normalised form appears elsewhere (strict transactions refuse it) |
| `--verify` | choice | no | no | , syntax, command, tests, none | `—` | the check to run after apply (default: the document's, else 'syntax') |
| `--verify-command` | string | no | no | — | `—` | a command string for --verify command (split without a shell) |
| `--verify-argv` | string | no | no | — | `—` | a JSON list of strings: the argv to run (no shell) |
| `--verify-tests` | string | no | yes | — | `—` | pytest targets, run through this interpreter (kind 'tests') |
| `--verify-timeout` | number | no | no | — | `—` | seconds for the verification command (default 120) |
| `--verify-expect-exit` | integer | no | no | — | `0` | the exit code that means 'verified' (default 0) |
| `--max-file-mb` | integer | no | no | — | `—` | one file, in MiB (default 2) |
| `--max-tx-mb` | integer | no | no | — | `—` | the whole transaction's bytes, in MiB (default 8) |
| `--max-files` | integer | no | no | — | `—` | files in one transaction (default 64) |
| `--max-edits` | integer | no | no | — | `—` | edits in one transaction (default 256) |
| `--max-diff-kb` | integer | no | no | — | `—` | the diff inside the payload, in KiB (default 195) |
| `--workspace` | string | no | no | — | `—` | the root every path in the document is resolved against (default: the current directory) |
| `--state` | string | no | no | — | `—` | where records and snapshots live (default: <runtime>/edit) |
| `--runtime` | string | no | no | — | `—` | the Skaldir runtime directory (default: the installation's) |
| `--text` | flag | no | no | — | `false` | human-readable output instead of the JSON record |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "schema_version": {
      "type": "string"
    },
    "tx_id": {
      "type": "string"
    },
    "created_at": {
      "type": "string"
    },
    "workspace": {
      "type": "string"
    },
    "mode": {
      "type": "string",
      "enum": [
        "apply",
        "dry_run"
      ]
    },
    "verdict": {
      "type": "string",
      "enum": [
        "applicable",
        "not_applicable",
        "applied",
        "blocked",
        "rolled_back",
        "applied_unverified",
        "undone"
      ]
    },
    "applied": {
      "type": "boolean"
    },
    "in_tree": {
      "type": "boolean"
    },
    "verified": {
      "type": [
        "boolean",
        "null"
      ]
    },
    "files": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "path": {
            "type": "string"
          },
          "abs_path": {
            "type": "string"
          },
          "ok": {
            "type": "boolean"
          },
          "reason": {
            "type": "string"
          },
          "message": {
            "type": "string"
          },
          "strategy": {
            "type": "string"
          },
          "score": {
            "type": "number"
          },
          "matches": {
            "type": "integer"
          },
          "occurrence_lines": {
            "type": "array",
            "items": {
              "type": "integer"
            }
          },
          "before_sha256": {
            "type": "string"
          },
          "after_sha256": {
            "type": "string"
          },
          "bytes_before": {
            "type": "integer"
          },
          "bytes_after": {
            "type": "integer"
          },
          "diff": {
            "type": "string"
          },
          "diff_truncated": {
            "type": "boolean"
          }
        },
        "required": [
          "path",
          "ok",
          "reason"
        ]
      }
    },
    "diff": {
      "type": "string"
    },
    "diff_truncated": {
      "type": "boolean"
    },
    "verification": {
      "type": "object"
    },
    "rollback": {
      "type": [
        "object",
        "null"
      ]
    },
    "undo": {
      "type": [
        "object",
        "null"
      ]
    },
    "record_path": {
      "type": "string"
    },
    "caps": {
      "type": "object"
    },
    "caps_hit": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "limits": {
      "type": "array",
      "items": {
        "type": "string"
      }
    }
  },
  "required": [
    "schema_version",
    "verdict",
    "files"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/edit_tx.py`
- `code`: `src/skaldir/edit_cli.py`
- `code`: `src/skaldir/tools_core/patch_tool.py` — the matching ladder this layer runs the edits through

**Contract note (verbatim)**

> exits with the code apply would exit with for the same document; failures are not_found / ambiguous_match / would_change_other_occurrences / cascade_edit; every cap is declared in data.caps and overridable through SKALDIR_EDIT_MAX_FILE_BYTES / _MAX_TX_BYTES / _MAX_FILES / _MAX_EDITS / _MAX_DIFF_BYTES (the legacy GODSV_* spelling is accepted)

#### `edit.show` — PERSIST

one transaction record (verdict, diffs, verification, snapshot, undo) or the list

| | |
|---|---|
| Group | `edit` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `tx_id` | string (positional) | yes | no | — | `last` | the transaction id, or 'last' (default: last) |
| `--list` | flag | no | no | — | `false` | list every record in the state directory instead |
| `--max-file-mb` | integer | no | no | — | `—` | one file, in MiB (default 2) |
| `--max-tx-mb` | integer | no | no | — | `—` | the whole transaction's bytes, in MiB (default 8) |
| `--max-files` | integer | no | no | — | `—` | files in one transaction (default 64) |
| `--max-edits` | integer | no | no | — | `—` | edits in one transaction (default 256) |
| `--max-diff-kb` | integer | no | no | — | `—` | the diff inside the payload, in KiB (default 195) |
| `--workspace` | string | no | no | — | `—` | the root every path in the document is resolved against (default: the current directory) |
| `--state` | string | no | no | — | `—` | where records and snapshots live (default: <runtime>/edit) |
| `--runtime` | string | no | no | — | `—` | the Skaldir runtime directory (default: the installation's) |
| `--text` | flag | no | no | — | `false` | human-readable output instead of the JSON record |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "transactions": {
      "type": "array"
    },
    "count": {
      "type": "integer"
    },
    "tx_id": {
      "type": "string"
    },
    "verdict": {
      "type": "string"
    },
    "files": {
      "type": "array"
    },
    "record_path": {
      "type": "string"
    }
  },
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/edit_tx.py`
- `code`: `src/skaldir/edit_cli.py`

**Contract note (verbatim)**

> --list returns {count, transactions[]}; a single record is returned as-is (data.undo.undone_at is what marks it undone)

#### `edit.undo` — PERSIST

restore every file of a transaction from its snapshot, byte-identically

| | |
|---|---|
| Group | `edit` |
| Layer | PERSIST |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `tx_id` | string (positional) | yes | no | — | `last` | the transaction id, or 'last' (default: last) |
| `--force` | flag | no | no | — | `false` | restore even when a touched file changed after the apply |
| `--max-file-mb` | integer | no | no | — | `—` | one file, in MiB (default 2) |
| `--max-tx-mb` | integer | no | no | — | `—` | the whole transaction's bytes, in MiB (default 8) |
| `--max-files` | integer | no | no | — | `—` | files in one transaction (default 64) |
| `--max-edits` | integer | no | no | — | `—` | edits in one transaction (default 256) |
| `--max-diff-kb` | integer | no | no | — | `—` | the diff inside the payload, in KiB (default 195) |
| `--workspace` | string | no | no | — | `—` | the root every path in the document is resolved against (default: the current directory) |
| `--state` | string | no | no | — | `—` | where records and snapshots live (default: <runtime>/edit) |
| `--runtime` | string | no | no | — | `—` | the Skaldir runtime directory (default: the installation's) |
| `--text` | flag | no | no | — | `false` | human-readable output instead of the JSON record |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "schema_version": {
      "type": "string"
    },
    "tx_id": {
      "type": "string"
    },
    "created_at": {
      "type": "string"
    },
    "workspace": {
      "type": "string"
    },
    "mode": {
      "type": "string",
      "enum": [
        "apply",
        "dry_run"
      ]
    },
    "verdict": {
      "type": "string",
      "enum": [
        "applicable",
        "not_applicable",
        "applied",
        "blocked",
        "rolled_back",
        "applied_unverified",
        "undone"
      ]
    },
    "applied": {
      "type": "boolean"
    },
    "in_tree": {
      "type": "boolean"
    },
    "verified": {
      "type": [
        "boolean",
        "null"
      ]
    },
    "files": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "path": {
            "type": "string"
          },
          "abs_path": {
            "type": "string"
          },
          "ok": {
            "type": "boolean"
          },
          "reason": {
            "type": "string"
          },
          "message": {
            "type": "string"
          },
          "strategy": {
            "type": "string"
          },
          "score": {
            "type": "number"
          },
          "matches": {
            "type": "integer"
          },
          "occurrence_lines": {
            "type": "array",
            "items": {
              "type": "integer"
            }
          },
          "before_sha256": {
            "type": "string"
          },
          "after_sha256": {
            "type": "string"
          },
          "bytes_before": {
            "type": "integer"
          },
          "bytes_after": {
            "type": "integer"
          },
          "diff": {
            "type": "string"
          },
          "diff_truncated": {
            "type": "boolean"
          }
        },
        "required": [
          "path",
          "ok",
          "reason"
        ]
      }
    },
    "diff": {
      "type": "string"
    },
    "diff_truncated": {
      "type": "boolean"
    },
    "verification": {
      "type": "object"
    },
    "rollback": {
      "type": [
        "object",
        "null"
      ]
    },
    "undo": {
      "type": [
        "object",
        "null"
      ]
    },
    "record_path": {
      "type": "string"
    },
    "caps": {
      "type": "object"
    },
    "caps_hit": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "limits": {
      "type": "array",
      "items": {
        "type": "string"
      }
    }
  },
  "required": [
    "schema_version",
    "verdict",
    "files"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/edit_tx.py`
- `code`: `src/skaldir/edit_cli.py`

**Contract note (verbatim)**

> refuses (6) when a touched file changed after the apply unless --force is given; every restore carries byte_identical + both sha256s

### Group `mcp`

#### `mcp.call` — EXPAND

call the registered MCP capability through its adapter (--via wrapper runs the generated CLI wrapper)

| | |
|---|---|
| Group | `mcp` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | — |
| `--input` | string | no | no | — | `{}` | the call payload as JSON |
| `--via` | choice | no | no | adapter, wrapper | `adapter` | adapter = the typed call path; wrapper = the generated CLI script |
| `--timeout` | number | no | no | — | `0.0` | — |
| `--runtime` | string | no | no | — | `—` | — |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/adapter_mcp.py`

#### `mcp.map` — EXPAND

map a server's tools to typed adapters; an untypable schema is refused with its reason

| | |
|---|---|
| Group | `mcp` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--command` | string | no | no | — | `—` | the MCP server command, e.g. "python -m my.server" |
| `--arg` | string | no | yes | — | `[]` | one extra argv for --command (repeatable) |
| `--url` | string | no | no | — | `—` | a streamable-HTTP MCP endpoint (instead of --command) |
| `--env` | string | no | yes | — | `[]` | environment for the stdio server (repeatable) |
| `--header` | string | no | yes | — | `[]` | HTTP header for the endpoint (repeatable) |
| `--name` | string | no | no | — | `—` | a short name for this server |
| `--tool` | string | no | yes | — | `[]` | map only this tool (repeatable; default: all of them) |
| `--capability` | string | no | no | — | `—` | the capability text recorded on the adapter |
| `--adapter-name` | string | no | no | — | `—` | registry name for a single --tool mapping |
| `--all` | flag | no | no | — | `false` | explicitly map every listed tool (the default) |
| `--no-stage` | flag | no | no | — | `false` | show the mapping without writing anything to disk |
| `--protocol` | string | no | no | — | `2025-06-18` | — |
| `--allow-unknown-protocol` | flag | no | no | — | `false` | — |
| `--timeout` | number | no | no | — | `30.0` | — |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $SKALDIR_HOME) |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/adapter_mcp.py`

**Contract note (verbatim)**

> --stage writes the adapter/module/wrapper/manifest; a refused tool is never registered

#### `mcp.probe` — EXPAND

handshake + tools/list with the raw transcript

| | |
|---|---|
| Group | `mcp` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--command` | string | no | no | — | `—` | the MCP server command, e.g. "python -m my.server" |
| `--arg` | string | no | yes | — | `[]` | one extra argv for --command (repeatable) |
| `--url` | string | no | no | — | `—` | a streamable-HTTP MCP endpoint (instead of --command) |
| `--env` | string | no | yes | — | `[]` | environment for the stdio server (repeatable) |
| `--header` | string | no | yes | — | `[]` | HTTP header for the endpoint (repeatable) |
| `--name` | string | no | no | — | `—` | a short name for this server |
| `--protocol` | string | no | no | — | `2025-06-18` | protocol version to request (default 2025-06-18) |
| `--allow-unknown-protocol` | flag | no | no | — | `false` | continue even when the server negotiates an unknown version |
| `--transcript` | flag | no | no | — | `false` | print every raw JSON-RPC message of the handshake |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/adapter_mcp.py`

**Contract note (verbatim)**

> nothing is written: this is the read-only door-opener

#### `mcp.register` — EXPAND

register a TESTED MCP adapter live

| | |
|---|---|
| Group | `mcp` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | — |
| `--version` | string | no | no | — | `—` | explicit version (default: 0.1.0; re-registering bumps the patch) |
| `--project-id` | string | no | no | — | `—` | project id recorded in the manifest |
| `--project` | string | no | no | — | `—` | project context for the registry |
| `--requested-by` | string | no | no | — | `cli` | — |
| `--runtime` | string | no | no | — | `—` | — |
| `--db` | string | no | no | — | `—` | explicit tools DB override |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/adapter_mcp.py`
- `code`: `src/skaldir/tools.py`

**Contract note (verbatim)**

> exit 2 = the adapter is not (still) proven: no registration

#### `mcp.serve` — kernel

run the stdio MCP server

| | |
|---|---|
| Group | `mcp` |
| Layer | kernel |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--db` | string | no | no | — | `—` | explicit tools DB (default: the runtime DB; see `skaldir registry status`) |
| `--name` | string | no | no | — | `godsv-tools` | — |
| `--project` | string | no | no | — | `—` | project path — expose global tools plus this project's tools only (§7) |
| `--all` | flag | no | no | — | `false` | explicit all view: also expose legacy (scope-less) tools |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: resolved runtime; explicit --db: its directory) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/mcpserver.py`

**Contract note (verbatim)**

> blocks by design (stdio server)

#### `mcp.test` — EXPAND

run a real tools/call against the server and check the declared outputs

| | |
|---|---|
| Group | `mcp` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | — |
| `--input` | string | no | no | — | `{}` | the call payload as JSON |
| `--expect` | string | no | no | — | `—` | the expected outputs as JSON |
| `--timeout` | number | no | no | — | `0.0` | — |
| `--runtime` | string | no | no | — | `—` | — |
| `--json` | flag | no | no | — | `false` | — |
| `--text` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/adapter_mcp.py`

**Contract note (verbatim)**

> exit 2 = the real call did not return the declared outputs (or --expect was missing); the evidence binds to the adapter bytes

### Group `stream`

#### `stream.measure` — PERSIST

measure a streamed answer with and without coalescing

| | |
|---|---|
| Group | `stream` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--chunks` | integer | no | no | — | `600` | chunks in the fixture sequence |
| `--chunk-bytes` | integer | no | no | — | `8` | bytes per chunk (a token is 3-12 bytes) |
| `--hz` | number | no | no | — | `30.0` | renders per second (must be at least 1) |
| `--max-buffer` | integer | no | no | — | `8192` | force a render at this many buffered characters |
| `--chunk-rate` | number | no | no | — | `300.0` | chunks per second the fixture is paced at (0 = tight loop) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "chunks": {
      "type": "integer",
      "description": "chunks in the sequence"
    },
    "chunk_bytes": {
      "type": "integer",
      "description": "bytes per chunk"
    },
    "hz": {
      "type": "number",
      "description": "requested render rate"
    },
    "before": {
      "type": "object",
      "properties": {
        "chunks_in": {
          "type": "integer",
          "description": "chunks accepted from the source"
        },
        "chars_in": {
          "type": "integer",
          "description": "characters accepted"
        },
        "renders": {
          "type": "integer",
          "description": "writes that reached the terminal"
        },
        "renders_per_s": {
          "type": "number",
          "description": "renders per second, measured"
        },
        "first_render_ms": {
          "type": "integer",
          "description": "ms to the first visible byte"
        },
        "cpu_ms": {
          "type": "integer",
          "description": "process CPU time, user+system"
        },
        "byte_identical": {
          "type": "boolean",
          "description": "input reproduced byte for byte"
        }
      },
      "required": [
        "chunks_in",
        "renders"
      ]
    },
    "after": {
      "type": "object",
      "properties": {
        "chunks_in": {
          "type": "integer",
          "description": "chunks accepted from the source"
        },
        "chars_in": {
          "type": "integer",
          "description": "characters accepted"
        },
        "renders": {
          "type": "integer",
          "description": "writes that reached the terminal"
        },
        "renders_per_s": {
          "type": "number",
          "description": "renders per second, measured"
        },
        "first_render_ms": {
          "type": "integer",
          "description": "ms to the first visible byte"
        },
        "cpu_ms": {
          "type": "integer",
          "description": "process CPU time, user+system"
        },
        "byte_identical": {
          "type": "boolean",
          "description": "input reproduced byte for byte"
        }
      },
      "required": [
        "chunks_in",
        "renders"
      ]
    },
    "renders_saved": {
      "type": "integer",
      "description": "renders avoided"
    },
    "cpu_saved_ms": {
      "type": "integer",
      "description": "CPU milliseconds saved"
    },
    "byte_identical": {
      "type": "boolean",
      "description": "both arms reproduced the input bytes"
    }
  },
  "required": [
    "chunks",
    "before",
    "after",
    "byte_identical"
  ]
}
```

**Default evidence**

- `run`: `src/skaldir/streaming.py:_measure` — the same chunks through the same sink, coalescing off then on

**Contract note (verbatim)**

> the source is a deterministic synthetic sequence at a tight loop — the upper bound on render pressure, not a modelled token rate; the payload says so

#### `stream.policy` — COMPOSE

the parallel-tool grouping a run would use, call by call

| | |
|---|---|
| Group | `stream` |
| Layer | COMPOSE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--tool` | string | no | yes | — | `[]` | a call: name with an optional :parallel/:sequential |
| `--default` | string | no | no | — | `sequential` | mode for calls that declare none (parallel\|sequential) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "default": {
      "type": "string",
      "description": "mode for undeclared calls"
    },
    "calls": {
      "type": "array",
      "description": "the calls as they were declared",
      "items": {
        "type": "object"
      }
    },
    "batches": {
      "type": "array",
      "description": "groups that may overlap",
      "items": {
        "type": "object"
      }
    },
    "parallel_batches": {
      "type": "integer",
      "description": "groups that overlap"
    },
    "barriers": {
      "type": "integer",
      "description": "sequential barriers"
    }
  },
  "required": [
    "default",
    "batches"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/streaming.py:plan_tool_batches` — adjacent-parallel grouping with a sequential barrier

**Contract note (verbatim)**

> read-only: it reports the grouping, it does not execute a tool call

#### `stream.replay` — PERSIST

replay a real file as a chunked stream (the measurement's source)

| | |
|---|---|
| Group | `stream` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--source` | string | no | no | — | `—` | the file to stream (default: README.md, else AGENTS.md) |
| `--chunk-bytes` | integer | no | no | — | `8` | bytes per chunk (a token is 3-12 bytes) |
| `--rate` | number | no | no | — | `300.0` | chunks per second (0 = as fast as it can write) |
| `--limit` | integer | no | no | — | `0` | stop after this many bytes (0 = the whole file) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "source": {
      "type": "string",
      "description": "where the bytes came from"
    },
    "chunks": {
      "type": "integer",
      "description": "writes performed"
    },
    "chunk_bytes": {
      "type": "integer",
      "description": "bytes per chunk"
    },
    "chars": {
      "type": "integer",
      "description": "characters streamed"
    },
    "rate": {
      "type": "number",
      "description": "declared chunks per second"
    },
    "wall_ms": {
      "type": "integer",
      "description": "wall time, ms"
    },
    "cpu_ms": {
      "type": "integer",
      "description": "this process's CPU, ms"
    }
  },
  "required": [
    "source",
    "chunks",
    "chars"
  ]
}
```

**Default evidence**

- `run`: `src/skaldir/streaming.py:_cmd_replay` — one chunk per write, flushed, at the declared rate

**Contract note (verbatim)**

> the bytes are a real file; the chunk boundaries and the rate are declared. It is a benchmark source, not a model: the payload says `pacing: declared`. In text mode the answer goes to stdout and the facts to stderr, so the stream stays a clean stream; under --json the answer is captured by the driver and only the facts travel in the envelope.

#### `stream.selfcheck` — PERSIST · **declared limitation**

prove the render bound holds (byte-identical, no dropped bytes)

| | |
|---|---|
| Group | `stream` |
| Layer | PERSIST |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--chunks` | integer | no | no | — | `600` | chunks in the fixture sequence |
| `--hz` | number | no | no | — | `30.0` | the render rate under test |
| `--chunk-rate` | number | no | no | — | `300.0` | chunks per second the fixture is paced at (0 = tight loop) |
| `--max-buffer` | integer | no | no | — | `8192` | force a render at this many buffered characters |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "ok": {
      "type": "boolean",
      "description": "every invariant held"
    },
    "checks": {
      "type": "array",
      "description": "one row per invariant",
      "items": {
        "type": "object"
      }
    },
    "checks_passed": {
      "type": "integer",
      "description": "invariants that held"
    },
    "checks_total": {
      "type": "integer",
      "description": "invariants evaluated"
    },
    "measurement": {
      "type": "object",
      "properties": {
        "chunks_in": {
          "type": "integer",
          "description": "chunks accepted from the source"
        },
        "chars_in": {
          "type": "integer",
          "description": "characters accepted"
        },
        "renders": {
          "type": "integer",
          "description": "writes that reached the terminal"
        },
        "renders_per_s": {
          "type": "number",
          "description": "renders per second, measured"
        },
        "first_render_ms": {
          "type": "integer",
          "description": "ms to the first visible byte"
        },
        "cpu_ms": {
          "type": "integer",
          "description": "process CPU time, user+system"
        },
        "byte_identical": {
          "type": "boolean",
          "description": "input reproduced byte for byte"
        }
      },
      "required": [
        "chunks_in",
        "renders"
      ]
    }
  },
  "required": [
    "ok",
    "checks_passed",
    "checks_total"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/streaming.py:Coalescer` — accepts every chunk, renders at most once per interval
- `test`: `src/skaldir/streaming.py:_cmd_selfcheck` — five invariants: byte identity, bound, suppression, no drop, first byte

**Contract note (verbatim)**

> read-only, in-process, deterministic: no terminal and no network involved. A failing invariant exits 3 with the reason, never 0.

### Group `tools`

#### `tools.approve` — EXPAND

run gates, then approve

| | |
|---|---|
| Group | `tools` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | — |
| `--db` | string | no | no | — | `—` | explicit tools DB (default: the runtime DB; see `skaldir registry status`) |
| `--workdir` | string | no | no | — | `.` | — |
| `--force` | flag | no | no | — | `false` | — |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/gates.py:336`

#### `tools.build` — EXPAND

capability → gated tool (or a container tool with --image)

| | |
|---|---|
| Group | `tools` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `4` (NEEDS_CREDENTIALS), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--capability` | string | yes | no | — | `—` | what capability is missing |
| `--reason` | string | no | no | — | `—` | why the main agent needs it |
| `--requested-by` | string | no | no | — | `cli` | — |
| `--kind` | choice | no | no | auto, mcp, cli, testing_tool | `auto` | — |
| `--name` | string | no | no | — | `—` | — |
| `--project` | string | no | no | — | `—` | project path — records the stable project id (default: cwd) |
| `--validation-case` | string | no | yes | — | `—` | testing tools: TARGET:pass\|fail (broken target must FAIL, working must PASS) |
| `--source` | string | no | no | — | `—` | generated tool source file |
| `--tests` | string | no | no | — | `—` | generated tool tests file |
| `--module` | string | no | no | — | `—` | — |
| `--entrypoint` | string | no | no | — | `—` | — |
| `--sample` | string | no | no | — | `—` | JSON args for the determinism gate |
| `--research` | string | no | no | — | `—` | research notes (text) |
| `--research-file` | string | no | no | — | `—` | research notes (file) |
| `--secret` | string | no | yes | — | `—` | required secret HANDLE (repeatable) |
| `--domain` | string | no | yes | — | `—` | allowed domain (repeatable) |
| `--dependency` | string | no | yes | — | `—` | declared dependency (repeatable) |
| `--db` | string | no | no | — | `—` | explicit tools DB (default: the runtime DB; see `skaldir registry status`) |
| `--json` | flag | no | no | — | `false` | — |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--image` | string | no | no | — | `—` | build a container tool from this image (runtime=docker, pinned by digest) |
| `--digest` | string | no | no | — | `—` | pin the image by sha256 digest |
| `--pull` | flag | no | no | — | `false` | ask the registry even when the image is already local (default: pin the LOCAL digest — no network, no rate limit) |
| `--image-digest` | string | no | no | — | `—` | alias of --digest: pin the image by its sha256 digest |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/toolbuilder.py:298`
- `code`: `src/skaldir/toolbuilder.py ToolBuilder.manager`

**Contract note (verbatim)**

> --image/--digest build the container half (runtime=docker, pinned by digest): the container probe runs through the docker runtime and the payload carries data.container_probe; exit 3 when the build is blocked, 6 on a duplicate registration, 4 when the gate needs credentials

#### `tools.builds` — EXPAND

the tool-build queue

| | |
|---|---|
| Group | `tools` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--limit` | integer | no | no | — | `20` | — |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/toolbuilder.py`

#### `tools.call` — EXPAND

call one approved registry tool by name and report result + sha256

| | |
|---|---|
| Group | `tools` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | the tool to run (see `tools list --json`) |
| `--json-args` | string | no | no | — | `—` | the arguments as ONE JSON object literal ('{}' when the tool takes none) |
| `--runtime` | string | no | no | — | `—` | runtime dir override |
| `--project` | string | no | no | — | `—` | project path for the visible-scope rule (default: cwd) |
| `--text` | flag | no | no | — | `false` | humans over JSON |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "name": {
      "type": "string"
    },
    "ok": {
      "type": "boolean"
    },
    "arguments": {
      "type": "object"
    },
    "result": {},
    "result_sha256": {
      "type": "string"
    },
    "tools_db": {
      "type": "string"
    },
    "refusal": {
      "type": "string"
    },
    "failure_class": {
      "type": "string"
    }
  },
  "required": [
    "name",
    "ok",
    "result",
    "result_sha256"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/tools_call_cli.py` — one call path: approved_python_specs + health_verdict resolve the spec, the call goes through skaldir.agent_loop._call_registry_tool (partial(_call_registry_tool, _wrapper_for(spec))) exactly as the loop does
- `code`: `src/skaldir/mcpserver.py` — the executable-surface rule (scope + health gate) the verb resolves by

**Contract note (verbatim)**

> unknown name exits 5; an off-surface or health-blocked tool exits 3 with the resolver's own reason. tools.db missing ⇒ exit 5 (nothing to resolve)

#### `tools.candidates` — EXPAND

list mined candidates

| | |
|---|---|
| Group | `tools` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/capability_mining.py:115`

#### `tools.compose` — COMPOSE

list composition candidates for a need

| | |
|---|---|
| Group | `tools` |
| Layer | COMPOSE |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `need` | string (positional) | yes | no | — | `—` | — |
| `--db` | string | no | no | — | `—` | explicit tools DB (default: the runtime DB; see `skaldir registry status`) |
| `--tag` | string | no | yes | — | `—` | — |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "need": {
      "type": "string"
    },
    "candidates": {
      "type": "array"
    },
    "count": {
      "type": "integer"
    }
  },
  "required": [
    "need",
    "candidates",
    "count"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/tools.py:469`

#### `tools.fulfill` — EXPAND

run the build pipeline

| | |
|---|---|
| Group | `tools` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `request_id` | string (positional) | yes | no | — | `—` | — |
| `--json` | flag | no | no | — | `false` | — |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/capability.py:145`

#### `tools.health` — EXPAND

health of a container capability (ledger state + live container facts)

| | |
|---|---|
| Group | `tools` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | — |
| `--probe` | flag | no | no | — | `false` | run the adapter once (real call) |
| `--runtime` | string | no | no | — | `—` | runtime dir |
| `--db` | string | no | no | — | `—` | explicit tools DB override |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/toolbuilder.py ContainerToolBuilder.container_health`
- `code`: `src/skaldir/toolbuilder_cli.py _cmd_tools_health`

**Contract note (verbatim)**

> exit 3 when docker is not reachable; the ledger state is still reported (and --probe makes a real call, never a simulated one)

#### `tools.list` — EXPAND

list tool specs from the runtime DB

| | |
|---|---|
| Group | `tools` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--db` | string | no | no | — | `—` | explicit tools DB (default: the runtime DB; see `skaldir registry status`) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "tools": {
      "type": "array"
    },
    "count": {
      "type": "integer"
    },
    "tools_db": {
      "type": "string"
    },
    "tools_db_exists": {
      "type": "boolean"
    }
  },
  "required": [
    "tools",
    "count"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/registry.py:123`

#### `tools.mine` — EXPAND

scan for candidates, or mine one from a container image (--image)

| | |
|---|---|
| Group | `tools` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `5` (NOT_FOUND), `6` (CONFLICT), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--image` | string | no | no | — | `—` | mine a capability from this container image (inspect → adapt → test) |
| `--capability` | string | no | no | — | `—` | what the capability must do (required with --image) |
| `--name` | string | no | no | — | `—` | tool name for the mined capability |
| `--verb` | string | no | no | — | `—` | pin the container verb (e.g. sha256sum) |
| `--digest` | string | no | no | — | `—` | pin the image by sha256 digest |
| `--pull` | flag | no | no | — | `false` | pull the image when it is not local |
| `--test-input` | string | no | no | — | `—` | JSON inputs for the adapter test |
| `--expect` | string | no | no | — | `—` | the value the test must observe |
| `--session` | string | no | no | — | `—` | session id recorded with the build |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--db` | string | no | no | — | `—` | explicit tools DB override |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/capability_mining.py:311`
- `code`: `src/skaldir/toolbuilder.py ContainerToolBuilder.mine_container`

**Contract note (verbatim)**

> --image/--capability mine a container capability (inspect → adapt → test); gains --name/--verb/--digest/--pull/--test-input/--expect/--session (the --db/--runtime overrides come from the registry context); exit 6 when an existing capability already covers the need, exit 3 when the surface cannot be inspected, 5 when the image is missing, 2 when the test fails; 1 is kept from the legacy set and is real on the new path too (--capability missing / --test-input not JSON)

#### `tools.promote` — EXPAND

candidate → build request

| | |
|---|---|
| Group | `tools` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `candidate` | string (positional) | yes | no | — | `—` | candidate id (see `tools candidates`) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/capability_mining.py:379`

**Contract note (verbatim)**

> an unknown candidate is exit 5 (not argparse's 2)

#### `tools.promote-global` — EXPAND

promote a validated project tool to global

| | |
|---|---|
| Group | `tools` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | yes |
| Exit codes | `0` (OK), `1` (USAGE), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | — |
| `--evidence-project` | string | no | yes | — | `[]` | evidence project PATH or project id (repeatable; >=2 foreign contexts required) |
| `--db` | string | no | no | — | `—` | explicit tools DB (default: the runtime DB; see `skaldir registry status`) |
| `--runtime` | string | no | no | — | `—` | — |
| `--json` | flag | no | no | — | `false` | — |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/capability.py:690`

**Contract note (verbatim)**

> a refusal exits 1, not the designed 3/2 (tests/test_tool_scopes.py pins 1 x3); compat_top_level_keys: the pre-contract payload is read at top level by tests/test_tool_scopes.py

#### `tools.propose` — EXPAND

propose a tool spec

| | |
|---|---|
| Group | `tools` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--spec` | string | yes | no | — | `—` | — |
| `--db` | string | no | no | — | `—` | explicit tools DB (default: the runtime DB; see `skaldir registry status`) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/tools.py:492`

#### `tools.register` — EXPAND

register a TESTED container adapter live (version/health/rollback snapshot)

| | |
|---|---|
| Group | `tools` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `2` (VERIFICATION_FAILED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | — |
| `--version` | string | no | no | — | `—` | explicit version (default: 0.1.0; re-registering bumps the patch) |
| `--project-id` | string | no | no | — | `—` | project id recorded in the manifest |
| `--requested-by` | string | no | no | — | `cli` | — |
| `--runtime` | string | no | no | — | `—` | runtime dir |
| `--db` | string | no | no | — | `—` | explicit tools DB override |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/toolbuilder.py ContainerToolBuilder.register_container_tool`
- `code`: `src/skaldir/toolbuilder_cli.py _cmd_tools_register`

**Contract note (verbatim)**

> exit 2 = the adapter is not (still) proven: no registration

#### `tools.request` — EXPAND

record a missing capability

| | |
|---|---|
| Group | `tools` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--capability` | string | yes | no | — | `—` | — |
| `--reason` | string | no | no | — | `—` | — |
| `--requested-by` | string | no | no | — | `main-agent` | — |
| `--session` | string | no | no | — | `—` | — |
| `--kind` | choice | no | no | auto, mcp, cli, testing_tool | `auto` | — |
| `--name` | string | no | no | — | `—` | — |
| `--source` | string | no | no | — | `—` | — |
| `--tests` | string | no | no | — | `—` | — |
| `--module` | string | no | no | — | `—` | — |
| `--entrypoint` | string | no | no | — | `—` | — |
| `--sample` | string | no | no | — | `—` | — |
| `--research` | string | no | no | — | `—` | — |
| `--secret` | string | no | yes | — | `—` | required secret HANDLE |
| `--domain` | string | no | yes | — | `—` | — |
| `--dependency` | string | no | yes | — | `—` | — |
| `--validation-case` | string | no | yes | — | `—` | testing tools: TARGET:pass\|fail (broken target must FAIL, working must PASS) |
| `--project` | string | no | no | — | `—` | project path — records the stable project id (default: cwd) |
| `--json` | flag | no | no | — | `false` | — |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "request_id": {
      "type": "string"
    },
    "capability": {
      "type": "string"
    },
    "status": {
      "type": "string"
    },
    "kind": {
      "type": "string"
    },
    "project_id": {
      "type": "string"
    },
    "created_at": {
      "type": "string"
    }
  },
  "required": [
    "request_id",
    "capability",
    "status"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/capability.py:38`

**Contract note (verbatim)**

> pre-contract this command mixed human text and JSON on stdout

#### `tools.revoke` — EXPAND

revoke a tool

| | |
|---|---|
| Group | `tools` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | — |
| `--db` | string | no | no | — | `—` | explicit tools DB (default: the runtime DB; see `skaldir registry status`) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/tools.py`

#### `tools.rollback` — EXPAND

take a container capability back (registry/files/health/containers)

| | |
|---|---|
| Group | `tools` |
| Layer | EXPAND |
| Runtime effect | mutates state |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `3` (BLOCKED), `5` (NOT_FOUND), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | — |
| `--reason` | string | no | no | — | `—` | — |
| `--runtime` | string | no | no | — | `—` | runtime dir |
| `--db` | string | no | no | — | `—` | explicit tools DB override |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/toolbuilder.py ContainerToolBuilder.rollback_container_tool`
- `code`: `src/skaldir/toolbuilder_cli.py _cmd_tools_rollback`

**Contract note (verbatim)**

> nothing is deleted — the tool directory is quarantined; the container teardown is label-scoped; an unknown name exits 5

#### `tools.scan` — EXPAND

static security scan

| | |
|---|---|
| Group | `tools` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `path` | string (positional) | yes | no | — | `—` | — |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

No structured payload: `data` is `null` in the envelope — no structured payload yet — the command's --json envelope reports data: null and the human text moves to stderr

**Default evidence**

- `code`: `src/skaldir/toolscan.py`

**Contract note (verbatim)**

> findings exit 1, not the designed 2: tests/test_tools_store.py::test_scan_cli_blocks_evil pins 1

#### `tools.show` — EXPAND

one tool spec as JSON

| | |
|---|---|
| Group | `tools` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | not required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `name` | string (positional) | yes | no | — | `—` | — |
| `--db` | string | no | no | — | `—` | explicit tools DB (default: the runtime DB; see `skaldir registry status`) |
| `--runtime` | string | no | no | — | `—` | runtime dir (default: $HERMES_HOME/godsv) |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "additionalProperties": true
}
```

**Default evidence**

- `code`: `src/skaldir/tools.py`

### Group `vision`

#### `vision.capabilities` — EXPAND

measure whether the configured model accepts an image part

| | |
|---|---|
| Group | `vision` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `2` (VERIFICATION_FAILED), `3` (BLOCKED), `4` (NEEDS_CREDENTIALS), `7` (INTERNAL) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--no-probe` | flag | no | no | — | `false` | skip the 1x1 PNG probe (the verdict is then NOT measured: exit 2) |
| `--model` | string | no | no | — | `—` | the model to ask (default: $SKALDIR_LLM_VISION_MODEL, then tiers.vision, then the configured model) |
| `--tier` | string | no | no | — | `—` | the tier to probe as (default: the model/vision tier) |
| `--text` | flag | no | no | — | `false` | human-readable output instead of the JSON report |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "ok": {
      "type": "boolean"
    },
    "model": {
      "type": "string"
    },
    "endpoint": {
      "type": "string"
    },
    "accepts_images": {
      "type": [
        "boolean",
        "null"
      ]
    },
    "measured": {
      "type": "boolean"
    },
    "agreement": {
      "type": "string"
    },
    "catalog": {
      "type": "object"
    },
    "probe": {
      "type": "object"
    },
    "cost": {
      "type": "object"
    }
  },
  "required": [
    "ok",
    "model",
    "measured",
    "catalog",
    "probe"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/llm_provider.py` — vision_probe — one 1x1 PNG through vision_chat
- `code`: `src/skaldir/llm_provider.py:vision_catalog` — the model-name catalog, reported as an expectation only

**Contract note (verbatim)**

> exit 0 with accepts_images true/false is a real answer; --no-probe exits 2 because nothing was measured then

#### `vision.describe` — EXPAND

send one image (file path or http(s) URL) to the configured model

| | |
|---|---|
| Group | `vision` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `4` (NEEDS_CREDENTIALS), `5` (NOT_FOUND), `7` (INTERNAL), `8` (UNKNOWN) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `source` | string (positional) | yes | no | — | `—` | image file path or http(s) URL |
| `--prompt` | string | no | no | — | `—` | override the instruction sent with the image |
| `--no-remote` | flag | no | no | — | `false` | refuse http(s) URLs (only a local file is sent) |
| `--model` | string | no | no | — | `—` | the model to ask (default: $SKALDIR_LLM_VISION_MODEL, then tiers.vision, then the configured model) |
| `--max-image-mb` | integer | no | no | — | `0` | cap for one image (default 10 MiB, or $SKALDIR_VISION_MAX_IMAGE_MB) |
| `--workspace` | string | no | no | — | `—` | the root a symlink may not leave (default: $SKALDIR_VISION_WORKSPACE, else the current directory) |
| `--tier` | string | no | no | — | `—` | the tier to send as (default: the vision/model tier) |
| `--text` | flag | no | no | — | `false` | human-readable output instead of the JSON report |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "ok": {
      "type": "boolean"
    },
    "verb": {
      "type": "string"
    },
    "answer": {
      "type": "string"
    },
    "model": {
      "type": "string"
    },
    "endpoint": {
      "type": "string"
    },
    "sent_to": {
      "type": "string"
    },
    "disclosure": {
      "type": "string"
    },
    "latency_ms": {
      "type": "integer"
    },
    "finish_reason": {
      "type": "string"
    },
    "source": {
      "type": "object",
      "properties": {
        "kind": {
          "type": "string"
        },
        "path": {
          "type": "string"
        },
        "url": {
          "type": "string"
        },
        "mime": {
          "type": "string"
        },
        "bytes": {
          "type": "integer"
        },
        "sha256": {
          "type": "string"
        },
        "width": {
          "type": "integer"
        },
        "height": {
          "type": "integer"
        },
        "sent_to": {
          "type": "string"
        }
      },
      "required": [
        "kind"
      ]
    },
    "usage": {
      "type": "object"
    },
    "request": {
      "type": "object"
    },
    "cost": {
      "type": "object"
    },
    "cache_key": {
      "type": "string"
    }
  },
  "required": [
    "ok",
    "verb",
    "model"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/vision_service.py` — resolve → guard → one image part → report
- `code`: `src/skaldir/llm_provider.py` — guard_image/image_part_from_bytes/vision_chat (the wire format)

**Contract note (verbatim)**

> exit 3 when the model refuses the image part or a guard/symlink rule refuses the file; exit 4 for a remote endpoint with no key (no request is sent); exit 5 for a missing file. the payload names the file, the endpoint its bytes went to (sent_to) and the sha256; the cost block carries the estimate and the provider's own usage counters

#### `vision.ocr` — EXPAND

read the text in an image (the describe call with an OCR instruction)

| | |
|---|---|
| Group | `vision` |
| Layer | EXPAND |
| Runtime effect | read-only |
| Credentials | required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `4` (NEEDS_CREDENTIALS), `5` (NOT_FOUND), `7` (INTERNAL), `8` (UNKNOWN) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `source` | string (positional) | yes | no | — | `—` | image file path |
| `--prompt` | string | no | no | — | `—` | override the OCR instruction sent with the image |
| `--no-remote` | flag | no | no | — | `false` | refuse http(s) URLs (only a local file is sent) |
| `--model` | string | no | no | — | `—` | the model to ask (default: $SKALDIR_LLM_VISION_MODEL, then tiers.vision, then the configured model) |
| `--max-image-mb` | integer | no | no | — | `0` | cap for one image (default 10 MiB, or $SKALDIR_VISION_MAX_IMAGE_MB) |
| `--workspace` | string | no | no | — | `—` | the root a symlink may not leave (default: $SKALDIR_VISION_WORKSPACE, else the current directory) |
| `--tier` | string | no | no | — | `—` | the tier to send as (default: the vision/model tier) |
| `--text` | flag | no | no | — | `false` | human-readable output instead of the JSON report |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "ok": {
      "type": "boolean"
    },
    "verb": {
      "type": "string"
    },
    "answer": {
      "type": "string"
    },
    "model": {
      "type": "string"
    },
    "endpoint": {
      "type": "string"
    },
    "sent_to": {
      "type": "string"
    },
    "disclosure": {
      "type": "string"
    },
    "latency_ms": {
      "type": "integer"
    },
    "finish_reason": {
      "type": "string"
    },
    "source": {
      "type": "object",
      "properties": {
        "kind": {
          "type": "string"
        },
        "path": {
          "type": "string"
        },
        "url": {
          "type": "string"
        },
        "mime": {
          "type": "string"
        },
        "bytes": {
          "type": "integer"
        },
        "sha256": {
          "type": "string"
        },
        "width": {
          "type": "integer"
        },
        "height": {
          "type": "integer"
        },
        "sent_to": {
          "type": "string"
        }
      },
      "required": [
        "kind"
      ]
    },
    "usage": {
      "type": "object"
    },
    "request": {
      "type": "object"
    },
    "cost": {
      "type": "object"
    },
    "cache_key": {
      "type": "string"
    }
  },
  "required": [
    "ok",
    "verb",
    "model"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/vision_service.py` — DEFAULT_OCR_PROMPT — a prompt, not a second code path
- `code`: `src/skaldir/llm_provider.py` — the same vision_chat answer

**Contract note (verbatim)**

> no OCR engine of our own: the answer is the model's reading, quoted verbatim in `answer` (and repeated in `text`)

#### `vision.screenshot` — ACT

capture the screen (or one window) through Computer Use, then describe it

| | |
|---|---|
| Group | `vision` |
| Layer | ACT |
| Runtime effect | read-only |
| Credentials | required |
| Registered in the parser | True |
| Compatibility top-level mirror | no |
| Exit codes | `0` (OK), `1` (USAGE), `3` (BLOCKED), `4` (NEEDS_CREDENTIALS), `5` (NOT_FOUND), `7` (INTERNAL), `8` (UNKNOWN) |

**Arguments**

| Argument | Type | Required | Repeatable | Choices | Default | Help |
|---|---|---|---|---|---|---|
| `--out` | string | no | no | — | `—` | where the capture is written (default: <runtime>/vision/screen-<timestamp>.png) |
| `--window` | string | no | no | — | `—` | capture one window instead of the screen: a title or WM_CLASS substring from the Computer-Use inventory |
| `--capture-only` | flag | no | no | — | `false` | capture and stop — nothing is sent to any provider |
| `--list-windows` | flag | no | no | — | `false` | print the Computer-Use window inventory and exit |
| `--prompt` | string | no | no | — | `—` | override the instruction sent with the capture |
| `--model` | string | no | no | — | `—` | the model to ask (default: $SKALDIR_LLM_VISION_MODEL, then tiers.vision, then the configured model) |
| `--max-image-mb` | integer | no | no | — | `0` | cap for one image (default 10 MiB, or $SKALDIR_VISION_MAX_IMAGE_MB) |
| `--workspace` | string | no | no | — | `—` | the root a symlink may not leave (default: $SKALDIR_VISION_WORKSPACE, else the current directory) |
| `--tier` | string | no | no | — | `—` | the tier to send as (default: the vision/model tier) |
| `--text` | flag | no | no | — | `false` | human-readable output instead of the JSON report |
| `--json` | flag | no | no | — | `false` | machine-readable output: exactly one JSON envelope on stdout |
| `--format` | choice | no | no | text, json, jsonl | `—` | output format (text\|json\|jsonl); SKALDIR_FORMAT is the fallback |
| `--quiet` | flag | no | no | — | `false` | suppress the human lines on stderr |
| `--no-color` | flag | no | no | — | `false` | disable ANSI colour even on a TTY (NO_COLOR is honoured too) |

**Envelope `data` schema**

```json
{
  "type": "object",
  "properties": {
    "ok": {
      "type": "boolean"
    },
    "verb": {
      "type": "string"
    },
    "capture": {
      "type": "object"
    },
    "windows": {
      "type": "array"
    },
    "answer": {
      "type": "string"
    },
    "model": {
      "type": "string"
    },
    "cost": {
      "type": "object"
    }
  },
  "required": [
    "ok",
    "verb",
    "capture"
  ]
}
```

**Default evidence**

- `code`: `src/skaldir/workflows.py:screenshot` — the existing Computer-Use screenshot backend (import, X11/XWayland)
- `code`: `src/skaldir/vision_service.py:capture_screen` — the bridge: windows inventory + capture + missing_capability

**Contract note (verbatim)**

> with no Computer-Use screenshot bridge it exits 3 and names the missing capability in `missing_capability` — never a substitute image; --capture-only sends nothing to any provider

## 6. Declared limitations (quoted from the contract records)

These are the places where a command's own contract record says it does not yet do the whole job. Nothing here is inferred from the source code or from a test run — each row is the command's `description` or `notes` field, copied unchanged.

| Command | Field | Verbatim text |
|---|---|---|
| `act.run` | `notes` | exit 2 when an action or the end state is not verified (an action the runner REFUSED is never 'verified' — the run stops instead of publishing evidence for something that did not run); exit 3 when no real bridge can carry the run; --runner dry-run declares data.scope='in-process fixture' and is never chosen by 'auto'. `runner.bridge` is `host-session` (a display this run owns, reported in data.cleanup with its display/closed/still_present) or `live-desktop` (one named window of the user's session, only with the opt-in); with --screenshot, data.screenshot carries {path, sha256, size_bytes, width, height, scope, label} of a real capture, and a capture that could not be taken is reported as absent (a warning), never as an empty file |
| `approval.check` | `notes` | --dry-run: nothing is executed. Exit 0 when it would be allowed, 3 when it would be prompted or forbidden — with the deciding rule and what the user would need to allow. |
| `checkpoint.prune` | `notes` | a dry run unless --yes; every readable manifest is read first and a blob one of them names is never a candidate; a manifest that cannot be read blocks the sweep (exit 3) and a directory without a manifest blocks it too unless --force; at most caps.prune_max_blobs blobs per call, the rest reported as truncated |
| `completion.begin` | `description` | record a new goal (no execution loop is wired yet) |
| `completion.begin` | `notes` | exit 3 while no loop is wired: the goal is recorded, nothing continues after the command returns |
| `context.compact` | `description` | judge the tool history of a run per call (keep / shorten / remove) and report what would change; --dry-run applies nothing |
| `cu.demo` | `description` | deterministic channel-ordering demo (in-process fixture) |
| `cu.demo` | `notes` | exit 0 stays (tests/test_computer_use.py::test_cu_demo_cli pins it) but the envelope declares data.scope='in-process fixture' + a warning: no desktop, no window, no real action |
| `eval` | `description` | run the in-process eval episodes (not real-world verification) |
| `eval` | `notes` | exit 2 when any episode fails; in-process, never a PASS authority |
| `jev.decide` | `notes` | the request arrives on stdin or via --request/--transcript FILE; exit 3 = no advisory (provider disabled/stalled), exit 4 = a live-capable provider without an authorized credential (needs_credentials), exit 7 = malformed request, kind mismatch or a hard-rule violation. Every decision is appended to --state/jev_decisions.jsonl unless --dry-run; `--apply` is refused with exit 1 because rebuilding a transcript belongs to the compaction port (src/skaldir/jevcomp). Never a goal PASS, never a permission, never a security exception |
| `jev.selftest` | `notes` | exit 1 = a hard rule lost its tests or a kind is not reachable — the façade's own vocabulary, not the designed 2 (verification_failed): tests/test_jev_facade_cli.py pins 1. A kind that is not wired reports reachable=false with its blocked_path, never a fabricated answer. Wired, the report is nested under data.selftest; standalone (`python -m skaldir.jev`) it is the top level |
| `memory.consolidate` | `description` | merge duplicates and resolve contradictions (dry run unless --apply) |
| `memory.remember` | `notes` | dry run writes nothing and says written=false; provenance is always recorded (source/refs/session) |
| `run` | `notes` | exit 0 is reserved for a verification PASS; a bound reached with no accepted completion parks the goal BLOCKED_EXTERNAL and exits 3 (the unmet criterion is named); a completion accepted by the gate whose independent pass still does not reach PASS exits 2. --resume continues the stored goal from its durable checkpoint (files, cursor, prior rejections) — the process may die at any point and the goal survives. --compaction-asker fixture marks every compaction as a simulation in the report; 'provider' (the default) uses the real model. |
| `selftest` | `notes` | exit 2 when any layer is not reachable; a layer that is not wired reports ok=false with blocked_reason, never reachable |
| `session.purge` | `description` | archive / truncate / delete a session's bodies — dry run unless --apply |
| `session.purge` | `notes` | dry run by default (exit 0, nothing written); --apply performs the purge |
| `shell` | `notes` | the shell executes whatever is typed through the product CLI: a bare line becomes `skaldir <line>`, a `/…` line is a real command group alias. A command the build does not wire is refused with its reason (never a guessed verb). Ctrl-C cancels the running command (the record marks it cancelled; the session continues). Commands are appended to <runtime>/shell/history.jsonl; --resume restores that state. Two gates run first: `approval run` is judged by the approval ladder and its answer is recorded through `approval allow`/`deny` (allow-once is a one-shot session grant; a non-interactive session never prompts and keeps the CLI's exit 3), and `edit apply` is previewed with a real `edit dry-run` before it applies. `/init` writes a project AGENTS.md; `/diff` renders the last transaction |
| `stream.selfcheck` | `notes` | read-only, in-process, deterministic: no terminal and no network involved. A failing invariant exits 3 with the reason, never 0. |

20 declared-limitation sentence(s) across 271 commands.

**Commands that declare no structured payload yet (`data: null`), 45:**

`completion.override`, `cu.demo`, `daemon.serve`, `demo`, `desktop.build-app`, `desktop.install`, `desktop.start`, `desktop.status`, `desktop.uninstall`, `init`, `ledger-show`, `mcp.serve`, `memory-retrieval-check`, `nightshift`, `pact-show`, `prompts.list`, `serve`, `skills.demote`, `skills.export`, `skills.install`, `skills.list`, `skills.mine`, `skills.promote`, `skills.show`, `skills.validate`, `spend.approve`, `spend.complete`, `spend.deny`, `spend.list`, `spend.request`, `tools.approve`, `tools.builds`, `tools.candidates`, `tools.fulfill`, `tools.promote`, `tools.propose`, `tools.revoke`, `tools.scan`, `vault.add`, `vault.describe`, `vault.list`, `vault.revoke`, `warm.start`, `warm.status`, `warm.stop`

---

Generated by `tools/gv_cli_reference.py` from `/home/willy/projects/gods-voice/.venv/bin/godsvoice contract dump --json`. Re-run it after any CLI change; `--check` fails when this file is stale (a hand-edit is a stale file by definition).
