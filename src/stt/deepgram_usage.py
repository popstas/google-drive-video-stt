from __future__ import annotations

from typing import Any

import requests


DEEPGRAM_API_BASE_URL = "https://api.deepgram.com/v1"
REQUEST_TIMEOUT_SECONDS = 30


def fetch_request_cost_usd(
    api_key: str,
    request_id: str,
    *,
    proxy_url: str = "",
) -> float | None:
    projects = _request_json(
        api_key,
        f"{DEEPGRAM_API_BASE_URL}/projects",
        proxy_url=proxy_url,
    ).get("projects", [])
    if not isinstance(projects, list) or not projects:
        return None

    project = projects[0]
    if not isinstance(project, dict):
        return None
    project_id = project.get("project_id")
    if not project_id:
        return None

    detail = _request_json(
        api_key,
        f"{DEEPGRAM_API_BASE_URL}/projects/{project_id}/requests/{request_id}",
        proxy_url=proxy_url,
    )
    direct_cost = _usd_from_request_payload(detail.get("request", {}))
    if direct_cost is not None:
        return direct_cost

    recent = _request_json(
        api_key,
        f"{DEEPGRAM_API_BASE_URL}/projects/{project_id}/requests?limit=100&status=succeeded",
        proxy_url=proxy_url,
    )
    requests_payload = recent.get("requests", [])
    if not isinstance(requests_payload, list):
        return None
    for item in requests_payload:
        if isinstance(item, dict) and item.get("request_id") == request_id:
            return _usd_from_request_payload(item)
    return None


def _request_json(api_key: str, url: str, *, proxy_url: str = "") -> dict[str, Any]:
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    response = requests.get(
        url,
        headers={"Authorization": f"Token {api_key}"},
        proxies=proxies,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Deepgram returned non-object JSON from {url}")
    return payload


def _usd_from_request_payload(request_payload: object) -> float | None:
    if not isinstance(request_payload, dict):
        return None
    response_payload = request_payload.get("response", {})
    if not isinstance(response_payload, dict):
        return None
    details = response_payload.get("details", {})
    if not isinstance(details, dict):
        return None
    usd = details.get("usd")
    return float(usd) if usd is not None else None
