"""Unit tests for script_generator — one sample brief per niche."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from script_generator import ContentBrief, StructuredScript, generate_script
from script_generator.generator import (
    _NICHE_DEFAULT_HASHTAGS,
    _NICHE_SYSTEM_PROMPTS,
    MODEL,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_response(payload: dict) -> MagicMock:
    """Build a minimal mock that mimics anthropic.types.Message."""
    text_block = SimpleNamespace(type="text", text=json.dumps(payload))
    usage = SimpleNamespace(
        input_tokens=50,
        output_tokens=200,
        cache_creation_input_tokens=400,
        cache_read_input_tokens=0,
    )
    response = MagicMock()
    response.content = [text_block]
    response.usage = usage
    return response


def _make_client(payload: dict) -> MagicMock:
    client = MagicMock()
    client.messages.create.return_value = _make_mock_response(payload)
    return client


# ---------------------------------------------------------------------------
# Fixtures — one sample brief per niche
# ---------------------------------------------------------------------------

DARK_PSYCHOLOGY_BRIEF = ContentBrief(
    niche="dark_psychology",
    topic="How people use silence as a power move",
    hook="The most powerful thing you can do in an argument is say nothing at all...",
    format="monologue",
    length_target_sec=30,
)

PERSONAL_FINANCE_BRIEF = ContentBrief(
    niche="personal_finance",
    topic="Index fund investing for beginners",
    hook="The average American loses $500,000 by not knowing this one fact",
    format="data_card",
    length_target_sec=45,
)

AI_TECH_BRIEF = ContentBrief(
    niche="ai_tech",
    topic="How GPT models are trained",
    hook="This AI trained on more text than all humans have ever read",
    format="monologue",
    length_target_sec=30,
)

# Representative model outputs — each niche should have a distinct voice
DARK_PSYCHOLOGY_OUTPUT = {
    "hook": "The most powerful weapon is silence... and most people never learn to use it.",
    "body": "When someone attacks you... say nothing. Their words fill the void... "
            "but your silence fills the room. The one who speaks first... loses control. "
            "Masters of dark psychology know... silence isn't empty. It's loaded.",
    "cta": "Next time someone tries to provoke you... just smile. Say nothing. "
           "Watch what happens...",
    "data_card": "",
    "hashtags": ["#darkpsychology", "#silence", "#powerplay", "#manipulation", "#psychology"],
}

PERSONAL_FINANCE_OUTPUT = {
    "hook": "67% of Americans have less than $1,000 in savings. Here's why that number is actually worse than it sounds.",
    "body": "The average American spends $18,000 per year on non-essentials. "
            "If you invested just 20% of that — $3,600/year — at a 7% average index fund return, "
            "you'd have $363,000 in 30 years. That's the gap between retiring at 55 and working until 70.",
    "cta": "Open a low-cost index fund account today. Start with $50. Time is the only thing money can't buy back.",
    "data_card": "• $3,600/year invested\n• 7% avg annual return\n• 30 years = $363,000\n• Expense ratio: <0.05% (VTSAX/VTI)",
    "hashtags": ["#personalfinance", "#indexfunds", "#investing", "#wealthbuilding", "#financetok"],
}

AI_TECH_OUTPUT = {
    "hook": "This AI read more text than every human combined. And it learned to think.",
    "body": "GPT models train on trillions of tokens. One token equals roughly one word. "
            "That's more text than humanity has produced in 10,000 years. "
            "The model predicts the next word. Again. Again. Billions of times. "
            "Then something unexpected happens. It starts to reason.",
    "cta": "Follow for daily AI breakdowns. The future is moving FAST. Don't fall behind.",
    "data_card": "",
    "hashtags": ["#AI", "#GPT", "#artificialintelligence", "#machinelearning", "#tech"],
}


# ---------------------------------------------------------------------------
# Tests — Dark Psychology niche
# ---------------------------------------------------------------------------

class TestDarkPsychology:
    def test_returns_structured_script(self):
        client = _make_client(DARK_PSYCHOLOGY_OUTPUT)
        result = generate_script(DARK_PSYCHOLOGY_BRIEF, client=client)
        assert isinstance(result, StructuredScript)

    def test_hook_is_present(self):
        client = _make_client(DARK_PSYCHOLOGY_OUTPUT)
        result = generate_script(DARK_PSYCHOLOGY_BRIEF, client=client)
        assert result.hook and len(result.hook) > 10

    def test_body_is_present(self):
        client = _make_client(DARK_PSYCHOLOGY_OUTPUT)
        result = generate_script(DARK_PSYCHOLOGY_BRIEF, client=client)
        assert result.body and len(result.body) > 10

    def test_cta_is_present(self):
        client = _make_client(DARK_PSYCHOLOGY_OUTPUT)
        result = generate_script(DARK_PSYCHOLOGY_BRIEF, client=client)
        assert result.cta and len(result.cta) > 5

    def test_pause_markers_in_output(self):
        """Dark psychology niche should use '...' pause markers."""
        client = _make_client(DARK_PSYCHOLOGY_OUTPUT)
        result = generate_script(DARK_PSYCHOLOGY_BRIEF, client=client)
        full = result.as_tagged_script()
        assert "..." in full

    def test_data_card_empty_for_monologue(self):
        client = _make_client(DARK_PSYCHOLOGY_OUTPUT)
        result = generate_script(DARK_PSYCHOLOGY_BRIEF, client=client)
        assert result.data_card == ""

    def test_hashtags_present(self):
        client = _make_client(DARK_PSYCHOLOGY_OUTPUT)
        result = generate_script(DARK_PSYCHOLOGY_BRIEF, client=client)
        assert len(result.hashtags) >= 3

    def test_niche_system_prompt_used(self):
        """Verify the correct niche system prompt is sent to the API."""
        client = _make_client(DARK_PSYCHOLOGY_OUTPUT)
        generate_script(DARK_PSYCHOLOGY_BRIEF, client=client)
        call_kwargs = client.messages.create.call_args.kwargs
        system_blocks = call_kwargs["system"]
        assert any(
            "dark" in b["text"].lower() or "silence" in b["text"].lower()
            for b in system_blocks
        )

    def test_cache_control_on_system_prompt(self):
        """System prompt block must carry cache_control for prompt caching."""
        client = _make_client(DARK_PSYCHOLOGY_OUTPUT)
        generate_script(DARK_PSYCHOLOGY_BRIEF, client=client)
        call_kwargs = client.messages.create.call_args.kwargs
        system_blocks = call_kwargs["system"]
        assert any(b.get("cache_control") for b in system_blocks)

    def test_correct_model_used(self):
        client = _make_client(DARK_PSYCHOLOGY_OUTPUT)
        generate_script(DARK_PSYCHOLOGY_BRIEF, client=client)
        assert client.messages.create.call_args.kwargs["model"] == MODEL


# ---------------------------------------------------------------------------
# Tests — Personal Finance niche
# ---------------------------------------------------------------------------

class TestPersonalFinance:
    def test_returns_structured_script(self):
        client = _make_client(PERSONAL_FINANCE_OUTPUT)
        result = generate_script(PERSONAL_FINANCE_BRIEF, client=client)
        assert isinstance(result, StructuredScript)

    def test_data_card_populated_for_data_card_format(self):
        """data_card format should produce non-empty data card content."""
        client = _make_client(PERSONAL_FINANCE_OUTPUT)
        result = generate_script(PERSONAL_FINANCE_BRIEF, client=client)
        assert result.data_card and len(result.data_card) > 10

    def test_numbers_in_output(self):
        """Personal finance niche should cite numbers."""
        client = _make_client(PERSONAL_FINANCE_OUTPUT)
        result = generate_script(PERSONAL_FINANCE_BRIEF, client=client)
        combined = result.hook + result.body
        # Should contain at least one numeric figure
        assert any(ch.isdigit() for ch in combined)

    def test_data_card_instruction_in_user_message(self):
        """data_card format must include the data card instruction in the user message."""
        client = _make_client(PERSONAL_FINANCE_OUTPUT)
        generate_script(PERSONAL_FINANCE_BRIEF, client=client)
        messages = client.messages.create.call_args.kwargs["messages"]
        user_content = messages[0]["content"]
        assert "data_card" in user_content.lower() or "on-screen" in user_content.lower()

    def test_tagged_script_includes_data_card_section(self):
        client = _make_client(PERSONAL_FINANCE_OUTPUT)
        result = generate_script(PERSONAL_FINANCE_BRIEF, client=client)
        tagged = result.as_tagged_script()
        assert "[DATA_CARD]" in tagged

    def test_hashtags_are_finance_related(self):
        client = _make_client(PERSONAL_FINANCE_OUTPUT)
        result = generate_script(PERSONAL_FINANCE_BRIEF, client=client)
        assert any(
            "finance" in h.lower() or "invest" in h.lower() or "money" in h.lower()
            for h in result.hashtags
        )

    def test_niche_recorded_on_result(self):
        client = _make_client(PERSONAL_FINANCE_OUTPUT)
        result = generate_script(PERSONAL_FINANCE_BRIEF, client=client)
        assert result.niche == "personal_finance"


# ---------------------------------------------------------------------------
# Tests — AI & Tech niche
# ---------------------------------------------------------------------------

class TestAiTech:
    def test_returns_structured_script(self):
        client = _make_client(AI_TECH_OUTPUT)
        result = generate_script(AI_TECH_BRIEF, client=client)
        assert isinstance(result, StructuredScript)

    def test_hook_body_cta_all_populated(self):
        client = _make_client(AI_TECH_OUTPUT)
        result = generate_script(AI_TECH_BRIEF, client=client)
        assert result.hook and result.body and result.cta

    def test_ai_keywords_in_output(self):
        """AI & Tech niche should use tech vocabulary."""
        client = _make_client(AI_TECH_OUTPUT)
        result = generate_script(AI_TECH_BRIEF, client=client)
        combined = (result.hook + result.body + result.cta).lower()
        tech_words = {"ai", "gpt", "model", "train", "token", "neural", "tech"}
        assert any(w in combined for w in tech_words)

    def test_tagged_script_format(self):
        client = _make_client(AI_TECH_OUTPUT)
        result = generate_script(AI_TECH_BRIEF, client=client)
        tagged = result.as_tagged_script()
        assert "[HOOK]" in tagged
        assert "[BODY]" in tagged
        assert "[CTA]" in tagged

    def test_no_data_card_section_for_monologue(self):
        client = _make_client(AI_TECH_OUTPUT)
        result = generate_script(AI_TECH_BRIEF, client=client)
        tagged = result.as_tagged_script()
        assert "[DATA_CARD]" not in tagged

    def test_hashtags_present(self):
        client = _make_client(AI_TECH_OUTPUT)
        result = generate_script(AI_TECH_BRIEF, client=client)
        assert len(result.hashtags) >= 3


# ---------------------------------------------------------------------------
# Tone differentiation — verify the three niches use distinct system prompts
# ---------------------------------------------------------------------------

class TestNicheToneDifferentiation:
    def test_three_distinct_system_prompts_exist(self):
        assert len(_NICHE_SYSTEM_PROMPTS) == 3
        prompts = list(_NICHE_SYSTEM_PROMPTS.values())
        assert prompts[0] != prompts[1]
        assert prompts[1] != prompts[2]
        assert prompts[0] != prompts[2]

    def test_dark_psychology_prompt_mentions_pauses(self):
        prompt = _NICHE_SYSTEM_PROMPTS["dark_psychology"]
        assert "..." in prompt or "pause" in prompt.lower()

    def test_personal_finance_prompt_mentions_numbers(self):
        prompt = _NICHE_SYSTEM_PROMPTS["personal_finance"]
        assert "number" in prompt.lower() or "statistic" in prompt.lower() or "data" in prompt.lower()

    def test_ai_tech_prompt_mentions_energy(self):
        prompt = _NICHE_SYSTEM_PROMPTS["ai_tech"]
        assert "energetic" in prompt.lower() or "staccato" in prompt.lower() or "fast" in prompt.lower()

    def test_different_system_prompts_sent_per_niche(self):
        """Each niche must send a different system prompt to the API."""
        payloads = {
            "dark_psychology": DARK_PSYCHOLOGY_OUTPUT,
            "personal_finance": PERSONAL_FINANCE_OUTPUT,
            "ai_tech": AI_TECH_OUTPUT,
        }
        briefs = {
            "dark_psychology": DARK_PSYCHOLOGY_BRIEF,
            "personal_finance": PERSONAL_FINANCE_BRIEF,
            "ai_tech": AI_TECH_BRIEF,
        }
        system_texts: dict[str, str] = {}
        for niche, brief in briefs.items():
            client = _make_client(payloads[niche])
            generate_script(brief, client=client)
            call_kwargs = client.messages.create.call_args.kwargs
            system_texts[niche] = call_kwargs["system"][0]["text"]

        assert system_texts["dark_psychology"] != system_texts["personal_finance"]
        assert system_texts["personal_finance"] != system_texts["ai_tech"]
        assert system_texts["dark_psychology"] != system_texts["ai_tech"]


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_invalid_niche_raises(self):
        client = MagicMock()
        brief = ContentBrief(niche="cooking", topic="pasta", hook="yummy")  # type: ignore
        with pytest.raises(ValueError, match="Unknown niche"):
            generate_script(brief, client=client)

    def test_markdown_fenced_json_is_parsed(self):
        """The generator should handle model output wrapped in ```json ... ``` fences."""
        payload = DARK_PSYCHOLOGY_OUTPUT
        fenced = f"```json\n{json.dumps(payload)}\n```"
        text_block = SimpleNamespace(type="text", text=fenced)
        usage = SimpleNamespace(
            input_tokens=50, output_tokens=200,
            cache_creation_input_tokens=0, cache_read_input_tokens=400,
        )
        response = MagicMock()
        response.content = [text_block]
        response.usage = usage

        client = MagicMock()
        client.messages.create.return_value = response
        result = generate_script(DARK_PSYCHOLOGY_BRIEF, client=client)
        assert result.hook == payload["hook"]

    def test_usage_stats_captured(self):
        client = _make_client(DARK_PSYCHOLOGY_OUTPUT)
        result = generate_script(DARK_PSYCHOLOGY_BRIEF, client=client)
        assert "cache_creation_input_tokens" in result.usage
        assert "cache_read_input_tokens" in result.usage

    def test_default_hashtags_used_when_model_returns_empty(self):
        payload = {**DARK_PSYCHOLOGY_OUTPUT, "hashtags": []}
        client = _make_client(payload)
        result = generate_script(DARK_PSYCHOLOGY_BRIEF, client=client)
        assert result.hashtags == _NICHE_DEFAULT_HASHTAGS["dark_psychology"]
