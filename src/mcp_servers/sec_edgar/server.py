"""
Custom SEC EDGAR MCP Server — Task 3.1

Exposes 3 tools over the Model Context Protocol:
  • search_filings      — list filings for a US public company by CIK
  • get_filing_text     — extract plain text from a specific EDGAR filing
  • get_company_facts   — fetch structured XBRL financial facts

Auth: No API key — SEC EDGAR is public. Requires User-Agent header per SEC policy:
      "<app>/<version> <email>"  (missing this header returns HTTP 403)
Rate limit: 10 req/sec. Exceeding it returns HTTP 429.
Base URLs:
  - https://data.sec.gov    — submissions metadata, company facts (XBRL)
  - https://www.sec.gov     — filing documents in the Archives
"""

import asyncio
import logging
import re
from html.parser import HTMLParser

import httpx
from mcp.server.fastmcp import FastMCP

from src.config import settings

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "sec-edgar",
    instructions=(
        "SEC EDGAR gives free access to US public company filings. "
        "Use search_filings(cik) to list recent filings for a company — "
        "provide the SEC CIK number (e.g. '1318605' for Tesla, '789019' for Microsoft). "
        "Use get_filing_text(accession_number) to extract plain text from a filing "
        "(use accession numbers returned by search_filings). "
        "Use get_company_facts(cik) to retrieve structured XBRL financial data including "
        "revenue, net income, shares outstanding, and other reported figures. "
        "Only works for US public companies that file with the SEC. "
        "Private companies are not in EDGAR — use Registry Lookup or Companies House instead."
    ),
)

_DATA_URL = "https://data.sec.gov"
_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data"
_MAX_RETRIES = 3
_RETRY_CODES = {429, 503}
_EDGAR_UA = f"DueDiligencePlatform/1.0 {settings.contact_email}"


# ── HTML → plain text ─────────────────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "head", "nav", "footer", "noscript"}

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        if tag.split(":")[-1].lower() in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        if tag.split(":")[-1].lower() in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self._chunks.append(stripped)

    def get_text(self) -> str:
        return " ".join(self._chunks)


def _html_to_text(html: str) -> str:
    extractor = _TextExtractor()
    try:
        extractor.feed(html)
        text = extractor.get_text()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html)
    return " ".join(text.split())[:50_000]


# ── CIK normalisation ─────────────────────────────────────────────────────────

def _pad_cik(cik: str | int) -> str:
    """Zero-pad CIK to 10 digits as EDGAR expects."""
    return str(cik).strip().lstrip("0").zfill(10)


def _normalise_accession(accession_number: str) -> tuple[str, str]:
    """Return (with_dashes, no_dashes) for an accession number.

    Accepts both formats:
        '0001318605-24-000080'   (with dashes, 20 chars)
        '000131860524000080'     (no dashes, 18 chars)
    """
    no_dash = accession_number.replace("-", "")
    if len(no_dash) == 18:
        with_dash = f"{no_dash[:10]}-{no_dash[10:12]}-{no_dash[12:]}"
        return with_dash, no_dash
    # Unexpected length — return as-is and let the caller handle errors
    return accession_number, no_dash


# ── Core HTTP helper ──────────────────────────────────────────────────────────

async def _edgar_get(url: str, *, as_text: bool = False) -> dict | list | str:
    """GET a SEC EDGAR URL with required User-Agent and exponential backoff.

    Returns:
        Parsed JSON (dict or list) when content-type is JSON.
        Raw text string when as_text=True or content is not JSON.
        Structured error dict on all non-200 outcomes.
    """
    headers = {
        "User-Agent": _EDGAR_UA,
        "Accept-Encoding": "gzip, deflate",
    }

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for attempt in range(_MAX_RETRIES):
            try:
                r = await client.get(url, headers=headers, timeout=20)

                if r.status_code == 200:
                    if as_text:
                        return r.text
                    try:
                        return r.json()
                    except Exception:
                        return r.text

                if r.status_code in _RETRY_CODES:
                    if attempt < _MAX_RETRIES - 1:
                        wait = 2 ** attempt
                        logger.warning(
                            "EDGAR HTTP %s — retrying in %ds (attempt %d/%d)",
                            r.status_code, wait, attempt + 1, _MAX_RETRIES,
                        )
                        await asyncio.sleep(wait)
                        continue
                    return {
                        "error": "rate_limited",
                        "message": (
                            f"SEC EDGAR returned HTTP {r.status_code} after {_MAX_RETRIES} retries. "
                            "EDGAR rate limit is 10 req/sec — wait a moment and retry."
                        ),
                    }

                if r.status_code == 404:
                    return {"error": "not_found", "message": f"Not found: {url}"}

                return {
                    "error": "api_error",
                    "message": f"HTTP {r.status_code}: {r.text[:300]}",
                }

            except httpx.TimeoutException:
                if attempt < _MAX_RETRIES - 1:
                    wait = 2 ** attempt
                    logger.warning(
                        "EDGAR timed out — retrying in %ds (attempt %d/%d)",
                        wait, attempt + 1, _MAX_RETRIES,
                    )
                    await asyncio.sleep(wait)
                    continue
                return {"error": "timeout", "message": "Request timed out after retries."}

            except Exception as exc:
                logger.error("EDGAR request failed (%s): %s", url, exc)
                return {"error": "request_failed", "message": str(exc)}

    return {"error": "max_retries", "message": "Max retries exceeded"}


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def search_filings(
    cik: str,
    filing_type: str = "",
    date_from: str = "",
    date_to: str = "",
) -> dict:
    """List recent SEC filings for a US public company by CIK.

    Retrieves up to 40 recent filings from EDGAR's company submissions endpoint.
    Optionally filter by filing type and/or date range.

    Args:
        cik: SEC Central Index Key (e.g. '1318605' for Tesla, '789019' for Microsoft).
             Leading zeros are added automatically — '1318605' and '0001318605' both work.
        filing_type: Optional filing type filter. Common values:
            '10-K'  — annual report (most important for due diligence)
            '10-Q'  — quarterly report
            '8-K'   — current report (material events: lawsuits, leadership changes, etc.)
            '20-F'  — annual report for foreign private issuers
            '6-K'   — current report for foreign private issuers
            '13F-HR' — institutional investment manager holdings
            Leave empty to return all recent filing types (sorted newest-first).
        date_from: Optional start date filter, ISO format 'YYYY-MM-DD'.
        date_to:   Optional end date filter, ISO format 'YYYY-MM-DD'.

    Returns:
        On success: {
            "cik": "0001318605",
            "company_name": "Tesla, Inc.",
            "filings": [
                {
                    "accession_number": "0001318605-24-000080",
                    "filing_type": "10-K",
                    "filing_date": "2024-01-29",
                    "report_date": "2023-12-31",
                    "primary_document": "tsla20231231_10k.htm",
                    "description": "Annual report [Section 13 or 15(d)]"
                },
                ...
            ],
            "total_returned": N
        }
        On failure: {"error": "<code>", "message": "<reason>"}

        Error codes: not_found | rate_limited | api_error | timeout | request_failed
    """
    cik_padded = _pad_cik(cik)
    url = f"{_DATA_URL}/submissions/CIK{cik_padded}.json"
    data = await _edgar_get(url)

    if isinstance(data, dict) and "error" in data:
        return data
    if not isinstance(data, dict):
        return {"error": "api_error", "message": "Unexpected response format from EDGAR submissions API"}

    company_name: str = data.get("name", "")
    recent: dict = data.get("filings", {}).get("recent", {})

    # EDGAR returns parallel arrays — each index corresponds to one filing
    accession_numbers: list[str] = recent.get("accessionNumber", [])
    forms: list[str] = recent.get("form", [])
    filing_dates: list[str] = recent.get("filingDate", [])
    report_dates: list[str] = recent.get("reportDate", [])
    primary_docs: list[str] = recent.get("primaryDocument", [])
    primary_descs: list[str] = recent.get("primaryDocDescription", [])

    filings: list[dict] = []
    for i, raw_acc in enumerate(accession_numbers):
        form = forms[i] if i < len(forms) else ""
        fdate = filing_dates[i] if i < len(filing_dates) else ""
        rdate = report_dates[i] if i < len(report_dates) else ""
        pdoc = primary_docs[i] if i < len(primary_docs) else ""
        pdesc = primary_descs[i] if i < len(primary_descs) else ""

        if filing_type and form.upper() != filing_type.upper():
            continue
        if date_from and fdate < date_from:
            continue
        if date_to and fdate > date_to:
            continue

        acc_with_dash, _ = _normalise_accession(raw_acc)

        filings.append({
            "accession_number": acc_with_dash,
            "filing_type": form,
            "filing_date": fdate,
            "report_date": rdate,
            "primary_document": pdoc,
            "description": pdesc,
        })

        if len(filings) >= 40:
            break

    return {
        "cik": cik_padded,
        "company_name": company_name,
        "filings": filings,
        "total_returned": len(filings),
    }


@mcp.tool()
async def get_filing_text(accession_number: str, cik: str = "") -> dict:
    """Extract plain text from a specific SEC EDGAR filing.

    Fetches the primary document for the given accession number and converts
    it from HTML/XML to clean plain text (up to 50 000 characters).

    Args:
        accession_number: EDGAR accession number, with or without dashes.
            Examples: '0001318605-24-000080' or '000131860524000080'.
            Get this from search_filings().
        cik: Optional SEC CIK of the *subject* company (e.g. '12927' for Boeing).
            Strongly recommended: the EDGAR archive path is keyed by the subject
            company's CIK, which differs from the accession-number prefix when a
            filing agent submits on the company's behalf. Without it, agent-filed
            documents resolve to the wrong path and 404. If omitted, the
            accession-number prefix is used as a best-effort fallback.

    Returns:
        On success: {
            "accession_number": "0001318605-24-000080",
            "filing_type": "10-K",
            "filing_date": "2024-01-29",
            "primary_document": "tsla20231231_10k.htm",
            "text": "...",       ← plain text, up to 50 000 chars
            "text_length": N,
            "source_url": "https://www.sec.gov/Archives/edgar/data/..."
        }
        On failure: {"error": "<code>", "message": "<reason>"}
    """
    acc_with_dash, acc_no_dash = _normalise_accession(accession_number)

    # The EDGAR archive path and submissions metadata are keyed by the SUBJECT
    # company's CIK. That differs from the accession-number prefix when a filing
    # agent submits on the company's behalf — so prefer an explicit subject CIK
    # and only fall back to the accession prefix when none is provided.
    cik_padded = _pad_cik(cik) if cik else acc_no_dash[:10]
    cik_int = int(cik_padded)  # strip leading zeros for the URL path

    # Look up primary document filename from the submissions metadata
    submissions_url = f"{_DATA_URL}/submissions/CIK{cik_padded}.json"
    meta = await _edgar_get(submissions_url)

    primary_doc = ""
    form = ""
    filing_date = ""

    if isinstance(meta, dict) and "error" not in meta:
        recent = meta.get("filings", {}).get("recent", {})
        acc_list: list[str] = recent.get("accessionNumber", [])
        for i, raw_acc in enumerate(acc_list):
            candidate_with_dash, _ = _normalise_accession(raw_acc)
            if candidate_with_dash == acc_with_dash:
                primary_doc = recent.get("primaryDocument", [])[i] if i < len(recent.get("primaryDocument", [])) else ""
                form = recent.get("form", [])[i] if i < len(recent.get("form", [])) else ""
                filing_date = recent.get("filingDate", [])[i] if i < len(recent.get("filingDate", [])) else ""
                break

    if not primary_doc:
        # Fall back to the complete submission text file (.txt) in the Archives
        primary_doc = f"{acc_with_dash}.txt"

    doc_url = f"{_ARCHIVE_URL}/{cik_int}/{acc_no_dash}/{primary_doc}"
    raw = await _edgar_get(doc_url, as_text=True)

    if isinstance(raw, dict) and "error" in raw:
        return raw

    text = _html_to_text(str(raw)) if "<" in str(raw) else str(raw)[:50_000]

    return {
        "accession_number": acc_with_dash,
        "filing_type": form,
        "filing_date": filing_date,
        "primary_document": primary_doc,
        "text": text,
        "text_length": len(text),
        "source_url": doc_url,
    }


@mcp.tool()
async def get_company_facts(cik: str) -> dict:
    """Retrieve structured XBRL financial facts for a US public company.

    Returns standardised financial data reported across all EDGAR filings,
    including revenue, net income, assets, liabilities, EPS, and shares
    outstanding. Data is keyed by US-GAAP or IFRS taxonomy concepts.

    Args:
        cik: SEC Central Index Key (e.g. '1318605' for Tesla).
             Leading zeros are added automatically.

    Returns:
        On success: {
            "cik": "0001318605",
            "company_name": "Tesla, Inc.",
            "facts_summary": {
                "us-gaap": {
                    "Revenues": {
                        "label": "Revenues",
                        "description": "Amount of revenue recognized ...",
                        "latest_value": 97690000000,
                        "latest_unit": "USD",
                        "latest_filed": "2024-01-29",
                        "latest_period_end": "2023-12-31"
                    },
                    ...
                }
            },
            "total_concepts": N
        }
        On failure: {"error": "<code>", "message": "<reason>"}
    """
    cik_padded = _pad_cik(cik)
    url = f"{_DATA_URL}/api/xbrl/companyfacts/CIK{cik_padded}.json"
    data = await _edgar_get(url)

    if isinstance(data, dict) and "error" in data:
        return data
    if not isinstance(data, dict):
        return {"error": "api_error", "message": "Unexpected response from EDGAR company facts API"}

    company_name: str = data.get("entityName", "")
    raw_facts: dict = data.get("facts", {})

    # Distil each taxonomy down to: label, description, and the most recently filed value
    facts_summary: dict[str, dict] = {}
    total_concepts = 0

    for taxonomy, concepts in raw_facts.items():
        facts_summary[taxonomy] = {}
        for concept, concept_data in concepts.items():
            total_concepts += 1
            label = concept_data.get("label", concept)
            description = concept_data.get("description", "")
            units_data: dict = concept_data.get("units", {})

            latest_value = None
            latest_unit = None
            latest_filed = ""
            latest_end = ""

            for unit_name, entries in units_data.items():
                # Find the most recently filed 10-K or annual entry
                annual = [
                    e for e in entries
                    if e.get("form") in {"10-K", "20-F", "10-K/A", "20-F/A"}
                    and e.get("filed", "") != ""
                ]
                if not annual:
                    annual = entries

                if annual:
                    most_recent = max(annual, key=lambda e: e.get("filed", ""))
                    if most_recent.get("filed", "") > latest_filed:
                        latest_value = most_recent.get("val")
                        latest_unit = unit_name
                        latest_filed = most_recent.get("filed", "")
                        latest_end = most_recent.get("end", "")

            if latest_value is not None:
                facts_summary[taxonomy][concept] = {
                    "label": label,
                    "description": description[:200] if description else "",
                    "latest_value": latest_value,
                    "latest_unit": latest_unit,
                    "latest_filed": latest_filed,
                    "latest_period_end": latest_end,
                }

    return {
        "cik": cik_padded,
        "company_name": company_name,
        "facts_summary": facts_summary,
        "total_concepts": total_concepts,
    }


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
