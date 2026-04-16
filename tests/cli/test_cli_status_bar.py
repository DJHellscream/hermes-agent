from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cli import HermesCLI
from hermes_state import AccountingDB, SessionDB


def _make_cli(model: str = "anthropic/claude-sonnet-4-20250514"):
    cli_obj = HermesCLI.__new__(HermesCLI)
    cli_obj.model = model
    cli_obj.session_start = datetime.now() - timedelta(minutes=14, seconds=32)
    cli_obj.conversation_history = [{"role": "user", "content": "hi"}]
    cli_obj.agent = None
    return cli_obj


def _attach_agent(
    cli_obj,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    api_calls: int,
    context_tokens: int,
    context_length: int,
    compressions: int = 0,
):
    cli_obj.agent = SimpleNamespace(
        model=cli_obj.model,
        provider="anthropic" if cli_obj.model.startswith("anthropic/") else None,
        base_url="",
        session_input_tokens=input_tokens if input_tokens is not None else prompt_tokens,
        session_output_tokens=output_tokens if output_tokens is not None else completion_tokens,
        session_cache_read_tokens=cache_read_tokens,
        session_cache_write_tokens=cache_write_tokens,
        session_prompt_tokens=prompt_tokens,
        session_completion_tokens=completion_tokens,
        session_total_tokens=total_tokens,
        session_api_calls=api_calls,
        get_rate_limit_state=lambda: None,
        context_compressor=SimpleNamespace(
            last_prompt_tokens=context_tokens,
            context_length=context_length,
            compression_count=compressions,
        ),
    )
    return cli_obj


class TestCLIStatusBar:
    def test_context_style_thresholds(self):
        cli_obj = _make_cli()

        assert cli_obj._status_bar_context_style(None) == "class:status-bar-dim"
        assert cli_obj._status_bar_context_style(10) == "class:status-bar-good"
        assert cli_obj._status_bar_context_style(50) == "class:status-bar-warn"
        assert cli_obj._status_bar_context_style(81) == "class:status-bar-bad"
        assert cli_obj._status_bar_context_style(95) == "class:status-bar-critical"

    def test_build_status_bar_text_for_wide_terminal(self):
        cli_obj = _attach_agent(
            _make_cli(),
            prompt_tokens=10_230,
            completion_tokens=2_220,
            total_tokens=12_450,
            api_calls=7,
            context_tokens=12_450,
            context_length=200_000,
        )

        text = cli_obj._build_status_bar_text(width=120)

        assert "claude-sonnet-4-20250514" in text
        assert "12.4K/200K" in text
        assert "6%" in text
        assert "$0.06" not in text  # cost hidden by default
        assert "15m" in text

    def test_input_height_counts_wide_characters_using_cell_width(self):
        cli_obj = _make_cli()

        class _Doc:
            lines = ["你" * 10]

        class _Buffer:
            document = _Doc()

        input_area = SimpleNamespace(buffer=_Buffer())

        def _input_height():
            try:
                from prompt_toolkit.application import get_app
                from prompt_toolkit.utils import get_cwidth

                doc = input_area.buffer.document
                prompt_width = max(2, get_cwidth(cli_obj._get_tui_prompt_text()))
                try:
                    available_width = get_app().output.get_size().columns - prompt_width
                except Exception:
                    import shutil
                    available_width = shutil.get_terminal_size((80, 24)).columns - prompt_width
                if available_width < 10:
                    available_width = 40
                visual_lines = 0
                for line in doc.lines:
                    line_width = get_cwidth(line)
                    if line_width <= 0:
                        visual_lines += 1
                    else:
                        visual_lines += max(1, -(-line_width // available_width))
                return min(max(visual_lines, 1), 8)
            except Exception:
                return 1

        mock_app = MagicMock()
        mock_app.output.get_size.return_value = MagicMock(columns=14)
        with patch.object(HermesCLI, "_get_tui_prompt_text", return_value="❯ "), \
             patch("prompt_toolkit.application.get_app", return_value=mock_app):
            assert _input_height() == 2

    def test_input_height_uses_prompt_toolkit_width_over_shutil(self):
        cli_obj = _make_cli()

        class _Doc:
            lines = ["你" * 10]

        class _Buffer:
            document = _Doc()

        input_area = SimpleNamespace(buffer=_Buffer())

        def _input_height():
            try:
                from prompt_toolkit.application import get_app
                from prompt_toolkit.utils import get_cwidth

                doc = input_area.buffer.document
                prompt_width = max(2, get_cwidth(cli_obj._get_tui_prompt_text()))
                try:
                    available_width = get_app().output.get_size().columns - prompt_width
                except Exception:
                    import shutil
                    available_width = shutil.get_terminal_size((80, 24)).columns - prompt_width
                if available_width < 10:
                    available_width = 40
                visual_lines = 0
                for line in doc.lines:
                    line_width = get_cwidth(line)
                    if line_width <= 0:
                        visual_lines += 1
                    else:
                        visual_lines += max(1, -(-line_width // available_width))
                return min(max(visual_lines, 1), 8)
            except Exception:
                return 1

        mock_app = MagicMock()
        mock_app.output.get_size.return_value = MagicMock(columns=14)
        with patch.object(HermesCLI, "_get_tui_prompt_text", return_value="❯ "), \
             patch("prompt_toolkit.application.get_app", return_value=mock_app), \
             patch("shutil.get_terminal_size") as mock_shutil:
            assert _input_height() == 2
        mock_shutil.assert_not_called()

    def test_build_status_bar_text_no_cost_in_status_bar(self):
        cli_obj = _attach_agent(
            _make_cli(),
            prompt_tokens=10000,
            completion_tokens=5000,
            total_tokens=15000,
            api_calls=7,
            context_tokens=50000,
            context_length=200_000,
        )

        text = cli_obj._build_status_bar_text(width=120)
        assert "$" not in text  # cost is never shown in status bar

    def test_build_status_bar_text_collapses_for_narrow_terminal(self):
        cli_obj = _attach_agent(
            _make_cli(),
            prompt_tokens=10000,
            completion_tokens=2400,
            total_tokens=12400,
            api_calls=7,
            context_tokens=12400,
            context_length=200_000,
        )

        text = cli_obj._build_status_bar_text(width=60)

        assert "⚕" in text
        assert "$0.06" not in text  # cost hidden by default
        assert "15m" in text
        assert "200K" not in text

    def test_build_status_bar_text_handles_missing_agent(self):
        cli_obj = _make_cli()

        text = cli_obj._build_status_bar_text(width=100)

        assert "⚕" in text
        assert "claude-sonnet-4-20250514" in text

    def test_minimal_tui_chrome_threshold(self):
        cli_obj = _make_cli()

        assert cli_obj._use_minimal_tui_chrome(width=63) is True
        assert cli_obj._use_minimal_tui_chrome(width=64) is False

    def test_bottom_input_rule_hides_on_narrow_terminals(self):
        cli_obj = _make_cli()

        assert cli_obj._tui_input_rule_height("top", width=50) == 1
        assert cli_obj._tui_input_rule_height("bottom", width=50) == 0
        assert cli_obj._tui_input_rule_height("bottom", width=90) == 1

    def test_agent_spacer_reclaimed_on_narrow_terminals(self):
        cli_obj = _make_cli()
        cli_obj._agent_running = True

        assert cli_obj._agent_spacer_height(width=50) == 0
        assert cli_obj._agent_spacer_height(width=90) == 1
        cli_obj._agent_running = False
        assert cli_obj._agent_spacer_height(width=90) == 0

    def test_spinner_line_hidden_on_narrow_terminals(self):
        cli_obj = _make_cli()
        cli_obj._spinner_text = "thinking"

        assert cli_obj._spinner_widget_height(width=50) == 0
        assert cli_obj._spinner_widget_height(width=90) == 1
        cli_obj._spinner_text = ""
        assert cli_obj._spinner_widget_height(width=90) == 0

    def test_voice_status_bar_compacts_on_narrow_terminals(self):
        cli_obj = _make_cli()
        cli_obj._voice_mode = True
        cli_obj._voice_recording = False
        cli_obj._voice_processing = False
        cli_obj._voice_tts = True
        cli_obj._voice_continuous = True

        fragments = cli_obj._get_voice_status_fragments(width=50)

        assert fragments == [("class:voice-status", " 🎤 Ctrl+B ")]

    def test_voice_recording_status_bar_compacts_on_narrow_terminals(self):
        cli_obj = _make_cli()
        cli_obj._voice_mode = True
        cli_obj._voice_recording = True
        cli_obj._voice_processing = False

        fragments = cli_obj._get_voice_status_fragments(width=50)

        assert fragments == [("class:voice-status-recording", " ● REC ")]


class TestCLIUsageReport:
    def test_show_usage_includes_estimated_cost(self, capsys):
        cli_obj = _attach_agent(
            _make_cli(),
            prompt_tokens=10_230,
            completion_tokens=2_220,
            total_tokens=12_450,
            api_calls=7,
            context_tokens=12_450,
            context_length=200_000,
            compressions=1,
        )
        cli_obj.verbose = False

        cli_obj._show_usage()
        output = capsys.readouterr().out

        assert "Model:" in output
        assert "Cost status:" in output
        assert "Cost source:" in output
        assert "Total cost:" in output
        assert "$" in output
        assert "0.064" in output
        assert "Session duration:" in output
        assert "Compressions:" in output

    def test_show_usage_marks_unknown_pricing(self, capsys):
        cli_obj = _attach_agent(
            _make_cli(model="local/my-custom-model"),
            prompt_tokens=1_000,
            completion_tokens=500,
            total_tokens=1_500,
            api_calls=1,
            context_tokens=1_000,
            context_length=32_000,
        )
        cli_obj.verbose = False

        cli_obj._show_usage()
        output = capsys.readouterr().out

        assert "Total cost:" in output
        assert "n/a" in output
        assert "Pricing unknown for local/my-custom-model" in output

    def test_zero_priced_provider_models_stay_unknown(self, capsys):
        cli_obj = _attach_agent(
            _make_cli(model="glm-5"),
            prompt_tokens=1_000,
            completion_tokens=500,
            total_tokens=1_500,
            api_calls=1,
            context_tokens=1_000,
            context_length=32_000,
        )
        cli_obj.verbose = False

        cli_obj._show_usage()
        output = capsys.readouterr().out

        assert "Total cost:" in output
        assert "n/a" in output
        assert "Pricing unknown for glm-5" in output


class TestCLIAccountingReport:
    def test_show_accounting_reports_current_task_tree(self, tmp_path, capsys):
        cli_obj = _make_cli()
        cli_obj.session_id = "session-root"

        accounting_db = AccountingDB(db_path=tmp_path / "accounting.db")
        session_db = SessionDB(db_path=tmp_path / "state.db")
        try:
            session_db.create_session(session_id="session-root", source="cli")
            session_db.create_session(session_id="session-child", source="cli", parent_session_id="session-root")
            accounting_db.create_agent_run(
                run_id="root-run",
                root_run_id="root-run",
                local_session_id="session-root",
                home_id="default",
                launch_kind="root",
                transport_kind="direct",
                started_at=1_700_000_000.0,
            )
            accounting_db.create_agent_run(
                run_id="child-run",
                root_run_id="root-run",
                parent_run_id="root-run",
                local_session_id="session-child",
                home_id="worker",
                profile_name="worker-profile",
                launch_kind="delegate_task",
                transport_kind="acp",
                started_at=1_700_000_010.0,
            )
            accounting_db.append_usage_event(
                run_id="root-run",
                root_run_id="root-run",
                local_session_id="session-root",
                home_id="default",
                provider="openai-codex",
                base_url="https://chatgpt.com/backend-api/codex",
                model="gpt-5.4",
                input_tokens=100,
                output_tokens=10,
                estimated_cost_usd=0.001,
                usage_status="exact",
            )
            accounting_db.append_usage_event(
                run_id="child-run",
                root_run_id="root-run",
                local_session_id="session-child",
                home_id="worker",
                profile_name="worker-profile",
                provider="custom",
                base_url="http://worker.example/v1",
                model="google/gemma-4-26B-A4B-it",
                input_tokens=40,
                output_tokens=4,
                cache_write_tokens=3,
                reasoning_tokens=2,
                estimated_cost_usd=0.002,
                usage_status="exact",
            )

            cli_obj._session_db = session_db
            cli_obj.agent = SimpleNamespace(root_run_id="root-run", run_id="root-run", _accounting_db=accounting_db)

            cli_obj._show_accounting()
            output = capsys.readouterr().out

            assert "Task accounting" in output
            assert "Root run: root-run" in output
            assert "Root agent only" in output
            assert "Subagents only" in output
            assert "Whole task" in output
            assert "Breakdown by provider | model | base_url | api_mode" in output
            assert "openai-codex | gpt-5.4 | https://chatgpt.com/backend-api/codex" in output
            assert "custom | google/gemma-4-26B-A4B-it | http://worker.example/v1" in output
            assert "cache-write 3" in output
            assert "reasoning 2" in output
            assert "Run tree" in output
            assert "root     root-run" in output
            assert "child    child-ru" in output
            assert "profile=worker-profile" in output
            assert "transport=acp" in output
            assert "launch=delegate_task" in output
            assert "Session links" in output
            assert "Accounting notes" in output
            assert "NOTE: Run is still active; totals may still change." in output
        finally:
            session_db.close()
            accounting_db.close()

    def test_show_accounting_breakdown_labels_include_api_mode(self, tmp_path, capsys):
        cli_obj = _make_cli()
        cli_obj.session_id = "session-root"

        accounting_db = AccountingDB(db_path=tmp_path / "accounting.db")
        session_db = SessionDB(db_path=tmp_path / "state.db")
        try:
            session_db.create_session(session_id="session-root", source="cli")
            accounting_db.create_agent_run(
                run_id="root-run",
                root_run_id="root-run",
                local_session_id="session-root",
                home_id="default",
                launch_kind="root",
                transport_kind="direct",
                started_at=1_700_000_000.0,
            )
            for api_mode, input_tokens, output_tokens in (
                ("responses", 100, 10),
                ("chat_completions", 40, 4),
            ):
                accounting_db.append_usage_event(
                    run_id="root-run",
                    root_run_id="root-run",
                    local_session_id="session-root",
                    home_id="default",
                    provider="openai",
                    base_url="https://api.openai.com/v1",
                    model="gpt-5.4",
                    api_mode=api_mode,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    usage_status="exact",
                )

            cli_obj._session_db = session_db
            cli_obj.agent = SimpleNamespace(root_run_id="root-run", run_id="root-run", _accounting_db=accounting_db)

            cli_obj._show_accounting()
            output = capsys.readouterr().out

            assert "Breakdown by provider | model | base_url | api_mode" in output
            assert "openai | gpt-5.4 | https://api.openai.com/v1 | api_mode=responses" in output
            assert "openai | gpt-5.4 | https://api.openai.com/v1 | api_mode=chat_completions" in output
        finally:
            session_db.close()
            accounting_db.close()

    def test_show_accounting_current_aggregates_all_roots_for_session(self, tmp_path, capsys):
        cli_obj = _make_cli()
        cli_obj.session_id = "session-root"
        cli_obj.agent = None

        accounting_db = AccountingDB(db_path=tmp_path / "accounting.db")
        session_db = SessionDB(db_path=tmp_path / "state.db")
        try:
            session_db.create_session(session_id="session-root", source="cli")
            accounting_db.create_agent_run(
                run_id="older-root",
                root_run_id="older-root",
                local_session_id="session-root",
                home_id="default",
                launch_kind="root",
                transport_kind="direct",
                started_at=100.0,
            )
            accounting_db.create_agent_run(
                run_id="newer-root",
                root_run_id="newer-root",
                local_session_id="session-root",
                home_id="default",
                launch_kind="root",
                transport_kind="direct",
                started_at=200.0,
            )
            accounting_db.append_usage_event(
                run_id="older-root",
                root_run_id="older-root",
                local_session_id="session-root",
                home_id="default",
                provider="openai-codex",
                base_url="https://chatgpt.com/backend-api/codex",
                model="gpt-5.4",
                input_tokens=5,
                output_tokens=1,
                usage_status="exact",
            )
            accounting_db.append_usage_event(
                run_id="newer-root",
                root_run_id="newer-root",
                local_session_id="session-root",
                home_id="default",
                provider="custom",
                base_url="http://worker.example/v1",
                model="google/gemma-4-26B-A4B-it",
                input_tokens=7,
                output_tokens=2,
                usage_status="exact",
            )

            cli_obj._session_db = session_db
            with patch("hermes_state.AccountingDB", return_value=accounting_db):
                cli_obj._show_accounting("/accounting current")
            output = capsys.readouterr().out

            assert "Scope: current session" in output
            assert "Root runs: 2" in output
            assert "openai-codex | gpt-5.4 | https://chatgpt.com/backend-api/codex" in output
            assert "custom | google/gemma-4-26B-A4B-it | http://worker.example/v1" in output
            assert "in 12  out 3  total 15" in output
        finally:
            session_db.close()
            accounting_db.close()

    def test_show_accounting_breakdown_aggregates_same_route_across_runs(self, tmp_path, capsys):
        cli_obj = _make_cli()
        cli_obj.session_id = "session-root"
        cli_obj.agent = None

        accounting_db = AccountingDB(db_path=tmp_path / "accounting.db")
        session_db = SessionDB(db_path=tmp_path / "state.db")
        try:
            session_db.create_session(session_id="session-root", source="cli")
            for run_id, started_at, input_tokens, output_tokens in (
                ("older-root", 100.0, 5, 1),
                ("newer-root", 200.0, 7, 2),
            ):
                accounting_db.create_agent_run(
                    run_id=run_id,
                    root_run_id=run_id,
                    local_session_id="session-root",
                    home_id="default",
                    launch_kind="root",
                    transport_kind="direct",
                    started_at=started_at,
                )
                accounting_db.append_usage_event(
                    run_id=run_id,
                    root_run_id=run_id,
                    local_session_id="session-root",
                    home_id="default",
                    provider="openai-codex",
                    base_url="https://chatgpt.com/backend-api/codex",
                    model="gpt-5.4",
                    api_mode="codex_responses",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    usage_status="exact",
                )

            cli_obj._session_db = session_db
            with patch("hermes_state.AccountingDB", return_value=accounting_db):
                cli_obj._show_accounting("/accounting current")
            output = capsys.readouterr().out

            route = "openai-codex | gpt-5.4 | https://chatgpt.com/backend-api/codex | api_mode=codex_responses"
            assert output.count(route) == 1
            assert "in 12  out 3  total 15  events 2  exact 2 unknown 0" in output
        finally:
            session_db.close()
            accounting_db.close()

    def test_show_accounting_all_aggregates_every_root_run(self, tmp_path, capsys):
        cli_obj = _make_cli()
        cli_obj.session_id = "session-root"
        cli_obj.agent = None

        accounting_db = AccountingDB(db_path=tmp_path / "accounting.db")
        session_db = SessionDB(db_path=tmp_path / "state.db")
        try:
            session_db.create_session(session_id="session-root", source="cli")
            session_db.create_session(session_id="other-session", source="cli")
            accounting_db.create_agent_run(
                run_id="root-a",
                root_run_id="root-a",
                local_session_id="session-root",
                home_id="default",
                launch_kind="root",
                transport_kind="direct",
                started_at=100.0,
            )
            accounting_db.create_agent_run(
                run_id="root-b",
                root_run_id="root-b",
                local_session_id="other-session",
                home_id="default",
                launch_kind="root",
                transport_kind="direct",
                started_at=200.0,
            )
            accounting_db.append_usage_event(
                run_id="root-a",
                root_run_id="root-a",
                local_session_id="session-root",
                home_id="default",
                provider="openai-codex",
                base_url="https://chatgpt.com/backend-api/codex",
                model="gpt-5.4",
                input_tokens=5,
                output_tokens=1,
                usage_status="exact",
            )
            accounting_db.append_usage_event(
                run_id="root-b",
                root_run_id="root-b",
                local_session_id="other-session",
                home_id="default",
                provider="custom",
                base_url="http://worker/v1",
                model="local-model",
                input_tokens=7,
                output_tokens=2,
                usage_status="exact",
            )

            cli_obj._session_db = session_db
            with patch("hermes_state.AccountingDB", return_value=accounting_db):
                cli_obj._show_accounting("/accounting all")
            output = capsys.readouterr().out

            assert "Scope: all" in output
            assert "Root runs: 2" in output
            assert "openai-codex | gpt-5.4 | https://chatgpt.com/backend-api/codex" in output
            assert "custom | local-model | http://worker/v1" in output
            assert "in 12  out 3  total 15" in output
            assert "Run tree" not in output
            assert "Session links" not in output
            assert "/accounting <root_run_id> for per-task details" in output
        finally:
            session_db.close()
            accounting_db.close()

    def test_show_accounting_all_summarizes_repeated_provenance_warnings(self, tmp_path, capsys):
        cli_obj = _make_cli()
        cli_obj.session_id = "session-root"
        cli_obj.agent = None

        accounting_db = AccountingDB(db_path=tmp_path / "accounting.db")
        session_db = SessionDB(db_path=tmp_path / "state.db")
        try:
            for run_id, session_id, started_at in (
                ("root-a", "missing-a", 100.0),
                ("root-b", "missing-b", 200.0),
                ("root-c", "missing-c", 300.0),
            ):
                accounting_db.create_agent_run(
                    run_id=run_id,
                    root_run_id=run_id,
                    local_session_id=session_id,
                    home_id="default",
                    launch_kind="root",
                    transport_kind="direct",
                    started_at=started_at,
                )
                accounting_db.append_usage_event(
                    run_id=run_id,
                    root_run_id=run_id,
                    local_session_id=session_id,
                    home_id="default",
                    provider="openai-codex",
                    base_url="https://chatgpt.com/backend-api/codex",
                    model="gpt-5.4",
                    input_tokens=5,
                    output_tokens=1,
                    usage_status="exact",
                )

            cli_obj._session_db = session_db
            with patch("hermes_state.AccountingDB", return_value=accounting_db):
                cli_obj._show_accounting("/accounting all")
            output = capsys.readouterr().out

            assert output.count("No matching local session row was found") == 1
            assert "No matching local session row was found for 3 runs in this scope." in output
            assert "Run tree" not in output
            assert "Session links" not in output
        finally:
            session_db.close()
            accounting_db.close()

    def test_show_accounting_all_uses_compact_scope_metadata(self, tmp_path, capsys):
        cli_obj = _make_cli()
        cli_obj.session_id = "session-root"
        cli_obj.agent = None

        accounting_db = AccountingDB(db_path=tmp_path / "accounting.db")
        session_db = SessionDB(db_path=tmp_path / "state.db")
        try:
            session_db.create_session(session_id="session-root", source="cli")
            session_db.create_session(session_id="other-session", source="cli")
            for run_id, session_id, started_at in (
                ("root-a", "session-root", 100.0),
                ("root-b", "other-session", 200.0),
            ):
                accounting_db.create_agent_run(
                    run_id=run_id,
                    root_run_id=run_id,
                    local_session_id=session_id,
                    home_id="default",
                    launch_kind="root",
                    transport_kind="direct",
                    started_at=started_at,
                )
                accounting_db.append_usage_event(
                    run_id=run_id,
                    root_run_id=run_id,
                    local_session_id=session_id,
                    home_id="default",
                    provider="openai-codex",
                    base_url="https://chatgpt.com/backend-api/codex",
                    model="gpt-5.4",
                    input_tokens=5,
                    output_tokens=1,
                    usage_status="exact",
                )

            cli_obj._session_db = session_db
            with patch("hermes_state.AccountingDB", return_value=accounting_db):
                cli_obj._show_accounting("/accounting all")
            output = capsys.readouterr().out

            assert "⏳ Summarizing all-scope accounting..." in output
            assert "Sessions: 2 total" in output
            assert "Sessions: other-session, session-root" not in output
            assert "Ended: running" in output
            assert "NOTE: Some root runs are still active; totals may still change." not in output
        finally:
            session_db.close()
            accounting_db.close()

    def test_show_accounting_all_keeps_summary_shape_with_single_root(self, tmp_path, capsys):
        cli_obj = _make_cli()
        cli_obj.session_id = "session-root"
        cli_obj.agent = None

        accounting_db = AccountingDB(db_path=tmp_path / "accounting.db")
        session_db = SessionDB(db_path=tmp_path / "state.db")
        try:
            session_db.create_session(session_id="session-root", source="cli")
            accounting_db.create_agent_run(
                run_id="root-a",
                root_run_id="root-a",
                local_session_id="session-root",
                home_id="default",
                launch_kind="root",
                transport_kind="direct",
                started_at=100.0,
            )
            accounting_db.append_usage_event(
                run_id="root-a",
                root_run_id="root-a",
                local_session_id="session-root",
                home_id="default",
                provider="openai-codex",
                base_url="https://chatgpt.com/backend-api/codex",
                model="gpt-5.4",
                input_tokens=5,
                output_tokens=1,
                usage_status="exact",
            )

            cli_obj._session_db = session_db
            with patch("hermes_state.AccountingDB", return_value=accounting_db):
                cli_obj._show_accounting("/accounting all")
            output = capsys.readouterr().out

            assert "Accounting\nScope: all" in output
            assert "Task accounting" not in output
            assert "Root run:" not in output
            assert "Session:" not in output
            assert "Home:" not in output
            assert "Sessions: 1 total" in output
            assert "Whole scope" in output
            assert "Whole task" not in output
            assert "Run tree" not in output
            assert "Session links" not in output
        finally:
            session_db.close()
            accounting_db.close()

    def test_show_accounting_all_reports_mixed_lifecycle_when_some_roots_ended(self, tmp_path, capsys):
        cli_obj = _make_cli()
        cli_obj.session_id = "session-root"
        cli_obj.agent = None

        accounting_db = AccountingDB(db_path=tmp_path / "accounting.db")
        session_db = SessionDB(db_path=tmp_path / "state.db")
        try:
            session_db.create_session(session_id="session-root", source="cli")
            session_db.create_session(session_id="other-session", source="cli")
            accounting_db.create_agent_run(
                run_id="root-a",
                root_run_id="root-a",
                local_session_id="session-root",
                home_id="default",
                launch_kind="root",
                transport_kind="direct",
                started_at=100.0,
            )
            accounting_db.create_agent_run(
                run_id="root-b",
                root_run_id="root-b",
                local_session_id="other-session",
                home_id="default",
                launch_kind="root",
                transport_kind="direct",
                started_at=200.0,
            )
            accounting_db.end_agent_run("root-a")
            for run_id, session_id in (("root-a", "session-root"), ("root-b", "other-session")):
                accounting_db.append_usage_event(
                    run_id=run_id,
                    root_run_id=run_id,
                    local_session_id=session_id,
                    home_id="default",
                    provider="openai-codex",
                    base_url="https://chatgpt.com/backend-api/codex",
                    model="gpt-5.4",
                    input_tokens=5,
                    output_tokens=1,
                    usage_status="exact",
                )

            cli_obj._session_db = session_db
            with patch("hermes_state.AccountingDB", return_value=accounting_db):
                cli_obj._show_accounting("/accounting all")
            output = capsys.readouterr().out

            assert "Ended: mixed" in output
            assert "NOTE: Some root runs are still active; totals may still change." not in output
        finally:
            session_db.close()
            accounting_db.close()

    def test_show_accounting_all_limits_breakdown_rows(self, tmp_path, capsys):
        cli_obj = _make_cli()
        cli_obj.session_id = "session-root"
        cli_obj.agent = None

        accounting_db = AccountingDB(db_path=tmp_path / "accounting.db")
        session_db = SessionDB(db_path=tmp_path / "state.db")
        try:
            for idx in range(12):
                session_id = f"session-{idx}"
                session_db.create_session(session_id=session_id, source="cli")
                run_id = f"root-{idx}"
                accounting_db.create_agent_run(
                    run_id=run_id,
                    root_run_id=run_id,
                    local_session_id=session_id,
                    home_id="default",
                    launch_kind="root",
                    transport_kind="direct",
                    started_at=100.0 + idx,
                )
                accounting_db.append_usage_event(
                    run_id=run_id,
                    root_run_id=run_id,
                    local_session_id=session_id,
                    home_id="default",
                    provider=f"provider-{idx}",
                    base_url=f"http://route-{idx}.example/v1",
                    model=f"model-{idx}",
                    api_mode="chat_completions",
                    input_tokens=100 - idx,
                    output_tokens=1,
                    usage_status="exact",
                )

            cli_obj._session_db = session_db
            with patch("hermes_state.AccountingDB", return_value=accounting_db):
                cli_obj._show_accounting("/accounting all")
            output = capsys.readouterr().out

            assert "provider-0 | model-0 | http://route-0.example/v1 | api_mode=chat_completions" in output
            assert "provider-9 | model-9 | http://route-9.example/v1 | api_mode=chat_completions" in output
            assert "provider-10 | model-10 | http://route-10.example/v1 | api_mode=chat_completions" not in output
            assert "provider-11 | model-11 | http://route-11.example/v1 | api_mode=chat_completions" not in output
            assert "2 more routes omitted from /accounting all" in output
        finally:
            session_db.close()
            accounting_db.close()

    def test_show_accounting_all_keeps_high_cost_routes_visible(self, tmp_path, capsys):
        cli_obj = _make_cli()
        cli_obj.session_id = "session-root"
        cli_obj.agent = None

        accounting_db = AccountingDB(db_path=tmp_path / "accounting.db")
        session_db = SessionDB(db_path=tmp_path / "state.db")
        try:
            for idx in range(10):
                session_id = f"cheap-session-{idx}"
                session_db.create_session(session_id=session_id, source="cli")
                run_id = f"cheap-root-{idx}"
                accounting_db.create_agent_run(
                    run_id=run_id,
                    root_run_id=run_id,
                    local_session_id=session_id,
                    home_id="default",
                    launch_kind="root",
                    transport_kind="direct",
                    started_at=100.0 + idx,
                )
                accounting_db.append_usage_event(
                    run_id=run_id,
                    root_run_id=run_id,
                    local_session_id=session_id,
                    home_id="default",
                    provider=f"cheap-provider-{idx}",
                    base_url=f"http://cheap-{idx}.example/v1",
                    model=f"cheap-model-{idx}",
                    api_mode="chat_completions",
                    input_tokens=1000 - idx,
                    output_tokens=1,
                    estimated_cost_usd=0.001,
                    usage_status="exact",
                )

            session_db.create_session(session_id="expensive-session", source="cli")
            accounting_db.create_agent_run(
                run_id="expensive-root",
                root_run_id="expensive-root",
                local_session_id="expensive-session",
                home_id="default",
                launch_kind="root",
                transport_kind="direct",
                started_at=500.0,
            )
            accounting_db.append_usage_event(
                run_id="expensive-root",
                root_run_id="expensive-root",
                local_session_id="expensive-session",
                home_id="default",
                provider="expensive-provider",
                base_url="http://expensive.example/v1",
                model="expensive-model",
                api_mode="chat_completions",
                input_tokens=5,
                output_tokens=1,
                estimated_cost_usd=5.0,
                usage_status="exact",
            )

            session_db.create_session(session_id="omitted-session", source="cli")
            accounting_db.create_agent_run(
                run_id="omitted-root",
                root_run_id="omitted-root",
                local_session_id="omitted-session",
                home_id="default",
                launch_kind="root",
                transport_kind="direct",
                started_at=600.0,
            )
            accounting_db.append_usage_event(
                run_id="omitted-root",
                root_run_id="omitted-root",
                local_session_id="omitted-session",
                home_id="default",
                provider="omitted-provider",
                base_url="http://omitted.example/v1",
                model="omitted-model",
                api_mode="chat_completions",
                input_tokens=900,
                output_tokens=1,
                estimated_cost_usd=0.0001,
                usage_status="exact",
            )

            cli_obj._session_db = session_db
            with patch("hermes_state.AccountingDB", return_value=accounting_db):
                cli_obj._show_accounting("/accounting all")
            output = capsys.readouterr().out

            assert "expensive-provider | expensive-model | http://expensive.example/v1 | api_mode=chat_completions" in output
            assert "omitted-provider | omitted-model | http://omitted.example/v1 | api_mode=chat_completions" not in output
            assert "2 more routes omitted from /accounting all" in output
        finally:
            session_db.close()
            accounting_db.close()

    def test_show_accounting_all_counts_all_distinct_sessions_in_scope(self, tmp_path, capsys):
        cli_obj = _make_cli()
        cli_obj.session_id = "session-root-a"
        cli_obj.agent = None

        accounting_db = AccountingDB(db_path=tmp_path / "accounting.db")
        session_db = SessionDB(db_path=tmp_path / "state.db")
        try:
            for session_id, parent in (
                ("session-root-a", None),
                ("session-root-b", None),
                ("session-child-a", "session-root-a"),
                ("session-child-b", "session-root-b"),
            ):
                session_db.create_session(session_id=session_id, source="cli", parent_session_id=parent)

            accounting_db.create_agent_run(
                run_id="root-a",
                root_run_id="root-a",
                local_session_id="session-root-a",
                home_id="default",
                launch_kind="root",
                transport_kind="direct",
                started_at=100.0,
            )
            accounting_db.create_agent_run(
                run_id="child-a",
                root_run_id="root-a",
                parent_run_id="root-a",
                local_session_id="session-child-a",
                home_id="worker",
                profile_name="worker-a",
                launch_kind="delegate_task",
                transport_kind="acp",
                started_at=110.0,
            )
            accounting_db.create_agent_run(
                run_id="root-b",
                root_run_id="root-b",
                local_session_id="session-root-b",
                home_id="default",
                launch_kind="root",
                transport_kind="direct",
                started_at=200.0,
            )
            accounting_db.create_agent_run(
                run_id="child-b",
                root_run_id="root-b",
                parent_run_id="root-b",
                local_session_id="session-child-b",
                home_id="worker",
                profile_name="worker-b",
                launch_kind="delegate_task",
                transport_kind="acp",
                started_at=210.0,
            )
            for run_id, root_run_id, session_id in (
                ("root-a", "root-a", "session-root-a"),
                ("child-a", "root-a", "session-child-a"),
                ("root-b", "root-b", "session-root-b"),
                ("child-b", "root-b", "session-child-b"),
            ):
                accounting_db.append_usage_event(
                    run_id=run_id,
                    root_run_id=root_run_id,
                    local_session_id=session_id,
                    home_id="default",
                    provider="openai-codex",
                    base_url="https://chatgpt.com/backend-api/codex",
                    model="gpt-5.4",
                    input_tokens=5,
                    output_tokens=1,
                    usage_status="exact",
                )

            cli_obj._session_db = session_db
            with patch("hermes_state.AccountingDB", return_value=accounting_db):
                cli_obj._show_accounting("/accounting all")
            output = capsys.readouterr().out

            assert "Sessions: 4 total" in output
        finally:
            session_db.close()
            accounting_db.close()

    def test_show_accounting_warns_when_usage_is_non_exact(self, tmp_path, capsys):
        cli_obj = _make_cli()
        cli_obj.session_id = "session-root"

        accounting_db = AccountingDB(db_path=tmp_path / "accounting.db")
        session_db = SessionDB(db_path=tmp_path / "state.db")
        try:
            session_db.create_session(session_id="session-root", source="cli")
            accounting_db.create_agent_run(
                run_id="root-run",
                root_run_id="root-run",
                local_session_id="session-root",
                home_id="default",
                launch_kind="root",
                transport_kind="direct",
                started_at=1_700_000_000.0,
            )
            accounting_db.append_usage_event(
                run_id="root-run",
                root_run_id="root-run",
                local_session_id="session-root",
                home_id="default",
                provider="openai-codex",
                base_url="https://chatgpt.com/backend-api/codex",
                model="gpt-5.4",
                input_tokens=100,
                output_tokens=10,
                estimated_cost_usd=0.001,
                usage_status="exact",
            )
            accounting_db.append_usage_event(
                run_id="root-run",
                root_run_id="root-run",
                local_session_id="session-root",
                home_id="default",
                provider="openai-codex",
                base_url="https://chatgpt.com/backend-api/codex",
                model="gpt-5.4",
                input_tokens=25,
                output_tokens=5,
                estimated_cost_usd=0.0002,
                usage_status="unknown",
            )

            cli_obj._session_db = session_db
            cli_obj.agent = SimpleNamespace(root_run_id="root-run", run_id="root-run", _accounting_db=accounting_db)

            cli_obj._show_accounting()
            output = capsys.readouterr().out

            assert "Unknown events:  1" in output
            assert "WARNING: Unknown/non-exact usage present" in output
            assert "totals and estimated cost are not exact" in output
        finally:
            session_db.close()
            accounting_db.close()


class TestStatusBarWidthSource:
    """Ensure status bar fragments don't overflow the terminal width."""

    def _make_wide_cli(self):
        from datetime import datetime, timedelta
        cli_obj = _attach_agent(
            _make_cli(),
            prompt_tokens=100_000,
            completion_tokens=5_000,
            total_tokens=105_000,
            api_calls=20,
            context_tokens=100_000,
            context_length=200_000,
        )
        cli_obj._status_bar_visible = True
        return cli_obj

    def test_fragments_fit_within_announced_width(self):
        """Total fragment text length must not exceed the width used to build them."""
        from unittest.mock import MagicMock, patch
        cli_obj = self._make_wide_cli()

        for width in (40, 52, 76, 80, 120, 200):
            mock_app = MagicMock()
            mock_app.output.get_size.return_value = MagicMock(columns=width)

            with patch("prompt_toolkit.application.get_app", return_value=mock_app):
                frags = cli_obj._get_status_bar_fragments()

            total_text = "".join(text for _, text in frags)
            display_width = cli_obj._status_bar_display_width(total_text)
            assert display_width <= width + 4, (  # +4 for minor padding chars
                f"At width={width}, fragment total {display_width} cells overflows "
                f"({total_text!r})"
            )

    def test_fragments_use_pt_width_over_shutil(self):
        """When prompt_toolkit reports a width, shutil.get_terminal_size must not be used."""
        from unittest.mock import MagicMock, patch
        cli_obj = self._make_wide_cli()

        mock_app = MagicMock()
        mock_app.output.get_size.return_value = MagicMock(columns=120)

        with patch("prompt_toolkit.application.get_app", return_value=mock_app) as mock_get_app, \
             patch("shutil.get_terminal_size") as mock_shutil:
            cli_obj._get_status_bar_fragments()

        mock_shutil.assert_not_called()

    def test_fragments_fall_back_to_shutil_when_no_app(self):
        """Outside a TUI context (no running app), shutil must be used as fallback."""
        from unittest.mock import MagicMock, patch
        cli_obj = self._make_wide_cli()

        with patch("prompt_toolkit.application.get_app", side_effect=Exception("no app")), \
             patch("shutil.get_terminal_size", return_value=MagicMock(columns=100)) as mock_shutil:
            frags = cli_obj._get_status_bar_fragments()

        mock_shutil.assert_called()
        assert len(frags) > 0

    def test_build_status_bar_text_uses_pt_width(self):
        """_build_status_bar_text() must also prefer prompt_toolkit width."""
        from unittest.mock import MagicMock, patch
        cli_obj = self._make_wide_cli()

        mock_app = MagicMock()
        mock_app.output.get_size.return_value = MagicMock(columns=80)

        with patch("prompt_toolkit.application.get_app", return_value=mock_app), \
             patch("shutil.get_terminal_size") as mock_shutil:
            text = cli_obj._build_status_bar_text()  # no explicit width

        mock_shutil.assert_not_called()
        assert isinstance(text, str)
        assert len(text) > 0

    def test_explicit_width_skips_pt_lookup(self):
        """An explicit width= argument must bypass both PT and shutil lookups."""
        from unittest.mock import patch
        cli_obj = self._make_wide_cli()

        with patch("prompt_toolkit.application.get_app") as mock_get_app, \
             patch("shutil.get_terminal_size") as mock_shutil:
            text = cli_obj._build_status_bar_text(width=100)

        mock_get_app.assert_not_called()
        mock_shutil.assert_not_called()
        assert len(text) > 0
