import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DB_PATH = BASE_DIR / "jobs.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

UPLOAD_DIR = BASE_DIR / "uploads" / "cvs"
MAX_CV_BYTES = 15 * 1024 * 1024  # 15MB

POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "60"))

ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY", "")
REED_API_KEY = os.getenv("REED_API_KEY", "")
USAJOBS_API_KEY = os.getenv("USAJOBS_API_KEY", "")
USAJOBS_USER_AGENT = os.getenv("USAJOBS_USER_AGENT", "")

GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

# Primary target cities from the user's passport/eligibility set (US, UK, Israel).
# lat/lon center the map pin; radius_km is used when a source supports geo search.
TARGET_CITIES = {
    "london":        {"label": "London",        "country": "UK",  "lat": 51.5074, "lon": -0.1278, "aliases": ["london"]},
    "tel_aviv":      {"label": "Tel Aviv",       "country": "IL",  "lat": 32.0853, "lon": 34.7818, "aliases": ["tel aviv", "tel-aviv", "herzliya", "ramat gan"]},
    "miami":         {"label": "Miami",          "country": "US",  "lat": 25.7617, "lon": -80.1918, "aliases": ["miami", "fort lauderdale"]},
    "new_york":      {"label": "New York",       "country": "US",  "lat": 40.7128, "lon": -74.0060, "aliases": ["new york", "nyc", "brooklyn", "manhattan"]},
    "silicon_valley":{"label": "Silicon Valley", "country": "US",  "lat": 37.3875, "lon": -122.0575, "aliases": ["san francisco", "san jose", "palo alto", "mountain view", "menlo park", "sunnyvale", "santa clara", "silicon valley", "bay area"]},
    "boston":        {"label": "Boston",         "country": "US",  "lat": 42.3601, "lon": -71.0589, "aliases": ["boston", "cambridge, ma"]},
    "chicago":       {"label": "Chicago",        "country": "US",  "lat": 41.8781, "lon": -87.6298, "aliases": ["chicago"]},
    "austin":        {"label": "Austin",         "country": "US",  "lat": 30.2672, "lon": -97.7431, "aliases": ["austin"]},
    "dallas":        {"label": "Dallas",         "country": "US",  "lat": 32.7767, "lon": -96.7970, "aliases": ["dallas", "fort worth", "plano"]},
}

# Secondary net: EU cities reachable via Hungarian-passport EU work rights.
# Kept separate so the map/UI can visually de-emphasize these vs. the primary list.
EU_CITIES = {
    "budapest":  {"label": "Budapest",  "country": "HU", "lat": 47.4979, "lon": 19.0402, "aliases": ["budapest"]},
    "berlin":    {"label": "Berlin",    "country": "DE", "lat": 52.5200, "lon": 13.4050, "aliases": ["berlin"]},
    "amsterdam": {"label": "Amsterdam", "country": "NL", "lat": 52.3676, "lon": 4.9041, "aliases": ["amsterdam"]},
    "dublin":    {"label": "Dublin",    "country": "IE", "lat": 53.3498, "lon": -6.2603, "aliases": ["dublin"]},
    "munich":    {"label": "Munich",    "country": "DE", "lat": 48.1351, "lon": 11.5820, "aliases": ["munich", "münchen"]},
    "warsaw":    {"label": "Warsaw",    "country": "PL", "lat": 52.2297, "lon": 21.0122, "aliases": ["warsaw"]},
    "paris":     {"label": "Paris",     "country": "FR", "lat": 48.8566, "lon": 2.3522, "aliases": ["paris"]},
    "zurich":    {"label": "Zurich",    "country": "CH", "lat": 47.3769, "lon": 8.5417, "aliases": ["zurich", "zürich"]},
}

ALL_CITIES = {**TARGET_CITIES, **EU_CITIES}

REMOTE_KEY = "remote"

# Broad tech-role net: entry-level/graduate SWE first, but not exclusively —
# the user asked to also surface general early-career tech roles.
ROLE_KEYWORDS = [
    "software engineer", "software developer", "swe",
    "graduate software", "graduate engineer", "new grad",
    "entry level engineer", "entry-level engineer", "junior engineer",
    "junior developer", "junior software",
    "backend engineer", "frontend engineer", "full stack", "fullstack",
    "platform engineer", "devops engineer", "site reliability",
    "data engineer", "machine learning engineer", "ml engineer",
    "qa engineer", "test engineer",
    "hardware engineer", "firmware engineer", "robotics engineer",
    "systems engineer", "embedded engineer",
    "technical program manager", "solutions engineer", "developer relations",
]

SENIORITY_EXCLUDE = [
    "senior", "staff", "principal", "lead ", "director", "head of",
    "vp ", "vice president", "10+ years", "8+ years", "7+ years", "6+ years",
    "engineering manager", "manager, software", "manager, engineering",
]

# A final-year student wants full-time new-grad roles, not internships/co-ops.
INTERNSHIP_EXCLUDE = ["intern", "internship", "co-op", "co op", "coop"]

# Greenhouse/Lever board slugs are the company's identifier in that ATS's public API,
# not necessarily the company's common name. Each entry below was verified live
# (returns HTTP 200 with jobs) — add more the same way: curl
# boards-api.greenhouse.io/v1/boards/<slug>/jobs or api.lever.co/v0/postings/<slug>.
GREENHOUSE_COMPANIES = [
    "stripe", "figma", "airbnb", "coinbase", "robinhood", "discord",
    "brex", "asana", "gitlab", "airtable", "affirm", "instacart",
    "reddit", "pinterest", "cloudflare", "datadog",
    "monzo", "wolt", "gocardless", "riskified", "fireblocks", "jfrog",
    "cybereason", "samsara", "webflow", "vercel", "mixpanel", "amplitude",
    "mongodb", "elastic", "twilio", "squarespace",
]

LEVER_COMPANIES = ["palantir", "ro"]
