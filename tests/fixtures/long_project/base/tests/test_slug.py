"""The project's own definition of done for ``slugify``."""

from textkit import slugify


def test_lowercases_and_hyphenates() -> None:
    assert slugify("Hello World") == "hello-world"


def test_strips_punctuation() -> None:
    assert slugify("Hello, World!") == "hello-world"


def test_collapses_repeats_and_trims() -> None:
    assert slugify("  Hello   World  ") == "hello-world"


def test_keeps_digits_and_drops_accents() -> None:
    assert slugify("Café 42") == "cafe-42"
