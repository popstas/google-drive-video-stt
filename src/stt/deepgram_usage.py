from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


DEEPGRAM_API_BASE_URL = "https://api.deepgram.com/v1"
REQUEST_TIMEOUT_SECONDS = 30


def fetch_request_cost_usd(
    api_key: str,
    request_id: str,
    *,
    proxy_url: str = "",
) -> float | None:
    """Best-effort lookup of a request's USD cost via the Deepgram usage API.

    A Deepgram API key can be scoped to several projects, so we never assume the
    first project owns the request. Instead we ask each project for the request
    by id; the owning project returns a payload with the cost, while the others
    return a 404 (or an empty body) that we skip over. The whole thing is purely
    informational: any failure returns ``None`` and must never break the caller.
    """
    try:
        listing = _request_json(
            api_key,
            f"{DEEPGRAM_API_BASE_URL}/projects",
            proxy_url=proxy_url,
        )
    except (requests.RequestException, ValueError) as exc:
        logger.debug("Deepgram cost lookup could not list projects: %s", exc)
        return None
    projects = listing.get("projects", [])
    if not isinstance(projects, list) or not projects:
        return None

    project_ids = [
        project["project_id"]
        for project in projects
        if isinstance(project, dict) and project.get("project_id")
    ]
    if not project_ids:
        return None

    for project_id in project_ids:
        cost = _request_cost_in_project(
            api_key,
            project_id,
            request_id,
            proxy_url=proxy_url,
        )
        if cost is not None:
            return cost
    return None


def _request_cost_in_project(
    api_key: str,
    project_id: str,
    request_id: str,
    *,
    proxy_url: str = "",
) -> float | None:
    """Return the request cost if ``request_id`` belongs to ``project_id``.

    Projects that do not own the request typically answer with a 404; we treat
    that (and any other per-project error) as "not here" and let the caller try
    the next project, so a multi-project key still resolves the right cost.
    """
    try:
        detail = _request_json(
            api_key,
            f"{DEEPGRAM_API_BASE_URL}/projects/{project_id}/requests/{request_id}",
            proxy_url=proxy_url,
        )
    except requests.RequestException as exc:
        logger.debug(
            "Deepgram cost lookup skipped project %s for request %s: %s",
            project_id,
            request_id,
            exc,
        )
        return None
    return _usd_from_request_payload(detail.get("request", {}))


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
    if usd is None:
        return None
    try:
        return float(usd)
    except (TypeError, ValueError):
        return None
