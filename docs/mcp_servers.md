# Custom MCP Servers

This platform's data access is built entirely on the [Model Context Protocol
(MCP)](https://modelcontextprotocol.io). Each external data source is an
independent MCP server exposing typed tools. The research agent calls them
concurrently; **adding a new source means writing a new server — zero agent-code
changes**. This is the project's core extensibility thesis.

All servers are built with the `mcp` Python SDK (`FastMCP`), launch over stdio,
and are registered in [`mcp_config.json`](../mcp_config.json). Each one isolates
its own auth, rate-limiting, and exponential backoff.

| Server | Module | Auth | Rate limit |
|---|---|---|---|
| Registry Lookup | `src.mcp_servers.registry_lookup.server` | `X-API-Key` header | 5,000/mo · 1,000/day · 10/s |
| Companies House | `src.mcp_servers.companies_house.server` | HTTP Basic (key as username) | none (backoff on 429/503) |
| SEC EDGAR | `src.mcp_servers.sec_edgar.server` | none (User-Agent required) | 10 req/s |

---

## Registry Lookup MCP — the differentiator

Global corporate-registry coverage: **521M+ entities across 309 jurisdictions**.
This is the primary entity-resolution source.

> **Note:** the upstream API is geo-blocked outside the US. When it is
> unreachable, the entity resolver falls back to SEC EDGAR / Companies House
> automatically — the pipeline degrades gracefully rather than failing.

| Tool | Signature | Returns |
|---|---|---|
| `search_company` | `(name, jurisdiction="", status="")` | Matching companies (paginated) across all jurisdictions. |
| `get_company_by_id` | `(id)` | Full entity details for a Registry Lookup entity ID. |
| `get_company_by_jurisdiction` | `(jurisdiction_code, registry_number)` | A company by its official registry number within a jurisdiction. |

**Test standalone:**

```bash
# Run the server over stdio (Ctrl-C to stop):
python -m src.mcp_servers.registry_lookup.server

# Or exercise its tools directly via the unit/integration tests:
pytest tests/test_mcp_registry_lookup.py -v
```

---

## Companies House MCP — UK enrichment

Enriched UK company data: officers, filing history, charges. Free and unlimited.

| Tool | Signature | Returns |
|---|---|---|
| `search_company` | `(name, items_per_page=10)` | UK companies matching a name. |
| `get_company_details` | `(company_number)` | Full company profile (status, address, incorporation). |
| `get_company_officers` | `(company_number, items_per_page=50, include_resigned=True)` | Current + past directors/secretaries (beneficial-ownership / disqualification checks). |
| `get_company_filings` | `(company_number, category="", items_per_page=25)` | Filing history: accounts, confirmation statements, director changes, mortgages/charges. |

**Test standalone:**

```bash
python -m src.mcp_servers.companies_house.server
pytest tests/test_mcp_companies_house.py -v
```

---

## SEC EDGAR MCP — US public filings

US public-company filings and structured financials. No API key; requires a
descriptive `User-Agent`. Integrated only when entity resolution marks a company
public (`is_public=True`), so private companies skip it cleanly.

| Tool | Signature | Returns |
|---|---|---|
| `search_filings` | `(cik, filing_type="", date_from="", date_to="")` | Recent filings (up to 40) by CIK, optionally filtered by type/date. |
| `get_filing_text` | `(accession_number, cik="")` | Plain text extracted from a specific filing (HTML → text). |
| `get_company_facts` | `(cik)` | Structured XBRL financial facts. |

**Test standalone:**

```bash
python -m src.mcp_servers.sec_edgar.server
pytest tests/test_mcp_sec_edgar.py -v
```

---

## How the research agent uses them

The research agent (`src/agents/research_agent.py`) fans out to all applicable
servers via `asyncio.gather(..., return_exceptions=True)`:

- Every company → Registry Lookup + Tavily web search.
- UK company (`jurisdiction` starts `gb`) → + Companies House officers/filings.
- Public company (`is_public`) → + SEC EDGAR filings.

A failing source is appended to `sources_failed` and never crashes the run —
this is the "graceful degradation" property validated in the evaluation
(`5.1.6`). Adding a new source (e.g. the News API in Phase 5.6) is purely a
matter of writing the server and registering it in `mcp_config.json`.
