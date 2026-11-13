"""SEC EDGAR and XBRL access for the PS-01 filing auditor.

Fair-access behavior:
- Every request carries the ``SEC_USER_AGENT`` header (override via env).
- Requests are spaced by ``SEC_REQUEST_DELAY`` seconds (default 0.2s).

The public SEC endpoints used here are:
- ``https://data.sec.gov/submissions/CIK##########.json``
- ``https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json``
- ``https://www.sec.gov/Archives/edgar/data/...``
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import threading
import time
import warnings
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from dotenv import load_dotenv

load_dotenv()

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

logger = logging.getLogger(__name__)

__all__ = [
    "AUDITABLE_FORMS",
    "COMPANY_DIRECTORY_URL",
    "DEFAULT_SEC_USER_AGENT",
    "SEC_MAX_ATTEMPTS",
    "SEC_REQUEST_DELAY",
    "SEC_RETRY_BASE_DELAY",
    "SEC_USER_AGENT",
    "TARGET_COMPANIES",
    "cache_only",
    "company_filings",
    "fetch_latest_10k_tables",
    "fetch_xbrl_ground_truth",
    "normalize_cik",
    "resolve_company",
    "search_companies",
    "sec_cache_dir",
]

DEFAULT_SEC_USER_AGENT = "MineArenaAuditor team@bietdavangere.edu"
SEC_USER_AGENT = (
    os.getenv("SEC_USER_AGENT", DEFAULT_SEC_USER_AGENT).strip() or DEFAULT_SEC_USER_AGENT
)

try:
    SEC_REQUEST_DELAY = max(0.0, float(os.getenv("SEC_REQUEST_DELAY", "0.2")))
except ValueError:
    SEC_REQUEST_DELAY = 0.2

try:
    SEC_MAX_ATTEMPTS = max(1, int(os.getenv("SEC_MAX_ATTEMPTS", "4")))
except ValueError:
    SEC_MAX_ATTEMPTS = 4

try:
    SEC_RETRY_BASE_DELAY = max(0.1, float(os.getenv("SEC_RETRY_BASE_DELAY", "1.0")))
except ValueError:
    SEC_RETRY_BASE_DELAY = 1.0

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

# EDGAR responses are cached on disk so an audit can be replayed with the
# network disabled.  The cache is read-through: online requests refresh it and
# a network failure falls back to the last successful response.
_CACHE_ROOT = Path(__file__).resolve().parent.parent / "data" / "sec_cache"


def sec_cache_dir() -> Path:
    """Return the cache directory, allowing tests/deployments to override it."""
    override = (os.getenv("SEC_CACHE_DIR") or "").strip()
    return Path(override).expanduser().resolve() if override else _CACHE_ROOT


def cache_only() -> bool:
    """Return True when callers have requested strict offline mode."""
    return any(
        (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}
        for name in ("SEC_CACHE_ONLY", "TRACE_OFFLINE")
    )


def _cache_tag_from_url(url: str) -> str:
    match = re.search(r"CIK(\d{10})", url, re.IGNORECASE)
    return match.group(1) if match else "sec"


def _cache_path(url: str, cache_tag: str | None = None) -> Path:
    tag = re.sub(r"[^0-9A-Za-z]+", "", str(cache_tag or _cache_tag_from_url(url)))
    tag = tag[:24] or "sec"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return sec_cache_dir() / f"{tag}-{digest}.json"


class _CachedResponse:
    """Small response shim so cached and live bytes are interchangeable."""

    def __init__(
        self,
        *,
        url: str,
        body: bytes,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        fetched_at: str | None = None,
    ) -> None:
        self.url = url
        self.content = body
        self.status_code = status_code
        self.headers = headers or {}
        self.fetched_at = fetched_at or ""

    @property
    def text(self) -> str:
        encoding = "utf-8"
        content_type = self.headers.get("content-type", "")
        match = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
        if match:
            encoding = match.group(1)
        return self.content.decode(encoding, errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code} for {self.url}")


def _read_cache(path: Path) -> _CachedResponse | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        body = base64.b64decode(payload.get("body_b64") or "")
        return _CachedResponse(
            url=str(payload.get("url") or ""),
            body=body,
            status_code=int(payload.get("status_code") or 200),
            headers=dict(payload.get("headers") or {}),
            fetched_at=str(payload.get("fetched_at") or ""),
        )
    except (OSError, ValueError, TypeError):
        return None


def _write_cache(path: Path, response: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "url": str(getattr(response, "url", "")),
            "status_code": int(getattr(response, "status_code", 200)),
            "headers": {
                key: value
                for key, value in dict(getattr(response, "headers", {}) or {}).items()
                if key.lower() in {"content-type", "content-encoding"}
            },
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "body_b64": base64.b64encode(bytes(response.content)).decode("ascii"),
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(path)
    except OSError as error:  # a read-only cache must not break a live audit
        logger.warning("Could not write SEC cache %s: %s", path, error)

TARGET_COMPANIES: dict[str, dict[str, str]] = {
    "Apple": {"ticker": "AAPL", "cik": "0000320193"},
    "Microsoft": {"ticker": "MSFT", "cik": "0000789019"},
    "NVIDIA": {"ticker": "NVDA", "cik": "0001045810"},
    "Tesla": {"ticker": "TSLA", "cik": "0001318605"},
    "Amazon": {"ticker": "AMZN", "cik": "0001018724"},
}

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": SEC_USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
    }
)
_RATE_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0


def normalize_cik(cik: str | int) -> str:
    """Return a 10-digit, zero-padded CIK."""
    digits = re.sub(r"\D", "", str(cik))
    if not digits:
        raise ValueError(f"Invalid CIK: {cik!r}")
    return digits.zfill(10)


def _throttle() -> None:
    global _LAST_REQUEST_AT
    with _RATE_LOCK:
        elapsed = time.monotonic() - _LAST_REQUEST_AT
        wait_for = SEC_REQUEST_DELAY - elapsed
        if wait_for > 0:
            time.sleep(wait_for)
        _LAST_REQUEST_AT = time.monotonic()


def _retry_delay(attempt: int, response: requests.Response | None = None) -> float:
    """Return an exponential backoff delay, honoring ``Retry-After``."""
    delay = SEC_RETRY_BASE_DELAY * (2 ** max(0, attempt - 1))
    if response is not None:
        retry_after = (response.headers.get("Retry-After") or "").strip()
        if retry_after.isdigit():
            delay = max(delay, float(retry_after))
    return min(delay, 10.0)


def _get_network(url: str, *, timeout: float = 30.0) -> requests.Response:
    """GET an SEC resource with retries for dropped connections and 5xx/429.

    SEC EDGAR occasionally closes an otherwise valid connection (especially for
    large 10-K documents). A transient reset should not end an audit run, so
    every request is retried with jitter-free exponential backoff before the
    error is surfaced to the caller.
    """
    last_error: Exception | None = None
    for attempt in range(1, SEC_MAX_ATTEMPTS + 1):
        _throttle()
        try:
            response = _SESSION.get(url, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as error:
            last_error = error
            if attempt >= SEC_MAX_ATTEMPTS:
                raise
            delay = _retry_delay(attempt)
            logger.warning(
                "SEC request failed (%s: %s); retrying in %.1fs (%d/%d).",
                type(error).__name__,
                error,
                delay,
                attempt,
                SEC_MAX_ATTEMPTS,
            )
            time.sleep(delay)
            continue

        if (
            response.status_code in _RETRYABLE_STATUS_CODES
            and attempt < SEC_MAX_ATTEMPTS
        ):
            delay = _retry_delay(attempt, response)
            logger.warning(
                "SEC returned HTTP %s; retrying in %.1fs (%d/%d).",
                response.status_code,
                delay,
                attempt,
                SEC_MAX_ATTEMPTS,
            )
            response.close()
            time.sleep(delay)
            continue

        response.raise_for_status()
        return response

    # The loop either returns a response or raises; this keeps type checkers and
    # future edits honest if the retry policy is changed.
    raise RuntimeError(f"SEC request failed after {SEC_MAX_ATTEMPTS} attempts: {last_error}")


def _get(
    url: str,
    *,
    timeout: float = 30.0,
    cache_tag: str | None = None,
) -> requests.Response | _CachedResponse:
    """Return an SEC response, using the on-disk cache for offline replay.

    Online requests refresh the cache.  If the network is unavailable (or
    ``SEC_CACHE_ONLY``/``TRACE_OFFLINE`` is set), the most recent successful
    response is returned instead.  A cache miss in strict offline mode raises a
    clear error rather than silently fabricating data.
    """
    path = _cache_path(url, cache_tag)
    if cache_only():
        cached = _read_cache(path)
        if cached is None:
            raise RuntimeError(
                f"Offline mode is enabled but no cached SEC response exists for {url}. "
                "Run the audit once while online, or unset SEC_CACHE_ONLY/TRACE_OFFLINE."
            )
        logger.info("Using cached SEC response for %s", url)
        return cached

    try:
        response = _get_network(url, timeout=timeout)
    except (requests.RequestException, RuntimeError) as error:
        cached = _read_cache(path)
        if cached is None:
            raise
        logger.warning(
            "SEC request failed (%s: %s); falling back to cached response from %s.",
            type(error).__name__,
            error,
            cached.fetched_at or "an earlier run",
        )
        return cached

    _write_cache(path, response)
    return response


@lru_cache(maxsize=32)
def _submissions(cik: str) -> dict[str, Any]:
    return _get(f"https://data.sec.gov/submissions/CIK{cik}.json").json()


COMPANY_DIRECTORY_URL = "https://www.sec.gov/files/company_tickers.json"

# Forms whose primary document carries the financial statements the auditor reads.
AUDITABLE_FORMS: tuple[str, ...] = ("10-K", "10-Q", "20-F", "40-F")


@lru_cache(maxsize=1)
def _company_directory() -> list[dict[str, str]]:
    """The SEC's own ticker/CIK directory (the list EDGAR's search box uses)."""
    payload = _get(COMPANY_DIRECTORY_URL, cache_tag="companies").json()
    companies: list[dict[str, str]] = []
    for entry in payload.values() if isinstance(payload, dict) else ():
        try:
            companies.append(
                {
                    "name": str(entry["title"]).strip(),
                    "ticker": str(entry["ticker"]).strip().upper(),
                    "cik": normalize_cik(entry["cik_str"]),
                }
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return companies


def search_companies(query: str, *, limit: int = 8) -> list[dict[str, str]]:
    """Type-ahead over every company EDGAR knows, not only the five configured."""
    q = (query or "").strip().upper()
    if not q:
        return []
    scored: list[tuple[int, int, dict[str, str]]] = []
    for company in _company_directory():
        ticker, name = company["ticker"], company["name"].upper()
        if ticker == q:
            rank = 0
        elif ticker.startswith(q):
            rank = 1
        elif name.startswith(q):
            rank = 2
        elif q in name:
            rank = 3
        else:
            continue
        scored.append((rank, len(ticker), company))
    scored.sort(key=lambda row: (row[0], row[1], row[2]["ticker"]))
    return [company for _, _, company in scored[: max(1, limit)]]


def resolve_company(value: str) -> dict[str, str] | None:
    """A ticker or a CIK for any company EDGAR lists."""
    raw = (value or "").strip()
    if not raw:
        return None
    upper = raw.upper()
    digits = re.sub(r"\D", "", raw)
    for company in _company_directory():
        if company["ticker"] == upper:
            return company
    if digits and len(digits) <= 10:
        wanted = digits.zfill(10)
        for company in _company_directory():
            if company["cik"] == wanted:
                return company
    return None


@lru_cache(maxsize=8)
def _company_facts(cik: str) -> dict[str, Any]:
    return _get(
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
        timeout=60.0,
    ).json()


def _latest_10k_filing(submissions: dict[str, Any]) -> dict[str, Any]:
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    candidates: list[dict[str, Any]] = []

    for index, form in enumerate(forms):
        if form != "10-K":
            continue
        candidates.append(
            {
                "accession_number": recent["accessionNumber"][index],
                "filing_date": recent["filingDate"][index],
                "report_date": recent["reportDate"][index],
                "primary_document": recent["primaryDocument"][index],
            }
        )

    if not candidates:
        raise RuntimeError("No 10-K filings found in the SEC submissions feed.")

    return max(
        candidates,
        key=lambda item: (item["filing_date"], item["accession_number"]),
    )


def _filing_rows(submissions: dict[str, Any], cik: str) -> list[dict[str, Any]]:
    """Recent columnar filings plus the older files the submissions API links to."""
    filings = submissions.get("filings", {})
    blocks = [filings.get("recent", {})]
    for extra in filings.get("files", []) or []:
        name = str(extra.get("name") or "").strip()
        if not name:
            continue
        try:
            blocks.append(
                _get(
                    f"https://data.sec.gov/submissions/{name}", cache_tag=cik
                ).json()
            )
        except Exception:  # noqa: BLE001 - one missing old file must not hide the rest
            continue
    rows: list[dict[str, Any]] = []
    for block in blocks:
        forms = block.get("form", [])
        for index in range(len(forms)):
            rows.append(
                {
                    "form": str(forms[index]).strip().upper(),
                    "accession_number": block["accessionNumber"][index],
                    "filing_date": block["filingDate"][index],
                    "report_date": block["reportDate"][index],
                    "primary_document": block["primaryDocument"][index],
                }
            )
    return rows


def _filing_by_accession(
    submissions: dict[str, Any], cik: str, accession: str
) -> dict[str, Any]:
    wanted = str(accession or "").strip().replace("-", "")
    for row in _filing_rows(submissions, cik):
        if str(row["accession_number"]).replace("-", "") == wanted:
            return row
    raise RuntimeError(f"No SEC filing {accession!r} found for this company.")


def company_filings(
    cik: str, *, form: str | None = None, limit: int = 25, auditable_only: bool = False
) -> list[dict[str, Any]]:
    """Every filing for a CIK, newest first, with the fields the dashboard needs."""
    cik = normalize_cik(cik)
    wanted = (form or "").strip().upper()
    rows: list[dict[str, Any]] = []
    for row in _filing_rows(_submissions(cik), cik):
        base_form = row["form"].split("/")[0]
        if wanted and row["form"] != wanted and row["form"] != f"{wanted}/A":
            continue
        auditable = base_form in AUDITABLE_FORMS
        if auditable_only and not auditable:
            continue
        report = row.get("report_date") or row.get("filing_date") or ""
        rows.append(
            {
                **row,
                "cik": cik,
                "auditable": auditable,
                "fiscal_year": int(str(report)[:4]) if str(report)[:4].isdigit() else None,
            }
        )
    rows.sort(key=lambda item: (item["filing_date"], item["accession_number"]), reverse=True)
    return rows[: max(1, limit)]


def _filing_document_url(cik: str, accession: str, primary_document: str) -> str:
    accession_digits = accession.replace("-", "")
    base = (
        f"https://www.sec.gov/Archives/edgar/data/"
        f"{int(cik)}/{accession_digits}"
    )
    if re.search(r"\.(?:htm|html|xhtml)$", primary_document, re.IGNORECASE):
        return f"{base}/{primary_document}"

    # Some filings point at an XBRL exhibit; fall back to the largest HTML file.
    index = _get(f"{base}/index.json").json()
    items = [
        item
        for item in index.get("directory", {}).get("item", [])
        if re.search(r"\.(?:htm|html|xhtml)$", item.get("name", ""), re.IGNORECASE)
        and not item.get("name", "").startswith("R")
    ]
    if not items:
        raise RuntimeError(f"No HTML primary document found for accession {accession}.")
    items.sort(key=lambda item: int(item.get("size") or 0), reverse=True)
    return f"{base}/{items[0]['name']}"


# ---------------------------------------------------------------------------
# Table extraction
# ---------------------------------------------------------------------------

_CURRENCY_TOKENS = {"$", "€", "£", "¥"}
_INCOME_HEADINGS = (
    "statements of operations",
    "statement of operations",
    "statements of income",
    "statement of income",
    "statements of earnings",
    "statement of earnings",
)
_NUMERIC_CELL_CHARS = set("0123456789$(),.%-− ")


def _clean_text(value: str) -> str:
    value = (
        value.replace("\xa0", " ")
        .replace("\u200b", "")
        .replace("\ufeff", "")
        .replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("\u2212", "-")
        .replace("\ufffd", "'")
    )
    return re.sub(r"\s+", " ", value).strip()


def _looks_numeric(cell: str) -> bool:
    if not cell or not re.search(r"\d", cell):
        return False
    remainder = re.sub(r"\d", "", cell)
    return bool(remainder) and set(remainder) <= _NUMERIC_CELL_CHARS


def _row_cells(row: Any) -> list[str]:
    raw = [
        _clean_text(cell.get_text(" ", strip=True))
        for cell in row.find_all(["td", "th"], recursive=False)
    ]

    merged: list[str] = []
    index = 0
    while index < len(raw):
        cell = raw[index]
        if cell in _CURRENCY_TOKENS:
            lookahead = index + 1
            while lookahead < len(raw) and not raw[lookahead]:
                lookahead += 1
            if lookahead < len(raw):
                merged.append(cell + raw[lookahead])
                index = lookahead + 1
                continue
        if cell:
            merged.append(cell)
        index += 1

    # Some filings put a negative value's closing parenthesis in a separate
    # table cell (``( 11,047`` + ``)``). Re-join those fragments so each value
    # occupies exactly one column.
    compacted: list[str] = []
    for cell in merged:
        if (
            cell == ")"
            and compacted
            and "(" in compacted[-1]
            and re.search(r"\d", compacted[-1])
            and not compacted[-1].rstrip().endswith(")")
        ):
            compacted[-1] = f"{compacted[-1]} )"
        else:
            compacted.append(cell)
    return compacted


def _table_rows(table: Any) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.find_all("tr"):
        cells = _row_cells(row)
        if not cells:
            continue
        if len(cells) == 1:
            lowered = cells[0].lower()
            if lowered == "table of contents" or re.fullmatch(r"\d+", cells[0]):
                continue
        rows.append(cells)
    return rows


def _preceding_text(table: Any, max_chars: int = 1800) -> str:
    parts: list[str] = []
    total = 0
    for node in table.find_all_previous(string=True):
        text = _clean_text(str(node))
        if not text:
            continue
        parts.append(text)
        total += len(text) + 1
        if total >= max_chars:
            break
    return " ".join(reversed(parts))


def _row_to_markdown(cells: list[str], max_cols: int) -> str:
    padded = (cells + [""] * max_cols)[:max_cols]
    cleaned = [cell.replace("|", "\\|")[:200] for cell in padded]
    return "| " + " | ".join(cleaned) + " |"


_DATE_HEADER_RE = re.compile(
    r"^(?:"
    r"(?:years?|fiscal\s+years?)\s+ended\b"
    r"|(?:as\s+of\s+)?"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\s+\d{1,2},?\s*$"
    r")",
    re.IGNORECASE,
)
_UNIT_HEADER_RE = re.compile(
    r"^\(?\s*(?:(?:amounts?\s+)?in\s+|\$\s*in\s+)?"
    r"(?:thousands|millions|billions)(?:\s*,[^)]*)?\)?$",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def _is_date_series(cells: list[str]) -> bool:
    values = [cell for cell in cells if cell]
    return bool(values) and all(_YEAR_RE.search(cell) for cell in values)


def _normalize_header_rows(rows: list[list[str]]) -> list[list[str]]:
    """Turn common filing header layouts into one compact header row."""
    rows = [list(row) for row in rows]

    # Drop a leading "(In millions)" / "($ in millions)" unit row.
    while rows and len(rows[0]) == 1 and _UNIT_HEADER_RE.match(rows[0][0]):
        rows.pop(0)

    if not rows:
        return rows

    first = rows[0]
    if first and _DATE_HEADER_RE.match(first[0]):
        prefix = first[0].rstrip(" ,")
        if len(first) == 1 and len(rows) > 1 and _is_date_series(rows[1]):
            rows[0] = ["Item"] + [
                f"{prefix} {cell}".strip() for cell in rows[1]
            ]
            del rows[1]
        elif len(first) > 1:
            rows[0] = ["Item"] + [
                f"{prefix} {cell}".strip() if _YEAR_RE.search(cell) else cell
                for cell in first[1:]
            ]

    return rows


def _table_to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""

    rows = _normalize_header_rows(rows)
    max_cols = min(max(len(row) for row in rows), 12)
    rows = [row[:max_cols] for row in rows]

    header = rows[0]
    if header and header[0].lower() != "item" and (
        len(header) < max_cols or _is_date_series(header)
    ):
        rows[0] = ["Item"] + header[: max_cols - 1]

    lines = [
        _row_to_markdown(rows[0], max_cols),
        "| " + " | ".join(["---"] * max_cols) + " |",
    ]
    lines.extend(_row_to_markdown(row, max_cols) for row in rows[1:])
    return "\n".join(lines)


def _income_score(text: str, context: str) -> int:
    score = 0
    if "net income" in text or "net loss" in text:
        score += 4
    if "revenue" in text or "net sales" in text:
        score += 2
    if "gross profit" in text:
        score += 2
    if "cost of revenue" in text or "cost of sales" in text:
        score += 1
    if "operating income" in text:
        score += 1
    if any(heading in context for heading in _INCOME_HEADINGS):
        score += 2
    return score if score >= 7 else 0


def _balance_score(text: str, context: str) -> int:
    score = 0
    if "total assets" in text:
        score += 4
    if "total liabilities" in text:
        score += 3
    if "shareholders' equity" in text or "stockholders' equity" in text:
        score += 3
    if "total liabilities and" in text:
        score += 1
    if "balance sheet" in context:
        score += 1
    return score if score >= 8 else 0


def _compact_alpha(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.lower())


def _cashflow_score(text: str, context: str) -> int:
    score = 0
    operating_phrases = (
        "cash flows from operating activities",
        "cash provided by (used in): operating activities",
        "net cash provided by (used in) operating activities",
        "net cash provided by operating activities",
        "net cash used in operating activities",
        "net cash generated by operating activities",
        "cash generated by operating activities",
        "net cash from operating activities",
        "net cash from operations",
        "cash flows from operations",
        "cash provided by operations",
    )
    if any(phrase in text for phrase in operating_phrases):
        score += 4
    if "operating activities" in text or "net cash from operations" in text:
        score += 2
    if "investing activities" in text or re.search(r"\binvesting\b", text):
        score += 1
    if "financing activities" in text or re.search(r"\bfinancing\b", text):
        score += 1

    # A full statement includes opening/closing cash and the reconciliation,
    # which distinguishes it from liquidity-summary tables in the MD&A.
    if "beginning of period" in text and "end of period" in text:
        score += 3
    if "adjustments to reconcile" in text:
        score += 2

    compact_context = _compact_alpha(context)
    if (
        "statementsofcashflows" in compact_context
        or "cashflowsstatements" in compact_context
        or "cashflowstatement" in compact_context
    ):
        score += 3
    if "cash flows" in context:
        score += 1

    return score if score >= 7 else 0


def _segment_score(text: str, context: str) -> int:
    score = 0
    if "segment" in text or "segment" in context:
        score += 2
    if "revenue" in text or "net sales" in text:
        score += 2
    if "operating income" in text:
        score += 1
    return score if score >= 4 else 0


def _derive_title(context: str, category: str) -> str:
    compact = _compact_alpha(context)
    patterns = (
        ("statementsofoperations", "Consolidated Statements of Operations"),
        ("statementsofincome", "Consolidated Statements of Income"),
        ("incomestatements", "Consolidated Statements of Income"),
        ("statementsofearnings", "Consolidated Statements of Earnings"),
        ("balancesheets", "Consolidated Balance Sheets"),
        ("balancesheet", "Consolidated Balance Sheets"),
        ("statementsofcashflows", "Consolidated Statements of Cash Flows"),
        ("cashflowsstatements", "Consolidated Statements of Cash Flows"),
        ("cashflowstatement", "Consolidated Statements of Cash Flows"),
    )
    matches: list[tuple[int, str]] = []
    for pattern, title in patterns:
        matches.extend(
            (match.start(), title) for match in re.finditer(pattern, compact)
        )
    if matches:
        return max(matches, key=lambda item: item[0])[1]

    return {
        "income": "Income Statement",
        "balance": "Balance Sheet",
        "cashflow": "Cash Flow Statement",
        "segment": "Segment Information",
    }.get(category, "Financial Table")


@dataclass
class _TableCandidate:
    order: int
    category: str
    title: str
    markdown: str
    score: int
    numeric_count: int
    text: str


def _extract_financial_tables(soup: BeautifulSoup) -> list[dict[str, Any]]:
    candidates: list[_TableCandidate] = []
    seen_hashes: set[str] = set()

    for order, table in enumerate(soup.find_all("table")):
        rows = _table_rows(table)
        if len(rows) < 3:
            continue

        numeric_count = sum(
            1 for row in rows for cell in row if _looks_numeric(cell)
        )
        if numeric_count < 3:
            continue

        text = " ".join(" ".join(row) for row in rows)
        lowered_text = text.lower()
        context = _preceding_text(table)
        lowered_context = context.lower()

        scores = {
            "income": _income_score(lowered_text, lowered_context),
            "balance": _balance_score(lowered_text, lowered_context),
            "cashflow": _cashflow_score(lowered_text, lowered_context),
            "segment": _segment_score(lowered_text, lowered_context),
        }
        category = max(scores, key=scores.get)
        if scores[category] == 0:
            continue

        markdown = _table_to_markdown(rows)
        if not markdown:
            continue

        digest = hashlib.sha1(
            re.sub(r"\s+", " ", markdown).encode("utf-8")
        ).hexdigest()
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)

        candidates.append(
            _TableCandidate(
                order=order,
                category=category,
                title=_derive_title(context, category),
                markdown=markdown,
                score=scores[category],
                numeric_count=numeric_count,
                text=text,
            )
        )

    selected: list[_TableCandidate] = []
    for category, limit in (("income", 1), ("balance", 1), ("cashflow", 1), ("segment", 2)):
        matches = sorted(
            (candidate for candidate in candidates if candidate.category == category),
            key=lambda candidate: (
                -candidate.score,
                -candidate.numeric_count,
                candidate.order,
            ),
        )
        selected.extend(matches[:limit])

    return [
        {
            "title": candidate.title,
            "category": candidate.category,
            "markdown": candidate.markdown,
            "row_count": candidate.markdown.count("\n") + 1,
            "char_count": len(candidate.markdown),
        }
        for candidate in selected
    ]


# ---------------------------------------------------------------------------
# Public EDGAR API
# ---------------------------------------------------------------------------


def fetch_latest_10k_tables(cik: str, accession: str | None = None) -> dict[str, Any]:
    """Fetch a 10-K (latest, or a specific EDGAR accession) and return its statement tables."""
    cik = normalize_cik(cik)
    submissions = _submissions(cik)
    filing = (
        _filing_by_accession(submissions, cik, accession)
        if accession
        else _latest_10k_filing(submissions)
    )

    filing_url = _filing_document_url(
        cik,
        filing["accession_number"],
        filing["primary_document"],
    )
    html = _get(filing_url, timeout=60.0, cache_tag=cik).content
    soup = BeautifulSoup(html, "lxml")
    tables = _extract_financial_tables(soup)

    if not tables:
        raise RuntimeError(
            f"No financial statement tables matched the expected statements for CIK {cik}."
        )

    report_date = filing["report_date"] or filing["filing_date"]
    return {
        "company_name": submissions.get("name", ""),
        "cik": cik,
        "accession_number": filing["accession_number"],
        "filing_date": filing["filing_date"],
        "report_date": report_date,
        "fiscal_year": f"FY{report_date[:4]}",
        "primary_document": filing["primary_document"],
        "filing_url": filing_url,
        "tables": tables,
    }


# ---------------------------------------------------------------------------
# XBRL ground truth
# ---------------------------------------------------------------------------

_GAAP_TAG_ALIASES: dict[str, list[str]] = {
    "Revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ],
    "Net Income": ["NetIncomeLoss", "ProfitLoss"],
    "Total Assets": ["Assets"],
    "Total Liabilities": ["Liabilities"],
    "Shareholders' Equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "Operating Cash Flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "Cash and Cash Equivalents": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "Research and Development Expense": ["ResearchAndDevelopmentExpense"],
    "Operating Income": ["OperatingIncomeLoss"],
}

_DURATION_ROLES = {
    "Revenue",
    "Net Income",
    "Operating Cash Flow",
    "Research and Development Expense",
    "Operating Income",
}


def _annual_entries(
    facts: dict[str, Any],
    tag: str,
    accession: str,
    *,
    duration: bool,
) -> list[dict[str, Any]]:
    node = facts.get(tag)
    if not node:
        return []

    entries = node.get("units", {}).get("USD")
    if not entries:
        return []

    annual: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("form") != "10-K" or entry.get("fp") != "FY":
            continue
        if entry.get("accn") != accession:
            continue

        end = entry.get("end")
        start = entry.get("start")
        if not end:
            continue

        if duration:
            if not start:
                continue
            try:
                days = (date.fromisoformat(end) - date.fromisoformat(start)).days
            except ValueError:
                continue
            if not 300 <= days <= 400:
                continue
        elif start:
            continue

        if entry.get("val") is None:
            continue
        annual.append(entry)

    # One value per period-end; keep the entry filed latest.
    by_end: dict[str, dict[str, Any]] = {}
    for entry in annual:
        end = entry["end"]
        current = by_end.get(end)
        if current is None or (entry.get("filed", ""), entry.get("accn", "")) > (
            current.get("filed", ""),
            current.get("accn", ""),
        ):
            by_end[end] = entry

    return [by_end[end] for end in sorted(by_end, reverse=True)]


def _pick_series(
    facts: dict[str, Any],
    aliases: Iterable[str],
    accession: str,
    *,
    duration: bool,
) -> tuple[str, list[dict[str, Any]]] | tuple[None, list[dict[str, Any]]]:
    candidates: list[tuple[str, list[dict[str, Any]]]] = []
    for tag in aliases:
        entries = _annual_entries(
            facts,
            tag,
            accession,
            duration=duration,
        )
        if entries:
            candidates.append((tag, entries))

    if not candidates:
        return None, []

    # Prefer the series whose latest period is newest; alias order breaks ties.
    return max(candidates, key=lambda item: item[1][0]["end"])


def _normalized_metric(
    tag: str,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    current = entries[0]
    prior = entries[1] if len(entries) > 1 else None

    current_value = round(current["val"] / 1_000_000, 6)
    prior_value = (
        round(prior["val"] / 1_000_000, 6)
        if prior is not None
        else None
    )

    return {
        "tag": tag,
        "value": current_value,
        "prior_year_value": prior_value,
        "unit": "USD Millions",
        "period": f"FY{current['end'][:4]}",
        "prior_period": f"FY{prior['end'][:4]}" if prior else None,
        "end": current["end"],
        "start": current.get("start"),
        "prior_end": prior["end"] if prior else None,
        "prior_start": prior.get("start") if prior else None,
        "raw_value": current["val"],
        "raw_unit": "USD",
        "accession_number": current.get("accn"),
        "filed": current.get("filed"),
        "fy": current.get("fy"),
        "fp": current.get("fp"),
        "frame": current.get("frame"),
    }


def fetch_xbrl_ground_truth(cik: str, accession: str | None = None) -> dict[str, Any]:
    """Return US-GAAP facts for the latest annual 10-K, in USD millions."""
    cik = normalize_cik(cik)
    submissions = _submissions(cik)
    filing = _latest_10k_filing(submissions) if accession is None else None
    if accession is None and filing is not None:
        accession = filing["accession_number"]
        filing_date = filing["filing_date"]
        report_date = filing["report_date"]
    else:
        # Find filing details for given accession
        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        report_dates = recent.get("reportDate", [])
        filing_date = None
        report_date = None
        for i, acc in enumerate(accessions):
            if acc == accession or acc.replace("-", "") == str(accession).replace("-", ""):
                filing_date = filing_dates[i] if i < len(filing_dates) else None
                report_date = report_dates[i] if i < len(report_dates) else None
                break
        if filing_date is None:
            filing_date = report_date

    facts = _company_facts(cik).get("facts", {}).get("us-gaap", {})
    if not facts:
        raise RuntimeError(f"No US-GAAP facts returned for CIK {cik}.")

    metrics: dict[str, dict[str, Any]] = {}
    for role, aliases in _GAAP_TAG_ALIASES.items():
        tag, entries = _pick_series(
            facts,
            aliases,
            accession,
            duration=role in _DURATION_ROLES,
        )
        if tag and entries:
            metrics[role] = _normalized_metric(tag, entries)

    # If R&D is not reported, Operating Income is the required eighth metric.
    if "Research and Development Expense" not in metrics and "Operating Income" in metrics:
        metrics["Research and Development Expense"] = metrics.pop("Operating Income")
        metrics["Research and Development Expense"]["reported_as"] = "Operating Income"
    elif "Research and Development Expense" in metrics:
        metrics.pop("Operating Income", None)

    # Some filers show "Total liabilities and equity" without a Liabilities tag.
    if "Total Liabilities" not in metrics:
        assets = metrics.get("Total Assets")
        equity = metrics.get("Shareholders' Equity")
        if assets and equity:
            prior_value = None
            if (
                assets.get("prior_year_value") is not None
                and equity.get("prior_year_value") is not None
            ):
                prior_value = round(
                    assets["prior_year_value"] - equity["prior_year_value"],
                    6,
                )
            metrics["Total Liabilities"] = {
                "tag": "Computed as Assets - Shareholders' Equity",
                "value": round(assets["value"] - equity["value"], 6),
                "prior_year_value": prior_value,
                "unit": "USD Millions",
                "period": assets["period"],
                "prior_period": assets.get("prior_period"),
                "end": assets["end"],
                "start": None,
                "prior_end": assets.get("prior_end"),
                "prior_start": None,
                "raw_value": assets["raw_value"] - equity["raw_value"],
                "raw_unit": "USD",
                "accession_number": accession,
                "filed": filing_date,
                "fy": assets.get("fy"),
                "fp": "FY",
                "frame": None,
                "computed": True,
            }

    if not metrics:
        raise RuntimeError(f"No target US-GAAP metrics found for CIK {cik}.")

    period_end = max(metric["end"] for metric in metrics.values())
    return {
        "company_name": _company_facts(cik).get("entityName", submissions.get("name", "")),
        "cik": cik,
        "fiscal_year": f"FY{period_end[:4]}",
        "period_end": period_end,
        "accession_number": accession,
        "filing_date": filing_date,
        "source": "SEC XBRL company facts (US-GAAP), normalized to USD millions",
        "unit": "USD Millions",
        "metrics": metrics,
    }
