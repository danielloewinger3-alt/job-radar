import anthropic
import httpx

from backend.config import ANTHROPIC_MODEL
from backend.util import strip_html

SYSTEM_PROMPT = """You assess small UK service businesses for automation/software opportunities, for someone \
who wants to approach the owner and offer to build a solution using software, AI agents, integrations, \
automation, dashboards, or improved workflows.

Opportunity areas to consider:
- Missed lead recovery: missed-call auto-SMS, AI enquiry handling, lead capture/qualification/routing, CRM \
entry, appointment/viewing/consultation booking, urgent-lead alerts. Highest value where a single missed \
enquiry could mean hundreds or thousands of pounds.
- AI receptionist: website/WhatsApp/SMS/email/voice assistant handling hours, pricing, service and \
appointment questions, booking assistance, FAQs, escalation to a human.
- Lead follow-up automation: instant responses, automated/personalised sequences, appointment/quote/viewing \
reminders, dormant-lead reactivation, pipeline management, AI-suggested responses.
- Booking and scheduling automation: online booking, calendar integration, automatic scheduling/rescheduling, \
reminders, waitlists, staff availability, no-show reduction, auto-rebooking.
- Quote and estimate intake (trades especially): structured request forms, photo/file upload, AI extraction \
of requirements, postcode/urgency/budget collection, AI-generated job summaries, site-visit scheduling, quote \
follow-ups. This is for gathering and organising information, not making final pricing decisions.
- CRM and customer management: lightweight CRM, contact management, pipelines, histories, staff assignment, \
for businesses that appear to run on spreadsheets, inbox, or WhatsApp.
- Business dashboards: revenue/lead/booking dashboards, quote pipelines, outstanding invoices, staff \
performance, a daily "what needs my attention today" summary (overdue quotes, unanswered enquiries, unpaid \
invoices, upcoming appointments, high-value leads).
- Email and inbox automation: classification, lead/customer/urgency detection, suggested replies, automatic \
CRM updates, daily inbox summaries.
- Document automation: proposals, quotes, onboarding docs, job sheets, reports, invoices, PDF/form data \
extraction, automatic organisation.
- Review and reputation management: automated review requests, feedback collection, sentiment analysis, \
suggested responses, complaint detection, monthly reports.
- Customer retention and reactivation: reactivation campaigns, service/rebooking/renewal/MOT/check-up \
reminders, dormant-customer identification, personalised outreach.
- Internal workflow automation: eliminating manual data entry between systems, form processing, task \
creation, approvals, onboarding, job assignment, status tracking, automated reporting.
- AI knowledge assistants: internal search across documents/pricing/policies/SOPs, employee onboarding \
assistant, customer-history summaries.

You will be given a business name, category, and either its website's text content or a note that no \
website was found. Base your assessment ONLY on what's actually given to you — do not invent reviews, \
complaints, or website features you have not been shown. A missing website, or a site with no visible \
booking system / contact method beyond a phone number, is itself strong, legitimate evidence.

Answer this question: what is this business currently doing manually, slowly, inconsistently, or not at \
all that software/AI could improve in a way the owner would realistically pay for? Prioritise specific, \
evidenced problems over generic claims that a business "could use AI."

Respond in exactly this format, nothing else:
OPPORTUNITIES:
- <concrete opportunity>: <one-sentence reason tied to specific evidence you were given>
- <concrete opportunity>: <one-sentence reason>
(2 to 4 bullets, most valuable first)
TAGS: <comma-separated tags chosen from: missed_lead_recovery, ai_receptionist, lead_follow_up, \
booking_automation, quote_intake, crm, dashboards, email_automation, document_automation, \
reputation_management, retention, workflow_automation, knowledge_assistant>"""


def fetch_website_text(url: str) -> str:
    if not url:
        return ""
    if not url.startswith("http"):
        url = "https://" + url
    try:
        resp = httpx.get(
            url, timeout=12, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; prospect-scan/1.0)"},
        )
        if resp.status_code != 200:
            return ""
        return strip_html(resp.text)[:8000]
    except httpx.HTTPError:
        return ""


def analyze_business(name: str, category_label: str, website_text: str) -> tuple[str, str]:
    """Returns (opportunity_summary, comma_separated_tags)."""
    client = anthropic.Anthropic()
    evidence = f"Website content (fetched live, truncated):\n{website_text}" if website_text else "No usable website content was found for this business."
    user = f"Business: {name}\nCategory: {category_label}\n\n{evidence}"

    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in response.content if block.type == "text").strip()

    if "TAGS:" in text:
        summary, _, tag_line = text.partition("TAGS:")
        return summary.strip(), tag_line.strip()
    return text, ""
