"""Regression coverage for backend.matching.passes_filters and its helpers.

Behaviour was pinned against the current implementation on 2026-08-27.
"""

import pytest

from backend import matching


# --------------------------------------------------------------------------- #
# Accepted technical roles in target / remote locations
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "title, location",
    [
        ("Software Engineer", "London, UK"),
        ("SWE", "nyc"),
        ("Graduate Software Engineer", "Boston, MA"),
        ("ML Engineer", "san jose"),          # alias -> silicon_valley
        ("software engineer", "MÜNCHEN"),      # case-insensitive, EU city
        ("Backend Engineer", "Berlin"),        # EU secondary net
        ("Full Stack Developer", "Austin"),
    ],
)
def test_accepts_technical_roles_in_target_locations(title, location):
    assert matching.passes_filters(title, location) is True


def test_accepts_remote_via_text():
    assert matching.passes_filters("Software Engineer", "Remote - EMEA") is True


def test_accepts_remote_via_flag_even_when_location_unknown():
    assert matching.passes_filters("Software Engineer", "Atlantis", remote_flag=True) is True


# --------------------------------------------------------------------------- #
# Seniority exclusions (role keyword still matches, but title is senior)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "title",
    [
        "Senior Software Engineer",
        "Staff Backend Engineer",
        "Principal Software Engineer",
        "Software Engineer - 8+ years experience",
        "Lead Software Engineer",
    ],
)
def test_rejects_senior_titles(title):
    assert matching.is_senior(title) is True
    assert matching.passes_filters(title, "London") is False


# --------------------------------------------------------------------------- #
# Internship / co-op exclusions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "title",
    [
        "Software Engineer Intern",
        "Software Engineering Internship",
        "Software Engineer Co-op",
        "Software Engineer (coop)",
    ],
)
def test_rejects_internships(title):
    assert matching.is_internship(title) is True
    assert matching.passes_filters(title, "London") is False


# --------------------------------------------------------------------------- #
# Target-location matching
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "location_text, expected_key",
    [
        ("New York, NY", "new_york"),
        ("Brooklyn", "new_york"),
        ("Herzliya", "tel_aviv"),
        ("Palo Alto office", "silicon_valley"),
        ("münchen", "munich"),
        ("Cambridge, MA", "boston"),
        ("Nowhere Special", None),
        ("", None),
    ],
)
def test_match_city(location_text, expected_key):
    assert matching.match_city(location_text) == expected_key


def test_is_remote_variants():
    assert matching.is_remote("Fully remote role") is True
    assert matching.is_remote("Remote friendly") is True
    assert matching.is_remote("London") is False
    assert matching.is_remote("London", remote_flag=True) is True
    assert matching.is_remote(None) is False


# --------------------------------------------------------------------------- #
# Rejection cases
# --------------------------------------------------------------------------- #
def test_rejects_non_technical_role():
    assert matching.matches_role("Marketing Manager") is False
    assert matching.passes_filters("Marketing Manager", "London") is False


def test_rejects_technical_role_with_no_city_and_not_remote():
    assert matching.passes_filters("Backend Engineer", "Nowhere Special") is False


def test_rejects_technical_role_with_empty_location():
    assert matching.passes_filters("Software Engineer", "") is False


def test_rejects_none_title():
    assert matching.passes_filters(None, "London") is False


def test_rejects_none_location():
    assert matching.passes_filters("Software Engineer", None) is False
