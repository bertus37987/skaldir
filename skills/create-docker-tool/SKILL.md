---
name: create-docker-tool
description: Use when mining a container image into a registered Skaldir tool (EXPAND). The verified five-stage flow — image → mine → adapter → independent oracle → register → health → real call → rollback — plus the measured limits (three verb templates, and the images that are correctly refused).
version: 1.0.0
---

# create-docker-tool — mining a container image into a real, callable tool

EXPAND's promise is narrow and checkable: **a capability that lives in a container image becomes a
typed tool this runtime can really call** — mined, adapted, tested against an *independent* oracle,
registered with health and a rollback point. Nothing is registered on a claim.

Use this skill when you want a capability that an existing image already carries
(`sha256sum`, `md5sum`, `base64`, and today **only those three** — see §4) and you want it as a
first-class tool instead of an ad-hoc `docker run`.

**The whole flow is five stages, and every stage can fail loudly:**

```
search → resolve_image → inspect_surface → generate_adapter → test_adapter → stage → register → health → call → rollback
```

## 0. Environment (do this first, or the run lies to you)

```bash
unset HERMES_HOME GODSV_HOME GODSV_LLM_BASE_URL GODSV_LLM_MODEL GODSV_LLM_API_KEY \
      OPENAI_API_KEY OPENAI_BASE_URL DEEPSEEK_API_KEY PYTHONPATH SKALDIR_HOME
export GODSV_HOME=/home/willy/.hermes/godsv      # the REAL runtime, not a sandbox home
export PYTHONPATH=/home/willy/projects/gods-voice/src
export TMPDIR=/tmp                              # a TMPDIR inside $HOME breaks the sandbox allowlist
```

Guard — it must print this repo, not a foreign checkout:

```bash
python -c "import skaldir;print(skaldir.__file__)"
# /home/willy/projects/gods-voice/src/skaldir/__init__.py
```

Prerequisites, each one real: **Docker reachable without sudo** (`docker run --rm alpine:3.20 true`),
the image **local or `--pull`**, and a **free state dir** — a `skaldir run`/`tools` attempt that failed
can leave a writer lease, and re-running against the same `--state` then exits 6 with
`another process won the compare-and-swap for the writer lease`. Use a scratch `--state` when in doubt.

## 1. Mine `sha256` out of `alpine:3.20` (the reference run)

```bash
skaldir tools mine --image alpine:3.20 \
  --capability "compute the sha-256 digest of a text string" \
  --name gv_ex_sha256 --verb sha256sum \
  --test-input '{"text":"skaldir"}' \
  --expect ed4e0c9cd5c7879d564ba3684b280fa41b08f08f5dd4d0da45d93f931cf4acc6 \
  --json
```

* **Exit code `0`** is the only success. `staged: true`, and the six stages all `ok`.
* **Evidence to read back:** `adapter.template_id = "sha256-string"`,
  `adapter.image_digest = sha256:d9e853e8…a4b6bc`, `test.oracle = "local stdlib implementation
  (independent of the container)"`, `test.expected == test.observed`, and
  `tool_dir = …/generated_tools/gv_ex_sha256` with a `bin/gv_ex_sha256` wrapper.
* **`--expect` must be a real number you computed yourself**, not one you copied from the tool —
  otherwise the oracle tests itself. `printf %s skaldir | sha256sum` prints the same digest on the
  host; that is the point of calling the oracle *independent*.
* The stage list is the report of *what really ran*:
  `[["search",true],["resolve_image",true],["inspect_surface",true,{"verbs":311}],["generate_adapter",true],["test_adapter",true],["stage",true]]`.

A second surface proves it is not a one-off — different image, different verb, different template:

```bash
skaldir tools mine --image python:3.12-alpine \
  --capability "encode a text string as base64" --name gv_ex_b64 \
  --test-input '{"text":"skaldir"}' --expect c2thbGRpcg== --json
```
Observed: `template_id: "base64-string"`, `verb: "base64"`, `image_digest sha256:c4634f57…85f7b`;
the call later returned `{"base64": "TVZQ"}` and `printf %s MVP | base64` printed `TVZQ`.

## 2. Inspect what was generated — before you register it

```bash
skaldir tools adapter show gv_ex_sha256 --json      # the typed adapter, its permissions, its test evidence
skaldir tools register gv_ex_sha256 --json          # exit 0 · exit 2 = "not (still) proven: no registration"
```

`adapter show` is where you check the *attack surface you are about to install*:
`adapter.json` carries `argv` (the exact `sh -c` the container runs), `permissions`
(`network: "none"`, `read_only_rootfs: true`, `no_new_privileges: true`, `resource_limits`,
`mounts`, `secrets`), the image digest, and the test evidence with its `adapter_digest`.
Read `argv` yourself: an adapter is only as good as the argv the oracle actually ran.

`register` prints the human line worth keeping:

```
registered gv_ex_sha256 v0.1.1 (approved) — health HEALTHY, rollback 1789773019650
  call: /home/willy/.hermes/godsv/generated_tools/gv_ex_sha256/bin/gv_ex_sha256 '{"text": "…"}'
```

## 3. Health, then a **real call** — the only thing that counts

```bash
skaldir tools health gv_ex_sha256 --probe --json
/home/willy/.hermes/godsv/generated_tools/gv_ex_sha256/bin/gv_ex_sha256 '{"text":"halloween"}'
```

* `health --probe` → `state: "HEALTHY"`, `verified: container adapter tested against
  alpine:3.20@sha256:d9e853e8…`, `digest_match: true`, `selectable: true`, and a real
  `probe` block (mode, argv, exit_code, stdout, duration_ms). Exit `0`; **`3` means a *real* docker
  failure** — never a simulated success. `--probe` makes a real call; a health state without a probe
  is a ledger row, not a measurement.
* The wrapper call → exit `0`, typed JSON, and it must match the independent oracle:

```
$ bin/gv_ex_sha256 '{"text":"halloween"}'
{"sha256": "9eb7ee7f551d2f0ac684981bd1f1e2fa4a37590199636753efe614d4db30e8e1"}
$ printf %s "halloween" | sha256sum
9eb7ee7f551d2f0ac684981bd1f1e2fa4a37590199636753efe614d4db30e8e1  -
```

## 4. The measured limits — read this before choosing an image

**Only three verb templates exist today.** Not "a few": prove it in one line:

```bash
python3 -c "from skaldir.toolbuilder import VERB_TEMPLATES; print([(t['id'],t['verb']) for t in VERB_TEMPLATES])"
# [('sha256-string','sha256sum'), ('md5-string','md5sum'), ('base64-string','base64')]
```

The surface inspector is wider than the generator: for `nginx:1.27-alpine` it reported
`verbs.count: 330` and `known_generator_verbs: [base64, cksum, md5sum, sha1sum, sha256sum, sha3sum,
sha512sum, sum]` — **five of those eight have no template**, so they are refused, not invented.
`ffmpeg`, `imagemagick`, `pandoc` cannot be mined today **whatever the image**. The honest
prerequisite for those is a new template (or an `--entrypoint` override plus a template) — EXPAND
work, not an owner workaround.

All three refusals are exit **`3`**, and each one names its reason:

```bash
# (a) the surface has nothing for the capability — adapter_refused, staged: false
skaldir tools mine --image nginx:1.27-alpine \
  --capability "resize an image with ImageMagick" --name gv_ex_imagemagick --json
# the inspected surface offers no container verb for 'resize an image with ImageMagick' —
# refused instead of inventing an adapter (surface verbs: [, [[, acpid, …)

# (b) ENTRYPOINT swallows the probe argv — the probe never reaches a shell, docker_error
skaldir tools mine --image jrottenberg/ffmpeg:6.1-alpine \
  --capability "cut a segment out of a video file" --name gv_ex_video_cut --verb ffmpeg --json
# the surface cannot be inspected: the probe(s) api_mcp, cli, identity did not run in the image
# (ffmpeg version 6.1 Copyright (c) 2000-2023 …)
#   why: `docker run <image> sh -c …` is executed as `ffmpeg sh -c …`; the inspector has no
#        --entrypoint override

# (c) the image has no shell at all (distroless) — docker_error
skaldir tools mine --image pandoc/minimal:3.1.13 \
  --capability "convert a markdown document into a docx file" --name gv_ex_pandoc --json
# pandoc: sh: withBinaryFile: does not exist (No such file or directory)
```

A refusal is a **correct answer**, not a crash: nothing was staged, no adapter exists, nothing is
registered. Re-measured on this host (2026-09-19) — all three exit `3`.

### Exit codes of `tools mine`

| code | meaning |
| --- | --- |
| `0` | staged and proven (the six stages are all `ok`) |
| `2` | the adapter failed **its own** test — do not register |
| `3` | refused: the surface/adapter cannot carry this capability (`failure_class` says `adapter_refused` or `docker_error`) |
| `5` | the image is not there (add `--pull`) |
| `6` | an existing capability already covers the need (search-before-build refused the build) |

`tools register` maps "still proven?" to `0` (registered) or `2` (refused, nothing registered).
`tools health --probe` is `0`, or `3` when docker is unreachable (the ledger state is still reported).

## 5. Rollback — and what it does *not* do

```bash
skaldir tools rollback gv_ex_sha256 --reason "playbook example teardown" --json
skaldir tools revoke gv_ex_sha256 --json
```

Measured: `"rolled_back": true`, `"quarantine": "…/container_rollbacks/gv_ex_sha256/rollback"` (the
adapter, surface, manifest and wrapper are moved there — `generated_tools/gv_ex_sha256` is gone),
`"containers_removed": []`, `"teardown_ok": true`, `"teardown_detail": "removed 0 leftovers"`,
`"health": {"state": "RETIRED"}`.

**Two honest details.** The container sweep is scoped by label
(`godsv.capability=<name>`), so it never touches other workloads on a shared daemon. And the
registry row is not deleted by `rollback` — a later `tools list` still shows the row (as `[revoked]`,
version `-`) until you run `tools revoke <name>`. In the run recorded by the examples block,
`rollback` reported `"removed_registration": false` and the follow-up `revoke` was needed; in the
2026-09-19 re-measurement `rollback` itself reported `"removed_registration": true` and `revoke`
still exited `0` and left the row marked revoked. **Do both, then read `tools list`** — never assume
the cleanup happened.

## 6. Verification checklist (all of it observable)

- [ ] `tools mine … --json` → exit `0`, `staged: true`, six stages `ok`
- [ ] `adapter.template_id` is one of the three; `adapter.image_digest` is a digest, not a tag
- [ ] `test.oracle` is the *independent* implementation, and `test.expected == test.observed`
- [ ] `adapter show` → read `argv` and `permissions` before registering
- [ ] `tools register` → exit `0`, health `HEALTHY`, a rollback id printed
- [ ] `tools health … --probe` → `state: HEALTHY`, `digest_match: true`, `selectable: true`
- [ ] the wrapper call returns typed JSON that **matches your own oracle on a fresh input**
- [ ] `tools rollback` + `tools revoke` → quarantine path exists, health `RETIRED`, row `[revoked]`

## Pitfalls (each one measured)

- **A refusal is not a bug.** Exit `3` with `failure_class: adapter_refused` means the generator has no
  template for that verb; a `docker_error` means the image cannot be inspected. Both are the product
  working as declared. Do not "fix" either by hand-editing `adapter.json` — the oracle would no
  longer be independent.
- **`--expect` from the container is circular.** Compute the expected value on the host, ideally with
  a different implementation than the image's.
- **`staged` is not `registered`.** `mine` stages under `generated_tools/`; only `register` makes the
  tool live, and only a `HEALTHY` probe makes it *selectable*.
- **`TMPDIR` inside `$HOME` breaks the sandbox** the checks run in. Export `TMPDIR=/tmp`.
- **`HERMES_HOME`/`GODSV_HOME` leaking from a sandbox** points the run at an empty runtime and the
  "missing" image looks like an image problem. Unset them, then set `GODSV_HOME` deliberately.
- **A stale writer lease exits `6`.** `another process won the compare-and-swap for the writer lease`
  means another writer (or an earlier aborted run) holds it — use a fresh `--state`, don't retry blindly.
- **`sha1sum`/`cksum`/`sha512sum`/`sum` are recognised but not generatable.** The inspector lists them;
  the generator has no template. Expect exit `3` until a template exists.

## Where the truth lives

- `skaldir contract show tools.mine|tools.register|tools.health|tools.rollback --json` — the declared
  exit codes and data schema of each verb.
- `src/skaldir/capability_mining.py` — the five stages and the surface inspector.
- `src/skaldir/toolbuilder.py` — `VERB_TEMPLATES` (the generator's real ceiling).
- `src/skaldir/capability_health.py` — `HEALTHY` / `RETIRED` / `digest_match`.
- `~/Documents/Hermes_Skaldir_Agent_Docs/MVP/70-test-examples.md` §1 — the runnable playbook these
  commands were taken from (executed on this host, 2026-09-19, Skaldir 1.5.0).
- `~/Documents/Hermes_Skaldir_Agent_Docs/MVP/77-slash-extra.md` — the same flow re-measured while
  adding `/scan`, `/telemetry`, `/doctor`, `/locking` and `/computer-use` to the slash surface.
