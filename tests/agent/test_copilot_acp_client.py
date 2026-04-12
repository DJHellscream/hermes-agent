from unittest.mock import patch

from agent.copilot_acp_client import CopilotACPClient


def test_create_chat_completion_returns_usage_when_prompt_result_includes_it():
    client = CopilotACPClient(api_key="dummy", base_url="acp://copilot", command="hermes", args=["acp"])

    with patch.object(
        client,
        "_run_prompt",
        return_value=(
            "hi",
            "",
            {
                "prompt_tokens": 123,
                "completion_tokens": 45,
                "total_tokens": 168,
                "reasoning_tokens": 6,
                "cached_tokens": 7,
            },
            None,
        ),
    ):
        resp = client.chat.completions.create(
            model="google/gemma-4-26B-A4B-it",
            messages=[{"role": "user", "content": "Reply exactly hi"}],
        )

    assert resp.choices[0].message.content == "hi"
    assert resp.usage is not None
    assert resp.usage.prompt_tokens == 123
    assert resp.usage.completion_tokens == 45
    assert resp.usage.total_tokens == 168
    assert resp.usage.prompt_tokens_details.cached_tokens == 7


def test_create_chat_completion_accepts_camel_case_acp_usage_fields():
    client = CopilotACPClient(api_key="dummy", base_url="acp://copilot", command="hermes", args=["acp"])

    with patch.object(
        client,
        "_run_prompt",
        return_value=(
            "hi",
            "",
            {
                "inputTokens": 10930,
                "outputTokens": 2,
                "totalTokens": 10932,
                "thoughtTokens": 0,
                "cachedReadTokens": 0,
            },
            None,
        ),
    ):
        resp = client.chat.completions.create(
            model="google/gemma-4-26B-A4B-it",
            messages=[{"role": "user", "content": "Reply exactly hi"}],
        )

    assert resp.usage is not None
    assert resp.usage.prompt_tokens == 10930
    assert resp.usage.completion_tokens == 2
    assert resp.usage.total_tokens == 10932
    assert resp.usage.prompt_tokens_details.cached_tokens == 0


def test_create_chat_completion_surfaces_runtime_metadata_from_acp_meta():
    client = CopilotACPClient(api_key="dummy", base_url="acp://copilot", command="hermes", args=["acp"])

    with patch.object(
        client,
        "_run_prompt",
        return_value=(
            "hi",
            "",
            {
                "prompt_tokens": 11,
                "completion_tokens": 3,
                "total_tokens": 14,
            },
            {
                "provider": "custom",
                "base_url": "http://superbif:8000/v1",
                "api_mode": "chat_completions",
            },
        ),
    ):
        resp = client.chat.completions.create(
            model="google/gemma-4-26B-A4B-it",
            messages=[{"role": "user", "content": "Reply exactly hi"}],
        )

    assert getattr(resp, "hermes_provider", None) == "custom"
    assert getattr(resp, "hermes_base_url", None) == "http://superbif:8000/v1"
    assert getattr(resp, "hermes_api_mode", None) == "chat_completions"
