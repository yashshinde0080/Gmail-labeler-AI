"""
Prompt construction for the email classifier.

The prompt instructs the LLM to:
    1. Read the email metadata and body.
    2. Pick the single best category from the supplied list.
    3. Propose a new label only if absolutely nothing fits.
    4. Always respond with valid JSON — no extra prose.
"""

from __future__ import annotations

from typing import Any

# Ordered from most to least specific — the model should prefer specifics
KNOWN_CATEGORIES: list[str] = [
    # Work & Professional
    "Work",
    "Clients",
    "Freelancing",
    "Support Tickets",
    # Communication
    "Personal",
    # Finance
    "Finance",
    "Banking",
    "Invoices",
    "Receipts",
    "Bills",
    "Crypto",
    "Investments",
    "Tax",
    # Shopping & Commerce
    "Shopping",
    "Orders",
    "Amazon",
    "Flipkart",
    # Developer Platforms
    "GitHub",
    "GitLab",
    "Docker",
    "Kubernetes",
    "Cloud",
    # Professional Networks
    "LinkedIn",
    "Jobs",
    "Interviews",
    # Academic
    "University",
    "Research",
    "Conferences",
    "IEEE",
    "Taylor & Francis",
    # Health
    "Healthcare",
    "Medical",
    "Insurance",
    # Government
    "Government",
    "Legal",
    # Security
    "OTP",
    "Password Reset",
    "Security Alert",
    # AI / Tech
    "AI",
    "Machine Learning",
    # Marketing
    "Marketing",
    "Promotions",
    "Newsletter",
    # Social
    "Social",
    # Spam-like (but not literal spam)
    "Spam",
    # Travel
    "Travel",
    "Hotels",
    "Flights",
    # Lifestyle
    "Food",
    "Entertainment",
]

RESPONSE_SCHEMA = """{
  "category": "<best matching category from the list, or a new one>",
  "confidence": <integer 0-100>,
  "importance": "<high | medium | low>",
  "archive": <true | false>,
  "star": <true | false>,
  "create_label": <true | false>,
  "new_label": "<only if create_label is true, else empty string>",
  "reason": "<one sentence explanation>"
}"""


def build_classification_prompt(email_data: dict[str, Any]) -> str:
    """
    Build the full system + user prompt string to send to the Groq API.

    The prompt is structured so the model can reliably parse it and the
    JSON output schema is crystal-clear.
    """
    categories_block = "\n".join(f"  - {c}" for c in KNOWN_CATEGORIES)

    email_block = f"""
Subject : {email_data.get("subject", "(no subject)")}
From    : {email_data.get("sender", "")}
To      : {email_data.get("recipient", "")}
Date    : {email_data.get("date", "")}
Attachments: {", ".join(email_data.get("attachments", [])) or "none"}

Body:
{email_data.get("cleaned_text", "") or email_data.get("snippet", "")}
""".strip()

    prompt = f"""You are an expert email classifier.

## Task
Analyse the email below and classify it into exactly one category.

## Available Categories
{categories_block}

## Rules
1. Always choose the most specific category available.
2. Use "create_label" = true ONLY if the email genuinely does not fit any
   known category. In that case, propose a concise label in "new_label".
3. Set "importance" to:
   - "high"   → time-sensitive, requires action, security, interviews, invoices
   - "medium" → informational but useful
   - "low"    → newsletters, marketing, promotions, social notifications
4. Set "archive" to true for low-importance emails (newsletters, promotions).
5. Set "star" to true for high-importance emails only.
6. "confidence" is your certainty (0-100).
7. Respond with ONLY the JSON below — no markdown fences, no extra text.

## Required JSON Response Format
{RESPONSE_SCHEMA}

## Email
{email_block}
"""
    return prompt
