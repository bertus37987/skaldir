"""AMD→Calc step 3 (VAL-CALC-002): the new uniquely named ODS is machine-checkable.

The builder lives in ``tools/amd_ods_build.py`` (a documented, reproducible script —
the user artifact ``/home/willy/AMD_latest_data.ods`` was also made with odfpy, and
the new file must never touch it). These tests run the REAL builder against fixture
evidence and then read the generated ``content.xml`` back — they do not trust any
return value without opening the file.

Hard requirements asserted here (from VAL-CALC-002):

* numeric cells are ``office:value-type="float"`` (never strings),
* date cells are TYPED (``office:value-type="date"`` with ``office:date-value``)
  — the old file wrongly stored dates as strings,
* explicit source rows exist (URL, retrieval time + timezone, price-type labels
  that separate "last market price" from "regular-session close"),
* formatting exists (bold header style + number/date data styles),
* the output path is UNIQUE (refuses to overwrite) and the user artifact path is
  REFUSED outright.
"""

from __future__ import annotations

import importlib.util
import json
import re
import zipfile
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "amd_ods_build.py"

USER_ARTIFACT = "/home/willy/AMD_latest_data.ods"


def load_builder():
    spec = importlib.util.spec_from_file_location("amd_ods_build", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def evidence(tmp_path: Path) -> dict[str, Path]:
    """A small, honest slice of the saved AMD evidence (fixture data)."""
    rows = [
        {"date": "2026-09-30", "open": 609.89, "high": 615.17, "low": 600.1,
         "close": 611.76, "volume": 16749090},
        {"date": "2026-10-01", "open": 612.7, "high": 619.3, "low": 601.5,
         "close": 615.73, "volume": 17134530},
    ]
    normalized = {
        "source": {
            "provider": "Nasdaq free public API (no key, no auth)",
            "quote_url": "https://api.nasdaq.com/api/quote/AMD/info?assetClass=stocks",
            "chart_url": (
                "https://api.nasdaq.com/api/quote/AMD/chart"
                "?assetclass=stocks&fromdate=2025-09-01&todate=2026-10-02"
            ),
        },
        "identity": {
            "symbol": "AMD",
            "company": "Advanced Micro Devices, Inc. Common Stock",
            "exchange": "NASDAQ-GS",
            "currency": "USD",
        },
        "market_status_at_fetch": "Pre-Market",
        "fetch_time": {
            "local": "2026-10-02T13:02:22+02:00",
            "utc": "2026-10-02T11:02:22Z",
            "timezone": "Europe/Berlin",
        },
        "price_types": {
            "last_market_price_PREMARKET": {
                "value": 624.35,
                "quote_time": "Oct 2, 2026 7:02 AM ET",
                "WARNING": "pre-market trade, NOT a regular-session close",
            },
            "regular_session_close_Oct01": {
                "value": 615.73,
                "quote_time": "Closed at Oct 1, 2026 4:00 PM ET",
            },
            "bid": 624.25,
            "ask": 624.51,
        },
    }
    n_path = tmp_path / "normalized.json"
    n_path.write_text(json.dumps(normalized), encoding="utf-8")
    r_path = tmp_path / "rows.json"
    r_path.write_text(json.dumps(rows), encoding="utf-8")
    return {"normalized": n_path, "rows": r_path}


def build_into(evidence: dict[str, Path], out: Path, **kwargs):
    return load_builder().build(
        normalized=evidence["normalized"],
        rows=evidence["rows"],
        out=out,
        **kwargs,
    )


def content_xml(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        return zf.read("content.xml").decode("utf-8")


def test_output_is_uniquely_named_and_new(evidence, tmp_path):
    """Default naming is AMD_Latest_Stock_<YYYYMMDD-HHMMSS>.ods; existing files refused."""
    builder = load_builder()
    assert builder.default_filename("20261002-130500") == "AMD_Latest_Stock_20261002-130500.ods"

    out = tmp_path / "AMD_Latest_Stock_20261002-130500.ods"
    built = build_into(evidence, out)
    assert built == out.resolve()
    assert out.is_file() and out.stat().st_size > 0

    # A second build to the SAME path must be refused (a new run = a new file).
    with pytest.raises(Exception, match="[Ee]xists"):
        build_into(evidence, out)


def test_numeric_cells_are_typed_floats(evidence, tmp_path):
    out = tmp_path / "AMD_Latest_Stock_x.ods"
    build_into(evidence, out)
    xml = content_xml(out)
    # the close of the last daily bar arrives as a float cell with office:value
    assert 'office:value-type="float"' in xml
    assert 'office:value="615.73"' in xml
    assert 'office:value="17134530"' in xml
    # and none of the evidence NUMBERS is smuggled in as a string cell only
    assert 'office:value-type="string" office:value' not in xml


def test_dates_are_typed_date_cells_not_strings(evidence, tmp_path):
    out = tmp_path / "AMD_Latest_Stock_d.ods"
    build_into(evidence, out)
    xml = content_xml(out)
    assert 'office:value-type="date"' in xml
    assert 'office:date-value="2026-10-01"' in xml
    assert 'office:date-value="2026-09-30"' in xml
    # the daily history must NOT store its dates as string-typed cells — the
    # typed cell may carry cached display text, but the cell TYPE must be date
    rows = re.findall(r"<table:table-row.*?</table:table-row>", xml)
    history = [r for r in rows if 'office:date-value="2026-10-01"' in r]
    assert history
    string_dates = [
        r for r in rows
        if re.search(r'<text:p>2026-10-0[12]</text:p>', r)
        and 'office:value-type="string"' in r
    ]
    assert not string_dates, string_dates[:1]


def test_source_and_retrieval_rows_present(evidence, tmp_path):
    out = tmp_path / "AMD_Latest_Stock_s.ods"
    build_into(evidence, out)
    xml = content_xml(out)
    assert "https://api.nasdaq.com/api/quote/AMD/info?assetClass=stocks" in xml
    assert "api.nasdaq.com/api/quote/AMD/chart" in xml
    assert "Europe/Berlin" in xml
    assert "2026-10-02T13:02:22" in xml       # retrieval time, local
    assert "2026-10-02T11:02:22" in xml       # retrieval time, UTC
    # price-type labels separate last market price from regular close
    assert "last market price" in xml.lower()
    assert "regular-session close" in xml.lower()
    assert "pre-market" in xml.lower()


def test_quote_time_labels_parse_despite_prefix(evidence, tmp_path):
    """Labels like 'Closed at Oct 1, 2026 4:00 PM ET' still yield a TYPED datetime."""
    out = tmp_path / "AMD_Latest_Stock_q.ods"
    build_into(evidence, out)
    xml = content_xml(out)
    assert 'office:date-value="2026-10-01T16:00:00"' in xml   # close quote time
    assert 'office:date-value="2026-10-02T07:02:00"' in xml   # pre-market quote time


def test_datetime_style_renders_padded(evidence, tmp_path):
    """The date-time data style renders padded (long) hours/minutes, not '7:2'."""
    out = tmp_path / "AMD_Latest_Stock_t.ods"
    build_into(evidence, out)
    xml = content_xml(out)
    assert re.search(r'<number:hours number:style="long"/>', xml)
    assert re.search(r'<number:minutes number:style="long"/>', xml)


def test_values_match_evidence_row_for_row(evidence, tmp_path):
    out = tmp_path / "AMD_Latest_Stock_v.ods"
    build_into(evidence, out)
    xml = content_xml(out)
    rows = re.findall(r"<table:table-row.*?</table:table-row>", xml)
    last = [r for r in rows if 'office:date-value="2026-10-01"' in r][0]
    for value in ('"612.7"', '"619.3"', '"601.5"', '"615.73"', '"17134530"'):
        assert f'office:value={value}' in last, value


def test_formatting_present(evidence, tmp_path):
    out = tmp_path / "AMD_Latest_Stock_f.ods"
    build_into(evidence, out)
    xml = content_xml(out)
    assert '<number:date-style' in xml          # typed dates carry a data style
    assert '<number:number-style' in xml        # numeric cells carry a number format
    assert 'fontweight="bold"' in xml or 'font-weight="bold"' in xml


def _column_widths(xml: str) -> dict[str, float]:
    """Map amd-col-* style names to their explicit width in cm."""
    widths: dict[str, float] = {}
    for block in re.findall(r"<style:style style:name=\"amd-col-.*?</style:style>", xml):
        name = re.search(r'style:name="(amd-col-[a-z]+)"', block)
        width = re.search(r'style:column-width="([0-9.]+)cm"', block)
        if name and width:
            widths[name.group(1)] = float(width.group(1))
    return widths


def test_columns_have_explicit_widths(evidence, tmp_path):
    """Calc live acceptance finding: without explicit widths, typed dates render ###.

    Every table must declare its columns via named column styles that carry an
    explicit cm width — wide enough for the FORMATTED value, not the odfpy
    default 0.889in that clips '2026-10-02 07:02' into ###.
    """
    out = tmp_path / "AMD_Latest_Stock_w.ods"
    build_into(evidence, out)
    xml = content_xml(out)
    widths = _column_widths(xml)
    assert widths, "no explicit column-width styles found in content.xml"
    # date-only cells ("2026-10-02") and datetime cells ("2026-10-02 07:02")
    # need their formatted text to fit: at least 2.4 / 3.0 cm respectively.
    assert min(widths.values()) >= 2.0, widths
    assert any(w >= 3.0 for w in widths.values()), widths  # datetime columns
    # the columns must actually be REFERENCED by the tables
    assert xml.count('table:table-column table:style-name="amd-col-') >= 6


def test_datetime_column_is_wider_than_plain_date_column(evidence, tmp_path):
    """'2026-10-02 07:02' is ~60% wider than '2026-10-02' — widths must reflect that.

    The Summary VALUE column carries the typed datetime quote times (the very
    cells that rendered ### in the Calc acceptance: B10/B13/B24/B25); the Daily
    OHLCV date column carries date-only values.
    """
    out = tmp_path / "AMD_Latest_Stock_wd.ods"
    build_into(evidence, out)
    widths = _column_widths(content_xml(out))
    assert widths.get("amd-col-value", 0) >= 3.0, widths
    assert widths.get("amd-col-date", 0) >= 2.4, widths
    assert widths["amd-col-value"] > widths["amd-col-date"]


def test_user_artifact_path_refused(evidence, tmp_path):
    """The builder refuses to write to the user artifact path — outright, not by luck."""
    with pytest.raises(Exception, match="AMD_latest_data"):
        build_into(evidence, Path(USER_ARTIFACT))
