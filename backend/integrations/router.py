"""Integrations capability endpoint.

Router-only shared surface: booleans only (plus the ``schema_version`` envelope).
Values are read from ``backend.config`` dynamically. Never returns API-key
values, prefixes or lengths, and never exposes an SMTP property.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend import config

router = APIRouter()

SCHEMA_VERSION = 1


@router.get("/api/integrations")
def get_integrations():
    anthropic_ok = bool(config.ANTHROPIC_API_KEY)
    openai_ok = bool(config.OPENAI_API_KEY)
    both_ai = anthropic_ok and openai_ok
    return {
        "schema_version": SCHEMA_VERSION,
        "job_sources": {
            "adzuna": bool(config.ADZUNA_APP_ID and config.ADZUNA_APP_KEY),
            "reed": bool(config.REED_API_KEY),
            "usajobs": bool(config.USAJOBS_API_KEY and config.USAJOBS_USER_AGENT),
            "greenhouse": True,
            "lever": True,
            "remoteok": True,
        },
        "ai": {
            "anthropic": anthropic_ok,
            "openai": openai_ok,
            "cover_letter_pipeline": both_ai,
            # Key-gated: these call the model.
            "pack_generation": both_ai,
            "pack_revision": both_ai,
            # Local capability -- retrieval/autofill of an existing pack never
            # calls AI, so this is always available.
            "pack_autofill": True,
        },
        "prospects": {
            "companies_house": bool(config.COMPANIES_HOUSE_API_KEY),
            "osm_overpass": True,
        },
        "github_repos": bool(config.GITHUB_TOKEN or config.GITHUB_USERNAME),
        "news": True,
        "outreach_mailto": True,
    }
