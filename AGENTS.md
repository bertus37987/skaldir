# Skaldir Agent — instructions for coding agents

## Mission and source of truth

Finish Skaldir in this workspace by migrating useful existing implementation and implementing the user's confirmed specification. Do not start an unrelated rewrite. Read `TRANSFER-UND-IMPLEMENTIERUNG.md`, then `docs/masterplan/00-START-HIER.md` and the linked masterplan and acceptance documents.

Priority of project material: current explicit user instructions → this file and transfer assignment → current masterplan → historical references. `docs/reference/` records history, not current acceptance. If a technical design must change, preserve the requested capability and document the reason.

## Workspace and migration

- Target: `/home/willy/Agentic_Projects/Skaldir_Agent`.
- Read-only migration source: `/home/willy/projects/gods-voice`.
- Preserve source changes, user files, browser sessions and running resources. Do not delete, move or reset the old project.
- Work only in the target after migration. Do not import the old `.git`, virtual environment, node_modules, credentials or live runtime databases.
- Inventory tracked AND untracked source files before deciding what to copy. Do not discard relevant work because it is uncommitted.
- No blanket copy or deletion. Record kept/excluded/deferred paths and justification in `docs/audit/MIGRATION-MANIFEST.md`.
- Preserve licenses, attribution, required assets, build metadata, relevant tests and dependencies.
- Reuse working registries, stores and executors; avoid duplicate architecture.

## Product invariants

- One main coding agent; workers only for independent research/exploration, with isolated GUI if required.
- Fast parallel independent tool calls; resource-conflicting mutations and host GUI actions coordinated.
- Real Fedora host desktop, X11 and Wayland targets; visible ownership and desktop queue.
- Global physical double-Escape pauses the affected run, stops further inputs and releases control, including outside TUI focus.
- Explicit user stop survives restarts. Terminal close detaches; accidental runtime interruption recovers with state reconciliation.
- Approval modes: Ask Everything, Approve for me, Approve Everything. New spending and irreversible harmful effects still require specific approval.
- Jev routes/triages; Voice intervenes only when needed; VERIFY checks actual outcomes; ENFORCE never treats UNKNOWN as success.
- Automatically reuse tested, cleaned GUI subtools; support general/container tool creation and mixed workflows.
- Selective Markdown memory, source/version references, context compaction preserving goals and unresolved actions.
- Do not mistake a registered command, a written file, a process or a passing unit test for a completed user goal.

## Confirmed terminal UI

`skaldir` without a subcommand opens the interactive full-screen terminal UI. Large chat left, tools upper-right, goal lower-right. Each tool shows a short result preview with expandable details. Subtle animated figure and occasional humor; mostly monochrome, errors red with text/symbols. Tab at an empty prompt with no completion menu cycles approval modes; active completion retains its Tab behavior. Desktop-control indicator is a small corner status bar. Keep noninteractive CLI/JSON/pipe behavior usable.

Product strings remain English unless the user requests otherwise; private planning/audit documents may be German. Do not build a separate general desktop application.

## Validation and evidence

Reproduce defects before claiming a fix. Run targeted regressions after each relevant change, integration checks after related changes, and required project checks before completion. Do not repeatedly queue identical full-suite runs. Preserve complete logs and actual exit status. A fixture/PTY/container result is not a host-live result.

Record actual commands, cwd, source revision, results and evidence. Use pass/fail/unknown/blocked honestly. Do not weaken tests or security controls to obtain green output. Advance independent work when an external prerequisite blocks one path.

## GitHub and installation

The user authorized a new PRIVATE GitHub repository for this project. Preferred new repository slug: `Skylia-Agent`, following the user's requested repository title; local folder and CLI remain `Skaldir_Agent` and `skaldir`. Do not mass-rename the Python package based on this repository title. Follow the collision/authentication rules in the transfer assignment.

For this transfer, initial repository creation and the initial reviewed, secret-checked commit/push are authorized. Do not force-push or modify existing unrelated repositories. Later commits follow the scoped task, with no automatic remote publication beyond the initial transfer unless authorized.

Do not overwrite a shared installed `skaldir` wrapper before proving the new entrypoint. Install from the target environment; preserve the old launcher as a recoverable backup if switching it. Never alter `~/.hermes/godsv/app-src` or kill existing owner sessions.

## Logging

Log significant work, decisions, commands, outcomes and blockers. Redact secrets and minimize personal data. Full unredacted transcripts, mail contents, cookies and credentials must never be committed. Raw logs stay under ignored `logs/`; commit concise sanitized evidence summaries under `docs/audit/`.

Every handoff states what changed, what was really tested, what is still uncertain, the next action and running resources. Never claim this specification is already implemented simply because it has been copied here.
