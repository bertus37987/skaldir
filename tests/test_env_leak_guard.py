"""The env-leak guard: no package module reads a runtime variable directly.

Why this file exists — three times in one session a leaked runtime home in the shell
(``SKALDIR_HOME`` / ``GODSV_HOME`` / ``HERMES_HOME``) made a run write into the **real**
installation instead of its scratch directory. A leaked value is not the whole problem: the
*read* is. As long as ``os.environ.get("HERMES_HOME")`` (or an ``*_LLM_*`` / ``*_VAULT_KEY``
name) can be written anywhere, the fallback the rename introduced can be forgotten in one
place again, and no test notices.

So: every read of a runtime spelling goes through :mod:`skaldir.env`, which owns the two
prefixes (``getenv``/``first_env``/``child_env``) and the host-owned names (``host_getenv``).
This file is the rule, and it is a *scanner* rather than a list of imports:

* :func:`direct_reads` reports every literal ``os.environ…``/``os.getenv(…)`` read of a
  runtime spelling in ``src/skaldir/**``;
* ``env.py`` is the only module that may touch the environment — it *is* the reader;
* :func:`test_the_scanner_bites` feeds the scanner three synthetic offenders, so a scanner
  that stopped matching (a refactor, a regex typo) fails instead of passing silently.
"""

from __future__ import annotations

import re
from pathlib import Path

from skaldir import env, registry

SRC = Path(__file__).resolve().parents[1] / "src" / "skaldir"

#: The spellings this guard owns: the three runtime homes, the vault key, and the LLM
#: layer's variables (including the third-party key names the fallback list names).
#:
#: ``SKALDIR_HOME``/``GODSV_HOME`` are *also* covered by
#: ``tests/test_rename_compat.py::test_no_module_reads_the_two_prefixes_directly_anymore``,
#: which owns the prefixed shapes. ``HERMES_HOME``, the vault key and the LLM names carry no
#: product prefix and were not covered by any guard until this file.
RUNTIME_SPELLINGS: tuple[str, ...] = (
    r"SKALDIR_HOME",
    r"GODSV_HOME",
    r"SKALDIR_VAULT_KEY",
    r"GODSV_VAULT_KEY",
    r"VAULT_KEY",
    r"HERMES_HOME",
    r"HERMES_DESKTOP_HERMES_ROOT",
    r"SKALDIR_LLM_[A-Z0-9_]*",
    r"GODSV_LLM_[A-Z0-9_]*",
    r"OPENAI_[A-Z0-9_]+",
)

_NAMES = "|".join(RUNTIME_SPELLINGS)

#: The read shapes a module could use. A *constructed* name (``os.environ.get(prefix + x)``)
#: is deliberately not matched — the three modules that do that carry their own
#: ``_prefixed()`` reader and are pinned in ``tests/test_rename_compat.py``.
_READ_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"""os\.environ\.get\(\s*["'](?:{_NAMES})["']"""),
    re.compile(rf"""os\.environ\[\s*["'](?:{_NAMES})["']"""),
    re.compile(rf"""os\.getenv\(\s*["'](?:{_NAMES})["']"""),
    re.compile(rf"""["'](?:{_NAMES})["']\s+in\s+os\.environ"""),
)

#: The one module allowed to touch ``os.environ``: it *is* the reader, and every other
#: module imports it. An exemption here is a decision with a reason, never a convenience.
EXEMPT_MODULES: dict[str, str] = {
    "env.py": "the reader itself — the single place the two prefixes and the host names live",
}


def direct_reads(root: Path = SRC) -> list[str]:
    """``path:line`` for every direct read of a runtime spelling under ``root``."""
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if path.name in EXEMPT_MODULES:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in _READ_PATTERNS:
            for match in pattern.finditer(text):
                line = text[: match.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(root.parent.parent)}:{line}")
    return offenders


def test_no_module_reads_a_runtime_variable_directly() -> None:
    """A new direct read is how the fallback (and the scratch home) gets forgotten."""
    offenders = direct_reads()
    assert offenders == [], (
        "these read a runtime variable without skaldir.env, so a leaked value is read in a "
        "second place nobody checks: "
        + ", ".join(offenders)
        + " — use env.getenv()/env.first_env() (SKALDIR_*/GODSV_*) or env.host_getenv() "
        "(HERMES_HOME)"
    )


def test_the_scanner_bites(tmp_path: Path) -> None:
    """Three offenders and one clean module: a scanner that matches nothing fails here."""
    (tmp_path / "bad_env.py").write_text(
        'import os\n\nhome = os.environ.get("HERMES_HOME")\n', encoding="utf-8"
    )
    (tmp_path / "bad_home.py").write_text(
        'import os\n\nruntime = os.getenv("SKALDIR_HOME")\n', encoding="utf-8"
    )
    (tmp_path / "bad_key.py").write_text(
        'import os\n\nkey = os.environ["GODSV_LLM_API_KEY"]\n', encoding="utf-8"
    )
    (tmp_path / "bad_membership.py").write_text(
        'import os\n\nif "SKALDIR_VAULT_KEY" in os.environ:\n    pass\n', encoding="utf-8"
    )
    (tmp_path / "good.py").write_text(
        "from .env import getenv, host_getenv\n\n"
        'home = host_getenv("HERMES_HOME")\n'
        'key = getenv("LLM_API_KEY")\n',
        encoding="utf-8",
    )
    offenders = direct_reads(tmp_path)
    assert len(offenders) == 4, offenders
    assert all("good.py" not in item for item in offenders)
    assert sorted(Path(item.split(":")[0]).name for item in offenders) == [
        "bad_env.py",
        "bad_home.py",
        "bad_key.py",
        "bad_membership.py",
    ]


def test_the_guard_owns_the_spellings_the_other_guards_do_not() -> None:
    """The reason this file exists: no other guard covers HERMES_HOME or the key names."""
    from tests.test_rename_compat import PREFIX_LITERAL_EXCEPTIONS  # noqa: PLC0415

    assert PREFIX_LITERAL_EXCEPTIONS, "the rename guard's exemption list is its own rule"
    for spelling in ("HERMES_HOME", "VAULT_KEY", "SKALDIR_LLM_BASE_URL", "OPENAI_API_KEY"):
        line = f'os.environ.get("{spelling}")'
        assert any(pattern.search(line) for pattern in _READ_PATTERNS), spelling
    assert "env.py" in EXEMPT_MODULES


def test_the_host_reader_reads_literally_and_refuses_a_product_name() -> None:
    """``HERMES_HOME`` has no ``SKALDIR_`` twin — prefixing it would invent a variable."""
    assert env.host_getenv("HERMES_HOME", "fallback", environ={}) == "fallback"
    assert env.host_getenv("HERMES_HOME", environ={"HERMES_HOME": "/tmp/h"}) == "/tmp/h"
    assert env.host_isset("HERMES_HOME", environ={"HERMES_HOME": ""}) is True
    assert env.host_isset("HERMES_HOME", environ={}) is False
    assert "HERMES_HOME" in env.HOST_NAMES
    assert not hasattr(env, "SKALDIR_HERMES_HOME")
    try:
        env.host_getenv("SKALDIR_HOME")
    except KeyError as exc:
        assert "host-owned" in str(exc)
    else:  # pragma: no cover — a product name must never be read as a host name
        raise AssertionError("host_getenv accepted a product variable")


def test_the_runtime_home_is_resolved_through_skaldir_env(monkeypatch, tmp_path) -> None:
    """The routing proof: the value the product uses is the one ``skaldir.env`` handed out.

    The static scanner says "no module reads the environment"; this says the runtime home
    comes from the helper — the two together mean a leaked variable has exactly one door
    into the product.
    """
    for name in ("SKALDIR_HOME", "GODSV_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        registry, "env_host_getenv", lambda token, default=None, **kwargs: str(tmp_path)
    )
    assert registry.default_runtime_dir() == tmp_path / registry.RUNTIME_DIRNAME
    assert "$HERMES_HOME" in registry.runtime_reason(tmp_path / registry.RUNTIME_DIRNAME)


def test_a_leaked_runtime_home_names_the_variable_it_came_from(monkeypatch, tmp_path) -> None:
    """Why the leak hurt: the leaked value *wins*, so it must be the one place read."""
    for name in ("SKALDIR_HOME", "GODSV_HOME", "HERMES_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SKALDIR_HOME", str(tmp_path / "scratch"))
    assert registry.default_runtime_dir() == tmp_path / "scratch"
    assert "SKALDIR_HOME" in registry.runtime_reason(tmp_path / "scratch")
