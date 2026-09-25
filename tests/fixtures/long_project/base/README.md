# textkit

A tiny text utility library. One function is promised, the CLI is promised, and the
package is promised to survive being packaged.

## What is promised

* `textkit.slugify(text)` returns an ASCII URL slug: lowercase, `[a-z0-9]` only,
  any run of other characters becomes a single `-`, and no leading/trailing `-`.
* `python3 cli.py slugify "<text>"` prints exactly that slug.
* the packaged artifact (`tools/check_build.py`) imports the library from the zip and
  slugifies correctly.

## Status

The test suite in `tests/` is the definition of done. Run it with:

    python3 -m pytest -q
