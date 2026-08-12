from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .live_agent import (
    ANTHROPIC_PROVIDER,
    ANTHROPIC_VERSION,
    DEFAULT_ANTHROPIC_BASE_URL,
    DEFAULT_OPENAI_BASE_URL,
    HTTP_USER_AGENT,
    OPENAI_PROVIDER,
    PROVIDERS,
    credential_help,
    credential_is_available,
    default_model_for_provider,
    redact_sensitive,
    safe_base_url,
)


def models_endpoint(base_url: str) -> str:
    base = safe_base_url(base_url)
    return f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"


def auth_headers(provider: str) -> dict[str, str]:
    if provider == OPENAI_PROVIDER:
        token = os.getenv("OPENAI_API_KEY")
        if not token:
            raise RuntimeError(credential_help(provider))
        return {"Authorization": f"Bearer {token}"}

    auth_token = os.getenv("ANTHROPIC_AUTH_TOKEN")
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if auth_token:
        return {
            "Authorization": f"Bearer {auth_token}",
            "anthropic-version": ANTHROPIC_VERSION,
        }
    if api_key:
        return {
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
    raise RuntimeError(credential_help(provider))


def extract_model_ids(response: dict[str, Any]) -> list[str]:
    data = response.get("data") or response.get("models") or []
    result = []
    for item in data:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            model_id = item.get("id") or item.get("name")
            if model_id:
                result.append(str(model_id))
    return sorted(set(result))


def probe(
    *,
    provider: str,
    base_url: str,
    requested_model: str,
    timeout: int,
) -> dict[str, Any]:
    endpoint = models_endpoint(base_url)
    request = urllib.request.Request(
        endpoint,
        headers={
            **auth_headers(provider),
            "Accept": "application/json",
            "User-Agent": HTTP_USER_AGENT,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
            status = response.status
    except urllib.error.HTTPError as exc:
        detail = redact_sensitive(
            exc.read().decode("utf-8", errors="replace")
        )
        raise RuntimeError(
            f"Model-list preflight HTTP {exc.code}: {detail[:800]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Model-list preflight network error: {exc.reason}") from exc

    model_ids = extract_model_ids(body)
    return {
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "base_url": safe_base_url(base_url),
        "endpoint": endpoint,
        "http_status": status,
        "requested_model": requested_model,
        "requested_model_listed": requested_model in model_ids,
        "model_count": len(model_ids),
        "model_ids": model_ids,
        "interpretation": (
            "This only verifies authentication and model listing. A paid small "
            "multi-strategy gate must still verify native web search, complete "
            "sources and "
            "structured output."
        ),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Check provider authentication and model inventory without running "
            "a paid completion or web search."
        )
    )
    result.add_argument("--provider", choices=PROVIDERS, default="anthropic")
    result.add_argument("--base-url")
    result.add_argument("--model")
    result.add_argument("--timeout", type=int, default=30)
    result.add_argument(
        "--output",
        type=Path,
        default=Path("outputs") / "provider_probe.json",
    )
    result.add_argument(
        "--confirm-network",
        action="store_true",
        help="Send the authenticated GET /v1/models preflight.",
    )
    return result


def main() -> None:
    args = parser().parse_args()
    provider = args.provider
    default_base = (
        DEFAULT_ANTHROPIC_BASE_URL
        if provider == ANTHROPIC_PROVIDER
        else DEFAULT_OPENAI_BASE_URL
    )
    base_url = args.base_url or (
        os.getenv("ANTHROPIC_BASE_URL")
        if provider == ANTHROPIC_PROVIDER
        else os.getenv("OPENAI_BASE_URL")
    ) or default_base
    model = args.model or default_model_for_provider(provider)
    if not args.confirm_network:
        print("Plan only: authenticated GET /v1/models; no model/search call.")
        print(f"Provider={provider}; base_url={safe_base_url(base_url)}; model={model}")
        return
    if not credential_is_available(provider):
        raise RuntimeError(credential_help(provider))
    report = probe(
        provider=provider,
        base_url=base_url,
        requested_model=model,
        timeout=args.timeout,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Authenticated model inventory saved to {args.output}")
    print(
        f"Listed {report['model_count']} model(s); requested model listed: "
        f"{report['requested_model_listed']}"
    )


if __name__ == "__main__":
    main()
