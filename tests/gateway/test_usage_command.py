"""Tests for GatewayRunner._handle_usage_command."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _make_event(text="/usage"):
    source = SessionSource(
        platform="telegram",
        user_id="u1",
        chat_id="c1",
        user_name="tester",
    )
    return MessageEvent(text=text, source=source)


def _make_runner(session_db):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._session_db = session_db
    runner._running_agents = {}
    runner.config = SimpleNamespace(group_sessions_per_user=True, thread_sessions_per_user=False)
    runner.session_store = MagicMock()
    runner.session_store._generate_session_key.return_value = "telegram:u1:c1"
    session_entry = MagicMock()
    session_entry.session_id = "test_session_123"
    runner.session_store.get_or_create_session.return_value = session_entry
    runner.session_store.load_transcript.return_value = []
    return runner


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

        runner = _make_runner(db)
        result = await runner._handle_usage_command(_make_event())

        assert "Session Usage" in result
        assert "Input: 100" in result
        assert "Output: 20" in result
        assert "Cache read: 5" in result
        assert "Cache write: 1" in result
        assert "Reasoning: 7" in result
        assert "Total: 126" in result
        assert "Exact messages: 1" in result
        assert "openai-codex | gpt-5.4 | https://chatgpt.com/backend-api/codex" in result

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

        runner = _make_runner(db)
        result = await runner._handle_usage_command(_make_event())

        assert "Session Usage" in result
        assert "Input: 30" in result
        assert "Output: 12" in result
        assert "Cache read: 4" in result
        assert "Cache write: 1" in result
        assert "Reasoning: 2" in result
        assert "Total: 47" in result
        assert "Telemetry: coarse session totals" in result
        assert "openrouter | anthropic/claude-sonnet-4 | https://openrouter.ai/api/v1" in result

        db.close()

    @pytest.mark.asyncio
    async def test_inactive_session_without_persisted_telemetry_falls_back_to_transcript_estimate(self, tmp_path):
        from hermes_state import SessionDB

        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("test_session_123", "telegram", model="gpt-5.4")

        runner = _make_runner(db)
        runner.session_store.load_transcript.return_value = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]

        result = await runner._handle_usage_command(_make_event())

        assert "Session Info" in result
        assert "Messages: 2" in result
        assert "Estimated context:" in result
        assert "Detailed usage available during active conversations" in result

        db.close()