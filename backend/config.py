import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DB_PATH = BASE_DIR / "jobs.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

UPLOAD_DIR = BASE_DIR / "uploads" / "cvs"
MAX_CV_BYTES = 15 * 1024 * 1024  # 15MB

# ---------- Project files (Dossier project attachments) ----------
# Storage root for uploaded project files. init_db() deliberately does NOT create
# this directory; the future project-file service creates it lazily on first
# write. Future project-file modules MUST read this value dynamically as
# ``backend.config.PROJECTFILES_DIR`` (e.g. ``from backend import config`` then
# ``config.PROJECTFILES_DIR``) and MUST NOT bind it with
# ``from backend.config import PROJECTFILES_DIR`` -- the pytest fixture in
# tests/conftest.py monkeypatches this attribute to redirect uploads into a
# temporary directory before anything is written, and an early ``from``-import
# would capture the real path and defeat that isolation.
PROJECTFILES_DIR = BASE_DIR / "uploads" / "projectfiles"

MAX_PROJECT_FILE_BYTES = 50 * 1024 * 1024                  # per file
MAX_PROJECT_FILES_PER_PROJECT = 100                        # per project: file count
MAX_PROJECT_FILES_PER_PROJECT_BYTES = 1024 * 1024 * 1024   # per project: total bytes
MAX_PROJECT_FILES_TOTAL_BYTES = 5 * 1024 * 1024 * 1024     # whole store: total bytes
# Cap on the STORED extracted text (not the source-file size): text extraction
# stops writing after this many bytes.
PROJECT_FILE_TEXT_EXTRACT_MAX_BYTES = 200 * 1024

# Upload allowlist, keyed by lowercase file extension. Documents/data and images
# are stored; archives and CAD/engineering files are stored opaquely and are
# never unpacked or parsed.
PROJECT_FILE_EXTENSIONS = {
    # documents / data
    ".pdf", ".txt", ".md", ".csv", ".tsv", ".json", ".rtf",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt",
    # images (stored, never parsed)
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    # archives (stored opaquely, never unpacked)
    ".zip", ".tar", ".gz", ".tgz",
    # CAD / engineering (stored opaquely, never parsed)
    ".step", ".stp", ".stl", ".iges", ".igs", ".dwg", ".dxf",
    ".3mf", ".f3d", ".sldprt", ".ipt",
}

# Subset of PROJECT_FILE_EXTENSIONS whose text can be extracted for AI features
# this sprint. Legacy .doc/.xls/.ppt may be stored but are not AI-readable yet.
AI_READABLE_EXTENSIONS = {
    ".pdf", ".txt", ".md", ".csv", ".tsv", ".json",
    ".docx", ".xlsx", ".pptx",
}

# ---------- Outreach crawler ----------
# Deliberately tiny: a courtesy read of a business's own site, not a spider.
OUTREACH_CRAWL_MAX_PAGES = 5
OUTREACH_CRAWL_DELAY_SECONDS = 1.0

POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "60"))

ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY", "")
REED_API_KEY = os.getenv("REED_API_KEY", "")
USAJOBS_API_KEY = os.getenv("USAJOBS_API_KEY", "")
USAJOBS_USER_AGENT = os.getenv("USAJOBS_USER_AGENT", "")

GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

# AI-assisted applications: Claude drafts, GPT reviews for how human it sounds, Claude revises.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = "claude-opus-5"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")

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

# ---------- Prospects mode: local SME opportunity scanning ----------

COMPANIES_HOUSE_API_KEY = os.getenv("COMPANIES_HOUSE_API_KEY", "")

# Areas to scan. Start with Bristol; add more UK cities here the same way to expand.
PROSPECT_AREAS = {
    "bristol": {"label": "Bristol", "lat": 51.4545, "lon": -2.5879, "radius_km": 8},
}

# Sectors group the 23 categories into 7 for map-pin coloring. Individually hueing
# all 23 would fail standard categorical-palette limits (~8 max distinguishable hues)
# and be illegible at map-pin size anyway; the category's own label is always shown
# as text alongside its color, so color is never the only signal for which of the
# 23 it actually is. Hex values are the dark-mode steps of a CVD-validated 8-hue
# categorical palette (validated against this app's #070b0c ground), minus the red
# slot, which stays reserved for the app's existing alert/status color.
SECTORS = {
    "health":       {"label": "Health & Care", "color": "#3987e5"},
    "property":     {"label": "Property", "color": "#d95926"},
    "trades":       {"label": "Trades & Construction", "color": "#199e70"},
    "automotive":   {"label": "Automotive", "color": "#c98500"},
    "professional": {"label": "Professional & Financial", "color": "#d55181"},
    "fitness_beauty": {"label": "Fitness & Beauty", "color": "#008300"},
    "hospitality":  {"label": "Hospitality", "color": "#9085e9"},
}

# OSM tag -> our category. OSM only maps businesses with a physical, publicly-visible
# location, so coverage is good for storefronts (dentists, salons, gyms, estate agents)
# and weak-to-empty for van-based trades (plumbers, electricians, cleaners, landscapers)
# that don't have a mapped premises. osm_coverage flags that honestly for the UI.
BUSINESS_CATEGORIES = {
    "dentist":        {"label": "Dentists", "sector": "health", "osm_coverage": "good", "tags": [("amenity", "dentist")]},
    "medical_clinic": {"label": "Medical & Health Clinics", "sector": "health", "osm_coverage": "good", "tags": [("amenity", "clinic"), ("amenity", "doctors")]},
    "physio":         {"label": "Physio & Osteopaths", "sector": "health", "osm_coverage": "fair", "tags": [("healthcare", "physiotherapist")]},
    "estate_agent":   {"label": "Estate & Letting Agents", "sector": "property", "osm_coverage": "good", "tags": [("office", "estate_agent")]},
    "builder":        {"label": "Builders & Construction", "sector": "trades", "osm_coverage": "poor", "tags": [("craft", "builder")]},
    "plumber":        {"label": "Plumbers", "sector": "trades", "osm_coverage": "poor", "tags": [("craft", "plumber")]},
    "electrician":    {"label": "Electricians", "sector": "trades", "osm_coverage": "poor", "tags": [("craft", "electrician")]},
    "hvac":           {"label": "Heating / HVAC", "sector": "trades", "osm_coverage": "poor", "tags": [("craft", "hvac")]},
    "roofer":         {"label": "Roofing", "sector": "trades", "osm_coverage": "poor", "tags": [("craft", "roofer")]},
    "landscaper":     {"label": "Landscaping", "sector": "trades", "osm_coverage": "poor", "tags": [("craft", "gardener")]},
    "cleaning":       {"label": "Cleaning Companies", "sector": "trades", "osm_coverage": "poor", "tags": [("craft", "cleaning")]},
    "car_repair":     {"label": "Garages & MOT Centres", "sector": "automotive", "osm_coverage": "good", "tags": [("shop", "car_repair")]},
    "car_dealer":     {"label": "Car Dealerships", "sector": "automotive", "osm_coverage": "good", "tags": [("shop", "car")]},
    "recruitment":    {"label": "Recruitment Agencies", "sector": "professional", "osm_coverage": "fair", "tags": [("office", "employment_agency")]},
    "accountant":     {"label": "Accountants & Bookkeepers", "sector": "professional", "osm_coverage": "good", "tags": [("office", "accountant")]},
    "financial":      {"label": "Mortgage / Insurance / IFA", "sector": "professional", "osm_coverage": "fair", "tags": [("office", "financial"), ("office", "insurance")]},
    "lawyer":         {"label": "Law Firms", "sector": "professional", "osm_coverage": "good", "tags": [("office", "lawyer")]},
    "gym":            {"label": "Gyms & Personal Training", "sector": "fitness_beauty", "osm_coverage": "good", "tags": [("leisure", "fitness_centre")]},
    "sports_facility":{"label": "Padel / Tennis / Sports", "sector": "fitness_beauty", "osm_coverage": "good", "tags": [("leisure", "sports_centre")]},
    "beauty":         {"label": "Beauty Salons", "sector": "fitness_beauty", "osm_coverage": "good", "tags": [("shop", "beauty")]},
    "hair":           {"label": "Hair Salons & Barbers", "sector": "fitness_beauty", "osm_coverage": "good", "tags": [("shop", "hairdresser")]},
    "hotel":          {"label": "Independent Hotels", "sector": "hospitality", "osm_coverage": "good", "tags": [("tourism", "hotel")]},
    "restaurant":     {"label": "Restaurants & Hospitality", "sector": "hospitality", "osm_coverage": "good", "tags": [("amenity", "restaurant")]},
}

# ---------- News: daily headlines by category ----------
# Free RSS feeds, no key needed. Each URL was fetched live and confirmed to return
# real RSS/Atom XML (some redirect — httpx follows that automatically).
NEWS_CATEGORIES = {
    "world": {
        "label": "World & Geopolitics",
        "feeds": [
            {"name": "BBC World", "url": "http://feeds.bbci.co.uk/news/world/rss.xml"},
            {"name": "The Guardian World", "url": "https://www.theguardian.com/world/rss"},
            {"name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml"},
        ],
    },
    "tech": {
        "label": "Tech",
        "feeds": [
            {"name": "TechCrunch", "url": "https://techcrunch.com/feed/"},
            {"name": "Ars Technica", "url": "https://feeds.arstechnica.com/arstechnica/index"},
            {"name": "Hacker News", "url": "https://hnrss.org/frontpage"},
            {"name": "BBC Technology", "url": "http://feeds.bbci.co.uk/news/technology/rss.xml"},
        ],
    },
    "business": {
        "label": "Finance & Business",
        "feeds": [
            {"name": "Financial Times", "url": "https://www.ft.com/rss/home"},
            {"name": "BBC Business", "url": "http://feeds.bbci.co.uk/news/business/rss.xml"},
            {"name": "The Guardian Business", "url": "https://www.theguardian.com/uk/business/rss"},
            {"name": "CNBC", "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
        ],
    },
    "israel": {
        "label": "Israel",
        # Times of Israel blocks non-browser requests (403 even with a full browser
        # User-Agent); Haaretz's old RSS path no longer resolves to a feed. Both dropped.
        "feeds": [
            {"name": "The Jerusalem Post", "url": "https://www.jpost.com/rss/rssfeedsfrontpage.aspx"},
            {"name": "Ynet News", "url": "https://www.ynetnews.com/Integration/StoryRss3082.xml"},
        ],
    },
    "uk": {
        "label": "UK",
        "feeds": [
            {"name": "BBC UK", "url": "http://feeds.bbci.co.uk/news/uk/rss.xml"},
            {"name": "Sky News UK", "url": "https://feeds.skynews.com/feeds/rss/uk.xml"},
            {"name": "The Guardian UK", "url": "https://www.theguardian.com/uk-news/rss"},
        ],
    },
    "usa": {
        "label": "USA",
        "feeds": [
            {"name": "BBC US & Canada", "url": "http://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"},
            {"name": "NPR", "url": "https://feeds.npr.org/1001/rss.xml"},
            {"name": "The Guardian US", "url": "https://www.theguardian.com/us-news/rss"},
        ],
    },
    "europe": {
        "label": "Europe",
        "feeds": [
            {"name": "BBC Europe", "url": "http://feeds.bbci.co.uk/news/world/europe/rss.xml"},
            {"name": "Euronews", "url": "https://www.euronews.com/rss?level=theme&name=news"},
            {"name": "Politico Europe", "url": "https://www.politico.eu/feed/"},
        ],
    },
}
