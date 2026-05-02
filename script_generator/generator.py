"""Script generation via Claude API with niche-aware prompts and prompt caching."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

import anthropic

MODEL = "claude-sonnet-4-6"

# ---------------------------------------------------------------------------
# Niche definitions — system prompts are STABLE so they cache effectively.
# Any dynamic content must stay in the user message, never here.
# ---------------------------------------------------------------------------

_DARK_PSYCHOLOGY_PROMPT = """\
You write TikTok scripts for the Dark Psychology niche.

Tone: slow, intense, mysterious. Every line should feel like a revelation.
Pacing: use '...' to mark deliberate pauses. Sentences are short. Silences matter.
Hook: open with a disturbing truth or question that hijacks attention.
Body: build suspense layer by layer. Reveal one psychological insight at a time.
CTA: end with an unsettling question or command that lingers in the viewer's mind.

Vocabulary: manipulation, cognitive bias, dark triad, covert control, the unconscious.
Never be cheerful. Never rush. Make the viewer lean in.

Output format (JSON only, no other text):
{
  "hook": "<[HOOK] section — 1-2 gripping sentences with '...' pauses>",
  "body": "<[BODY] section — 3-6 sentences revealing the psychological insight, '...' pauses>",
  "cta": "<[CTA] section — 1-2 sentences, unsettling command or question>",
  "data_card": "<bullet-point text for on-screen data card, empty string if format != data_card>",
  "hashtags": ["<5-8 niche hashtags>"]
}"""

_PERSONAL_FINANCE_PROMPT = """\
You write TikTok scripts for the Personal Finance niche.

Tone: direct, confident, data-driven. Every claim needs a number.
Pacing: brisk but clear. Short sentences. No filler words.
Hook: open with a specific shocking statistic or dollar amount.
Body: walk through the mechanism with concrete numbers, percentages, and timelines.
CTA: tell the viewer exactly what to do next — one actionable step.

Vocabulary: compound interest, net worth, debt-to-income, index fund, FIRE, emergency fund.
Always cite specific numbers (e.g. "67% of Americans", "$1,000/month", "7% average return").
Be a trusted expert, not a salesperson.

Output format (JSON only, no other text):
{
  "hook": "<[HOOK] section — 1-2 sentences with a specific statistic or dollar figure>",
  "body": "<[BODY] section — 3-5 sentences with data, percentages, and timelines>",
  "cta": "<[CTA] section — 1 concrete action the viewer should take today>",
  "data_card": "<bullet-point text for on-screen data card, empty string if format != data_card>",
  "hashtags": ["<5-8 niche hashtags>"]
}"""

_AI_TECH_PROMPT = """\
You write TikTok scripts for the AI & Tech niche.

Tone: staccato, energetic, future-forward. High energy from the first word.
Pacing: fast. Very short sentences. Punch each one. No wasted syllables.
Hook: open with a bold claim or mind-blowing fact about AI/tech. Make it undeniable.
Body: rapid-fire insights. Each sentence drops a new revelation. Build momentum.
CTA: hype them up. Tell them to follow, share, or do something RIGHT NOW.

Vocabulary: LLM, neural net, AGI, disruption, exponential, compute, inference, open-source.
Be enthusiastic. Use emphasis. Talk like someone who just saw the future.

Output format (JSON only, no other text):
{
  "hook": "<[HOOK] section — 1-2 punchy sentences, bold AI/tech claim>",
  "body": "<[BODY] section — 4-7 rapid-fire sentences, each a new tech insight>",
  "cta": "<[CTA] section — 1-2 high-energy call-to-action sentences>",
  "data_card": "<bullet-point text for on-screen data card, empty string if format != data_card>",
  "hashtags": ["<5-8 niche hashtags>"]
}"""

_NICHE_SYSTEM_PROMPTS: dict[str, str] = {
    "dark_psychology": _DARK_PSYCHOLOGY_PROMPT,
    "personal_finance": _PERSONAL_FINANCE_PROMPT,
    "ai_tech": _AI_TECH_PROMPT,
}

_NICHE_DEFAULT_HASHTAGS: dict[str, list[str]] = {
    "dark_psychology": [
        "#darkpsychology", "#manipulation", "#mindtricks", "#psychology",
        "#cognitivebiases", "#humanbehavior", "#psychologyfacts",
    ],
    "personal_finance": [
        "#personalfinance", "#investing", "#moneytips", "#financetok",
        "#wealthbuilding", "#financialfreedom", "#moneymindset",
    ],
    "ai_tech": [
        "#AI", "#artificialintelligence", "#tech", "#AItools",
        "#futurism", "#machinelearning", "#tecknews",
    ],
}

NicheKey = Literal["dark_psychology", "personal_finance", "ai_tech"]
Format = Literal["monologue", "data_card", "listicle"]


# ---------------------------------------------------------------------------
# Public data classes
# ---------------------------------------------------------------------------

@dataclass
class ContentBrief:
    niche: NicheKey
    topic: str
    hook: str
    format: Format = "monologue"
    length_target_sec: int = 30


@dataclass
class StructuredScript:
    hook: str
    body: str
    cta: str
    data_card: str
    hashtags: list[str] = field(default_factory=list)
    niche: str = ""
    usage: dict = field(default_factory=dict)

    def as_tagged_script(self) -> str:
        parts = [f"[HOOK]\n{self.hook}", f"[BODY]\n{self.body}", f"[CTA]\n{self.cta}"]
        if self.data_card:
            parts.append(f"[DATA_CARD]\n{self.data_card}")
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

def generate_script(
    brief: ContentBrief,
    client: anthropic.Anthropic | None = None,
) -> StructuredScript:
    """Generate a structured TikTok script for the given ContentBrief.

    Uses prompt caching on the niche system prompt so repeated calls for the
    same niche hit the cache (target: >60% cache_read_input_tokens in batch).
    """
    if brief.niche not in _NICHE_SYSTEM_PROMPTS:
        raise ValueError(
            f"Unknown niche '{brief.niche}'. "
            f"Available: {list(_NICHE_SYSTEM_PROMPTS.keys())}"
        )

    if client is None:
        client = anthropic.Anthropic()

    system_prompt = _NICHE_SYSTEM_PROMPTS[brief.niche]

    data_card_instruction = (
        "Include detailed bullet-point content in 'data_card' suitable for an on-screen graphic."
        if brief.format == "data_card"
        else "Set 'data_card' to an empty string."
    )

    user_message = (
        f"Topic: {brief.topic}\n"
        f"Opening hook idea: {brief.hook}\n"
        f"Format: {brief.format}\n"
        f"Target length: ~{brief.length_target_sec} seconds of spoken content\n"
        f"{data_card_instruction}\n\n"
        "Write the script now. Return only valid JSON."
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                # Cache the stable niche system prompt — this is the key to >60% cache hits.
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_message}],
    )

    raw_text = next(b.text for b in response.content if b.type == "text")

    # Strip markdown code fences if the model wrapped the JSON
    stripped = raw_text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    data = json.loads(stripped)

    default_hashtags = _NICHE_DEFAULT_HASHTAGS.get(brief.niche, [])
    hashtags = data.get("hashtags") or default_hashtags

    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0),
        "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0),
    }

    return StructuredScript(
        hook=data["hook"],
        body=data["body"],
        cta=data["cta"],
        data_card=data.get("data_card", ""),
        hashtags=hashtags,
        niche=brief.niche,
        usage=usage,
    )
