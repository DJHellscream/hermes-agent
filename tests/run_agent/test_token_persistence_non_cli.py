from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hermes_state import AccountingDB
from run_agent import AIAgent
from tools.delegate_tool import _build_child_agent


def _mock_response(*, usage: dict, content: str = "done"):
    msg = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    return SimpleNamespace(
        choices=[choice],
        model="test/model",
        usage=SimpleNamespace(**usage),
    )


def _make_agent(session_db, *, platform: str, accounting_db=None):
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            session_db=session_db,
            accounting_db=accounting_db,
            session_id=f"{platform}-session",
            platform=platform,
        )
    agent.client = MagicMock()
    agent.client.chat.completions.create.return_value = _mock_response(
        usage={
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
        }
    )
    return agent


def test_run_conversation_persists_tokens_for_telegram_sessions():
    session_db = MagicMock()
    agent = _make_agent(session_db, platform="telegram")

    result = agent.run_conversation("hello")

    assert result["final_response"] == "done"
    session_db.update_token_counts.assert_called_once()
    assert session_db.update_token_counts.call_args.args[0] == "telegram-session"


def test_run_conversation_persists_tokens_for_cron_sessions():
    session_db = MagicMock()
    agent = _make_agent(session_db, platform="cron")

    result = agent.run_conversation("hello")

    assert result["final_response"] == "done"
    session_db.update_token_counts.assert_called_once()
    assert session_db.update_token_counts.call_args.args[0] == "cron-session"


def test_agent_init_creates_root_run_in_accounting_db(tmp_path):
    accounting_db = AccountingDB(db_path=tmp_path / "accounting.db")
    try:
        agent = _make_agent(MagicMock(), platform="telegram", accounting_db=accounting_db)

        run = accounting_db.get_agent_run(agent.run_id)
        assert run is not None
        assert run["run_id"] == agent.run_id
        assert run["root_run_id"] == agent.root_run_id == agent.run_id
        assert run["parent_run_id"] is None
        assert run["local_session_id"] == "telegram-session"
        assert run["launch_kind"] == "root"
        assert run["transport_kind"] == "direct"
        assert run["source"] == "telegram"
        assert run["home_id"] == "default"
    finally:
        accounting_db.close()


def test_run_conversation_appends_usage_event_to_accounting_db(tmp_path):
    accounting_db = AccountingDB(db_path=tmp_path / "accounting.db")
    try:
        session_db = MagicMock()
        agent = _make_agent(session_db, platform="cron", accounting_db=accounting_db)

        result = agent.run_conversation("hello")

        assert result["final_response"] == "done"
        events = accounting_db.get_usage_events(run_id=agent.run_id)
        assert len(events) == 1
        event = events[0]
        assert event["run_id"] == agent.run_id
        assert event["root_run_id"] == agent.root_run_id
        assert event["local_session_id"] == "cron-session"
        assert event["provider"] == agent.provider
        assert event["base_url"] == agent.base_url
        assert event["model"] == agent.model
        assert event["input_tokens"] == 11
        assert event["output_tokens"] == 7
        assert event["usage_status"] == "exact"
    finally:
        accounting_db.close()


def test_delegate_child_creates_child_run_and_usage_event_in_same_root_ledger(tmp_path):
    accounting_db = AccountingDB(db_path=tmp_path / "accounting.db")
    try:
        parent = _make_agent(MagicMock(), platform="cli", accounting_db=accounting_db)
        child = _build_child_agent(
            task_index=0,
            goal="Do a child task",
            context=None,
            toolsets=None,
            model=None,
            max_iterations=10,
            parent_agent=parent,
        )
        child.client = MagicMock()
        child.client.chat.completions.create.return_value = _mock_response(
            usage={
                "prompt_tokens": 5,
                "completion_tokens": 3,
                "total_tokens": 8,
            }
        )

        result = child.run_conversation("hello from child")

        assert result["final_response"] == "done"
        child_run = accounting_db.get_agent_run(child.run_id)
        assert child_run is not None
        assert child_run["parent_run_id"] == parent.run_id
        assert child_run["root_run_id"] == parent.root_run_id
        assert child_run["launch_kind"] == "delegate_task"
        assert child_run["transport_kind"] == "direct"

        events = accounting_db.get_usage_events(run_id=child.run_id)
        assert len(events) == 1
        event = events[0]
        assert event["run_id"] == child.run_id
        assert event["root_run_id"] == parent.root_run_id
        assert event["input_tokens"] == 5
        assert event["output_tokens"] == 3
        assert event["usage_status"] == "exact"
    finally:
        accounting_db.close()


def test_acp_run_records_unknown_usage_event_instead_of_fake_zero(tmp_path):
    accounting_db = AccountingDB(db_path=tmp_path / "accounting.db")
    try:
        session_db = MagicMock()
        with (
            patch("run_agent.get_tool_definitions", return_value=[]),
            patch("run_agent.check_toolset_requirements", return_value={}),
            patch("run_agent.OpenAI"),
        ):
            agent = AIAgent(
                api_key="test-key",
                provider="copilot-acp",
                base_url="acp://copilot",
                acp_command="copilot",
                acp_args=["--acp", "--stdio"],
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
                session_db=session_db,
                accounting_db=accounting_db,
                session_id="acp-session",
                platform="cli",
            )
        agent.client = MagicMock()
        msg = SimpleNamespace(content="done", tool_calls=None)
        choice = SimpleNamespace(message=msg, finish_reason="stop")
        agent.client.chat.completions.create.return_value = SimpleNamespace(
            choices=[choice],
            model="copilot-acp/gpt-5.4",
            usage=None,
        )

        result = agent.run_conversation("hello")

        assert result["final_response"] == "done"
        events = accounting_db.get_usage_events(run_id=agent.run_id)
        assert len(events) == 1
        event = events[0]
        assert event["usage_status"] == "unknown"
        assert event["provider"] == "copilot-acp"
        assert event["base_url"] == "acp://copilot"
        assert event["model"] == agent.model
        assert event["input_tokens"] == 0
        assert event["output_tokens"] == 0
    finally:
        accounting_db.close()


def test_context_compaction_keeps_same_global_run_id(tmp_path):
    accounting_db = AccountingDB(db_path=tmp_path / "accounting.db")
    try:
        session_db = MagicMock()
        agent = _make_agent(session_db, platform="cli", accounting_db=accounting_db)
        original_run_id = agent.run_id
        original_session_id = agent.session_id
        agent._cached_system_prompt = "cached-system"
        agent.context_compressor.compress = MagicMock(return_value=[{"role": "user", "content": "summary"}])
        agent._build_system_prompt = MagicMock(return_value="new-system")

        compressed, new_system_prompt = agent._compress_context(
            [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "world"}],
            "system",
            approx_tokens=20,
            task_id=agent.session_id,
        )

        assert compressed == [{"role": "user", "content": "summary"}]
        assert new_system_prompt == "new-system"
        assert agent.run_id == original_run_id
        assert agent.session_id != original_session_id
        runs = accounting_db.get_usage_events(run_id=original_run_id)
        assert runs == []
        stored_run = accounting_db.get_agent_run(original_run_id)
        assert stored_run is not None
        assert stored_run["run_id"] == original_run_id
    finally:
        accounting_db.close()
