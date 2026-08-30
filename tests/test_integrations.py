"""GET /api/integrations: boolean capability map.

Distinguishes key-gated pack generation/revision from the always-available local
pack_autofill capability. Never leaks key material; never exposes SMTP.
"""

from backend import config

_KEY_NAMES = [
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY",
    "REED_API_KEY", "USAJOBS_API_KEY", "USAJOBS_USER_AGENT", "GITHUB_TOKEN",
    "GITHUB_USERNAME", "COMPANIES_HOUSE_API_KEY",
]


def _leaves(node, out):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "schema_version":
                continue
            _leaves(v, out)
    else:
        out.append(node)
    return out


def test_all_keys_empty(client):
    body = client.get("/api/integrations").json()
    assert body["schema_version"] == 1
    assert body["job_sources"] == {
        "adzuna": False, "reed": False, "usajobs": False,
        "greenhouse": True, "lever": True, "remoteok": True,
    }
    assert body["ai"] == {
        "anthropic": False, "openai": False, "cover_letter_pipeline": False,
        "pack_generation": False, "pack_revision": False, "pack_autofill": True,
    }
    assert body["prospects"] == {"companies_house": False, "osm_overpass": True}
    assert body["github_repos"] is False
    assert body["news"] is True
    assert body["outreach_mailto"] is True


def test_every_leaf_is_bool(client):
    body = client.get("/api/integrations").json()
    assert all(isinstance(v, bool) for v in _leaves(body, []))


def test_no_smtp_property(client):
    assert "smtp" not in client.get("/api/integrations").text.lower()


def test_no_key_material_leaked(client, monkeypatch):
    for name in _KEY_NAMES:
        monkeypatch.setattr(config, name, f"secret-{name}-value")
    text = client.get("/api/integrations").text
    for name in _KEY_NAMES:
        assert f"secret-{name}-value" not in text


def test_both_ai_keys_enable_generation_and_revision(client, monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "y")
    ai = client.get("/api/integrations").json()["ai"]
    assert ai["pack_generation"] is True
    assert ai["pack_revision"] is True
    assert ai["cover_letter_pipeline"] is True
    assert ai["pack_autofill"] is True


def test_one_ai_key_is_not_enough(client, monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "x")
    ai = client.get("/api/integrations").json()["ai"]
    assert ai["pack_generation"] is False
    assert ai["pack_revision"] is False
    assert ai["cover_letter_pipeline"] is False
    assert ai["pack_autofill"] is True


def test_pack_autofill_is_key_independent(client, monkeypatch):
    assert client.get("/api/integrations").json()["ai"]["pack_autofill"] is True
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "y")
    assert client.get("/api/integrations").json()["ai"]["pack_autofill"] is True


def test_partial_job_source_keys(client, monkeypatch):
    monkeypatch.setattr(config, "ADZUNA_APP_ID", "id-only")
    assert client.get("/api/integrations").json()["job_sources"]["adzuna"] is False
    monkeypatch.setattr(config, "ADZUNA_APP_KEY", "key-too")
    assert client.get("/api/integrations").json()["job_sources"]["adzuna"] is True


def test_reed_key_flips_true(client, monkeypatch):
    monkeypatch.setattr(config, "REED_API_KEY", "r")
    assert client.get("/api/integrations").json()["job_sources"]["reed"] is True


def test_outreach_mailto_always_true(client, monkeypatch):
    monkeypatch.setattr(config, "REED_API_KEY", "r")
    assert client.get("/api/integrations").json()["outreach_mailto"] is True
