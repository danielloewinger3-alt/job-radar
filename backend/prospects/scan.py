import logging
from datetime import datetime

from sqlmodel import select

from backend.config import BUSINESS_CATEGORIES, PROSPECT_AREAS
from backend.db import get_session
from backend.models import Business
from backend.prospects import companies_house, osm
from backend.prospects.analysis import analyze_business, fetch_website_text

logger = logging.getLogger("prospects")


def discover(area_key: str, category_keys: list[str]) -> dict[str, int]:
    """Fetch businesses from OpenStreetMap for the given categories and insert new ones.
    Returns a per-category count of newly-discovered businesses. Fast, no LLM calls."""
    area = PROSPECT_AREAS[area_key]
    counts: dict[str, int] = {}

    with get_session() as session:
        for category_key in category_keys:
            category = BUSINESS_CATEGORIES[category_key]
            new_count = 0
            try:
                listings = osm.fetch_category(category["tags"], area["lat"], area["lon"], area["radius_km"])
            except Exception:
                logger.exception("osm fetch failed for %s", category_key)
                counts[category_key] = 0
                continue

            for listing in listings:
                business_id = f"osm:{listing['osm_type']}:{listing['osm_id']}"
                if session.get(Business, business_id) is not None:
                    continue
                session.add(Business(
                    id=business_id,
                    area_key=area_key,
                    category=category_key,
                    name=listing["name"],
                    lat=listing["lat"],
                    lon=listing["lon"],
                    address=listing["address"],
                    phone=listing["phone"],
                    website=listing["website"],
                ))
                new_count += 1
            session.commit()
            counts[category_key] = new_count
    return counts


def analyze_pending(area_key: str, limit: int = 10) -> int:
    """Run the Companies House lookup + Claude opportunity assessment on up to `limit`
    not-yet-analyzed businesses in an area. Capped per call since each business costs a
    website fetch plus an LLM call — this keeps a single request bounded."""
    with get_session() as session:
        pending = session.exec(
            select(Business)
            .where(Business.area_key == area_key, Business.analyzed_at == None)  # noqa: E711
            .limit(limit)
        ).all()

        analyzed = 0
        for business in pending:
            category_label = BUSINESS_CATEGORIES.get(business.category, {}).get("label", business.category)
            website_text = fetch_website_text(business.website) if business.website else ""

            ch = companies_house.lookup(business.name)
            if ch:
                business.companies_house_number = ch["number"]
                business.companies_house_status = ch["status"]

            try:
                summary, tags = analyze_business(business.name, category_label, website_text)
                business.opportunity_summary = summary
                business.opportunity_tags = tags
            except Exception:
                logger.exception("claude analysis failed for %s", business.id)
                business.opportunity_summary = "Analysis failed — try again later."

            business.analyzed_at = datetime.utcnow()
            session.add(business)
            session.commit()
            analyzed += 1
    return analyzed
