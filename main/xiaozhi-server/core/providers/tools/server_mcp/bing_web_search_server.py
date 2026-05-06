from __future__ import annotations

import os
from typing import Any

import requests
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("bing-web-search")

BING_SEARCH_ENDPOINT = "https://api.bing.microsoft.com/v7.0/search"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _get_bing_key() -> str:
    key = os.getenv("BING_SEARCH_API_KEY", "").strip()
    if not key:
        raise ValueError("Missing BING_SEARCH_API_KEY environment variable")
    return key


def _build_headers() -> dict[str, str]:
    headers = {
        "Ocp-Apim-Subscription-Key": _get_bing_key(),
        "User-Agent": os.getenv("BING_SEARCH_USER_AGENT", DEFAULT_USER_AGENT),
    }

    client_id = os.getenv("BING_SEARCH_CLIENT_ID", "").strip()
    client_ip = os.getenv("BING_SEARCH_CLIENT_IP", "").strip()
    search_location = os.getenv("BING_SEARCH_LOCATION", "").strip()

    if client_id:
        headers["X-MSEdge-ClientID"] = client_id
    if client_ip:
        headers["X-MSEdge-ClientIP"] = client_ip
    if search_location:
        headers["X-Search-Location"] = search_location

    return headers


def _normalize_count(count: int) -> int:
    if count <= 0:
        return 5
    return min(count, 10)


def _trim_web_pages(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in items:
        results.append(
            {
                "title": item.get("name", ""),
                "url": item.get("url", ""),
                "snippet": item.get("snippet", ""),
                "site_name": item.get("siteName", ""),
                "date_published": item.get("dateLastCrawled", ""),
            }
        )
    return results


@mcp.tool()
def web_search(
    query: str,
    count: int = 5,
    offset: int = 0,
    mkt: str = "es-ES",
    safe_search: str = "Moderate",
) -> dict[str, Any]:
    """Search the public web with Bing and return concise structured results."""
    query = (query or "").strip()
    if not query:
        return {"error": "invalid_query", "message": "query must not be empty"}

    params = {
        "q": query,
        "count": _normalize_count(count),
        "offset": max(offset, 0),
        "mkt": mkt,
        "safeSearch": safe_search,
        "textDecorations": False,
        "textFormat": "Raw",
    }

    response = requests.get(
        BING_SEARCH_ENDPOINT,
        headers=_build_headers(),
        params=params,
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()

    web_pages = payload.get("webPages", {}) or {}
    news = payload.get("news", {}) or {}
    related = payload.get("relatedSearches", {}) or {}

    return {
        "query": query,
        "market": payload.get("queryContext", {}).get("originalQuery", mkt),
        "results": _trim_web_pages(web_pages.get("value", []) or []),
        "news": _trim_web_pages(news.get("value", []) or []),
        "related_searches": [item.get("text", "") for item in (related.get("value", []) or [])],
    }


if __name__ == "__main__":
    mcp.run()
