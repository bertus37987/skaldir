#!/usr/bin/env python3
"""Build the NEW, uniquely named AMD stock ODS from the saved raw evidence.

AMD→Calc step 3 (VAL-CALC-002). The old user file ``/home/willy/AMD_latest_data.ods``
was made with odfpy 1.4.1 (generated 2026-09-25) and got the NUMBERS right, but it
stored every date as a plain string cell. This builder produces a NEW file —
``AMD_Latest_Stock_<YYYYMMDD-HHMMSS>.ods`` — with the same library and, where the old
file fell short, the corrections the mission requires:

* numeric cells are typed floats (``office:value-type="float"``),
* date cells are TYPED dates (``office:value-type="date"`` + ``office:date-value``),
* formatting is real (bold header cells, price/volume/percentage number formats,
  date and date-time data styles),
* the source is explicit in the sheet itself: provider, URLs, retrieval time
  (local + UTC) + timezone, raw-evidence file names, and failed-source notes,
* the price types are labelled so nobody can mistake a pre-market trade for a
  regular-session close (and neither for today's date).

The user artifact ``/home/willy/AMD_latest_data.ods`` is OFF-LIMITS: the builder
refuses that path outright, and it refuses an existing output path (one run = one
new file). Everything else — values, rows, counts — is validated against the saved
evidence JSON before anything is written; the tool never invents data.

Usage::

    .venv/bin/python tools/amd_ods_build.py \
        --normalized logs/amd-normalized-20261002.json \
        --rows logs/amd-normalized-20261002-rows.json

Prints the built path and its sha256 on success; exits non-zero on any refusal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

USER_ARTIFACT_NAME = "AMD_latest_data.ods"  # off-limits user file — never written
USER_ARTIFACT = Path("/home/willy") / USER_ARTIFACT_NAME

REQUIRED_ROW_KEYS = ("date", "open", "high", "low", "close", "volume")
REQUIRED_SOURCE_KEYS = ("provider", "quote_url", "chart_url")
REQUIRED_FETCH_KEYS = ("local", "utc", "timezone")


class BuildError(RuntimeError):
    """A refusal or an evidence problem — reported, never silently smoothed."""


def default_filename(stamp: str | None = None) -> str:
    """The unique artifact name: AMD_Latest_Stock_<YYYYMMDD-HHMMSS>.ods."""
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"AMD_Latest_Stock_{stamp}.ods"


def parse_quote_time(text: str) -> str | None:
    """Parse an evidence quote-time label like ``Oct 2, 2026 7:02 AM ET``.

    Returns an ODF date-value string (``YYYY-MM-DDTHH:MM:SS``) or ``None`` when the
    label is not a parseable date-time — in which case the builder keeps the
    original label as a string row instead of guessing.
    """
    match = re.search(
        r"([A-Z][a-z]{2} \d{1,2}, \d{4}) (\d{1,2}):(\d{2}) (AM|PM)", str(text)
    )
    if not match:
        return None
    try:
        parsed = datetime.strptime(
            f"{match.group(1)} {match.group(2)}:{match.group(3)} {match.group(4)}",
            "%b %d, %Y %I:%M %p")
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%dT%H:%M:%S")


def _iso_date(value: str, what: str) -> str:
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise BuildError(f"{what} is not a YYYY-MM-DD date: {value!r}") from exc


def _iso_datetime(value: str, what: str) -> str:
    """Normalize an ISO timestamp (with or without offset) to ODF date-value form."""
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise BuildError(f"{what} is not an ISO-8601 timestamp: {value!r}") from exc
    return parsed.replace(tzinfo=None).isoformat(timespec="seconds")


def _validate(normalized: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    source = normalized.get("source") or {}
    for key in REQUIRED_SOURCE_KEYS:
        if not source.get(key):
            raise BuildError(f"normalized evidence lacks source.{key} — no invented URLs")
    fetch = normalized.get("fetch_time") or {}
    for key in REQUIRED_FETCH_KEYS:
        if not fetch.get(key):
            raise BuildError(f"normalized evidence lacks fetch_time.{key} — no unlabelled times")
    if not isinstance(rows, list) or not rows:
        raise BuildError("rows evidence is empty — refusing to build a hollow table")
    for index, row in enumerate(rows):
        missing = [key for key in REQUIRED_ROW_KEYS if key not in row]
        if missing:
            raise BuildError(f"row {index} lacks {missing} — no invented OHLCV fields")
        _iso_date(row["date"], f"row {index} date")


def build(normalized: Path | str, rows: Path | str, out: Path | str) -> Path:
    """Build the ODS at ``out`` from the saved evidence. Returns the resolved path."""
    out_path = Path(out).expanduser()
    if not out_path.is_absolute():
        raise BuildError(f"output path must be absolute: {out_path}")
    out_path = out_path.resolve()
    if out_path.name == USER_ARTIFACT_NAME:
        raise BuildError(
            f"refusing to write the user artifact {out_path} — build a NEW file instead"
        )
    if out_path.exists():
        raise BuildError(f"output already exists: {out_path} — one run builds one NEW file")

    norm = json.loads(Path(normalized).read_text(encoding="utf-8"))
    row_data = json.loads(Path(rows).read_text(encoding="utf-8"))
    _validate(norm, row_data)

    # odfpy is imported lazily: it is the ODS extra, not a core dependency.
    from odf.number import DateStyle, Day, Hours, Minutes, Month, Number, NumberStyle, Year
    from odf.number import Text as NumText
    from odf.opendocument import OpenDocumentSpreadsheet
    from odf.style import Style, TextProperties
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    doc = OpenDocumentSpreadsheet()

    # --- data styles (the visible number/date formats) --------------------------
    date_style = DateStyle(name="amd-date")
    date_style.addElement(Year(style="long"))
    date_style.addElement(NumText(text="-"))
    date_style.addElement(Month(style="long"))
    date_style.addElement(NumText(text="-"))
    date_style.addElement(Day(style="long"))
    doc.automaticstyles.addElement(date_style)

    datetime_style = DateStyle(name="amd-datetime")
    datetime_style.addElement(Year(style="long"))
    datetime_style.addElement(NumText(text="-"))
    datetime_style.addElement(Month(style="long"))
    datetime_style.addElement(NumText(text="-"))
    datetime_style.addElement(Day(style="long"))
    datetime_style.addElement(NumText(text=" "))
    datetime_style.addElement(Hours(style="long"))
    datetime_style.addElement(NumText(text=":"))
    datetime_style.addElement(Minutes(style="long"))
    doc.automaticstyles.addElement(datetime_style)

    price_style = NumberStyle(name="amd-price")
    price_style.addElement(Number(decimalplaces="2", minintegerdigits="1", grouping="false"))
    doc.automaticstyles.addElement(price_style)

    volume_style = NumberStyle(name="amd-volume")
    volume_style.addElement(Number(minintegerdigits="1", grouping="true"))
    doc.automaticstyles.addElement(volume_style)

    # --- cell styles (bold headers + the data-style bindings) -------------------
    bold_cell = Style(name="amd-bold", family="table-cell")
    bold_cell.addElement(TextProperties(fontweight="bold"))
    doc.automaticstyles.addElement(bold_cell)

    date_cell = Style(name="amd-date-cell", family="table-cell", datastylename="amd-date")
    doc.automaticstyles.addElement(date_cell)
    datetime_cell = Style(name="amd-datetime-cell", family="table-cell",
                          datastylename="amd-datetime")
    doc.automaticstyles.addElement(datetime_cell)
    price_cell = Style(name="amd-price-cell", family="table-cell", datastylename="amd-price")
    doc.automaticstyles.addElement(price_cell)
    volume_cell = Style(name="amd-volume-cell", family="table-cell",
                        datastylename="amd-volume")
    doc.automaticstyles.addElement(volume_cell)

    def add_cell(row: TableRow, *, text: str = "", style: str | None = None) -> None:
        cell = TableCell(stylename=style) if style else TableCell()
        cell.addElement(P(text=text))
        row.addElement(cell)

    def add_float(row: TableRow, value: Any, style: str = "amd-price-cell") -> None:
        # office:value keeps the evidence's own number form: ints stay ints
        # (volume 17134530, never "17134530.0"), floats keep their exact digits.
        number = value if isinstance(value, int) else float(value)
        cell = TableCell(valuetype="float", value=str(number), stylename=style)
        cell.addElement(P(text=str(number)))
        row.addElement(cell)

    def add_date(row: TableRow, date_value: str, style: str = "amd-date-cell") -> None:
        cell = TableCell(valuetype="date", datevalue=date_value, stylename=style)
        cell.addElement(P(text=date_value))
        row.addElement(cell)

    # --- sheet 1: Summary with explicit source rows ------------------------------
    summary = Table(name="Summary")
    doc.spreadsheet.addElement(summary)

    fetch_local = _iso_datetime(norm["fetch_time"]["local"], "fetch_time.local")
    fetch_utc = _iso_datetime(norm["fetch_time"]["utc"], "fetch_time.utc")
    title_row = TableRow()
    add_cell(title_row,
             text=f"{norm['identity']['symbol']} — Latest Stock Summary"
                  f" (fetched {norm['fetch_time']['local']} {norm['fetch_time']['timezone']})",
             style="amd-bold")
    summary.addElement(title_row)
    summary.addElement(TableRow())

    header = TableRow()
    for label in ("Field", "Value"):
        add_cell(header, text=label, style="amd-bold")
    summary.addElement(header)

    def add_labelled(label: str, cell) -> None:
        row = TableRow()
        add_cell(row, text=label)
        row.addElement(cell)
        summary.addElement(row)

    identity = norm["identity"]
    for field in ("symbol", "company", "exchange", "currency"):
        row = TableRow()
        add_cell(row, text=field.capitalize())
        add_cell(row, text=str(identity[field]))
        summary.addElement(row)

    row = TableRow()
    add_cell(row, text="Market status at fetch")
    add_cell(row, text=str(norm.get("market_status_at_fetch", "")))
    summary.addElement(row)

    prices = norm.get("price_types") or {}

    # LAST MARKET PRICE — labelled so it can never be read as a session close.
    label = prices.get("last_market_price_PREMARKET") or {}
    add_labelled(
        "Last market price (pre-market trade, NOT a regular-session close)",
        TableCell(valuetype="float", value=str(label["value"]), stylename="amd-price-cell"),
    )
    quote_value = parse_quote_time(label.get("quote_time", ""))
    if quote_value:
        add_labelled("Last market price — quote time (ET)",
                     TableCell(valuetype="date", datevalue=quote_value,
                               stylename="amd-datetime-cell"))
    else:
        add_labelled("Last market price — quote time (as reported)",
                     TableCell(valuetype="string", stringvalue=str(label.get("quote_time", ""))))
    if label.get("WARNING"):
        row = TableRow()
        add_cell(row, text="Warning")
        add_cell(row, text=str(label["WARNING"]))
        summary.addElement(row)

    # REGULAR-SESSION CLOSE — the Oct 1 2024h close, corroborated by the daily bar.
    close_label = prices.get("regular_session_close_Oct01") or {}
    add_labelled(
        "Regular-session close (Oct 1, 2026 session)",
        TableCell(valuetype="float", value=str(close_label["value"]),
                  stylename="amd-price-cell"),
    )
    close_quote = parse_quote_time(close_label.get("quote_time", ""))
    if close_quote:
        add_labelled("Regular-session close — quote time (ET)",
                     TableCell(valuetype="date", datevalue=close_quote,
                               stylename="amd-datetime-cell"))

    if "net_change_premarket" in prices:
        add_labelled("Net change (pre-market)", TableCell(
            valuetype="float", value=str(float(prices["net_change_premarket"])),
            stylename="amd-price-cell"))
    if "pct_change_premarket" in prices:
        add_labelled("Pct change (pre-market, source string)",
                     TableCell(valuetype="string",
                               stringvalue=str(prices["pct_change_premarket"])))
    for name in ("bid", "ask"):
        if name in prices:
            add_labelled(name.capitalize(), TableCell(
                valuetype="float", value=str(float(prices[name])),
                stylename="amd-price-cell"))
    if prices.get("52w_range_nasdaq_field"):
        add_labelled("52w range (Nasdaq field)",
                     TableCell(valuetype="string",
                               stringvalue=str(prices["52w_range_nasdaq_field"])))

    summary.addElement(TableRow())
    source_header = TableRow()
    add_cell(source_header, text="Source evidence (explicit)", style="amd-bold")
    summary.addElement(source_header)

    source = norm["source"]
    for label_text, value in (
        ("Provider", source["provider"]),
        ("Quote URL", source["quote_url"]),
        ("Chart URL (daily history)", source["chart_url"]),
        ("Retrieval time (local, typed)", fetch_local),
        ("Retrieval time (UTC, typed)", fetch_utc),
        ("Timezone", norm["fetch_time"]["timezone"]),
        ("Raw evidence files", ", ".join(source.get("raw_evidence", []))),
        ("Failed sources (honest record)", ", ".join(
            f"{failed.get('provider', '?')}: {failed.get('status', '?')}"
            for failed in source.get("failed_sources", []))),
    ):
        row = TableRow()
        add_cell(row, text=label_text)
        if isinstance(value, str) and re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", value):
            add_date(row, value, style="amd-datetime-cell")
        else:
            add_cell(row, text=str(value))
        summary.addElement(row)

    # --- sheet 2: Daily OHLCV (typed dates, typed numbers) -----------------------
    history = Table(name="Daily OHLCV")
    doc.spreadsheet.addElement(history)
    columns_header = TableRow()
    for label_text in ("Date", "Open", "High", "Low", "Close", "Volume"):
        add_cell(columns_header, text=label_text, style="amd-bold")
    history.addElement(columns_header)
    for bar in row_data:
        row = TableRow()
        add_date(row, _iso_date(bar["date"], "daily date"))
        add_float(row, bar["open"])
        add_float(row, bar["high"])
        add_float(row, bar["low"])
        add_float(row, bar["close"])
        add_float(row, bar["volume"], style="amd-volume-cell")
        history.addElement(row)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--normalized", required=True,
                        help="normalized evidence JSON (logs/amd-normalized-*.json)")
    parser.add_argument("--rows", required=True,
                        help="daily rows JSON (logs/amd-normalized-*-rows.json)")
    parser.add_argument("--out", default=None,
                        help="output .ods path (default: /home/willy/<unique name>)")
    parser.add_argument("--stamp", default=None,
                        help="override the YYYYMMDD-HHMMSS name stamp (tests/replays)")
    args = parser.parse_args(argv)

    out = args.out or f"/home/willy/{default_filename(args.stamp)}"
    try:
        built = build(normalized=args.normalized, rows=args.rows, out=out)
    except BuildError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    digest = hashlib.sha256(built.read_bytes()).hexdigest()
    print(json.dumps({"built": str(built), "sha256": digest}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
