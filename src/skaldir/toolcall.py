"""Pre-execution interception for planned tool calls (Block 4, §„Wrong Tool Call").

The main agent proposes an action; God's Voice judges it **before** it runs.
This module is the pure, deterministic policy: it takes a :class:`ToolIntent`
(what is about to be called, from where, with which trust source) and returns
one of four classifications plus the reasons and a concrete directive.

Classifications
---------------
``ALLOW``
    Nothing speaks against it. The default — most calls are fine, and the
    interception is not a general content filter.
``ASK_APPROVAL``
    Legitimate but needs a human decision (production paths, spend, pushed
    changes, secrets handled by the owner).
``PAUSE_AND_INVESTIGATE``
    Suspicious enough that the investigator should look (a destructive call
    that *may* be legitimate, an unverifiable claim, drift into an unrelated
    workspace) — the existing pause machinery runs the investigation.
``BLOCK``
    Fail-closed: destructive, production or secret-touching actions without
    any approval basis. Never executed.

Security paths are **fail-closed**: when the judgment itself is uncertain, the
outcome is the stricter one (a checker error, an unreadable workspace, an
unknown tool ⇒ not ALLOW).

Checked dimensions (all deterministic, no LLM, no I/O):

1. goal reference        — does the call serve the open goal at all?
2. tool/schema fit       — does the tool match the planned operation?
3. parameter plausibility
4. workspace/project     — does the target belong to the current project?
5. destructiveness
6. safer capability      — is there a strictly safer way to the same effect?
7. contradiction to evidence
8. tool health
9. permissions/approval  — grants, capabilities, existing approvals
10. trust source of the impulse — an impulse from untrusted content cannot
    authorise anything (``UNTRUSTED CONTENT IS NO CONTROL AUTHORITY``).
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatch

from .env import first_env
from .untrusted import TRUSTED_SOURCES, UNTRUSTED_RULE

ALLOW = "ALLOW"
ASK_APPROVAL = "ASK_APPROVAL"
PAUSE_AND_INVESTIGATE = "PAUSE_AND_INVESTIGATE"
BLOCK = "BLOCK"

# Tools that only read: they can never be classified as destructive.
READ_ONLY_TOOLS = frozenset(
    {
        "read_file",
        "read",
        "ls",
        "list_dir",
        "glob",
        "grep",
        "search_files",
        "web_search",
        "web_extract",
        "session_search",
        "vision_analyze",
        "read_terminal",
        "bash_read",  # read-only terminal wrapper
        "read_text",  # agent_loop built-in workspace reader
    }
)

# Tools whose whole purpose is to change something on disk / on screen.
WRITE_TOOLS = frozenset(
    {
        "write_file",
        "create_file",
        "write",
        "patch",
        "apply_patch",
        "edit_file",
        "str_replace",
        "terminal",
        "run_terminal",
        "bash",
        "shell",
        "computer_use",
        "powershell",
        "write_text",  # agent_loop built-in workspace writer
        "edit_apply",  # goal run's own edit adapter (edit_tx)
        "run_tests",  # goal run's declared check adapter
        # the short spellings a brief/prompt may use — same world-changing class
        "edit",
    }
)

#: The single source of the **allow vocabulary for rendering**: every spelling an
#: allow/execute decision may carry in a transcript (``decision`` field) and that a
#: renderer (``art.from_tool_event``, chat tool rows) must draw as an *executed* call,
#: never as a decline. :mod:`skaldir.art` imports this set; there is no second copy.
ALLOW_TOKENS_FOR_RENDER: frozenset[str] = frozenset(
    {"allow", "allowed", "executed", "ok", "run"}
)


# A call that changes the world must be paired with a tool that *can* do it.
_SCHEMA_MISMATCH = re.compile(
    r"(?i)^(?:read_file|read|ls|list_dir|glob|grep|search_files)$"
)

# Catastrophic commands: they destroy a *system*, never a scoped artefact.
_CATASTROPHIC_COMMANDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mkfs", re.compile(r"(?i)\bmkfs(?:\.\w+)?\b")),
    ("dd_to_device", re.compile(r"(?i)\bdd\b[^\n]{0,40}of=/dev/(?:sd|nvme|hd|vd|mmcblk)")),
    ("fork_bomb", re.compile(r":\s*\(\s*\)\s*\{")),
    ("no_preserve_root", re.compile(r"(?i)--no-preserve-root")),
    ("drop_database", re.compile(r"(?i)\b(?:DROP\s+(?:DATABASE|SCHEMA)|TRUNCATE\s+TABLE)\b")),
    ("chmod_root", re.compile(r"(?i)\bchmod\s+-R\s+(?:777|000)\s+/\s*$")),
    ("reboot", re.compile(r"(?i)\b(?:shutdown|reboot|halt|poweroff)\b\s*(?:-h|now|\+0)?\s*$")),
    ("find_delete_root", re.compile(r"(?i)\bfind\s+/\s[^\n]{0,40}-delete\b")),
    ("kill_all", re.compile(r"(?i)\b(?:kill|pkill)\s+-9\s+-1\b")),
    ("truncate", re.compile(r"(?i)\btruncate\s+-s\s*0\b")),
)

# rm/rmdir of a root, home or system target — the classic catastrophe. A scoped
# `rm -rf build/` is destructive, not catastrophic, and must not be conflated.
_DANGEROUS_RM_TARGET = re.compile(
    r"(?i)(?:^|[\s\"'=])(?:/|/\*|~|~/|\$HOME|\$HOME/)(?:\s|$)"
    r"|(?:^|[\s\"'=])/(?:etc|usr|var|home|boot|bin|sbin|lib|lib64|opt|root|srv|sys|proc)(?:/|\s|$)"
)

# Scoped destructive patterns: they *do* destroy, but within reach — a build
# cleanup, a branch reset. Fail-closed still applies (no approval ⇒ no
# execution), but the answer is approval rather than a hard block, so normal
# work is not mistaken for an attack.
_DESTRUCTIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("rm_recursive", re.compile(r"(?i)\brm\s+(?:-\w*\s+)*-\w*(?:r|f)\w*")),
    ("force_push", re.compile(r"(?i)\bgit\s+push\b[^\n]{0,40}(?:--force\b|(?:^|\s)-\w*f)")),
    ("hard_reset", re.compile(r"(?i)\bgit\s+reset\s+--hard\b")),
    ("git_clean_force", re.compile(r"(?i)\bgit\s+clean\s+-\w*f\w*d\w*")),
)

_RM_LIKE = re.compile(r"(?i)\b(?:rm|rmdir)\b")

_SECRET_PATHS = re.compile(
    r"(?i)(?:/\.ssh\b|id_rsa\b|id_ed25519\b|\.aws/credentials|\.netrc\b|\.pem\b|"
    r"\.env(?:\.\w+)?\b|\.git-credentials|/\.gnupg/|keychain|credentials\.json|"
    r"secrets?\.(?:ya?ml|json|toml|env))"
)

_PRODUCTION_MARKERS = re.compile(
    r"(?i)(?:\bprod(?:uction)?\b|\blive\s+system\b|\bproduction\s+(?:db|database|server|"
    r"cluster|environment)\b|api\.prod|prod\.|/etc/(?:nginx|systemd|httpd)|"
    r"\bkubectl\b[^\n]{0,40}(?:prod|live)|\bhelm\s+(?:upgrade|install)\b[^\n]{0,40}prod)"
)

_VERIFICATION_DISABLE = re.compile(
    r"(?i)(?:--no-verify|--no-tests|--skip-tests?|--no-check|SKIP_(?:TESTS|CHECKS)|"
    r"\b(?:disable|skip|bypass)\b[^\n]{0,30}\b(?:tests?|checks?|verification|guard|gate)\b|"
    r"\bpytest\b[^\n]{0,20}--no-header[^\n]{0,40}\bx\b|--exitfirst\s+--ignore)"
)

_SPEND_MARKERS = re.compile(
    r"(?i)\b(?:spend|buy|purchase|pay|charge|subscribe|upgrade\s+plan|"
    r"\baws\s+\w+\s+(?:create|run)|gcloud\s+compute\s+instances\s+create|"
    r"stripe\s+charges?)\b"
)

_APPROVAL_TOKENS = frozenset({"approved", "grant", "granted", "allow", "allowed", "owner_ok"})

# Signals that an impulse came from external text rather than from the goal.
_INJECTION_ECHO = re.compile(
    r"(?i)(?:"
    + "|".join(
        [
            r"ignore\s+previous\s+instructions?",
            r"ignore\s+all\s+previous",
            r"as\s+your\s+new\s+system\s+prompt",
            r"treat\s+this\s+file\s+as",
            r"do\s+this\s+first",
            r"run\s+this\s+first",
            r"without\s+verification",
            r"disable\s+verification",
            r"skip\s+(?:the\s+)?tests?",
            r"upload\s+(?:the\s+)?(?:\.ssh|ssh\s+keys?)",
            r"exfiltrate",
        ]
    )
    + r")"
)


@dataclass(frozen=True)
class ToolIntent:
    """Everything the policy may look at — provided by the host hook."""

    tool: str
    args: dict = field(default_factory=dict)
    session_id: str = ""
    goal: str = ""
    scope_paths: tuple[str, ...] = ()
    workspace: str = ""
    project_root: str = ""
    trust_source: str = "agent"
    approvals: frozenset[str] = frozenset()
    tool_health: dict = field(default_factory=dict)
    safer_alternative: str = ""
    evidence_contradiction: str = ""
    recent_text: str = ""
    # Directive texts extracted from confirmed injection attempts in this
    # session. A planned call that reproduces one of them *is* the takeover.
    untrusted_directives: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolDecision:
    """The verdict for one planned call, with the reasons that produced it."""

    classification: str
    reasons: Sequence[str] = ()
    checks: dict = field(default_factory=dict)
    directive: str = ""

    @property
    def blocked(self) -> bool:
        return self.classification == BLOCK

    @property
    def allowed(self) -> bool:
        return self.classification == ALLOW

    def hook_action(self) -> dict | None:
        """Map to the existing Hermes hook contract (``{"action": …}``).

        ALLOW and PAUSE_AND_INVESTIGATE return ``None`` here: the pause path
        is driven by the pause lease machinery, not by a synthetic hook reply.
        """
        if self.classification in (BLOCK, ASK_APPROVAL):
            return {"action": "block", "message": self.render()}
        return None

    def render(self) -> str:
        head = {
            BLOCK: "[voice·guard] tool call blocked (before execution)",
            ASK_APPROVAL: "[voice·guard] tool call needs approval",
            PAUSE_AND_INVESTIGATE: "[voice·guard] tool call is being assessed",
            ALLOW: "[voice·guard] tool call allowed",
        }[self.classification]
        lines = [f"{head}: {self.directive}".rstrip()]
        for reason in self.reasons[:4]:
            lines.append(f"- {reason}")
        if self.classification is not ALLOW:
            # every refusal names what the user has to act on (host-agent contract):
            # the deciding component, the access in question, the rule, and the way out.
            checks = self.checks or {}
            affected = sorted(
                {
                    str(item)
                    for key in ("foreign_paths", "traversal_paths", "managed_tmp_paths",
                                "out_of_scope", "destructive", "secret_paths")
                    for item in (checks.get(key) or [])
                }
            )
            if affected:
                lines.append(f"- access affected: {', '.join(affected[:6])}")
            # The label is not the decider. `[voice·guard]` is what the user sees; the
            # verdict here comes from the path classification, and saying otherwise
            # would point the owner at the wrong component.
            lines.append(
                "- decided by: path classification (`skaldir.toolcall`), "
                "not by the voice model"
            )
            lines.append(
                "- rule: writes stay inside the project or the managed temporary area; "
                "reads anywhere, and the device targets /dev/null|stdout|stderr are "
                "never file writes"
            )
            lines.append(
                "- next step: pick a path inside the workspace, or use the managed "
                "temporary area for this run's own logs, or open an approval for the "
                "named host path"
            )
        return "\n".join(lines)


def _command(intent: ToolIntent) -> str:
    args = intent.args or {}
    for key in ("command", "cmd", "script", "input"):
        value = args.get(key)
        if value:
            return str(value)
    return ""


#: A discard target: writing here throws away the caller's own stream. It is not a file
#: write, so the project fence must never fire on it.
DEVICE_DISCARD = frozenset({"/dev/null", "/dev/tty"})

#: A *forward* target: it hands the stream on to the caller's own stdout/stderr. Like a
#: discard it is not a file write — but calling it "a discard" would be wrong, so the two
#: are separate sets (user correction: "/dev/stdout und /dev/stderr sind
#: Weiterleitungen, keine Verwerfziele").
DEVICE_FORWARD = frozenset({"/dev/stdout", "/dev/stderr", "/dev/fd/1", "/dev/fd/2"})

DEVICE_TARGETS = DEVICE_DISCARD | DEVICE_FORWARD

#: The runtime's own data directory, i.e. ``<SKALDIR_HOME>/tmp``. NOT ``/tmp``: the whole
#: shared /tmp holds other people's files, and a manipulated ``TMPDIR`` pointing at /etc
#: must not turn system files into "our own" (user correction, measured before the fix:
#: ``SKALDIR_TMPDIR=/etc`` made ``/etc/passwd`` count as managed).
_MANAGED_TMP_CACHE: tuple[tuple[str, str], tuple[str, ...]] | None = None
_MANAGED_TMP_ROOTS: tuple[str, ...] = ()


def _managed_tmp_roots() -> tuple[str, ...]:
    """The directories this runtime really owns for its own logs and artefacts.

    ``SKALDIR_TMPDIR`` is honoured only when it points *inside* the runtime directory
    or a Skaldir-owned path — a value like ``/etc`` or ``/`` is a manipulated setting and
    is ignored rather than believed.

    The result is cached per resolved home: a process that changes ``SKALDIR_HOME``
    (a test switching runtimes, a host run handing over a session) must not keep judging
    the *old* home, so the cache key is the home itself.
    """
    home = first_env(("SKALDIR_HOME", "GODSV_HOME"), default="") or ""
    override = (first_env(("SKALDIR_TMPDIR",), default="") or "").strip()
    cache_key = (home, override)
    global _MANAGED_TMP_CACHE
    if _MANAGED_TMP_CACHE is not None and _MANAGED_TMP_CACHE[0] == cache_key:
        return _MANAGED_TMP_CACHE[1]
    roots: list[str] = []
    if home:
        roots.append(os.path.join(home, "tmp"))
    # the shared but *owned* scratch area: `/tmp/skaldir` (or `$SKALDIR_TMPDIR` when it
    # points there). Plain `/tmp` deliberately stays OUT: it holds other people's files,
    # and the user's correction named exactly that ("Ganz /tmp ist kein eigener
    # verwalteter Arbeitsraum").
    roots.append("/tmp/skaldir")
    for candidate in override.split(os.pathsep):
        candidate = (candidate or "").rstrip("/")
        if not candidate:
            continue
        # a hostile value must not widen our own scope to the whole machine
        if candidate in ("/", "/tmp", "/etc", "/var", "/usr", "/home", "/opt", "/boot"):
            continue
        if home and (candidate == home or candidate.startswith(home + "/")):
            roots.append(candidate)
        elif candidate.startswith("/tmp/skaldir"):
            roots.append(candidate)
    resolved = tuple(dict.fromkeys(roots))
    _MANAGED_TMP_CACHE = (cache_key, resolved)
    return resolved


def in_managed_tmp(path: str) -> bool:
    """True when ``path`` is a directory this runtime owns, verified on disk.

    Three things are checked, because each one alone is bypassable:
    the path is under an owned root, it is **not a symlink** (a symlink inside our own
    tree can point anywhere), and its *real* target is still inside that root.
    """
    if not path.startswith("/"):
        return False
    normalized = os.path.normpath(path)
    roots = _managed_tmp_roots()
    if not roots:
        return False
    if not any(normalized == r or normalized.startswith(r + "/") for r in roots):
        return False
    # follow links before trusting the placement
    try:
        real = os.path.realpath(normalized)
    except OSError:
        return False
    return any(real == r or real.startswith(r + "/") for r in roots)


def owns_path(path: str) -> bool:
    """Alias kept for call sites that ask about ownership rather than scope."""
    return in_managed_tmp(path)


def redirect_target(command: str) -> list[str]:
    """The *redirect* targets of a command, with their access kind.

    This is what separates a redirect from an argument: ``soffice --calc x.ods`` names a
    file to READ, while ``soffice ... 2>/dev/null`` DISCARDS the caller's own stream. The
    old ``_paths`` regex pulled both into one flat list, so ``/dev/null`` was judged as
    a write into a foreign tree — the exact P0. ``/dev/...`` is filtered here, before any
    workspace question is asked.
    """
    targets: list[str] = []
    for match in re.finditer(
        r"(?P<fd>\d*)(?P<kind>>>?)\s*(?P<path>[^\s;&|<>'\"()]+)", command
    ):
        fd = match.group("fd")
        raw = match.group("path")
        if raw.startswith("/dev/"):
            targets.append(raw)
            continue
        # 2> is stderr; 2>> appends to it. Both are the *caller's* diagnostics,
        # not a write into the target file (they still may create/append the file).
        if fd in ("2", ""):
            targets.append(raw)
    # `tee -a <path>` and `tee <path>` write into the named file too.
    for match in re.finditer(r"\btee\s+(?:-a\s+)?([~./\w][^\s;&|<>'\"()]*)", command):
        targets.append(match.group(1))
    return targets


#: Commands that WRITE into the file they name as an argument. "Only redirects write"
#: was my own wrong assumption — `cp a /etc/passwd` and `sed -i /etc/hosts` never open a
#: redirect and still overwrite the target (user correction, measured: all four were
#: ALLOW before this table existed).
_WRITE_VIA_ARGUMENT: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("copy_over", re.compile(r"(?i)\bcp\s+(?:-\w+\s+)*\S+\s+")),
    ("copy_recursive", re.compile(r"(?i)\b(?:cp|rsync)\s+(?:-\w*r\w*\s+)")),
    ("move_over", re.compile(r"(?i)\bmv\s+(?:-\w+\s+)*\S+\s+")),
    ("in_place_edit", re.compile(r"(?i)\bsed\s+(?:-\w+\s+)*-i")),
    ("in_place_perl", re.compile(r"(?i)\bperl\s+(?:-\w+\s+)*-i")),
    ("install_over", re.compile(r"(?i)\binstall\s+(?:-\w+\s+)*-m")),
    ("truncate", re.compile(r"(?i)\btruncate\s+-s")),
    ("link_over", re.compile(r"(?i)\bln\s+-sf?")),
    ("chmod_chown", re.compile(r"(?i)\b(?:chmod|chown|chgrp|setfacl)\s")),
    ("mount_write", re.compile(r"(?i)\bmount\s+(?!-o\s+ro\b)")),
    ("dd_output", re.compile(r"(?i)\bdd\b[^\n]*\bof=")),
    ("redirect_helper", re.compile(r"(?i)\bsponge\b")),
)

_WRITE_ARG_KIND = {name for name, _ in _WRITE_VIA_ARGUMENT}


def writes_via_argument(command: str) -> list[str]:
    """The names of the write-by-argument patterns a command matches."""
    if not command:
        return []
    return [name for name, pattern in _WRITE_VIA_ARGUMENT if pattern.search(command)]


def argument_write_targets(command: str) -> list[str]:
    """The files a write-by-argument command names on its command line.

    Only the *destination* side is returned, and only when the command actually writes
    that way: ``cp src dst`` names ``dst``, ``sed -i file`` names ``file``. Everything
    that is not a destination stays out, so a read-only command is not widened.
    """
    if not command:
        return []
    kinds = writes_via_argument(command)
    if not kinds:
        return []
    targets: list[str] = []

    def collect(part: str) -> None:
        for token in _split_tokens(part):
            token = token.rstrip("'\"")
            if not token or token.startswith("-"):
                continue
            if token.startswith("/dev/"):
                continue
            # an absolute path is checked as written; a relative one is resolved against
            # the caller's cwd, because `cp a.txt /etc/x` and `cp a.txt ../x` both
            # write outside the tree
            if token.startswith("/"):
                targets.append(token)
            else:
                targets.append(os.path.abspath(token))

    for match in re.finditer(r"(?i)\bcp\s+(?:-\w+\s+)*(\S+)\s+(\S+)", command):
        collect(match.group(2))
    for match in re.finditer(r"(?i)\brsync\s+[^\n]*?\s(\S+)/?\s*$", command):
        collect(match.group(1))
    for match in re.finditer(r"(?i)\bmv\s+(?:-\w+\s+)*(\S+)\s+(\S+)", command):
        collect(match.group(2))
    for match in re.finditer(
        r"(?i)\b(?:sed|perl)\s+(?:-\w+\s+)*-i[^\n]*?[\s'\"](\S+)\s*$", command
    ):
        collect(match.group(1))
    for match in re.finditer(
        r"(?i)\b(?:install|truncate|sponge|ln)\s+(?:-\w+\s+)*-?[a-zA-Z]*\s*(\S+)", command
    ):
        collect(match.group(1))
    for match in re.finditer(
        r"(?i)\b(?:chmod|chown|chgrp|setfacl)\s+(?:-\w+\s+)*[0-7ugo+=\w,\s-]+\s+(\S+)", command
    ):
        collect(match.group(1))
    return [t for t in targets if t]


def _split_tokens(part: str) -> list[str]:
    """Split on whitespace, respecting simple quoting."""
    return [tok for tok in re.split(r"\s+", part.strip()) if tok]


def _argument_write_paths(intent: ToolIntent) -> list[str]:
    """The absolute targets a write-by-argument command names (cp/mv/sed -i/chmod/...)."""
    return argument_write_targets(_command(intent))


def _paths(intent: ToolIntent) -> list[str]:
    args = intent.args or {}
    found: list[str] = []
    for key in ("path", "file_path", "filepath", "target", "dest", "destination"):
        value = args.get(key)
        if value:
            found.append(str(value))
    for key in ("paths", "files"):
        value = args.get(key)
        if isinstance(value, (list, tuple)):
            found.extend(str(item) for item in value)
    command = _command(intent)
    if command:
        # only the redirect targets of a command are write candidates; the named
        # arguments stay where they were (they are reads/opens, judged separately)
        found.extend(redirect_target(command))
        # ... but a command that W R I T E S into its own argument (cp/mv/sed -i/chmod)
        # names a real target there. "Only redirects write" was my wrong assumption.
        found.extend(argument_write_targets(command))
    return [path for path in found if path]


def _has_approval(intent: ToolIntent) -> bool:
    return bool({str(item).lower() for item in intent.approvals} & _APPROVAL_TOKENS)


def _in_scope(intent: ToolIntent, path: str) -> bool:
    if not intent.scope_paths:
        return True
    if not path or path.startswith("/dev/"):
        return True
    return any(fnmatch(path, pattern) for pattern in intent.scope_paths)


def _in_workspace(intent: ToolIntent, path: str) -> bool:
    """Absolute paths outside the project root/workspace are foreign targets.

    Relative paths are always considered inside (the working directory defines
    them); traversal is caught separately by :func:`_escapes_by_traversal`.
    With no known root, an absolute path cannot be placed — it counts as
    unknown, not as inside (fail-safe, see the caller).
    """
    if not path.startswith("/"):
        return True
    roots = [root for root in (intent.workspace, intent.project_root) if root]
    if not roots:
        return False  # unknown root: an absolute path is unverifiable, not safe
    normalized = path.rstrip("/")
    for root in roots:
        root = root.rstrip("/")
        if normalized == root or normalized.startswith(root + "/"):
            return True
    return False


def _escapes_by_traversal(path: str) -> bool:
    """True when a *relative* path walks upward out of its root (``../../etc``).

    A workspace fence that only checks absolute paths is trivially bypassed by
    ``write_file(path="../../etc/passwd")`` — the write lands outside the tree
    while the check sees a relative path and waves it through.
    """
    if not path or path.startswith("/") or path.startswith("~"):
        return False
    depth = 0
    for part in re.split(r"[\\/]+", path):
        if part == "..":
            depth -= 1
            if depth < 0:
                return True
        elif part and part != ".":
            depth += 1
    return False


def _goal_terms(goal: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß0-9_]{3,}", goal or "")
    }


def _goal_reference(intent: ToolIntent) -> tuple[bool, str]:
    """Does the call plausibly serve the open goal (rough, deliberately generous)?"""
    terms = _goal_terms(intent.goal)
    if not terms:
        return True, ""
    haystack = " ".join(
        [intent.tool, _command(intent), " ".join(_paths(intent))]
    ).lower()
    if not haystack.strip():
        return True, ""
    if any(term in haystack for term in terms):
        return True, ""
    # read-only reconnaissance is never goal drift
    if intent.tool in READ_ONLY_TOOLS:
        return True, ""
    return False, (
        "no reference to the open goal is recognisable (neither the tool nor the "
        "target path/command shares a term with the contract)"
    )


def _destructive_findings(intent: ToolIntent) -> list[str]:
    command = _command(intent)
    blob = " ".join([command, " ".join(_paths(intent))])
    reasons: list[str] = []
    for name, pattern in _CATASTROPHIC_COMMANDS:
        if pattern.search(blob):
            reasons.append(f"destruktiv (katastrophal): {name}")
    if _RM_LIKE.search(command) and _DANGEROUS_RM_TARGET.search(command):
        reasons.append("destruktiv (katastrophal): rm auf Wurzel/Home/Systempfad")
    for name, pattern in _DESTRUCTIVE_PATTERNS:
        if pattern.search(blob):
            reasons.append(f"destruktiv: {name}")
    return reasons


def _catastrophic(intent: ToolIntent) -> bool:
    """A pattern that destroys a system rather than a scoped artefact."""
    return any(
        reason.startswith("destruktiv (katastrophal)")
        for reason in _destructive_findings(intent)
    )


def _secret_findings(intent: ToolIntent) -> list[str]:
    blob = " ".join([_command(intent), " ".join(_paths(intent)), _write_text(intent)])
    if _SECRET_PATHS.search(blob):
        return ["secret path touched (credentials/keys)"]
    return []


def _production_findings(intent: ToolIntent) -> list[str]:
    blob = " ".join([_command(intent), " ".join(_paths(intent)), _write_text(intent)])
    if _PRODUCTION_MARKERS.search(blob):
        return ["production target touched"]
    return []


def _trust_findings(intent: ToolIntent) -> list[str]:
    """An impulse from untrusted content may not authorise anything."""
    source = (intent.trust_source or "agent").strip().lower()
    reasons: list[str] = []
    if source and source not in TRUSTED_SOURCES:
        reasons.append(f"trust source '{source}' is untrusted — {UNTRUSTED_RULE}")
    if _INJECTION_ECHO.search(intent.recent_text or ""):
        reasons.append(
            "the impulse repeats an embedded foreign instruction "
            "(prompt-injection pattern)"
        )
    if intent.untrusted_directives:
        blob = " ".join([_command(intent), " ".join(_paths(intent))])
        if blob.strip() and _reproduces_directive(blob, intent.untrusted_directives):
            reasons.append(
                "the call acts on an instruction previously ignored as untrusted — "
                f"{UNTRUSTED_RULE}"
            )
    return reasons


def _reproduces_directive(blob: str, directives) -> bool:
    """Does this call carry the effect of an already-ignored external directive?

    Compared by *effect tokens*, not by string equality: re-wording a takeover
    does not make it the caller's own intent.
    """
    haystack = re.sub(r"\s+", " ", blob.lower())
    for directive in directives:
        tokens = {
            token
            for token in re.findall(r"[a-z0-9_./~-]{4,}", str(directive).lower())
            if token not in _DIRECTIVE_STOPWORDS
        }
        if not tokens:
            continue
        hits = sum(1 for token in tokens if token in haystack)
        if hits >= max(2, len(tokens) // 2):
            return True
    return False


_DIRECTIVE_STOPWORDS = frozenset(
    {
        "this",
        "that",
        "your",
        "with",
        "from",
        "into",
        "then",
        "must",
        "should",
        "please",
        "quickly",
        "instructions",
        "ignore",
        "previous",
    }
)


def _schema_fit(intent: ToolIntent) -> list[str]:
    """A read-only tool can never perform a world-changing operation.

    Missing information (no tool name, empty args) yields a *note*, not a
    classification: it is not a security signal, and inventing suspicion for
    an unremarkable call is exactly the over-intervention to avoid.
    """
    tool = (intent.tool or "").strip()
    command = _command(intent)
    if tool and _SCHEMA_MISMATCH.match(tool) and command:
        if re.search(r"(?i)\b(?:rm|mv|cp|dd|mkfs|chmod|chown|truncate)\b", command):
            return [
                f"Tool/schema mismatch: '{tool}' is read-only, the command "
                "changes the filesystem"
            ]
    return []


def _unjudgeable(intent: ToolIntent) -> list[str]:
    """Calls whose operation cannot even be determined (recorded, not blocked)."""
    tool = (intent.tool or "").strip()
    if not tool:
        return ["no tool name in the call — operation cannot be determined"]
    # terminal spellings: an exec call with no command and no target cannot be
    # judged at all — including ``powershell`` (audit #6) and the short ``exec``
    # spelling. The redundant ``tool in WRITE_TOOLS`` conjunct from the first
    # version is gone: every name here is already in WRITE_TOOLS.
    if tool in ("terminal", "run_terminal", "bash", "shell", "powershell", "exec",
                "computer_use"):
        if not _command(intent) and not _paths(intent):
            return ["terminal call without command/target — parameters cannot be determined"]
    return []


def _write_text(intent: ToolIntent) -> str:
    """The content a write tool plans to put on disk, bounded (audit #4).

    ``write_text``/``write_file`` carry their payload under ``text``/``content`` —
    the one argument the path/command scans never saw, so a secret or a foreign
    write through the content itself was invisible to the policy.
    """
    args = intent.args or {}
    for key in ("text", "content", "body", "value"):
        value = args.get(key)
        if value:
            return str(value)[:2000]
    return ""


def _parameter_findings(intent: ToolIntent) -> list[str]:
    args = intent.args
    reasons: list[str] = []
    if args is None:
        return reasons
    if not isinstance(args, dict):
        return ["arguments are not an object — schema not satisfied (fail-closed)"]
    for key in ("path", "file_path", "filepath"):
        value = args.get(key)
        if value is not None and not isinstance(value, str):
            reasons.append(f"parameter '{key}' is not a path string")
    for key in ("content", "text"):
        if args.get(key) is not None and not isinstance(args.get(key), str):
            reasons.append(f"parameter '{key}' is not a string")
    return reasons


def is_destructive_call(intent: ToolIntent) -> bool:
    """Public helper: a destructive call (for tests and callers)."""
    return bool(_destructive_findings(intent))


def judge_tool_call(intent: ToolIntent) -> ToolDecision:
    """Classify one planned tool call before execution (fail-closed).

    Reading order matters: the strongest classification wins. Uncertainty is
    resolved *strictly* — an error inside the judgment must never yield ALLOW.
    """
    try:
        return _judge(intent)
    except Exception as exc:  # fail-closed: an unjudgeable call is not allowed
        return ToolDecision(
            classification=BLOCK,
            reasons=[f"policy check failed ({type(exc).__name__}) — fail-closed"],
            directive="Call not executed: the assessment was not possible.",
            checks={"error": type(exc).__name__},
        )


def _judge(intent: ToolIntent) -> ToolDecision:
    tool = (intent.tool or "").strip()
    checks: dict = {}
    destructive = _destructive_findings(intent)
    secrets = _secret_findings(intent)
    production = _production_findings(intent)
    trust = _trust_findings(intent)
    schema = _schema_fit(intent)
    params = _parameter_findings(intent)
    unjudgeable = _unjudgeable(intent)
    goal_ok, goal_reason = _goal_reference(intent)
    approved = _has_approval(intent)
    checks.update(
        {
            "destructive": destructive,
            "secret_paths": secrets,
            "production": production,
            "trust": trust,
            "schema": schema,
            "parameters": params,
            "unjudgeable": unjudgeable,
            "goal": goal_ok,
            "approvals": sorted(intent.approvals),
        }
    )

    # -- foreign workspace: an absolute path outside the project -------------
    # Only *writes* are judged here. Reading an absolute path (a log, /usr/share
    # docs, /tmp) is normal work; treating that as suspicion is over-intervention.
    # Writing outside the project is the case the boundary exists for.
    paths = _paths(intent)
    # A redirect to a device is a *discard* or a *forward*, never a file: `2>/dev/null`
    # throws away the caller's own stream, `>/dev/stdout` hands it on. Judging either as a
    # write into a foreign tree is what blocked the user's LibreOffice diagnosis, so they
    # never reach the workspace question.
    paths = [p for p in paths if p not in DEVICE_TARGETS]
    foreign = [path for path in paths if not _in_workspace(intent, path)]
    # The runtime's own directories are its scratch space: a diagnosis run writing its
    # own log there is not "escaping the project" (user P0). It stays a WRITE for the
    # destructive/danger checks — only the project fence no longer applies.
    managed = [path for path in foreign if in_managed_tmp(path)]
    foreign = [path for path in foreign if not in_managed_tmp(path)]
    # A symlink escape: the path *looks* like it sits in our own area, but following it
    # lands somewhere else entirely. `in_managed_tmp` already resolves links, so the
    # escape has to be recognised on the link itself — otherwise the target is silently
    # classified as managed (measured: `echo x > /tmp/skaldir/escape -> /etc/passwd`
    # reported `symlink_escape_paths=[]` and blocked with the WRONG reason).
    escape: list[str] = []
    for path in paths:
        if path in escape or in_managed_tmp(os.path.realpath(path)):
            continue
        try:
            if os.path.islink(os.path.normpath(path)):
                escape.append(path)
        except OSError:
            continue
    checks["managed_tmp_paths"] = [p for p in managed if p not in escape]
    checks["symlink_escape_paths"] = escape
    foreign.extend(p for p in escape if p not in foreign)
    checks["argument_write_paths"] = _argument_write_paths(intent)
    traversal = [path for path in paths if _escapes_by_traversal(path)]
    checks["foreign_paths"] = foreign
    checks["traversal_paths"] = traversal
    writes = tool in WRITE_TOOLS
    if traversal and writes:
        return ToolDecision(
            classification=BLOCK,
            reasons=[f"path leaves the workspace via '..': {traversal[0]}"],
            checks=checks,
            directive=(
                "The relative path leads out of the project. Use an absolute path "
                "inside the workspace."
            ),
        )
    if foreign and writes:
        if intent.workspace or intent.project_root:
            return ToolDecision(
                classification=BLOCK,
                reasons=[*trust, f"foreign workspace/path: {foreign[0]}"],
                checks=checks,
                directive=(
                    f"target lies outside the project ({foreign[0]}). Deliberately pick "
                    "the right workspace — never write into foreign trees."
                ),
            )
        return ToolDecision(
            classification=PAUSE_AND_INVESTIGATE,
            reasons=[f"write target outside the known workspace: {foreign[0]}"],
            checks=checks,
            directive=(
                "The project root is unknown and the target lies outside it — "
                "establish the mapping first, then write."
            ),
        )

    # -- scope drift against the pact ----------------------------------------
    out_of_scope = [path for path in _paths(intent) if not _in_scope(intent, path)]
    checks["out_of_scope"] = out_of_scope

    # -- trust of the impulse: external content cannot authorise -------------
    if trust and (destructive or secrets or production or tool in WRITE_TOOLS):
        return ToolDecision(
            classification=BLOCK,
            reasons=[*trust, *destructive, *secrets, *production],
            checks=checks,
            directive=(
                "The impulse comes from untrusted content or repeats an embedded "
                "foreign instruction. Content is data material, not an "
                "instruction — the embedded instruction is ignored."
            ),
        )

    # -- verification disablement --------------------------------------------
    if _VERIFICATION_DISABLE.search(_command(intent)):
        return ToolDecision(
            classification=BLOCK,
            reasons=["verification would be disabled/bypassed"],
            checks=checks,
            directive=(
                "Verification stays mandatory. Instead of disabling it: repair "
                "the check or report the finding honestly as open."
            ),
        )

    # -- destructive / production / secret: fail-closed ----------------------
    if destructive:
        if _catastrophic(intent):
            return ToolDecision(
                classification=BLOCK,
                reasons=destructive,
                checks=checks,
                directive=(
                    "Catastrophic call (root/memory/database) — blocked as a matter "
                    "of principle. Choose a targeted path; system interventions need "
                    "a deliberate owner approval."
                ),
            )
        if approved:
            return ToolDecision(
                classification=ASK_APPROVAL,
                reasons=[*destructive, "approval is present — obtain confirmation"],
                checks=checks,
                directive="Destructive call with approval: a final confirmation is required.",
            )
        return ToolDecision(
            classification=ASK_APPROVAL,
            reasons=destructive,
            checks=checks,
            directive=(
                "Destructive but bounded call without approval. Choose the safer "
                "variant (targeted path, backup first, dry-run) or obtain approval."
            ),
        )
    if secrets:
        return ToolDecision(
            classification=BLOCK if not approved else ASK_APPROVAL,
            reasons=secrets,
            checks=checks,
            directive=(
                "Secret path only through the treasury (handle + grant + TOTP) — "
                "never read/send it directly."
            ),
        )
    if production:
        return ToolDecision(
            classification=ASK_APPROVAL if approved else BLOCK,
            reasons=production,
            checks=checks,
            directive=(
                "Production target: do not touch it without explicit approval. "
                "Staging/dry-run first, then obtain approval."
            ),
        )
    if _SPEND_MARKERS.search(_command(intent)):
        return ToolDecision(
            classification=ASK_APPROVAL,
            reasons=["operation that incurs spend/cost"],
            checks=checks,
            directive="Cost-relevant action: obtain approval (treasury/spend intent).",
        )

    # -- tool health ---------------------------------------------------------
    health = intent.tool_health or {}
    if str(health.get("state") or "").lower() in {"broken", "unavailable", "revoked"}:
        return ToolDecision(
            classification=BLOCK,
            reasons=[f"Tool-Health: {health.get('state')}"],
            checks=checks,
            directive="The tool is marked as not operational — do not use it.",
        )
    if str(health.get("state") or "").lower() in {"degraded", "unvalidated"}:
        return ToolDecision(
            classification=ASK_APPROVAL,
            reasons=[f"Tool-Health: {health.get('state')}"],
            checks=checks,
            directive="Tool is not verified — choose an approval or a safer alternative.",
        )

    # -- contradiction to existing evidence ----------------------------------
    if (intent.evidence_contradiction or "").strip():
        return ToolDecision(
            classification=PAUSE_AND_INVESTIGATE,
            reasons=[f"contradiction to the evidence: {intent.evidence_contradiction[:160]}"],
            checks=checks,
            directive=(
                "The call contradicts evidenced facts — resolve the evidence first, "
                "then act."
            ),
        )

    # -- tool/schema fit and parameter plausibility ---------------------------
    if schema or params:
        return ToolDecision(
            classification=PAUSE_AND_INVESTIGATE,
            reasons=[*schema, *params],
            checks=checks,
            directive=(
                "Tool/parameters do not obviously match the planned "
                "operation — check the call against the schema."
            ),
        )

    # -- safer capability ----------------------------------------------------
    if intent.safer_alternative and (out_of_scope or tool in WRITE_TOOLS):
        if out_of_scope:
            return ToolDecision(
                classification=PAUSE_AND_INVESTIGATE,
                reasons=[f"target outside the pact scope: {out_of_scope[0]}"],
                checks=checks,
                directive=(
                    f"Safer capability available: {intent.safer_alternative}. "
                    "Or extend the pact deliberately."
                ),
            )
        return ToolDecision(
            classification=ALLOW,
            reasons=[f"safer alternative known: {intent.safer_alternative}"],
            checks=checks,
            directive="Executed.",
        )

    # -- out of scope without a safer alternative ----------------------------
    if out_of_scope:
        return ToolDecision(
            classification=ASK_APPROVAL,
            reasons=[f"target outside the pact scope: {out_of_scope[0]}"],
            checks=checks,
            directive="A scope extension needs the owner's consent.",
        )

    # -- goal reference ------------------------------------------------------
    # Advisory only, by design. A single call cannot prove goal drift: real
    # commands (pytest, git status, ruff, ls) rarely share a term with the
    # contract text, and treating that as suspicion is exactly the
    # over-intervention the audit warns about. Goal drift is a *trajectory*
    # property — it is judged over a streak by the monitor/pause signals
    # (context_drift, scope_drift, progress_stall), not by one call.
    checks["goal_reference"] = goal_ok
    if not goal_ok:
        checks["goal_note"] = goal_reason

    return ToolDecision(
        classification=ALLOW,
        reasons=[],
        checks=checks,
        directive="Executed.",
    )


__all__ = [
    "ALLOW",
    "ALLOW_TOKENS_FOR_RENDER",
    "ASK_APPROVAL",
    "BLOCK",
    "PAUSE_AND_INVESTIGATE",
    "READ_ONLY_TOOLS",
    "WRITE_TOOLS",
    "ToolDecision",
    "ToolIntent",
    "is_destructive_call",
    "judge_tool_call",
]
