"""``skaldir checkpoint …`` — the unified workspace+conversation snapshot (MVP/55).

One call adds the group; the CLI owner needs two lines in ``build_parser()`` (the group
installs its own ``cli_contract.COMMANDS`` entries, so there is nothing to paste into
``cli_contract.py``). Nothing in this module edits ``cli.py``.

.. code-block:: python

    # src/skaldir/cli.py, inside build_parser(), right after
    #     sub = parser.add_subparsers(dest="command", required=False)
    from .checkpoints_cli import register_cli as register_checkpoint_cli

    register_checkpoint_cli(sub)

Verbs:

* ``checkpoint create``  — snapshot the declared file set (the files the run's edit
  transactions touched, plus any ``--file``) and the conversation named by ``--session``
  into one checkpoint; reports what it cost (``stats.create_ms`` / ``manifest_bytes`` /
  ``blob_bytes_written`` / ``blob_bytes_reused``). Exit ``3`` on a cap hit or an unsafe
  declared path: nothing partial is ever published.
* ``checkpoint list``    — the checkpoints (newest first) with their cost, plus the
  directories an interrupted create left behind (``interrupted``).
* ``checkpoint show``    — one checkpoint: the manifest and the **full restore preview**
  (per-file state, unified diff, conversation drift) without writing anything. Exit ``5``
  for an unknown id, ``3`` for a directory without a manifest.
* ``checkpoint restore`` — plan, confirm, act. Without ``--yes`` it prints the preview and
  writes nothing (exit ``1``, ``confirmation_required``); a tree that drifted since the
  checkpoint is refused with exit ``6`` unless ``--force``, which restores and *records*
  the drift. Every written file is re-hashed and reported as ``byte_identical``.
* ``checkpoint resume``  — the same restore, framed as the long-run protocol's **step 0**:
  files first, then the conversation, and the continuation
  (``next_role``/``next_seq``/``continuation_command``) comes from
  ``SessionStore.resume`` itself.
* ``checkpoint prune``   — the blob store's garbage collection: every readable manifest is
  read, and the blobs **no** checkpoint references are the only candidates. It is a **dry
  run by default** (``--yes`` removes), bounded by ``caps.prune_max_blobs``
  (``SKALDIR_CHECKPOINT_PRUNE_MAX_BLOBS``), and it refuses rather than guesses: a manifest
  that cannot be read blocks the sweep, and so does a directory without a manifest (a
  create writes its blobs *before* publishing the manifest, so those blobs may belong to a
  checkpoint that is about to exist) — ``--force`` overrides only the second and records it.

Every verb reports through ``cli_contract``'s envelope when that module is present
(``set_data`` + ``add_evidence``), so nothing here can report success it cannot back.
``python -m skaldir.checkpoints_cli …`` runs the same handlers with the same envelope,
which is how the kill/restore proofs in the tests drive this module.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from . import checkpoints as CP
from .cli_shared import (
    as_subparsers,
    dump,
    envelope_mode,
    install_contract_entries,
    product_contract,
    runtime_dir,
    share_common_flags,
    text_mode,
)

#: The product CLI's envelope module, or ``None`` in a build without one.
_CONTRACT = product_contract()

GROUP = "checkpoint"

GROUP_HELP = (
    "unified checkpoints: snapshot the conversation and the files a run touched, preview a "
    "restore, and go back to an earlier point exactly"
)


# --------------------------------------------------------------------------- #
# payload → the one envelope                                                    #
# --------------------------------------------------------------------------- #


def _hand_over(payload: dict[str, Any], *, refs: tuple[dict[str, Any], ...] = ()) -> None:
    """Publish the payload and its evidence to the product envelope, when there is one.

    A success without evidence is downgraded to exit 7 by the contract, so every verb
    passes at least the manifest (or the store) it read, plus the module that answered.
    """
    if _CONTRACT is None:
        return
    rows = refs or (
        {
            "ref": "src/skaldir/checkpoints.py",
            "kind": "code",
            "note": "the module that produced this answer",
        },
    )
    try:
        _CONTRACT.set_data(payload)
        for item in rows:
            _CONTRACT.add_evidence(
                str(item.get("ref", "")),
                str(item.get("kind", "code")),
                str(item.get("note", "")),
            )
    except Exception:  # noqa: BLE001 — an envelope problem must not break the verb
        return


def _emit(args: argparse.Namespace, payload: dict[str, Any], *,
          refs: tuple[dict[str, Any], ...] = ()) -> int:
    """Print the payload the way the caller asked for it, then publish it."""
    want_json = bool(getattr(args, "json", False)) or (
        str(getattr(args, "format", "") or "").strip().lower() == "json"
    )
    if text_mode(args) or not want_json:
        print(CP.render_text(payload))
    elif not envelope_mode():
        dump(payload)
    _hand_over(payload, refs=refs)
    return CP.exit_code_for(payload)


# --------------------------------------------------------------------------- #
# flags → inputs                                                                #
# --------------------------------------------------------------------------- #


def _state_of(args: argparse.Namespace) -> Path:
    """``--state`` when given, else ``<runtime>/checkpoint`` (the product's runtime)."""
    explicit = str(getattr(args, "state", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return runtime_dir(args) / CP.CHECKPOINT_DIRNAME


def _session_store_of(args: argparse.Namespace) -> str:
    """``--session-store`` when given, else ``<runtime>/sessions.db`` (may not exist)."""
    explicit = str(getattr(args, "session_store", "") or "").strip()
    if explicit:
        return str(Path(explicit).expanduser())
    return str(runtime_dir(args) / "sessions.db")


def _edit_state_of(args: argparse.Namespace) -> str:
    """``--edit-state`` when given, else ``<runtime>/edit`` (where edit_tx keeps records)."""
    explicit = str(getattr(args, "edit_state", "") or "").strip()
    if explicit:
        return str(Path(explicit).expanduser())
    return str(runtime_dir(args) / "edit")


def _manifest_ref(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """The evidence rows a checkpoint answer can honestly point at."""
    path = str(payload.get("manifest_path") or "")
    rows: list[dict[str, Any]] = []
    if path:
        rows.append({"ref": path, "kind": "file", "note": "the checkpoint's own manifest"})
    rows.append(
        {
            "ref": "src/skaldir/checkpoints.py",
            "kind": "code",
            "note": "sha256 per file, the identity-free conversation digest, and the caps",
        }
    )
    return tuple(rows)


# --------------------------------------------------------------------------- #
# the verbs                                                                     #
# --------------------------------------------------------------------------- #


def cmd_create(args: argparse.Namespace) -> int:
    """Snapshot the declared files and the named session into one checkpoint."""
    payload = CP.create_checkpoint(
        workspace=str(getattr(args, "workspace", "") or "") or str(Path.cwd()),
        state_dir=_state_of(args),
        session_id=str(getattr(args, "session", "") or ""),
        runtime=runtime_dir(args),
        session_store=_session_store_of(args),
        edit_state_dir=_edit_state_of(args),
        transactions=tuple(getattr(args, "tx", None) or ()),
        include_applied=not bool(getattr(args, "no_applied", False)),
        files=tuple(getattr(args, "file", None) or ()),
        label=str(getattr(args, "label", "") or ""),
    )
    return _emit(args, payload, refs=_manifest_ref(payload))


def cmd_list(args: argparse.Namespace) -> int:
    """The checkpoints and their cost, newest first (read-only)."""
    payload = CP.list_checkpoints(
        _state_of(args), limit=int(getattr(args, "limit", None) or 50)
    )
    refs = (
        {
            "ref": str(payload.get("state_dir") or ""),
            "kind": "file",
            "note": f"the checkpoint state directory ({payload.get('count', 0)} checkpoint(s))",
        },
    )
    return _emit(args, payload, refs=refs)


def cmd_show(args: argparse.Namespace) -> int:
    """One checkpoint plus the exact restore preview (read-only, nothing written)."""
    payload = CP.show_checkpoint(
        _state_of(args),
        str(getattr(args, "checkpoint_id", "") or ""),
        workspace=str(getattr(args, "workspace", "") or ""),
        store=_open_transient_store(args),
        diff=not bool(getattr(args, "no_diff", False)),
    )
    return _emit(args, payload, refs=_manifest_ref(payload))


def _open_transient_store(args: argparse.Namespace) -> Any:
    """The explicit ``--session-store`` when the caller named one, else ``None``.

    ``None`` is correct for a preview: the checkpoint's manifest records the store it was
    built from, and the module opens exactly that one.
    """
    explicit = str(getattr(args, "session_store", "") or "").strip()
    if not explicit:
        return None
    return CP.open_session_store(Path(explicit).expanduser())


def _restore_payload(args: argparse.Namespace, *, verb: str) -> dict[str, Any]:
    """Run the one restore path both ``restore`` and ``resume`` use."""
    payload = CP.restore_checkpoint(
        _state_of(args),
        str(getattr(args, "checkpoint_id", "") or ""),
        workspace=str(getattr(args, "workspace", "") or ""),
        force=bool(getattr(args, "force", False)),
        dry_run=bool(getattr(args, "dry_run", False)),
        confirm=bool(getattr(args, "yes", False)),
        runtime=runtime_dir(args),
        store=_open_transient_store(args),
    )
    payload["verb"] = verb
    resume = payload.get("resume")
    if verb == "resume" and isinstance(resume, dict):
        resume["requested_by"] = "checkpoint resume"
        resume["step"] = 0
    return payload


def cmd_restore(args: argparse.Namespace) -> int:
    """Preview without ``--yes``; restore exactly, or refuse on drift, with it."""
    payload = _restore_payload(args, verb="restore")
    return _emit(args, payload, refs=_manifest_ref(payload))


def cmd_resume(args: argparse.Namespace) -> int:
    """The long-run protocol's step 0: restore the point, hand back the continuation."""
    payload = _restore_payload(args, verb="resume")
    return _emit(args, payload, refs=_manifest_ref(payload))


def cmd_prune(args: argparse.Namespace) -> int:
    """Reclaim the blobs no checkpoint references — a dry run unless ``--yes``."""
    payload = CP.prune_checkpoints(
        _state_of(args),
        dry_run=not bool(getattr(args, "yes", False)),
        force=bool(getattr(args, "force", False)),
        limit=int(getattr(args, "limit", 0) or 0) or None,
    )
    payload["verb"] = "prune"
    refs = (
        {
            "ref": str(payload.get("state_dir") or ""),
            "kind": "file",
            "note": (
                f"{payload.get('checkpoints', {}).get('readable', 0)} readable manifest(s) "
                "read before anything was chosen for removal"
            ),
        },
        {
            "ref": "src/skaldir/checkpoints.py::prune_checkpoints",
            "kind": "code",
            "note": "referenced blobs are never candidates; a dry run removes nothing",
        },
    )
    return _emit(args, payload, refs=refs)


# --------------------------------------------------------------------------- #
# argument surface                                                              #
# --------------------------------------------------------------------------- #


def _leaf(sub: Any, name: str, help_text: str, handler: Any) -> Any:
    """One verb: the shared flags, the two stream flags, the handler and its contract path."""
    parser = sub.add_parser(name, help=help_text, description=help_text)
    parser.add_argument(
        "--state", default="", help="checkpoint state dir (default: <runtime>/checkpoint)"
    )
    parser.add_argument("--runtime", default="", help="the product runtime dir")
    parser.add_argument("--session-store", default="", help="sessions.db to use")
    parser.add_argument("--json", action="store_true", help="print the JSON payload")
    parser.add_argument("--text", action="store_true", help="print the human view")
    share_common_flags(parser)
    # The envelope names the exact command: `checkpoint.list`, not `skaldir`.
    parser.set_defaults(func=handler, command_path=f"{GROUP}.{name}")
    return parser


def _add_verbs(sub: Any) -> None:
    """Every leaf of the group, in one place (``build_parser`` and ``register_cli``)."""
    parser = _leaf(
        sub, "create", "snapshot the files a run touched and its conversation", cmd_create
    )
    parser.add_argument("--workspace", default="", help="the tree to snapshot (default: cwd)")
    parser.add_argument("--session", default="", help="the session id to fold in (or 'last')")
    parser.add_argument(
        "--edit-state", default="", help="the edit_tx state dir (default: <runtime>/edit)"
    )
    parser.add_argument(
        "--tx", action="append", default=[], help="name a transaction (repeatable; 'last' allowed)"
    )
    parser.add_argument(
        "--file", action="append", default=[], help="declare an extra relative path (repeatable)"
    )
    parser.add_argument("--label", default="", help="a label for this point")
    parser.add_argument(
        "--no-applied", action="store_true", help="do not fold in the transactions in the tree"
    )

    parser = _leaf(sub, "list", "the checkpoints with their cost (read-only)", cmd_list)
    parser.add_argument("--limit", type=int, default=50, help="how many rows to return")

    parser = _leaf(sub, "show", "one checkpoint and its exact restore preview", cmd_show)
    parser.add_argument("checkpoint_id", nargs="?", default="", help="a checkpoint id, or 'last'")
    parser.add_argument("--workspace", default="", help="preview against another tree")
    parser.add_argument("--no-diff", action="store_true", help="skip the per-file diffs")

    for name, handler, text in (
        ("restore", cmd_restore, "restore the declared files and the conversation, exactly"),
        ("resume", cmd_resume, "restore the point as the long-run protocol's step 0"),
    ):
        parser = _leaf(sub, name, text, handler)
        parser.add_argument(
            "checkpoint_id", nargs="?", default="", help="a checkpoint id, or 'last'"
        )
        parser.add_argument("--workspace", default="", help="restore into this tree")
        parser.add_argument(
            "--yes", action="store_true", help="confirm: without it nothing is overwritten"
        )
        parser.add_argument(
            "--force", action="store_true", help="restore over drift and record it"
        )
        if name == "restore":
            parser.add_argument("--dry-run", action="store_true", help="preview only")

    parser = _leaf(
        sub,
        "prune",
        "reclaim the blob store: remove what no checkpoint references (dry run by default)",
        cmd_prune,
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="really remove the orphan blobs (without it this is a dry run)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="also reclaim from a directory without a manifest (an interrupted create)",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="remove at most N orphan blobs (0 = the cap)"
    )


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``--help``)."""
    parser = argparse.ArgumentParser(prog="skaldir checkpoint", description=GROUP_HELP)
    sub = parser.add_subparsers(dest="command", required=True)
    _add_verbs(sub)
    return parser


def register_cli(subparsers: Any) -> Any:
    """Wire the group in once: ``register_cli(subparsers) -> parser`` (idempotent)."""
    sub = as_subparsers(subparsers)
    existing = getattr(sub, "choices", None) or {}
    if GROUP in existing:
        install_contract_entries(CONTRACT_ENTRIES, _CONTRACT)
        return existing[GROUP]
    parser = sub.add_parser(GROUP, help=GROUP_HELP, description=GROUP_HELP)
    inner = parser.add_subparsers(dest="command", required=True)
    _add_verbs(inner)
    install_contract_entries(CONTRACT_ENTRIES, _CONTRACT)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir checkpoint …`` — returns the exit code, never raises for a request."""
    tokens = [str(item) for item in (sys.argv[1:] if argv is None else argv)]
    if tokens[:1] == [GROUP]:
        tokens = tokens[1:]  # `python -m skaldir.checkpoints_cli checkpoint list` also works
    install_contract_entries(CONTRACT_ENTRIES, _CONTRACT)
    try:
        parsed = build_parser().parse_args(tokens)
    except SystemExit as exc:  # argparse already chose the code (--help exits 0)
        return CP.EXIT_OK if not exc.code else CP.EXIT_USAGE
    handler = getattr(parsed, "func", None)
    if handler is None:
        build_parser().print_help()
        return CP.EXIT_USAGE
    if _CONTRACT is not None and hasattr(_CONTRACT, "run_command"):
        return int(_CONTRACT.run_command(parsed, handler))
    return int(handler(parsed))


# --------------------------------------------------------------------------- #
# the CLI contract                                                              #
# --------------------------------------------------------------------------- #

_DATA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "checkpoint_id": {"type": "string", "description": "the checkpoint this payload answers"},
        "verdict": {
            "type": "string",
            "description": "created / list / preview / restored / refused_drift",
        },
        "workspace": {"type": "string", "description": "the tree the plan or restore targets"},
        "files": {"type": "array", "description": "the declared file set with its sha256s"},
        "conversation": {
            "type": "object",
            "description": "the conversation half: hashes, never bodies",
        },
        "preview": {"type": "object", "description": "what a restore would write, delete, skip"},
        "drift": {"type": "object", "description": "what changed since the checkpoint"},
        "written": {"type": "array", "description": "per-file results with byte_identical"},
        "resume": {"type": "object", "description": "the long-run continuation (step 0)"},
        "stats": {"type": "object", "description": "measured cost: ms and bytes"},
    },
    "additionalProperties": True,
}


def _entries() -> dict[str, Any]:
    """The five ``COMMANDS`` entries, built from the contract module itself."""
    make = getattr(_CONTRACT, "_C", None)
    evidence = getattr(_CONTRACT, "_ev", None)
    if make is None or evidence is None:  # pragma: no cover — a contract-less build
        return {}
    engine = evidence("src/skaldir/checkpoints.py")
    cli = evidence("src/skaldir/checkpoints_cli.py")
    drift_probe = evidence(
        "src/skaldir/checkpoints.py::plan_restore",
        "code",
        "the per-file sha256 check and the conversation prefix check behind every refusal",
    )
    prune_probe = evidence(
        "src/skaldir/checkpoints.py::prune_checkpoints",
        "code",
        "the reference set is built from every readable manifest before anything is removed",
    )
    note = (
        "every cap is declared in data.caps and overridable through SKALDIR_CHECKPOINT_MAX_FILES / "
        "_MAX_MESSAGES / _MAX_FILE_BYTES / _MAX_BYTES / _MAX_TRANSACTIONS / _MAX_DIFF_BYTES / "
        "_KEEP / _PRUNE_MAX_BLOBS (the legacy GODSV_* spelling is accepted)"
    )
    return {
        "checkpoint.create": make(
            "PERSIST",
            (engine, cli),
            "snapshot the files a run touched and its conversation into one checkpoint",
            exit_codes=(0, 1, 3, 5, 7),
            mutates=True,
            data_schema=_DATA_SCHEMA,
            notes="content-addressed blobs (an unchanged file costs nothing) and a manifest "
                  "published atomically, last; exit 3 on a cap hit or an unsafe declared path "
                  "— nothing partial is published; " + note,
        ),
        "checkpoint.list": make(
            "PERSIST",
            (engine, cli),
            "the checkpoints with their cost, plus the directories an interrupted create left",
            exit_codes=(0, 1, 7),
            data_schema=_DATA_SCHEMA,
            notes="read-only; data.interrupted names every directory without a manifest (the "
                  "shape a killed create leaves)",
        ),
        "checkpoint.show": make(
            "PERSIST",
            (engine, cli, drift_probe),
            "one checkpoint with the exact restore preview (per-file diff, conversation drift)",
            exit_codes=(0, 1, 3, 5, 7),
            data_schema=_DATA_SCHEMA,
            notes="writes nothing: it is the plan a restore would run; exit 5 for an unknown id",
        ),
        "checkpoint.restore": make(
            "PERSIST",
            (engine, cli, drift_probe),
            "restore the declared files and the conversation, byte-identically, or refuse",
            exit_codes=(0, 1, 2, 3, 5, 6, 7),
            mutates=True,
            data_schema=_DATA_SCHEMA,
            notes="without --yes nothing is written (exit 1, confirmation_required); drift since "
                  "the checkpoint exits 6 unless --force, which records the drift; a store whose "
                  "bodies no longer match the recorded hashes exits 2; every written file "
                  "carries byte_identical + its sha256s",
        ),
        "checkpoint.resume": make(
            "PERSIST",
            (engine, cli),
            "the long-run protocol's step 0: restore the point and hand back the continuation",
            exit_codes=(0, 1, 2, 3, 5, 6, 7),
            mutates=True,
            data_schema=_DATA_SCHEMA,
            notes="the same restore as checkpoint.restore; data.resume carries next_role, "
                  "next_seq and the continuation command taken from SessionStore.resume",
        ),
        "checkpoint.prune": make(
            "PERSIST",
            (engine, cli, prune_probe),
            "reclaim the blob store: remove the blobs no checkpoint references",
            exit_codes=(0, 1, 3, 7),
            mutates=True,
            data_schema=_DATA_SCHEMA,
            notes="a dry run unless --yes; every readable manifest is read first and a blob "
                  "one of them names is never a candidate; a manifest that cannot be read "
                  "blocks the sweep (exit 3) and a directory without a manifest blocks it "
                  "too unless --force; at most caps.prune_max_blobs blobs per call, the "
                  "rest reported as truncated",
        ),
    }


CONTRACT_ENTRIES: dict[str, Any] = _entries()

CONTRACTS_SNIPPET = '''\
# src/skaldir/cli.py — two lines instead of the five COMMANDS entries:
#     from .checkpoints_cli import register_cli as register_checkpoint_cli
#     register_checkpoint_cli(sub)
# (register_cli installs its own entries, so cli_contract.py needs no paste.)
"checkpoint.create": _C("PERSIST", (_ev("src/skaldir/checkpoints.py"),), "snapshot the files a "
                      "run touched and its conversation", exit_codes=(0, 1, 3, 5, 7),
                      mutates=True, data_schema={"type": "object", "additionalProperties": True},
                      notes="content-addressed blobs + an atomically published manifest"),
"checkpoint.list": _C("PERSIST", (_ev("src/skaldir/checkpoints.py"),), "the checkpoints with "
                     "their cost (read-only)", exit_codes=(0, 1, 7),
                     data_schema={"type": "object", "additionalProperties": True}),
"checkpoint.show": _C("PERSIST", (_ev("src/skaldir/checkpoints.py"),), "one checkpoint with the "
                     "exact restore preview", exit_codes=(0, 1, 3, 5, 7),
                     data_schema={"type": "object", "additionalProperties": True}),
"checkpoint.restore": _C("PERSIST", (_ev("src/skaldir/checkpoints.py"),), "restore the declared "
                        "files and the conversation, byte-identically, or refuse",
                        exit_codes=(0, 1, 2, 3, 5, 6, 7), mutates=True,
                        data_schema={"type": "object", "additionalProperties": True},
                        notes="without --yes nothing is written; drift exits 6 unless --force"),
"checkpoint.resume": _C("PERSIST", (_ev("src/skaldir/checkpoints.py"),), "the long-run protocol's "
                       "step 0: restore the point, hand back the continuation",
                       exit_codes=(0, 1, 2, 3, 5, 6, 7), mutates=True,
                       data_schema={"type": "object", "additionalProperties": True}),
"checkpoint.prune": _C("PERSIST", (_ev("src/skaldir/checkpoints.py"),), "reclaim the blob store: "
                      "remove the blobs no checkpoint references", exit_codes=(0, 1, 3, 7),
                      mutates=True, data_schema={"type": "object", "additionalProperties": True},
                      notes="a dry run unless --yes; a blob a readable manifest names is never "
                            "removed, an unreadable manifest blocks the sweep"),
'''


__all__ = [
    "CONTRACT_ENTRIES",
    "CONTRACTS_SNIPPET",
    "GROUP",
    "build_parser",
    "cmd_create",
    "cmd_list",
    "cmd_prune",
    "cmd_resume",
    "cmd_restore",
    "cmd_show",
    "main",
    "register_cli",
]


if __name__ == "__main__":  # pragma: no cover — a standalone run, not a product path
    raise SystemExit(main())
