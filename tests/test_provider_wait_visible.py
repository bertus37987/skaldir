"""A provider wait must be visible — the user finding behind Paket-2/Hang.

    "/computer-use bietet run_code an, liefert bei einer trivialen Berechnung jedoch
     laenger als 30 Sekunden kein Ergebnis"

Measured cause: ``run_code`` itself returns in ~58 ms (see the trace). The silence came
from the *provider* call: ``timeout_s`` (30) x ``max_retries`` (2 + 1) is a 90 second
window in which nothing at all was said. This asserts the fix: the wait is announced.
"""

from __future__ import annotations

import pytest

from skaldir.llm_provider import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT_S,
    OpenAICompatibleLLM,
    ProviderConfig,
    ProviderTimeoutError,
)


def test_the_total_wait_window_is_the_silence_the_user_saw():
    """30s x 3 attempts = 90s. That number is the bug, and it is now documented."""
    cfg = ProviderConfig(base_url="http://127.0.0.1:1/v1", model="m")
    assert cfg.timeout_s == DEFAULT_TIMEOUT_S == 30.0
    assert cfg.max_retries == DEFAULT_MAX_RETRIES == 2
    total = cfg.effective_total_timeout()
    assert total == 90.0, f"expected the 90s window, got {total}"
    # and it is configurable / bounded, not a mystery
    assert cfg.effective_total_timeout() <= cfg.timeout_s * (cfg.max_retries + 1)


def test_a_slow_provider_announces_the_wait_instead_of_sitting_silent():
    """The fix: the owner is told where the time goes, while it is being spent."""
    import time

    cfg = ProviderConfig(
        base_url="http://127.0.0.1:1/v1", model="m",
        timeout_s=0.2, max_retries=1, backoff_s=0.01,
    )
    seen: list[tuple[str, str]] = []
    provider = OpenAICompatibleLLM(cfg)
    provider.wait_observer = lambda kind, text: seen.append((kind, text))

    started = time.monotonic()
    # the measured type, not a bare Exception: a dead endpoint is a provider timeout
    with pytest.raises(ProviderTimeoutError):
        provider.complete(prompt_id="p1", prompt="hello", tier="fast")
    elapsed = time.monotonic() - started
    assert elapsed >= 0.0

    kinds = [k for k, _ in seen]
    assert "waiting" in kinds, f"the wait was silent: {seen}"
    assert any("waiting for the model" in t for _, t in seen), seen
    assert any(k == "retrying" for k in kinds), (
        f"a retry happened in silence: {seen}"
    )
    assert elapsed < 5.0, f"the wait was neither bounded nor announced ({elapsed:.1f}s)"


def test_a_json_run_stays_one_envelope(tmp_path, monkeypatch):
    """Machine surfaces must not get status noise mixed into the envelope."""

    monkeypatch.setenv("SKALDIR_FORMAT", "json")
    cfg = ProviderConfig(base_url="http://127.0.0.1:1/v1", model="m",
                         timeout_s=0.1, max_retries=0)
    provider = OpenAICompatibleLLM(cfg)
    # no observer installed, non-tty stderr, SKALDIR_FORMAT=json -> nothing is written
    import io

    captured = io.StringIO()
    monkeypatch.setattr("sys.stderr", captured)
    provider._announce("waiting", "waiting for the model")
    assert captured.getvalue() == "", "a JSON run got status noise"


def test_an_observer_failure_never_breaks_the_call():
    cfg = ProviderConfig(base_url="http://127.0.0.1:1/v1", model="m",
                         timeout_s=0.1, max_retries=0)
    provider = OpenAICompatibleLLM(cfg)

    def explode(kind, text):
        raise RuntimeError("the reporter broke")

    provider.wait_observer = explode
    provider._announce("waiting", "still fine")  # must not raise
