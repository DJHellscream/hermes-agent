"""Tests for GatewayRunner._handle_usage_command."""

import threading
from unittest.mock import MagicMock, patch

import pytest

from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


SK = "agent:main:telegram:private:12345"


def _make_event(text="/usage"):
    source = SessionSource(
        platform="telegram",
        user_id="u1",
        chat_id="c1",
        user_name="tester",
    )
    return MessageEvent(text=text, source=source)


def _make_mock_agent(**overrides):
    """Create a mock AIAgent with realistic session counters."""
    agent = MagicMock()
    defaults = {
        "model": "anthropic/claude-sonnet-4.6",
        "provider": "openrouter",
        "base_url": None,
        "session_total_tokens": 50_000,
        "session_api_calls": 5,
        "session_prompt_tokens": 40_000,
        "session_completion_tokens": 10_000,
        "session_input_tokens": 35_000,
        "session_output_tokens": 10_000,
        "session_cache_read_tokens": 5_000,
        "session_cache_write_tokens": 2_000,
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        setattr(agent, key, value)

    rl = MagicMock()
    rl.has_data = True
    agent.get_rate_limit_state.return_value = rl

    ctx = MagicMock()
    ctx.last_prompt_tokens = 30_000
    ctx.context_length = 200_000
    ctx.compression_count = 1
    agent.context_compressor = ctx

    return agent


def _make_cached_runner(session_key, agent=None, cached_agent=None):
    """Build a bare GatewayRunner for active/cached-agent /usage tests."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._session_db = None
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    runner.session_store = MagicMock()
    runner._session_key_for_source = MagicMock(return_value=session_key)

    if agent is not None:
        runner._running_agents[session_key] = agent
    if cached_agent is not None:
        runner._agent_cache[session_key] = (cached_agent, "sig")

    return runner


def _make_persisted_runner(session_db):
    """Build a bare GatewayRunner for persisted-telemetry /usage tests."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._session_db = session_db
    runner._running_agents = {}
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    runner._session_key_for_source = MagicMock(return_value="telegram:u1:c1")
    runner.session_store = MagicMock()
    session_entry = MagicMock()
    session_entry.session_id = "test_session_123"
    runner.session_store.get_or_create_session.return_value = session_entry
    runner.session_store.load_transcript.return_value = []
    return runner


class TestUsageCachedAgent:
    @pytest.mark.asyncio
    async def test_cached_agent_shows_detailed_usage(self):
        agent = _make_mock_agent()
        runner = _make_cached_runner(SK, cached_agent=agent)

        with patch("agent.rate_limit_tracker.format_rate_limit_compact", return_value="RPM: 50/60"), \
             patch("agent.usage_pricing.estimate_usage_cost") as mock_cost:
            mock_cost.return_value = MagicMock(amount_usd=0.1234, status="estimated")
            result = await runner._handle_usage_command(_make_event())

        assert "claude-sonnet-4.6" in result
        assert "35,000" in result
        assert "10,000" in result
        assert "5,000" in result
        assert "2,000" in result
        assert "50,000" in result
        assert "$0.1234" in result
        assert "30,000" in result
        assert "Compressions: 1" in result

    @pytest.mark.asyncio
    async def test_running_agent_preferred_over_cache(self):
        running = _make_mock_agent(session_api_calls=10, session_total_tokens=80_000)
        cached = _make_mock_agent(session_api_calls=5, session_total_tokens=50_000)
        runner = _make_cached_runner(SK, agent=running, cached_agent=cached)

        with patch("agent.rate_limit_tracker.format_rate_limit_compact", return_value="RPM: 50/60"), \
             patch("agent.usage_pricing.estimate_usage_cost") as mock_cost:
            mock_cost.return_value = MagicMock(amount_usd=None, status="unknown")
            result = await runner._handle_usage_command(_make_event())

        assert "80,000" in result
        assert "API calls: 10" in result

    @pytest.mark.asyncio
    async def test_sentinel_skipped_uses_cache(self):
        from gateway.run import _AGENT_PENDING_SENTINEL

        cached = _make_mock_agent()
        runner = _make_cached_runner(SK, cached_agent=cached)
        runner._running_agents[SK] = _AGENT_PENDING_SENTINEL

        with patch("agent.rate_limit_tracker.format_rate_limit_compact", return_value="RPM: 50/60"), \
             patch("agent.usage_pricing.estimate_usage_cost") as mock_cost:
            mock_cost.return_value = MagicMock(amount_usd=None, status="unknown")
            result = await runner._handle_usage_command(_make_event())

        assert "claude-sonnet-4.6" in result
        assert "Session Token Usage" in result

    @pytest.mark.asyncio
    async def test_no_agent_anywhere_falls_to_history(self):
        runner = _make_cached_runner(SK)
        session_entry = MagicMock()
        session_entry.session_id = "sess123"
        runner.session_store.get_or_create_session.return_value = session_entry
        runner.session_store.load_transcript.return_value = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]

        with patch("agent.model_metadata.estimate_messages_tokens_rough", return_value=500):
            result = await runner._handle_usage_command(_make_event())

        assert "Session Info" in result
        assert "Messages: 2" in result
        assert "~500" in result
        assert "Detailed usage available after the first agent response" in result

    @pytest.mark.asyncio
    async def test_cache_read_write_hidden_when_zero(self):
        agent = _make_mock_agent(session_cache_read_tokens=0, session_cache_write_tokens=0)
        runner = _make_cached_runner(SK, cached_agent=agent)

        with patch("agent.rate_limit_tracker.format_rate_limit_compact", return_value="RPM: 50/60"), \
             patch("agent.usage_pricing.estimate_usage_cost") as mock_cost:
            mock_cost.return_value = MagicMock(amount_usd=None, status="unknown")
            result = await runner._handle_usage_command(_make_event())

        assert "Cache read" not in result
        assert "Cache write" not in result

    @pytest.mark.asyncio
    async def test_cost_included_status(self):
        agent = _make_mock_agent(provider="openai-codex")
        runner = _make_cached_runner(SK, cached_agent=agent)

        with patch("agent.rate_limit_tracker.format_rate_limit_compact", return_value="RPM: 50/60"), \
             patch("agent.usage_pricing.estimate_usage_cost") as mock_cost:
            mock_cost.return_value = MagicMock(amount_usd=None, status="included")
            result = await runner._handle_usage_command(_make_event())

        assert "Cost: included" in result


class TestHandleUsageCommand:
    @pytest.mark.asyncio
    async def test_inactive_session_uses_persisted_message_telemetry(self, tmp_path):
        from hermes_state import SessionDB

        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("test_session_123", "telegram")
        db.append_message("test_session_123", role="user", content="hello")
        db.append_message(
            "test_session_123",
            role="assistant",
            content="hi",
            provider="openai-codex",
            base_url="https://chatgpt.com/backend-api/codex",
            model="gpt-5.4",
            api_mode="responses",
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=5,
            cache_write_tokens=1,
            reasoning_tokens=7,
            estimated_cost_usd=0.0123,
            usage_status="exact",
        )

        runner = _make_persisted_runner(db)
        result = await runner._handle_usage_command(_make_event())

        assert "Session Usage" in result
        assert "Input: 100" in result
        assert "Output: 20" in result
        assert "Cache read: 5" in result
        assert "Cache write: 1" in result
        assert "Reasoning: 7" in result
        assert "Total: 126" in result
        assert "Estimated cost: $0.0123" in result
        assert "Telemetry: exact message totals" in result
        assert "Exact messages: 1" in result
        assert "openai-codex | gpt-5.4 | https://chatgpt.com/backend-api/codex" in result

        db.close()

    @pytest.mark.asyncio
    async def test_inactive_session_labels_mixed_message_session_telemetry(self, tmp_path):
        from hermes_state import SessionDB

        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("test_session_123", "telegram")
        db.append_message("test_session_123", role="user", content="hello")
        db.append_message(
            "test_session_123",
            role="assistant",
            content="hi",
            provider="openai-codex",
            base_url="https://chatgpt.com/backend-api/codex",
            model="gpt-5.4",
            api_mode="responses",
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=5,
            cache_write_tokens=1,
            reasoning_tokens=3,
            estimated_cost_usd=0.0123,
            usage_status="exact",
        )
        db.set_token_counts(
            "test_session_123",
            input_tokens=150,
            output_tokens=25,
            cache_read_tokens=10,
            cache_write_tokens=2,
            reasoning_tokens=7,
            estimated_cost_usd=0.02,
            billing_provider="openai-codex",
            billing_base_url="https://chatgpt.com/backend-api/codex",
            billing_mode="responses",
            model="gpt-5.4",
        )

        runner = _make_persisted_runner(db)
        result = await runner._handle_usage_command(_make_event())

        assert "Session Usage" in result
        assert "Input: 150" in result
        assert "Output: 25" in result
        assert "Cache read: 10" in result
        assert "Cache write: 2" in result
        assert "Reasoning: 7" in result
        assert "Total: 187" in result
        assert "Estimated cost: $0.02" in result
        assert "Telemetry: mixed message/session totals" in result
        assert "Exact messages: 1" in result

        db.close()

    @pytest.mark.asyncio
    async def test_inactive_session_falls_back_to_session_row_telemetry(self, tmp_path):
        from hermes_state import SessionDB

        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("test_session_123", "telegram")
        db.set_token_counts(
            "test_session_123",
            input_tokens=30,
            output_tokens=12,
            cache_read_tokens=4,
            cache_write_tokens=1,
            reasoning_tokens=2,
            estimated_cost_usd=0.0042,
            billing_provider="openrouter",
            billing_base_url="https://openrouter.ai/api/v1",
            billing_mode="chat_completions",
            model="anthropic/claude-sonnet-4",
        )

        runner = _make_persisted_runner(db)
        result = await runner._handle_usage_command(_make_event())

        assert "Session Usage" in result
        assert "Input: 30" in result
        assert "Output: 12" in result
        assert "Cache read: 4" in result
        assert "Cache write: 1" in result
        assert "Reasoning: 2" in result
        assert "Total: 47" in result
        assert "Estimated cost: $0.0042" in result
        assert "Telemetry: coarse session totals" in result
        assert "openrouter | anthropic/claude-sonnet-4 | https://openrouter.ai/api/v1" in result

        db.close()

    @pytest.mark.asyncio
    async def test_inactive_session_without_persisted_telemetry_falls_back_to_transcript_estimate(self, tmp_path):
        from hermes_state import SessionDB

        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("test_session_123", "telegram", model="gpt-5.4")

        runner = _make_persisted_runner(db)
        runner.session_store.load_transcript.return_value = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]

        result = await runner._handle_usage_command(_make_event())

        assert "Session Info" in result
        assert "Messages: 2" in result
        assert "Estimated context:" in result
        assert "Detailed usage available after the first agent response" in result

        db.close()
