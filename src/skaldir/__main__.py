"""``python -m skaldir`` — the module spelling of the CLI (the console script is ``skaldir``).

This is the supported ``-m`` entry point. ``python -m skaldir.cli`` is *not* one:
``cli.py`` defines ``main()`` and deliberately carries no ``__main__`` guard (the module
is imported by nearly every command), so running it that way prints nothing.

``python -m godsv`` keeps working — the deprecated alias package hands this same module
override to the old name — and announces itself on a TTY like the rest of ``godsv``.
"""

if __name__ == "__main__":
    import sys

    try:
        from .cli import main
    except ImportError as exc:  # a bare interpreter without the project venv
        missing = str(getattr(exc, "name", "") or exc)
        sys.stderr.write(
            f"skaldir: cannot import '{missing}' — "
            "this build needs the project venv: source .venv/bin/activate "
            "(or use the skaldir launcher)\n"
        )
        raise SystemExit(1) from None
    raise SystemExit(main())
