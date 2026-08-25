import anthropic
import openai

from backend.config import ANTHROPIC_MODEL, OPENAI_MODEL
from backend.models import Job, Profile

DRAFT_SYSTEM = (
    "You write concise, specific, human-sounding cover letters for software/tech job "
    "applications. Avoid generic AI-sounding phrases like 'I am excited to apply', "
    "'I would be a perfect fit', or 'I am passionate about leveraging'. Reference 2-3 "
    "concrete things from the job description and connect them to 2-3 concrete things "
    "from the candidate's actual background — specific projects, technologies, or "
    "outcomes, not vague claims. Keep it under 300 words, plain prose, no markdown, "
    "no placeholder brackets. Output only the letter itself, ready to send."
)

CRITIQUE_SYSTEM = (
    "You are a blunt editor reviewing a job application cover letter. Your only job is "
    "to flag anything that sounds AI-generated, generic, or corporate-cliché: stock "
    "phrases, overly enthusiastic tone, vague claims with no specifics, repetitive "
    "sentence structure, anything a human wouldn't actually say out loud. List concrete, "
    "actionable fixes as short bullet points. If it already reads like a real person "
    "wrote it, say so briefly and don't invent problems."
)

REVISE_SYSTEM = (
    "You revise cover letters based on editorial feedback. Keep what already works; fix "
    "what's flagged. Keep it under 300 words, plain prose, no markdown. Output only the "
    "revised letter, ready to send — no preamble, no notes about what you changed."
)


def _claude_text(response: anthropic.types.Message) -> str:
    return "".join(block.text for block in response.content if block.type == "text").strip()


def draft_cover_letter(job: Job, cv_text: str, profile: Profile) -> str:
    client = anthropic.Anthropic()
    user = (
        f"Job title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Job description:\n{job.description_full[:6000]}\n\n"
        f"Candidate name: {profile.full_name or '(not given)'}\n\n"
        f"Candidate's CV text:\n{cv_text[:6000]}\n\n"
        "Write the cover letter."
    )
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=2048,
        system=DRAFT_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    return _claude_text(response)


def critique_for_human_tone(cover_letter: str, job: Job) -> str:
    client = openai.OpenAI()
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": CRITIQUE_SYSTEM},
            {"role": "user", "content": f"Job: {job.title} at {job.company}\n\nCover letter:\n{cover_letter}"},
        ],
    )
    return (response.choices[0].message.content or "").strip()


def revise_with_feedback(cover_letter: str, feedback: str, job: Job) -> str:
    client = anthropic.Anthropic()
    user = (
        f"Job: {job.title} at {job.company}\n\n"
        f"Current draft:\n{cover_letter}\n\n"
        f"Feedback to address:\n{feedback}"
    )
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=2048,
        system=REVISE_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    return _claude_text(response)


def generate_application(job: Job, cv_text: str, profile: Profile) -> tuple[str, str]:
    """Claude drafts, GPT critiques for how human it sounds, Claude revises.
    Returns (final_cover_letter, reviewer_notes)."""
    draft = draft_cover_letter(job, cv_text, profile)
    critique = critique_for_human_tone(draft, job)
    final = revise_with_feedback(draft, critique, job)
    return final, critique
