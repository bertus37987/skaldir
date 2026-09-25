"""Security audit of the CLI surface — the shipped, re-runnable hardening check.

One command (`skaldir security audit`) and one module answer the question the
owner asks of a hardened CLI: *which of the classic defect classes are actually
present in this tree, where, and what is the fix?* The audit is deliberately
mechanical: every finding names a rule, a file and a line, and every rule is
declared in :data:`RULES` with the fix it expects. Nothing is reported as clean
that was not scanned — a scan that could not run exits *blocked*, never *ok*.

The classes audited (one per ``--class``):

===========================  =====================================================
``command_injection``        a subprocess whose argv is a shell, or an argv
                             element a shell will re-parse (``sh -c "<value>"``),
                             or ``shell=True``/``os.system``
``path_traversal``           a user-supplied name joined onto a directory with no
                             containment check, so ``../..`` escapes the root
``secrets``                  a secret-shaped literal, a whole-environment
                             inheritance into a child, or a secret interpolated
                             into a message/argv
``unsafe_deserialisation``   ``yaml.load`` without ``SafeLoader``, ``pickle``/
                             ``marshal``/``dill``, ``eval``/``exec`` on a
                             non-literal, a dynamic ``__import__``
``permissions``              world-writable modes, a write-then-chmod window on a
                             secret-bearing file, a privileged container, a
                             docker-socket mount
``toctou``                   check-then-use on a path that another process can
                             swap between the check and the use
``resource_limits``          a subprocess or HTTP call with no deadline: the one
                             defect that turns a slow peer into a hung CLI
``error_leakage``            an internal path/exception/secret that reaches the
                             user-facing message
===========================  =====================================================

Two halves, one shape of finding:

* **static** — :func:`audit_source` parses Python and walks the AST (implemented
  with the pattern list in :data:`_SOURCE_RULES`); no regex-only guessing where
  the AST answers the question exactly.
* **runtime** — :func:`audit_runtime` *looks at the live filesystem*: the mode of
  every secret-bearing file under the runtime dir and of the directories above
  it, leftover atomic-write temporaries, tool manifests that ask for privileges
  the policy forbids, and whether a secret is sitting in this process's argv.

The audit is read-only. It opens nothing for writing, spawns no subprocess and
never prints a secret value — a secret-shaped match is shown masked
(:func:`mask`). ``RULES`` is the source of truth for the rule catalogue; the
contract's ``security.audit`` entry and the tests both read it.
"""

from __future__ import annotations

import ast
import os
import re
import stat
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# vocabulary                                                                    #
# --------------------------------------------------------------------------- #

CLASS_COMMAND_INJECTION = "command_injection"
CLASS_PATH_TRAVERSAL = "path_traversal"
CLASS_SECRETS = "secrets"
CLASS_DESERIALISATION = "unsafe_deserialisation"
CLASS_PERMISSIONS = "permissions"
CLASS_TOCTOU = "toctou"
CLASS_RESOURCE_LIMITS = "resource_limits"
CLASS_ERROR_LEAKAGE = "error_leakage"

#: Every class this audit covers, in report order.
CLASSES: tuple[str, ...] = (
    CLASS_COMMAND_INJECTION,
    CLASS_PATH_TRAVERSAL,
    CLASS_SECRETS,
    CLASS_DESERIALISATION,
    CLASS_PERMISSIONS,
    CLASS_TOCTOU,
    CLASS_RESOURCE_LIMITS,
    CLASS_ERROR_LEAKAGE,
)

#: What each class means, once (used by the report and by ``--explain``).
CLASS_HELP: dict[str, str] = {
    CLASS_COMMAND_INJECTION: "input that reaches a shell's command line as text",
    CLASS_PATH_TRAVERSAL: "a supplied name that escapes its directory",
    CLASS_SECRETS: "a credential that can reach a file, a child env or a message",
    CLASS_DESERIALISATION: "a loader that can build objects, not just data",
    CLASS_PERMISSIONS: "a mode, mount or capability wider than the file needs",
    CLASS_TOCTOU: "a path checked and then used again after the check",
    CLASS_RESOURCE_LIMITS: "a call with no deadline or no bound",
    CLASS_ERROR_LEAKAGE: "internals, paths or keys in a user-facing message",
}

SEVERITIES: tuple[str, ...] = ("info", "low", "medium", "high", "critical")
_SEVERITY_RANK = {name: index for index, name in enumerate(SEVERITIES)}


def rank(severity: str) -> int:
    """Numeric rank of a severity (unknown names rank lowest, never crash)."""
    return _SEVERITY_RANK.get(str(severity).strip().lower(), 0)


# --------------------------------------------------------------------------- #
# the rule catalogue                                                            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Rule:
    """One detection rule: what it looks for and the fix it expects."""

    id: str
    klass: str
    severity: str
    title: str
    fix: str
    #: ``static`` (AST/regex over source) · ``runtime`` (live filesystem/os)
    kind: str = "static"

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "class": self.klass,
            "severity": self.severity,
            "title": self.title,
            "fix": self.fix,
            "kind": self.kind,
        }


RULES: tuple[Rule, ...] = (
    # -- command injection ---------------------------------------------------
    Rule(
        "SHELL-TRUE", CLASS_COMMAND_INJECTION, "critical",
        "subprocess with shell=True",
        "pass an argv list and keep the shell out of it; if a shell is truly "
        "required, quote every interpolated value with shlex.quote()",
    ),
    Rule(
        "OS-SYSTEM", CLASS_COMMAND_INJECTION, "critical",
        "os.system / os.popen (always a shell)",
        "use subprocess.run([...], check=..., timeout=...) with an argv list",
    ),
    Rule(
        "SHELL-C-INTERPOLATION", CLASS_COMMAND_INJECTION, "critical",
        "an interpolated value is spliced into `sh -c` (and the shell re-parses it)",
        "keep the value out of the script text: pass it as an argument of the "
        "command (`sh -c 'tool \"$1\"' sh <value>`) or wrap it in shlex.quote()",
    ),
    Rule(
        "SHELL-STRING-ARGV", CLASS_COMMAND_INJECTION, "high",
        "a subprocess gets a command *string* instead of an argv list",
        "build a list of arguments; a string is either a wrong program name or an "
        "injection waiting for a shell",
    ),
    # -- path traversal ------------------------------------------------------
    Rule(
        "PATH-JOIN-UNTRUSTED", CLASS_PATH_TRAVERSAL, "medium",
        "a caller-supplied name is joined onto a directory without containment check",
        "validate the name (^[A-Za-z0-9_.-]{1,64}$) and assert containment before "
        "use: `resolved = (base / name).resolve()` then "
        "`if base.resolve() not in resolved.parents: raise ValueError(...)`",
    ),
    Rule(
        "PATH-DOTDOT-LITERAL", CLASS_PATH_TRAVERSAL, "low",
        "a literal path with a `..` segment",
        "resolve the path and compare it against the intended root",
    ),
    # -- secrets -------------------------------------------------------------
    Rule(
        "SECRET-LITERAL", CLASS_SECRETS, "high",
        "a secret-shaped literal in the source",
        "read the value from the environment (name only in files) and rotate the "
        "exposed credential",
    ),
    Rule(
        "SECRET-IN-ARGV", CLASS_SECRETS, "high",
        "a secret name/reference is interpolated into a child's argv",
        "pass credentials through the child *environment* (env=...), never as an "
        "argument — argv is world-readable in /proc",
    ),
    Rule(
        "WHOLE-ENVIRON", CLASS_SECRETS, "medium",
        "the whole parent environment is inherited by a child process",
        "build the child env explicitly (PATH, HOME, the names it needs) instead of "
        "inheriting every credential in the parent",
    ),
    Rule(
        "SECRET-INTERPOLATION", CLASS_SECRETS, "medium",
        "a secret-named value is interpolated into an f-string",
        "interpolate `bool(value)`/`'set' if value else 'unset'`, never the value",
    ),
    Rule(
        "ENVIRON-COPY", CLASS_SECRETS, "medium",
        "the whole process environment is copied into a variable",
        "copy the names the child needs (`{k: os.environ[k] for k in WANTED if k in "
        "os.environ}`) so every unrelated credential does not travel with it",
    ),
    # -- unsafe deserialisation ---------------------------------------------
    Rule(
        "YAML-UNSAFE-LOAD", CLASS_DESERIALISATION, "high",
        "yaml.load without SafeLoader",
        "use yaml.safe_load()",
    ),
    Rule(
        "PICKLE", CLASS_DESERIALISATION, "critical",
        "pickle/marshal/dill/jsonpickle — arbitrary object construction on load",
        "use json (or a schema validator); a pickle is code, not data",
    ),
    Rule(
        "EVAL-EXEC", CLASS_DESERIALISATION, "high",
        "eval()/exec() on a non-literal",
        "parse with ast.literal_eval for data, or dispatch through an explicit table",
    ),
    Rule(
        "DYNAMIC-IMPORT", CLASS_DESERIALISATION, "medium",
        "a dynamic __import__ / importlib call on a non-literal",
        "import through a fixed allowlist of module names",
    ),
    # -- permissions ---------------------------------------------------------
    Rule(
        "WORLD-WRITABLE", CLASS_PERMISSIONS, "high",
        "a chmod that grants write access to group/other",
        "grant the narrowest mode that works (0600 for private data, 0644 for a "
        "plain config, 0755 for an executable) — never 0666/0777",
    ),
    Rule(
        "WRITE-THEN-CHMOD", CLASS_PERMISSIONS, "medium",
        "a private file is written first and restricted afterwards",
        "create the file with the mode atomically — "
        "`fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)` — so the "
        "value is never briefly world-readable and a crash cannot leave it that way",
    ),
    Rule(
        "PRIVILEGED-CONTAINER", CLASS_PERMISSIONS, "critical",
        "an argv that asks docker/podman for privileges or a host namespace",
        "no --privileged, no --cap-add, no --pid=host/--network=host: bind only the "
        "paths the tool needs and keep the runtime's own policy check in front",
    ),
    Rule(
        "DOCKER-SOCKET", CLASS_PERMISSIONS, "high",
        "the docker socket is bound into a container",
        "never mount /var/run/docker.sock into a generated tool; a socket mount is "
        "host root",
    ),
    Rule(
        "RUNTIME-PERMISSIONS", CLASS_PERMISSIONS, "medium",
        "a secret-bearing runtime file (or its directory) is reachable by other users",
        "chmod 0700 the state directory and 0600 the credential files it holds",
    ),
    Rule(
        "TOOL-PRIVILEGE-REQUEST", CLASS_PERMISSIONS, "high",
        "a registered tool manifest asks for a privilege the runtime policy forbids",
        "drop the request from the manifest; the policy refuses it, so the tool's "
        "declared permissions and its real permissions would otherwise disagree",
    ),
    # -- toctou --------------------------------------------------------------
    Rule(
        "TOCTOU-CHECK-USE", CLASS_TOCTOU, "medium",
        "a path is tested (exists/is_file/resolve) and re-used later by name",
        "do the operation and handle its failure (open and catch OSError) instead of "
        "test-then-use; on the containment path, compare the *resolved* object once",
    ),
    Rule(
        "TOCTOU-SYMLINK", CLASS_TOCTOU, "medium",
        "a bind/mount source is the raw path, not the object that was checked",
        "pass the resolved path into the spec (the value a validation function "
        "resolved), so the check and the bind refer to the same object",
    ),
    # -- resource limits -----------------------------------------------------
    Rule(
        "SUBPROCESS-NO-TIMEOUT", CLASS_RESOURCE_LIMITS, "medium",
        "a blocking subprocess call with no timeout",
        "pass timeout=<seconds> and handle subprocess.TimeoutExpired explicitly "
        "(report it as a timeout, never as a pass)",
    ),
    Rule(
        "POPEN-NO-WAIT-DEADLINE", CLASS_RESOURCE_LIMITS, "medium",
        "a spawned child is waited for without a deadline",
        "use .communicate(timeout=...) or .wait(timeout=...); no wait may be unbounded",
    ),
    Rule(
        "HTTP-NO-TIMEOUT", CLASS_RESOURCE_LIMITS, "medium",
        "an HTTP call with no timeout",
        "pass timeout=<seconds> to every urlopen/request call — a peer that accepts "
        "the connection and then says nothing must not hang the CLI",
    ),
    Rule(
        "DECLARED-TIMEOUT-UNUSED", CLASS_RESOURCE_LIMITS, "medium",
        "a function takes a timeout parameter and a call below ignores it",
        "pass the parameter through to the blocking call it documents; a timeout that "
        "is accepted and dropped reads like a bound and is none",
    ),
    # -- error leakage -------------------------------------------------------
    Rule(
        "TRACEBACK-TO-USER", CLASS_ERROR_LEAKAGE, "low",
        "a formatted traceback is produced in this module",
        "keep traces in the log and return a stable message with a next_action; a "
        "traceback exposes paths and the call graph",
    ),
    Rule(
        "EXC-DETAIL-TO-USER", CLASS_ERROR_LEAKAGE, "low",
        "a raw exception/interpolated path is returned as the user-facing message",
        "map the exception to a stable message and put the detail in the evidence, "
        "not in `error.message`",
    ),
    Rule(
        "RUNTIME-EXPOSURE", CLASS_ERROR_LEAKAGE, "info",
        "live exposure facts (runtime path, argv, environment names)",
        "no fix required — recorded so the report states what it observed",
        kind="runtime",
    ),
)

RULES_BY_ID: dict[str, Rule] = {rule.id: rule for rule in RULES}


# --------------------------------------------------------------------------- #
# findings                                                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Finding:
    """One located defect (or one located observation, for ``info`` rules)."""

    rule: str
    path: str
    line: int
    column: int = 0
    message: str = ""
    snippet: str = ""
    fix: str = ""
    evidence: str = ""
    #: overrides the rule's own severity (a rule whose finding is context-dependent)
    severity_override: str = ""

    @property
    def klass(self) -> str:
        rule = RULES_BY_ID.get(self.rule)
        return rule.klass if rule else "unknown"

    @property
    def severity(self) -> str:
        if self.severity_override:
            return self.severity_override
        rule = RULES_BY_ID.get(self.rule)
        return rule.severity if rule else "info"

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}" if self.line else self.path

    def as_dict(self) -> dict:
        return {
            "rule": self.rule,
            "class": self.klass,
            "severity": self.severity,
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "location": self.location,
            "message": self.message,
            "snippet": self.snippet,
            "fix": self.fix,
            "evidence": self.evidence,
        }


# --------------------------------------------------------------------------- #
# shared patterns                                                               #
# --------------------------------------------------------------------------- #

#: Secret shapes (kept in step with ``skaldir.secretscan``; a superset is deliberate).
SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9]{20,}"
    r"|sk-ant-[A-Za-z0-9_\-]{20,}"
    r"|ghp_[A-Za-z0-9]{30,}"
    r"|gho_[A-Za-z0-9]{30,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[A-Za-z0-9\-]{10,}"
    r"|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\."
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)

#: Names that hold a credential unambiguously (used for interpolation/argv findings).
SECRET_NAME_RE = re.compile(
    r"(?i)^(api_?key|apikey|secret|password|passwd|credential|credentials|"
    r"master_?key|access_?token|refresh_?token|auth_?token|client_?secret|"
    r"private_?key|vault_?key|key_?value|bearer)$"
)

#: Ambiguous names: credentials in one file, a template placeholder in another.
#: They are only reported next to a credential word in the same message.
WEAK_SECRET_NAMES = frozenset({"token", "key", "secret_value", "auth"})

#: The corroborating words that make an ambiguous name a credential.
CREDENTIAL_CONTEXT_RE = re.compile(
    r"(?i)(api|authorization|bearer|credential|secret|password|vault|login|"
    r"api[_ -]?token|store it)"
)

#: Environment variable *names* that hold a credential (the value is never read).
CREDENTIAL_ENV_RE = re.compile(
    r"(?i)(^|_)(api_?key|key|token|secret|secrets|password|passwd|credential|"
    r"credentials|vault_?key|access_?key|private_?key)$"
)

#: Calls that make a value safe to splice into a shell script.
_QUOTING_CALLS = frozenset({"quote", "shlex_quote", "escape", "shellquote"})

#: A name that is used as a path segment and usually comes from a caller.
UNTRUSTED_SEGMENT_RE = re.compile(
    r"(?i)^(name|slug|tool|tool_?name|artifact|file|filename|file_?name|id|kind|"
    r"target|dest|destination|source|rel|relative|sub|item|path_?name)$"
)

SHELL_PROGRAMS = {"sh", "bash", "zsh", "dash", "ash", "/bin/sh", "/bin/bash", "busybox"}

#: The HTTP *client modules* an un-timeouted ``.get()``/``.post()`` can belong to.
#: The rule keys off the import: a local variable that happens to be called
#: ``requests`` (measured: ``requests = {step: event}`` in telemetry.py, and a list
#: of recorded requests in context_manager.py) is a ``dict.get``/``list.__getitem__``,
#: not an HTTP call, and reporting it teaches the reader to ignore the rule.
HTTP_CLIENT_MODULES = frozenset({"requests", "httpx"})
SUBPROCESS_FUNCS = {
    "subprocess.run",
    "subprocess.check_output",
    "subprocess.check_call",
    "subprocess.call",
    "subprocess.getoutput",
    "subprocess.getstatusoutput",
}

#: function names that only exist on the ``subprocess`` module (used to resolve
#: ``from subprocess import run`` / ``import subprocess as sp``).
_SUBPROCESS_TAILS = frozenset(
    {
        "run",
        "Popen",
        "check_output",
        "check_call",
        "call",
        "getoutput",
        "getstatusoutput",
    }
)

#: Where an f-string goes when interpolating a credential actually leaks it.
_MESSAGE_SINKS = frozenset(
    {"print", "warning", "warn", "error", "info", "debug", "critical",
     "exception", "log", "write", "append", "format"}
)


def mask(text: str, keep: int = 4) -> str:
    """Show the shape of a secret-shaped match without leaking it."""
    value = str(text)
    if len(value) <= keep * 2:
        return "***"
    return f"{value[:keep]}…{value[-2:]} ({len(value)} chars)"


def _redact_snippet(text: str) -> str:
    """A code line is evidence, not a leak: mask any secret-shaped run inside it.

    Without this the ``snippet`` field of a ``SECRET-LITERAL`` finding would quote
    the very credential the finding is about — the audit would leak what it reports.
    """
    if not text or not SECRET_RE.search(text):
        return text
    try:
        from .secretscan import redact

        cleaned, _findings = redact(text)
        return cleaned
    except Exception:  # noqa: BLE001 — standalone use without the sibling module
        return SECRET_RE.sub(lambda match: f"[REDACTED:{mask(match.group(0))}]", text)


def _snippet(lines: Sequence[str], line: int, limit: int = 200) -> str:
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()[:limit]
    return ""


def _dotted(node: ast.AST | None) -> str:
    """``subprocess.run`` / ``self.path`` as a dotted name (``""`` when unknown)."""
    if node is None:
        return ""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    if isinstance(node, ast.Subscript):
        return _dotted(node.value)
    return ""


def _static_dotted(node: ast.AST | None) -> str:
    """A dotted name built **only** from ``Name``/``Attribute`` nodes.

    Unlike :func:`_dotted`, a ``Call``/``Subscript`` breaks the chain instead of
    being skipped: ``requests[-1].get`` and ``urllib.parse.parse_qs(x).get`` are
    *not* ``requests.get``/``urllib.get`` — they are a ``list.__getitem__`` and a
    ``dict.get`` on a local value. A rule that matched the collapsed spelling
    reported "HTTP without timeout" for a dict lookup (measured: four false
    positives in this tree), so the call target must be the real one.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _static_dotted(node.value)
        return f"{base}.{node.attr}" if base else ""
    return ""


def _shell_program_and_script(elements: Sequence[ast.AST]) -> tuple[str, ast.AST | None]:
    """``(shell_name, script_element)`` for an ``sh -c …`` argv, else ``("", None)``.

    Only the element that **follows ``-c``** is script text: the shell parses that
    one and treats every other element as an ordinary argument. A runtime value
    sitting next to ``-c`` (``["bwrap", "--chdir", str(cwd), "/bin/sh", "-c", command]``)
    is not re-parsed and cannot inject — so only the script element is audited.
    """
    literals = [value for value in (_const_str(element) for element in elements) if value]
    shells = [value for value in literals if value.split("/")[-1] in SHELL_PROGRAMS]
    if not shells:
        return "", None
    for index, element in enumerate(elements):
        if _const_str(element) == "-c":
            following = elements[index + 1] if index + 1 < len(elements) else None
            return shells[0], following
    return shells[0], None


def _const_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_interpolated(node: ast.AST | None) -> bool:
    """True when an expression builds a string from runtime values."""
    if node is None:
        return False
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
        left, right = node.left, node.right
        if isinstance(left, ast.Constant) and isinstance(left.value, str):
            return not (
                isinstance(right, ast.Constant) and isinstance(right.value, str)
            )
        return _is_interpolated(left) or _is_interpolated(right)
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr == "format":
            return True
        return _dotted(node.func) in ("str", "join") and bool(node.args)
    return False


def _filled_names(node: ast.AST | None) -> set[str]:
    """The *leaf* identifier that supplies a value to an f-string/format call.

    ``f"{secret.kind}"`` yields ``{"kind"}`` (the value printed), not ``"secret"``
    (the object it came from) — otherwise every message that mentions an object
    called ``secret``/``token`` would be reported as a leak. A subscript key is
    used as the name when it is a literal (``cfg["api_key"]`` → ``"api_key"``).
    """
    names: set[str] = set()

    def walk(expr: ast.AST | None) -> None:
        if expr is None:
            return
        if isinstance(expr, ast.Name):
            names.add(expr.id)
        elif isinstance(expr, ast.Attribute):
            names.add(expr.attr)
        elif isinstance(expr, ast.Subscript):
            key = expr.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                names.add(key.value)
            walk(expr.value)
        elif isinstance(expr, ast.Call):
            for arg in list(expr.args) + [kw.value for kw in expr.keywords]:
                walk(arg)
        elif isinstance(expr, ast.JoinedStr):
            for part in expr.values:
                if isinstance(part, ast.FormattedValue):
                    walk(part.value)
        elif isinstance(expr, (ast.BinOp, ast.Compare, ast.IfExp, ast.Tuple, ast.List)):
            for child in ast.iter_child_nodes(expr):
                walk(child)

    walk(node)
    return names


def _literal_strings(node: ast.AST | None) -> list[str]:
    out: list[str] = []
    for sub in ast.walk(node) if node is not None else ():
        value = _const_str(sub)
        if value is not None:
            out.append(value)
    return out


def _argv_like(node: ast.AST | None) -> list[ast.AST]:
    """The elements of a list/tuple literal (an argv), else ``[]``."""
    if isinstance(node, (ast.List, ast.Tuple)):
        return list(node.elts)
    return []


def _keyed_name(node: ast.AST | None) -> str:
    """A stable name for a path expression, keeping literal subscript keys.

    ``mounts["cid_file"]["path"]`` → ``mounts[cid_file][path]`` — so two uses of
    *different* keys of the same dict are not confused with each other.
    """
    if node is None:
        return ""
    if isinstance(node, ast.Subscript):
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return f"{_keyed_name(node.value)}[{key.value}]"
        return f"{_keyed_name(node.value)}[?]"
    if isinstance(node, ast.Attribute):
        return f"{_keyed_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ""


def _is_quoted(
    node: ast.AST | None, quoted_names: frozenset[str] | set[str] = frozenset()
) -> bool:
    """Is every runtime value in this expression shell-quoted?

    ``f"...{shlex.quote(value)}..."`` and ``pattern = shlex.quote(value)`` +
    ``f"...{pattern}..."`` both count: a value that went through a quoting call is
    data to the shell, which is exactly what the rule asks for.
    """
    parts = [
        part.value
        for part in getattr(node, "values", [])
        if isinstance(part, ast.FormattedValue)
    ]
    if isinstance(node, ast.FormattedValue):
        parts = [node.value]
    if not parts:
        return False
    for expr in parts:
        if isinstance(expr, ast.Call) and _dotted(expr.func).split(".")[-1] in _QUOTING_CALLS:
            continue
        if isinstance(expr, ast.Name) and expr.id in quoted_names:
            continue
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            if _is_quoted(expr.left, quoted_names) or _is_quoted(expr.right, quoted_names):
                continue
        return False
    return True


def _opens_for_write(node: ast.Call) -> bool:
    """``open(path, "w")`` / ``"a"`` / ``"x"`` — a write, not a read."""
    if node.args:
        value = _const_str(node.args[1]) if len(node.args) > 1 else None
        if value is not None:
            return any(ch in value for ch in "wax")
    for kw in node.keywords:
        if kw.arg == "mode":
            value = _const_str(kw.value)
            return bool(value) and any(ch in str(value) for ch in "wax")
    return False


def _is_path_expression(node: ast.AST | None) -> bool:
    """Does this expression look like a directory (so ``expr / name`` is a path)?"""
    if node is None:
        return False
    if isinstance(node, ast.Call):
        return _dotted(node.func).split(".")[-1] in ("Path", "PurePath", "PosixPath")
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return True
    name = _dotted(node).split(".")[-1].lower()
    return name.endswith(("dir", "dirname", "root", "base", "home", "parent", "folder", "path"))


# --------------------------------------------------------------------------- #
# static analysis                                                               #
# --------------------------------------------------------------------------- #


class _Analyzer:
    """AST walker for one source file."""

    def __init__(self, rel_path: str, text: str) -> None:
        self.rel_path = rel_path
        self.text = text
        self.lines = text.splitlines()
        self.findings: list[Finding] = []
        #: local name → module, for ``import subprocess as sp`` / ``from subprocess import run``
        self.subprocess_aliases: dict[str, str] = {}
        #: local name → HTTP client module, for ``import requests``/``import httpx``
        #: (and their ``as`` spellings); a name that was never imported is not a client
        self.http_aliases: dict[str, str] = {}
        #: names bound to a shell-quoting call (``pattern = shlex.quote(value)``)
        self.quoted_names: set[str] = set()

    # -- helpers ---------------------------------------------------------
    def add(
        self,
        rule: str,
        line: int,
        column: int = 0,
        message: str = "",
        evidence: str = "",
    ) -> None:
        self.findings.append(
            Finding(
                rule=rule,
                path=self.rel_path,
                line=int(line),
                column=int(column),
                message=message,
                snippet=_redact_snippet(_snippet(self.lines, line)),
                fix=RULES_BY_ID[rule].fix,
                evidence=evidence,
            )
        )

    def enclosing(self, node: ast.AST, parents: dict[int, ast.AST]) -> ast.AST | None:
        current = parents.get(id(node))
        while current is not None:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return current
            current = parents.get(id(current))
        return None

    @staticmethod
    def _func_params(fn: ast.AST | None) -> set[str]:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return set()
        args = fn.args
        names = {a.arg for a in list(args.args) + list(args.posonlyargs) + list(args.kwonlyargs)}
        if args.vararg:
            names.add(args.vararg.arg)
        if args.kwarg:
            names.add(args.kwarg.arg)
        return names

    @staticmethod
    def _containment_guarded(fn: ast.AST | None) -> bool:
        """Does the enclosing function check containment / validate the name?

        A guard is a *path* check: ``is_relative_to``/``relative_to``, a name
        validator (``fullmatch``/``match``/``_safe_name``/``_require_name``/
        ``_slug``), a ``startswith`` against a base, or an ``in``/``not in`` test
        whose right side is one of the function's own path parameters or a
        ``.parents``/``.parts`` collection. A bare ``x in y`` on a dict is not a
        containment check — treating it as one silences the rule for whole files.
        """
        if fn is None:
            return False
        params = _Analyzer._func_params(fn)
        path_ish = bool(
            {name for name in params if name.lower().endswith(("dir", "root", "base", "path"))}
        )
        for node in ast.walk(fn):
            if isinstance(node, ast.Attribute) and node.attr in (
                "is_relative_to",
                "relative_to",
            ):
                return True
            if isinstance(node, ast.Call):
                name = _dotted(node.func)
                tail = name.split(".")[-1]
                if tail in ("fullmatch", "is_relative_to", "quote", "realpath"):
                    return True
                if re.fullmatch(
                    r"(?i)_?(safe|require|validate|check|ensure)_?(name|path|slug|tool|segment|file)?",
                    tail,
                ):
                    return True
                if tail == "startswith" and path_ish:
                    return True
            if isinstance(node, ast.Compare):
                for op, comparator in zip(node.ops, node.comparators, strict=False):
                    if not isinstance(op, (ast.In, ast.NotIn)):
                        continue
                    text = _dotted(comparator)
                    leaf = text.split(".")[-1]
                    if leaf in params or leaf in ("parents", "parts", "children"):
                        return True
                    if text.endswith((".parents", ".parts")):
                        return True
        return False

    # -- rules -----------------------------------------------------------
    def _subprocess_alias(self, name: str) -> str | None:
        """Resolve a call target to a ``subprocess`` function, else ``None``.

        Only real subprocess calls are audited: ``cli_contract``/``cli`` call
        ``executor.run(...)`` on their own executor, and a rule that fires there
        teaches the reader to ignore the rule. ``import subprocess as sp`` and
        ``from subprocess import run`` are resolved through :attr:`subprocess_aliases`.
        """
        if name.startswith("subprocess."):
            return name
        head, _, tail = name.rpartition(".")
        module = self.subprocess_aliases.get(head or tail)
        if module is not None and tail in _SUBPROCESS_TAILS:
            return f"subprocess.{tail}"
        if "." not in name and self.subprocess_aliases.get(name) == "subprocess":
            return f"subprocess.{name}"
        return None

    def check_subprocess(self, node: ast.Call, parents: dict[int, ast.AST]) -> None:
        name = _dotted(node.func)
        resolved = self._subprocess_alias(name)
        if resolved is None:
            return
        tail = resolved.split(".")[-1]
        fn = self.enclosing(node, parents)
        declared_timeout = self._declares_timeout(fn)

        keywords = {kw.arg for kw in node.keywords}
        command = node.args[0] if node.args else None
        if command is None:
            for kw in node.keywords:
                if kw.arg in ("args", "cmd"):
                    command = kw.value
        shell_true = any(
            kw.arg == "shell" and getattr(kw.value, "value", False) is True
            for kw in node.keywords
        )
        if shell_true:
            self.add("SHELL-TRUE", node.lineno, node.col_offset,
                     "shell=True hands the whole command line to /bin/sh",
                     "shell=True at this call")
        if command is not None and not isinstance(command, (ast.List, ast.Tuple)):
            if _is_interpolated(command) or _const_str(command) is not None:
                self.add("SHELL-STRING-ARGV", node.lineno, node.col_offset,
                         f"{name}() is given a string instead of an argv list",
                         f"argument 1 is a {type(command).__name__}, not a list/tuple")

        elements = _argv_like(command)
        if elements:
            literals = [s for s in _literal_strings(command) if s]
            shell, script = _shell_program_and_script(elements)
            if shell and script is not None and _is_interpolated(script):
                self.add(
                    "SHELL-C-INTERPOLATION",
                    int(getattr(script, "lineno", node.lineno)),
                    int(getattr(script, "col_offset", node.col_offset)),
                    "a runtime value is spliced into the shell script text of "
                    f"`{shell} -c` — the shell re-parses it",
                    "the element after -c is an interpolated string",
                )
            if any("docker.sock" in literal for literal in literals):
                self.add("DOCKER-SOCKET", node.lineno, node.col_offset,
                         "the docker socket is bound into the container",
                         "docker.sock appears in this argv")
            privileged_flags = ("--privileged", "--cap-add", "--pid", "--ipc")
            hit = [
                literal
                for literal in literals
                if literal in privileged_flags or literal.startswith("--cap-add")
            ]
            if hit:
                self.add("PRIVILEGED-CONTAINER", node.lineno, node.col_offset,
                         "the container argv requests privileges",
                         f"flags: {sorted(set(hit))}")

        # secret in argv
        for element in elements or []:
            for name_in_expr in _filled_names(element):
                if SECRET_NAME_RE.match(name_in_expr):
                    self.add("SECRET-IN-ARGV",
                             int(getattr(element, "lineno", node.lineno)),
                             int(getattr(element, "col_offset", node.col_offset)),
                             f"`{name_in_expr}` is interpolated into a child's argv",
                             f"argv element mentions {name_in_expr}")
            for literal in _literal_strings(element):
                found = SECRET_RE.search(literal)
                if found:
                    self.add("SECRET-LITERAL",
                             int(getattr(element, "lineno", node.lineno)),
                             int(getattr(element, "col_offset", node.col_offset)),
                             "a secret-shaped value is passed to a child process",
                             f"matched {mask(found.group(0))}")

        # whole-environment inheritance
        for kw in node.keywords:
            if kw.arg != "env":
                continue
            dotted_env = _dotted(kw.value)
            if dotted_env.endswith("environ") or dotted_env.split(".")[-1] == "copy":
                self.add("WHOLE-ENVIRON", node.lineno, node.col_offset,
                         "the child inherits the whole parent environment",
                         f"env={dotted_env or 'call'}")

        # timeouts — a ``**kwargs`` splat may carry the timeout, so it is not a finding
        has_splat = any(kw.arg is None for kw in node.keywords)
        if tail != "Popen" and "timeout" not in keywords and not has_splat:
            rule = "DECLARED-TIMEOUT-UNUSED" if declared_timeout else "SUBPROCESS-NO-TIMEOUT"
            self.add(
                rule,
                node.lineno,
                node.col_offset,
                f"{name or tail}() blocks with no timeout"
                + (
                    " although this function declares the `timeout` parameter"
                    if declared_timeout
                    else ""
                ),
                "no timeout= keyword at this call",
            )
        if tail == "Popen":
            waited = self._waited_without_deadline(node, fn)
            if waited is not None:
                self.add("POPEN-NO-WAIT-DEADLINE", waited[0], waited[1],
                         "the spawned child is waited for with no deadline",
                         f"wait at line {waited[0]}")

    @staticmethod
    def _declares_timeout(fn: ast.AST | None) -> bool:
        """Does the enclosing function declare a ``timeout``(-ish) parameter?"""
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return False
        args = fn.args
        names = [a.arg for a in list(args.args) + list(args.posonlyargs) + list(args.kwonlyargs)]
        return any(re.fullmatch(r"(?i)timeout(_s)?", name) for name in names)

    @staticmethod
    def _waited_without_deadline(
        call: ast.Call, fn: ast.AST | None
    ) -> tuple[int, int] | None:
        if fn is None:
            return None
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr in ("communicate", "wait"):
                if not any(kw.arg == "timeout" for kw in node.keywords):
                    return node.lineno, node.col_offset
        return None

    def check_http(self, node: ast.Call, parents: dict[int, ast.AST]) -> None:
        dotted = _static_dotted(node.func)
        name = dotted.split(".")[-1]
        root = dotted.split(".")[0]
        interesting = (
            dotted.endswith("urlopen")
            or dotted.endswith("urlretrieve")
            or self.http_aliases.get(root) in HTTP_CLIENT_MODULES
            or dotted.startswith("urllib.")
        )
        if not interesting or name not in (
            "urlopen", "urlretrieve", "request", "get", "post", "put", "delete", "patch", "head",
        ):
            return
        if any(kw.arg == "timeout" for kw in node.keywords):
            return
        if any(kw.arg is None for kw in node.keywords):
            return
        rule = (
            "DECLARED-TIMEOUT-UNUSED"
            if self._declares_timeout(self.enclosing(node, parents))
            else "HTTP-NO-TIMEOUT"
        )
        self.add(rule, node.lineno, node.col_offset,
                 f"{dotted}() is called without a timeout",
                 "no timeout= keyword at this call")

    def check_deserialisation(self, node: ast.Call) -> None:
        dotted = _dotted(node.func)
        tail = dotted.split(".")[-1]
        if dotted in ("eval", "exec", "builtins.eval", "builtins.exec"):
            literal = _const_str(node.args[0]) if node.args else None
            if literal is None:
                self.add("EVAL-EXEC", node.lineno, node.col_offset,
                         f"{tail}() is given a runtime value",
                         "argument 1 is not a literal")
            return
        if dotted in ("__import__", "importlib.import_module", "importlib.__import__"):
            literal = _const_str(node.args[0]) if node.args else None
            if literal is None:
                self.add("DYNAMIC-IMPORT", node.lineno, node.col_offset,
                         f"{dotted}() resolves a runtime module name",
                         "argument 1 is not a literal")
            return
        root = dotted.split(".")[0]
        if root in ("pickle", "marshal", "dill", "jsonpickle") and tail in ("load", "loads"):
            self.add("PICKLE", node.lineno, node.col_offset,
                     f"{dotted}() constructs arbitrary objects from data",
                     f"{root} deserialisation")
            return
        if dotted == "yaml.load":
            loader = next((kw for kw in node.keywords if kw.arg == "Loader"), None)
            safe = loader is not None and "safe" in _dotted(loader.value).lower()
            if not safe:
                self.add("YAML-UNSAFE-LOAD", node.lineno, node.col_offset,
                         "yaml.load() builds arbitrary Python objects by default",
                         "either no Loader or an unsafe one")

    def check_environ_copy(self, node: ast.Call) -> None:
        """``dict(os.environ)`` / ``os.environ.copy()`` — the whole env travels on."""
        dotted = _dotted(node.func)
        tail = dotted.split(".")[-1]
        if tail == "copy" and dotted.startswith(("os.environ", "environ")):
            self.add("ENVIRON-COPY", node.lineno, node.col_offset,
                     "the whole process environment is copied into a variable",
                     f"{dotted}()")
            return
        if tail in ("dict", "") and node.args:
            inner = _dotted(node.args[0])
            if inner.endswith("environ"):
                self.add("ENVIRON-COPY", node.lineno, node.col_offset,
                         "the whole process environment is copied into a variable",
                         f"dict({inner})")
        if dotted == "os.environ.items" or tail == "items" and dotted.startswith("os.environ"):
            return

    def check_permissions(self, node: ast.Call) -> None:
        dotted = _dotted(node.func)
        tail = dotted.split(".")[-1]
        if tail in ("chmod", "fchmod", "chown"):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, int):
                    mode = arg.value
                    if mode > 0 and (mode & 0o022):
                        self.add("WORLD-WRITABLE", node.lineno, node.col_offset,
                                 f"chmod grants group/other write ({oct(mode)})",
                                 f"mode {oct(mode)}")
            for arg in node.args:
                for sub in ast.walk(arg):
                    if isinstance(sub, ast.Attribute) and sub.attr == "S_IWOTH":
                        self.add("WORLD-WRITABLE", node.lineno, node.col_offset,
                                 "chmod grants world write (S_IWOTH)",
                                 "stat.S_IWOTH in the mode expression")

    @staticmethod
    def _is_chmod_call(node: ast.Call) -> bool:
        """``os.chmod``/``path.chmod`` and the local ``_restrict``-style wrappers."""
        tail = _dotted(node.func).split(".")[-1]
        return bool(re.fullmatch(r"(?i)_?(chmod|fchmod|chown|restrict|tighten|set_?mode)", tail))

    @staticmethod
    def _is_private_mode(node: ast.Call) -> bool:
        """A literal mode that no group/other user can write to."""
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, int) and arg.value > 0:
                return (arg.value & 0o077) == 0
        return False

    def check_write_then_chmod(self, tree: ast.AST) -> None:
        """A private file written first and restricted afterwards (the mode *window*).

        ``write_text()`` then ``chmod(0o600)`` leaves the value at the process umask
        between the two calls, and a crash there leaves it that way for good. The
        check pairs a write with a later restrictive chmod on the *same* name inside
        the *same* function, so unrelated names never pair up.
        """
        parents: dict[int, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[id(child)] = parent

        def owner(node: ast.AST) -> int:
            fn = node
            while fn is not None:
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return id(fn)
                fn = parents.get(id(fn))
            return 0

        writes: list[tuple[int, str, int]] = []
        chmods: list[tuple[int, str, int]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr in (
                "write_text", "write_bytes", "writelines",
            ):
                name = _keyed_name(node.func.value)
                if name:
                    writes.append((node.lineno, name, owner(node)))
                continue
            if self._is_chmod_call(node) and node.args:
                name = _keyed_name(node.args[0])
                if name:
                    chmods.append((node.lineno, name, owner(node)))
        for chmod_line, chmod_name, chmod_owner in chmods:
            for write_line, write_name, write_owner in writes:
                if write_line >= chmod_line or write_name != chmod_name:
                    continue
                if write_owner != chmod_owner:
                    continue
                self.add("WRITE-THEN-CHMOD", write_line, 0,
                         f"`{write_name}` is written at line {write_line} and restricted "
                         f"to a private mode only at line {chmod_line}",
                         f"write at {write_line}, chmod at {chmod_line}")
                break

    def check_toctou(self, node: ast.Call, parents: dict[int, ast.AST]) -> None:
        """A path is tested for existence and then written by name (check-then-use).

        ``if not path.exists(): path.write_text(...)`` is the classic window: the
        file can appear between the test and the write, so the "create only when
        absent" promise is not kept, and a concurrent writer's content is lost.
        The safe shapes are ``os.open(..., O_CREAT | O_EXCL)`` and
        ``tempfile`` + ``os.replace``.
        """
        dotted = _dotted(node.func)
        tail = dotted.split(".")[-1]
        if tail not in ("exists", "is_file", "is_dir", "lexists"):
            return
        receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
        base = _dotted(receiver)
        if not base:
            return
        leaf = _keyed_name(receiver)
        fn = self.enclosing(node, parents)
        if fn is None:
            return
        for other in ast.walk(fn):
            if not isinstance(other, ast.Call) or other.lineno <= node.lineno:
                continue
            if not isinstance(other.func, ast.Attribute):
                continue
            verb = other.func.attr
            if verb not in ("write_text", "write_bytes", "touch", "mkdir", "open"):
                continue
            if verb == "open" and not _opens_for_write(other):
                continue
            if _keyed_name(other.func.value) != leaf:
                continue
            self.add("TOCTOU-CHECK-USE", other.lineno, other.col_offset,
                     f"`{base}` is tested at line {node.lineno} and used at line "
                     f"{other.lineno} — a file created in between is silently "
                     "overwritten and the test proves nothing",
                     f"test at {node.lineno} ({tail}), use at {other.lineno} ({verb})")
            return

    def check_join(self, node: ast.BinOp, parents: dict[int, ast.AST]) -> None:
        """``base / name`` where ``name`` is a caller-supplied path segment.

        Only joins whose *left* side is already a path expression are reported
        (``Path(...)/``, ``<something>dir /``, a nested join) — that keeps the
        rule about directories and out of unrelated arithmetic on a name.
        """
        if not isinstance(node.op, ast.Div):
            return
        right = node.right
        if not isinstance(right, ast.Name) or not UNTRUSTED_SEGMENT_RE.match(right.id):
            return
        if not _is_path_expression(node.left):
            return
        fn = self.enclosing(node, parents)
        segment = right.id
        if segment not in self._func_params(fn):
            return
        if self._containment_guarded(fn):
            return
        self.add("PATH-JOIN-UNTRUSTED", node.lineno, node.col_offset,
                 f"`{segment}` is a caller-supplied name joined onto a directory "
                 "without a containment check",
                 f"parameter {segment!r} used as a path segment under "
                 f"{_dotted(node.left) or 'a path'}")

    def check_secret_literal(self, node: ast.Constant) -> None:
        if not isinstance(node.value, str) or len(node.value) < 16:
            return
        if node.value.startswith(("-----BEGIN", "sk-", "ghp_", "gho_", "AKIA", "xox", "eyJ")):
            found = SECRET_RE.search(node.value)
            if found and found.start() == 0 and found.end() == len(node.value):
                self.add("SECRET-LITERAL", node.lineno, node.col_offset,
                         "a secret-shaped literal is embedded in the source",
                         f"matched {mask(found.group(0))}")

    def check_secret_interpolation(
        self, node: ast.JoinedStr, parents: dict[int, ast.AST]
    ) -> None:
        """A credential value interpolated into a message *that goes somewhere*.

        Only message sinks count (``print``/logger/``append``/``format``/an
        exception being raised): interpolating a key into an HTTP header or a
        comparison is the intended use, and flagging it would bury the real leak.
        """
        if not self._is_sink(node, parents) and not self._is_raised_message(node, parents):
            return
        literal_text = "".join(
            part.value if isinstance(part.value, str) else str(part.value)
            for part in node.values
            if isinstance(part, ast.Constant)
        )
        # a guard may sit inside a nested expression (`{"set" if key else "unset"}`)
        for sub in ast.walk(node):
            value = _const_str(sub)
            if value is not None:
                literal_text += value
        lowered = literal_text.lower()
        if any(word in lowered for word in ("set", "unset", "redact", "present", "bool")):
            return
        corroborated = bool(CREDENTIAL_CONTEXT_RE.search(literal_text))
        for name in _filled_names(node):
            strong = bool(SECRET_NAME_RE.match(name))
            weak = name.lower() in WEAK_SECRET_NAMES
            if not strong and not (weak and corroborated):
                continue
            self.add("SECRET-INTERPOLATION", node.lineno, node.col_offset,
                     f"`{name}` is interpolated into a message",
                     f"f-string fills {name} at a message sink")

    def _is_sink(self, node: ast.AST, parents: dict[int, ast.AST]) -> bool:
        parent = parents.get(id(node))
        if not isinstance(parent, ast.Call):
            return False
        tail = _dotted(parent.func).split(".")[-1]
        return tail in _MESSAGE_SINKS

    def _is_raised_message(self, node: ast.AST, parents: dict[int, ast.AST]) -> bool:
        parent = parents.get(id(node))
        while isinstance(parent, ast.Call):
            if isinstance(parent.func, ast.Name) and parent.func.id.endswith(
                ("Error", "Exception")
            ):
                return True
            parent = parents.get(id(parent))
        return False

    def check_traceback(self, node: ast.Call) -> None:
        dotted = _dotted(node.func)
        if dotted in ("traceback.format_exc", "traceback.format_exception", "traceback.print_exc"):
            self.add("TRACEBACK-TO-USER", node.lineno, node.col_offset,
                     f"{dotted}() renders a full traceback",
                     "keep traces out of the user-facing message")

    def check_os_shell(self, node: ast.Call) -> None:
        dotted = _dotted(node.func)
        if dotted in ("os.system", "os.popen", "os.execvp", "os.execl"):
            self.add("OS-SYSTEM", node.lineno, node.col_offset,
                     f"{dotted}() always goes through a shell",
                     "replace with subprocess.run([...], timeout=...)")

    def _collect_quoted_names(self, tree: ast.AST) -> None:
        """Names that received a shell-quoting call: ``pattern = shlex.quote(v)``."""
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            value = node.value
            if (
                isinstance(value, ast.Call)
                and _dotted(value.func).split(".")[-1] in _QUOTING_CALLS
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.quoted_names.add(target.id)

    def check_shell_argv_literal(self, node: ast.List) -> None:
        """A ``["sh", "-c", <interpolated>]`` literal anywhere in the file.

        The argv may be built in one function and executed by another (the ACT
        layer's sub-tools do exactly that), so the list literal itself is the
        evidence — wherever it is handed to a process, the shell re-parses it.

        Only the element **after** ``-c`` is script text (see
        :func:`_shell_program_and_script`): a runtime value elsewhere in the argv
        is an ordinary argument of the shell program and is never re-parsed.
        """
        shell, script = _shell_program_and_script(node.elts)
        if not shell or script is None:
            return
        if not _is_interpolated(script):
            return
        if _is_quoted(script, self.quoted_names):
            # every runtime value in the script is shell-quoted (shlex.quote)
            return
        index = next((i for i, element in enumerate(node.elts) if element is script), None)
        self.add("SHELL-C-INTERPOLATION",
                 int(getattr(script, "lineno", node.lineno)),
                 int(getattr(script, "col_offset", node.col_offset)),
                 "a runtime value is spliced into the shell script text of "
                 f"`{shell} -c` — the shell re-parses it",
                 f"argv element {index} (the one after -c) is an interpolated string")

    def check_bind_source(self, node: ast.JoinedStr) -> None:
        """A container bind/mount spec built from the *raw* path.

        ``f"type=bind,src={mount.get('host_path','')},dst=..."`` binds whatever the
        raw value points to at run time. A validation pass that resolved the path
        (and approved the resolved object) therefore does not apply to this value:
        the checked object and the used object are different.
        """
        literal_text = "".join(
            part.value if isinstance(part.value, str) else str(part.value)
            for part in node.values
            if isinstance(part, ast.Constant)
        )
        if not re.search(r"(?i)\b(src|source|type=bind)\s*=", literal_text):
            return
        for part in node.values:
            if not isinstance(part, ast.FormattedValue):
                continue
            expr = part.value
            raw = (
                isinstance(expr, ast.Subscript)
                or isinstance(expr, ast.Attribute)
                or (isinstance(expr, ast.Call) and _dotted(expr.func).split(".")[-1] == "get")
            )
            resolved = isinstance(expr, ast.Call) and _dotted(expr.func).split(".")[-1] in (
                "resolve",
                "realpath",
                "abspath",
            )
            if raw and not resolved:
                self.add("TOCTOU-SYMLINK", node.lineno, node.col_offset,
                         "the bind source is the raw manifest path, not a resolved "
                         "object — any containment check ran on a different value",
                         f"bind spec fills {_dotted(expr) or type(expr).__name__}")
                return

    def check_dotdot_literal(self, node: ast.Constant) -> None:
        """A literal path with a ``..`` segment (regexes and ``".."`` tests are not paths)."""
        value = node.value
        if not isinstance(value, str) or ".." not in value:
            return
        if value.strip() in ("..", "/..", "../"):
            return
        if any(ch in value for ch in "()[]|*?+\\{}^$"):
            return
        if not re.search(r"(^|/|\\|,)\s*\.\.\s*(/|\\|,|$)", value):
            return
        if len(value) > 200 or value.count("..") > 3:
            return
        self.add("PATH-DOTDOT-LITERAL", node.lineno, node.col_offset,
                 f"literal path contains `..`: {value.strip()[:80]!r}",
                 "resolve and compare against the intended root")

    # -- entry -----------------------------------------------------------
    def _collect_aliases(self, tree: ast.AST) -> None:
        """Module imports a call target is resolved through (subprocess, HTTP clients)."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "subprocess":
                        self.subprocess_aliases[alias.asname or "subprocess"] = "subprocess"
                    elif alias.name in HTTP_CLIENT_MODULES:
                        self.http_aliases[alias.asname or alias.name] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                for alias in node.names:
                    if alias.name in _SUBPROCESS_TAILS:
                        self.subprocess_aliases[alias.asname or alias.name] = "subprocess"

    def run(self, tree: ast.AST) -> list[Finding]:
        self._collect_aliases(tree)
        self._collect_quoted_names(tree)
        parents: dict[int, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[id(child)] = parent
        self.check_write_then_chmod(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                self.check_subprocess(node, parents)
                self.check_http(node, parents)
                self.check_deserialisation(node)
                self.check_permissions(node)
                self.check_environ_copy(node)
                self.check_toctou(node, parents)
                self.check_traceback(node)
                self.check_os_shell(node)
            elif isinstance(node, ast.BinOp):
                self.check_join(node, parents)
            elif isinstance(node, ast.List):
                self.check_shell_argv_literal(node)
            elif isinstance(node, ast.JoinedStr):
                self.check_secret_interpolation(node, parents)
                self.check_bind_source(node)
            elif isinstance(node, ast.Constant):
                self.check_secret_literal(node)
                self.check_dotdot_literal(node)
        return sorted(self.findings, key=lambda f: (f.line, f.column, f.rule))


def audit_source(text: str, rel_path: str) -> list[Finding]:
    """Every static finding in one source text (the unit the tests drive)."""
    analyzer = _Analyzer(rel_path, text)
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [
            Finding(
                rule="RUNTIME-EXPOSURE",
                path=rel_path,
                line=int(getattr(exc, "lineno", 0) or 0),
                message=f"file could not be parsed ({exc.msg}) — skipped, not certified clean",
                fix="make the file parse; an unparsed file is an unscanned file",
                evidence=f"SyntaxError: {exc.msg}",
            )
        ]
    return analyzer.run(tree)


# --------------------------------------------------------------------------- #
# walking a tree                                                                #
# --------------------------------------------------------------------------- #

DEFAULT_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "app-src",
        "app-overlay",
        "dist",
        "build",
        "site-packages",
    }
)

MAX_FILE_BYTES = 1_500_000


def iter_sources(
    root: Path,
    *,
    skip_dirs: Iterable[str] = DEFAULT_SKIP_DIRS,
    include_tests: bool = False,
    max_bytes: int = MAX_FILE_BYTES,
) -> Iterator[Path]:
    """Every ``.py`` file under ``root`` worth scanning (deterministic order)."""
    root = Path(root)
    if root.is_file():
        yield root
        return
    skip = set(skip_dirs)
    for path in sorted(root.rglob("*.py")):
        parts = set(path.relative_to(root).parts[:-1])
        if parts & skip:
            continue
        if not include_tests and "tests" in parts:
            continue
        try:
            if path.stat().st_size > max_bytes:
                continue
        except OSError:
            continue
        yield path


def audit_paths(
    roots: Sequence[str | Path],
    *,
    base: Path,
    include_tests: bool = False,
    max_bytes: int = MAX_FILE_BYTES,
) -> tuple[list[Finding], int]:
    """Static-scan ``roots``; returns the findings and the number of files read."""
    findings: list[Finding] = []
    scanned = 0
    for root in roots:
        target = Path(root)
        if not target.exists():
            continue
        for path in iter_sources(target, include_tests=include_tests, max_bytes=max_bytes):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            scanned += 1
            try:
                rel = str(path.relative_to(base))
            except ValueError:
                rel = str(path)
            findings.extend(audit_source(text, rel))
    return findings, scanned


# --------------------------------------------------------------------------- #
# runtime checks (the live filesystem)                                          #
# --------------------------------------------------------------------------- #

#: File names in the runtime dir that hold credentials or private history.
SENSITIVE_NAME_RE = re.compile(
    r"(?i)(vault|secret|credential|token|key|\.db$|\.jsonl$|events|memory|inbox|\.env$)"
)


def _mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def _blocking_ancestor(path: Path) -> str:
    """The first directory above ``path`` that no other user may traverse (``""``).

    A wide mode only matters where another user can actually get to the file: on
    a typical Fedora desktop ``~/.hermes`` is ``0700``, so every ``0644`` file
    below it is unreachable. The report says which ancestor blocks, instead of
    claiming an exposure that does not exist.
    """
    current = path if path.is_dir() else path.parent
    while current != current.parent:
        mode = _mode(current)
        if mode is not None and not mode & 0o001:
            return f"{current} is {oct(mode)} (no other-traverse)"
        current = current.parent
    return ""


def audit_runtime(
    runtime: str | Path,
    *,
    argv: Sequence[str] | None = None,
    environ: dict[str, str] | None = None,
) -> tuple[list[Finding], dict]:
    """Live checks under ``runtime`` (read-only) plus the observed facts.

    Returns ``(findings, environment)``. ``environment`` states what was seen —
    the runtime path, the sandbox availability, which credential *names* are set
    (never values) — so the report can be honest about the machine it ran on.
    """
    root = Path(runtime)
    findings: list[Finding] = []
    env = dict(os.environ if environ is None else environ)
    facts: dict[str, Any] = {
        "runtime": str(root),
        "runtime_exists": root.is_dir(),
        "runtime_mode": None if _mode(root) is None else oct(_mode(root) or 0),
        "sandbox": {},
        "credential_names_set": [],
        "argv_secret_shapes": 0,
    }
    if not root.is_dir():
        return findings, facts

    # 1. permissions of the runtime dir and every sensitive file in it
    rooted = _blocking_ancestor(root)
    root_mode = _mode(root)
    if root_mode is not None and root_mode & 0o077:
        findings.append(
            Finding(
                rule="RUNTIME-PERMISSIONS",
                path=str(root),
                line=0,
                message=(
                    f"the runtime directory is {oct(root_mode)}, so another local user "
                    + (
                        f"could list it — blocked today by {rooted}"
                        if rooted
                        else "can list and read its contents"
                    )
                ),
                evidence=f"stat {root} -> {oct(root_mode)}"
                + (f"; {rooted}" if rooted else ""),
                fix=RULES_BY_ID["RUNTIME-PERMISSIONS"].fix,
                severity_override="info" if rooted else "",
            )
        )
    try:
        entries = sorted(root.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        if not entry.is_file():
            continue
        if not SENSITIVE_NAME_RE.search(entry.name):
            continue
        mode = _mode(entry)
        if mode is None:
            continue
        if mode & 0o077:
            blocked = _blocking_ancestor(entry)
            readable_by_other = bool(mode & 0o004)
            findings.append(
                Finding(
                    rule="RUNTIME-PERMISSIONS",
                    path=str(entry),
                    line=0,
                    message=(
                        f"{entry.name} is {oct(mode)} (group/other bits set)"
                        + (
                            f" — blocked today by {blocked}"
                            if blocked
                            else (
                                " and readable by another local user"
                                if readable_by_other
                                else " and listable/traversable by another local user"
                            )
                        )
                    ),
                    evidence=f"stat {entry} -> {oct(mode)}"
                    + (f"; {blocked}" if blocked else ""),
                    fix=RULES_BY_ID["RUNTIME-PERMISSIONS"].fix,
                    severity_override="info" if blocked else "medium",
                )
            )

    # 2. leftovers from a non-atomic write (a crash between write and rename)
    for pattern in ("*.tmp", "*.tmp-*", ".*.tmp"):
        for leftover in sorted(root.glob(pattern)):
            if leftover.is_file():
                mode = _mode(leftover)
                findings.append(
                    Finding(
                        rule="WRITE-THEN-CHMOD",
                        path=str(leftover),
                        line=0,
                        message=(
                            "a temporary write target was left behind — evidence of an "
                            "interrupted replace (its mode is whatever the umask gave it)"
                        ),
                        evidence=f"{leftover.name} mode {oct(mode or 0)}",
                        fix=RULES_BY_ID["WRITE-THEN-CHMOD"].fix,
                    )
                )

    # 3. tool manifests that ask for privileges the policy refuses
    tools_dir = root / "generated_tools"
    if tools_dir.is_dir():
        for manifest_path in sorted(tools_dir.glob("*/manifest.yaml")):
            try:
                text = manifest_path.read_text(encoding="utf-8")
            except OSError:
                continue
            lowered = text.lower()
            hit: list[str] = []
            if re.search(r"^\s*privileged:\s*true", lowered, re.M):
                hit.append("privileged: true")
            if re.search(r"^\s*network:\s*[\"']?host", lowered, re.M):
                hit.append("network: host")
            for literal in ("/var/run/docker.sock", "docker.sock"):
                if literal in lowered:
                    hit.append(literal)
            if hit:
                findings.append(
                    Finding(
                        rule="TOOL-PRIVILEGE-REQUEST",
                        path=str(manifest_path),
                        line=0,
                        message="the manifest asks for a privilege the runtime refuses",
                        evidence=", ".join(sorted(set(hit))),
                        fix=RULES_BY_ID["TOOL-PRIVILEGE-REQUEST"].fix,
                    )
                )

    # 4. facts: sandbox availability, credential names, argv hygiene
    import shutil

    facts["sandbox"] = {
        "bwrap": shutil.which("bwrap") or "",
        "docker": shutil.which("docker") or "",
        "podman": shutil.which("podman") or "",
        "generated_code_runs": bool(shutil.which("bwrap")),
        "note": (
            "no bwrap: generated tools and gated runs are blocked (fail-closed), "
            "never executed unsandboxed"
            if not shutil.which("bwrap")
            else "bwrap present: gated runs execute inside the sandbox"
        ),
    }
    facts["credential_names_set"] = sorted(
        name
        for name, value in env.items()
        if value and CREDENTIAL_ENV_RE.search(name)
    )
    secret_argv = [
        argument
        for argument in (argv if argv is not None else [])
        if SECRET_RE.search(argument)
    ]
    facts["argv_secret_shapes"] = len(secret_argv)
    if secret_argv:
        findings.append(
            Finding(
                rule="SECRET-IN-ARGV",
                path="(this process)",
                line=0,
                message=(
                    "a secret-shaped value reached this process's command line — "
                    "argv is world-readable in /proc"
                ),
                evidence=f"{len(secret_argv)} argument(s) matched a secret shape",
                fix=RULES_BY_ID["SECRET-IN-ARGV"].fix,
            )
        )
    return findings, facts


# --------------------------------------------------------------------------- #
# the report                                                                    #
# --------------------------------------------------------------------------- #


def _counts(findings: Sequence[Finding], key) -> dict[str, int]:
    out: dict[str, int] = {}
    for finding in findings:
        name = str(key(finding))
        out[name] = out.get(name, 0) + 1
    return dict(sorted(out.items(), key=lambda item: (-item[1], item[0])))


def audit(
    roots: Sequence[str | Path] | None = None,
    *,
    base: Path | None = None,
    runtime: str | Path | None = None,
    include_tests: bool = False,
    live: bool = True,
    classes: Sequence[str] | None = None,
    min_severity: str = "info",
    argv: Sequence[str] | None = None,
    max_file_bytes: int = MAX_FILE_BYTES,
) -> dict:
    """Run the audit and return the payload (the ``data`` half of the envelope).

    ``classes``/``min_severity`` filter the *report*; ``scanned`` and the class
    coverage are reported for the unfiltered run so a filtered report still says
    what it did not look at.
    """
    repo_root = Path(base).resolve() if base is not None else Path(__file__).resolve().parents[2]
    scan_roots = list(roots) if roots else [repo_root / "src", repo_root / "integrations"]
    existing = [str(Path(item)) for item in scan_roots if Path(item).exists()]

    findings, files_scanned = audit_paths(
        [Path(item) for item in existing],
        base=repo_root,
        include_tests=include_tests,
        max_bytes=max_file_bytes,
    )
    environment: dict[str, Any] = {}
    runtime_dir = Path(runtime).expanduser() if runtime else _default_runtime_dir()
    runtime_findings: list[Finding] = []
    if live and runtime_dir is not None:
        runtime_findings, environment = audit_runtime(runtime_dir, argv=argv)
        findings.extend(runtime_findings)

    all_classes = sorted({finding.klass for finding in findings})
    filtered = [
        finding
        for finding in findings
        if (not classes or finding.klass in set(classes))
        and rank(finding.severity) >= rank(min_severity)
    ]
    blocking = [f for f in filtered if rank(f.severity) >= rank("high")]
    return {
        "schema": "godsv.security_audit/1",
        "root": str(repo_root),
        "scan_roots": existing,
        "runtime": str(runtime_dir) if runtime_dir is not None else "",
        "files_scanned": files_scanned,
        "rules": len(RULES),
        "filter": {
            "classes": sorted(classes or []),
            "min_severity": min_severity,
            "include_tests": bool(include_tests),
            "live": bool(live),
        },
        "totals": {
            "findings": len(findings),
            "reported": len(filtered),
            "blocking": len(blocking),
            "by_severity": _counts(findings, lambda f: f.severity),
            "by_class": _counts(findings, lambda f: f.klass),
            "by_rule": _counts(findings, lambda f: f.rule),
        },
        "classes_audited": list(CLASSES),
        "classes_with_findings": all_classes,
        "classes_clean": [name for name in CLASSES if name not in all_classes],
        "environment": environment,
        "findings": [finding.as_dict() for finding in
                     sorted(filtered, key=lambda f: (f.path, f.line, f.rule))],
    }


def _runtime_env(name: str, default: str | None = None) -> str | None:
    """``SKALDIR_<name>`` / legacy ``GODSV_<name>`` — this module also runs standalone."""
    try:
        from .env import getenv

        return getenv(name, default)
    except ImportError:  # pragma: no cover — standalone use (no package parent)
        for prefix in ("SKALDIR_", "GODSV_"):
            if prefix + name in os.environ:
                return os.environ[prefix + name]
        return default


def _default_runtime_dir() -> Path | None:
    """The product runtime dir, when the build has a resolver (else ``None``)."""
    try:
        from .registry import default_runtime_dir

        home = _runtime_env("HOME", "")
        if home:
            return Path(home).expanduser()
        return Path(default_runtime_dir())
    except Exception:  # noqa: BLE001 — standalone use has no registry
        home = _runtime_env("HOME", "")
        return Path(home).expanduser() if home else None


def render_text(report: dict) -> str:
    """The human view: per-class counts first, then every finding with its fix."""
    lines: list[str] = []
    totals = report.get("totals", {})
    lines.append(
        f"security audit — {report.get('files_scanned', 0)} files scanned, "
        f"{report.get('rules', 0)} rules, "
        f"{totals.get('findings', 0)} findings "
        f"({totals.get('blocking', 0)} at high or above)"
    )
    lines.append(f"root: {report.get('root', '')}")
    if report.get("runtime"):
        lines.append(f"runtime: {report['runtime']}")
    by_class = totals.get("by_class") or {}
    lines.append("")
    lines.append("by class:")
    for name in report.get("classes_audited", []):
        count = by_class.get(name, 0)
        marker = "clean" if not count else f"{count}"
        lines.append(f"  {name:<24} {marker:>5}   {CLASS_HELP.get(name, '')}")
    lines.append("")
    by_severity = totals.get("by_severity") or {}
    lines.append(
        "by severity: "
        + ", ".join(f"{name}={by_severity.get(name, 0)}" for name in reversed(SEVERITIES))
    )
    findings = report.get("findings") or []
    if findings:
        lines.append("")
        lines.append("findings:")
        for item in findings:
            lines.append(
                f"  [{item['severity']:<8}] {item['class']:<24} "
                f"{item['location']}  {item['rule']}"
            )
            if item.get("message"):
                lines.append(f"        {item['message']}")
            if item.get("evidence"):
                lines.append(f"        evidence: {item['evidence']}")
            if item.get("snippet"):
                lines.append(f"        code: {item['snippet']}")
        lines.append("")
        lines.append("each finding's fix is in the JSON report (`security audit --json`)")
    else:
        lines.append("")
        lines.append("no findings in the reported classes")
    return "\n".join(lines)


def rules_payload() -> dict:
    """The rule catalogue as JSON (``--list-rules``)."""
    return {
        "rules": [rule.as_dict() for rule in RULES],
        "classes": [
            {"class": name, "help": CLASS_HELP[name],
             "rules": [r.id for r in RULES if r.klass == name]}
            for name in CLASSES
        ],
        "severities": list(reversed(SEVERITIES)),
    }


__all__ = [
    "CLASSES",
    "CLASS_HELP",
    "CLASS_COMMAND_INJECTION",
    "CLASS_DESERIALISATION",
    "CLASS_ERROR_LEAKAGE",
    "CLASS_PATH_TRAVERSAL",
    "CLASS_PERMISSIONS",
    "CLASS_RESOURCE_LIMITS",
    "CLASS_SECRETS",
    "CLASS_TOCTOU",
    "DEFAULT_SKIP_DIRS",
    "Finding",
    "RULES",
    "RULES_BY_ID",
    "Rule",
    "SEVERITIES",
    "audit",
    "audit_paths",
    "audit_runtime",
    "audit_source",
    "iter_sources",
    "mask",
    "rank",
    "render_text",
    "rules_payload",
]
