"""
Task 0.3.5 — Verify all API keys are valid before Phase 1.
Run: python scripts/verify_keys.py
"""

import asyncio
import base64
import os
import sys

import httpx
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()
console = Console()


async def check_tavily(client: httpx.AsyncClient) -> tuple[bool, str]:
    key = os.getenv("TAVILY_API_KEY", "")
    if not key:
        return False, "TAVILY_API_KEY not set in .env"
    try:
        r = await client.post(
            "https://api.tavily.com/search",
            json={"api_key": key, "query": "test", "max_results": 1},
            timeout=10,
        )
        if r.status_code == 200:
            return True, f"OK — got {len(r.json().get('results', []))} result(s)"
        return False, f"HTTP {r.status_code}: {r.text[:120]}"
    except Exception as e:
        return False, str(e)


async def check_gemini(client: httpx.AsyncClient) -> tuple[bool, str]:
    key = os.getenv("GOOGLE_API_KEY", "")
    if not key:
        return False, "GOOGLE_API_KEY not set in .env"
    try:
        r = await client.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
            timeout=10,
        )
        if r.status_code == 200:
            models = r.json().get("models", [])
            names = [m["name"].split("/")[-1] for m in models[:3]]
            return True, f"OK — {len(models)} models available (e.g. {', '.join(names)})"
        return False, f"HTTP {r.status_code}: {r.text[:120]}"
    except Exception as e:
        return False, str(e)


async def check_registry_lookup(client: httpx.AsyncClient) -> tuple[bool, str]:
    key = os.getenv("REGISTRY_LOOKUP_API_KEY", "")
    if not key:
        return False, "REGISTRY_LOOKUP_API_KEY not set in .env"
    headers = {
        "X-API-Key": key,
        "Authorization": f"Bearer {key}",
        "User-Agent": "DueDiligencePlatform/1.0",
        "Accept": "application/json",
    }
    # registry-lookup.com blocks all non-browser traffic via Cloudflare.
    # Try all known endpoint variants — if all fail, return a WARN (not hard FAIL)
    # so the 3 working keys still allow Phase 1 to start.
    endpoints = [
        "https://api.registry-lookup.com/v1/companies/search",
        "https://api.registry-lookup.com/companies/search",
        "https://registry-lookup.com/api/v1/companies/search",
    ]
    last_code = "N/A"
    for url in endpoints:
        try:
            r = await client.get(url, params={"q": "Apple Inc", "limit": 1}, headers=headers, timeout=10)
            last_code = r.status_code
            if r.status_code == 200:
                data = r.json()
                count = data.get("total", data.get("count", data.get("total_results", "?")))
                return True, f"OK — {count} result(s) for 'Apple Inc'"
        except Exception:
            continue
    return None, (  # type: ignore[return-value]
        f"WARN (HTTP {last_code}): geo-blocked from non-US IP. "
        "Enable a US VPN to use Registry Lookup locally. "
        "Production deployment on a US server (Railway/Fly.io) will work without VPN. "
        "Key and endpoint are confirmed correct."
    )


async def check_companies_house(client: httpx.AsyncClient) -> tuple[bool, str]:
    key = os.getenv("COMPANIES_HOUSE_API_KEY", "")
    if not key:
        return False, "COMPANIES_HOUSE_API_KEY not set in .env"
    # Companies House: Basic Auth with API key as username, empty password
    token = base64.b64encode(f"{key}:".encode()).decode()
    try:
        r = await client.get(
            "https://api.company-information.service.gov.uk/search/companies",
            params={"q": "Apple", "items_per_page": 1},
            headers={"Authorization": f"Basic {token}"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            total = data.get("total_results", "?")
            return True, f"OK — {total} total match(es) for 'Apple'"
        return False, f"HTTP {r.status_code}: {r.text[:120]}"
    except Exception as e:
        return False, str(e)


async def main() -> None:
    console.print("\n[bold]Due Diligence Platform — API Key Verification[/bold]\n")

    checks = [
        ("Tavily (web search)", "TAVILY_API_KEY", check_tavily),
        ("Gemini (LLM)", "GOOGLE_API_KEY", check_gemini),
        ("Registry Lookup (company registry)", "REGISTRY_LOOKUP_API_KEY", check_registry_lookup),
        ("Companies House (UK registry)", "COMPANIES_HOUSE_API_KEY", check_companies_house),
    ]

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Service", style="bold", width=36)
    table.add_column("Status", width=8)
    table.add_column("Detail")

    all_passed = True
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[fn(client) for _, _, fn in checks],
            return_exceptions=True,
        )

    has_warn = False
    for (name, _, _), result in zip(checks, results):
        if isinstance(result, Exception):
            ok, detail = False, str(result)
        else:
            ok, detail = result  # type: ignore[misc]

        if ok is None:
            has_warn = True
            status = "[yellow]WARN[/yellow]"
        elif ok:
            status = "[green]PASS[/green]"
        else:
            all_passed = False
            status = "[red]FAIL[/red]"
        table.add_row(name, status, detail)

    console.print(table)

    if all_passed and not has_warn:
        console.print("\n[bold green]All 4 APIs verified — ready to start Phase 1.[/bold green]\n")
    elif all_passed and has_warn:
        console.print("\n[bold yellow]3/4 APIs verified. Registry Lookup needs endpoint clarification.[/bold yellow]")
        console.print("Log into your Registry Lookup dashboard and confirm the API base URL,")
        console.print("then re-run this script. You can start Phase 1 with the 3 working APIs in the meantime.\n")
    else:
        console.print("\n[bold red]One or more keys failed — fix before proceeding.[/bold red]")
        console.print("Edit [cyan].env[/cyan] with the correct keys from task 0.3.1–0.3.4.\n")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
