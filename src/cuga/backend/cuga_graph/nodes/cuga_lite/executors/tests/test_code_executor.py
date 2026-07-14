import asyncio
import pytest
from unittest.mock import Mock

from cuga.backend.cuga_graph.state.agent_state import AgentState, VariablesManager
from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.benchmark_mode import (
    reset_skills_relaxed_execution,
    set_skills_relaxed_execution,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.security import SecurityValidator
from cuga.config import settings


@pytest.fixture
def mock_state():
    """Create a mock AgentState with VariablesManager."""
    state = Mock(spec=AgentState)
    state.variables_manager = VariablesManager()
    state.reflection_skills_enabled = False
    return state


@pytest.mark.asyncio
async def test_basic_execution_local(mock_state):
    """Test basic code execution in local mode."""
    code = "x = 5 + 3\nprint(x)"
    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={},
        state=mock_state,
        mode='local',
    )

    assert "8" in result
    assert 'x' in new_vars
    assert new_vars['x'] == 8


@pytest.mark.asyncio
async def test_execution_with_variables(mock_state):
    """Test execution with existing variables."""
    code = "y = x * 2\nprint(y)"
    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={'x': 5},
        state=mock_state,
        mode='local',
    )

    assert "10" in result
    assert 'y' in new_vars
    assert new_vars['y'] == 10


@pytest.mark.asyncio
async def test_async_tool_execution(mock_state):
    """Test execution with async tools."""

    async def my_tool(value: int) -> int:
        await asyncio.sleep(0.01)
        return value * 2

    code = "result = await my_tool(5)\nprint(result)"
    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={'my_tool': my_tool},
        state=mock_state,
        mode='local',
    )

    assert "10" in result
    assert 'result' in new_vars
    assert new_vars['result'] == 10


@pytest.mark.asyncio
async def test_unknown_tool_name_gets_correction(mock_state):
    """A fabricated tool name returns a correction listing the closest real tools.

    Regression guard for the gpt-oss tool-name hallucination loop: calling a
    non-existent tool used to surface only a raw NameError, so the agent kept
    re-inventing the same bogus name until the step limit. The correction must
    name the closest real tool so the agent can recover in one step.
    """

    async def mondial_geo_get_top_country_by_gdp_and_agriculture() -> int:
        return 0

    async def mondial_geo_get_mountain_count_by_country(country: str) -> int:
        return 0

    # References a real tool (so the context-usage guard passes) then calls a
    # fabricated one — mirrors the real trajectory where the agent invents
    # generic CRUD names after a valid find_tools/real-tool reference.
    code = (
        "mc = mondial_geo_get_mountain_count_by_country\n"
        "result = await mondial_geo_get_countries_countries_get()\n"
        "print(result)"
    )
    result, _ = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={
            'mondial_geo_get_top_country_by_gdp_and_agriculture': (
                mondial_geo_get_top_country_by_gdp_and_agriculture
            ),
            'mondial_geo_get_mountain_count_by_country': mondial_geo_get_mountain_count_by_country,
        },
        state=mock_state,
        mode='local',
    )

    assert "tool-name correction" in result
    assert "mondial_geo_get_countries_countries_get" in result
    assert "Did you mean" in result
    # the real tool the shortlister meant must be offered as a candidate
    assert "mondial_geo_get_top_country_by_gdp_and_agriculture" in result


@pytest.mark.asyncio
async def test_unknown_name_no_close_match_points_to_find_tools(mock_state):
    """With no similar tool loaded, the correction tells the agent to re-query find_tools."""

    async def authors_get_author_details(author_id: int) -> dict:
        return {}

    code = "ad = authors_get_author_details\nresult = await repo_browser()\nprint(result)"
    result, _ = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={'authors_get_author_details': authors_get_author_details},
        state=mock_state,
        mode='local',
    )

    assert "tool-name correction" in result
    assert "find_tools" in result


@pytest.mark.asyncio
async def test_undefined_variable_keeps_bare_name_error(mock_state):
    """A NameError on a plain variable reference must NOT get the tool correction.

    Real trajectory (PR #416 review): the agent printed `formatted_total`
    before computing it, recovered on the next step by writing the actual
    computation, and the task passed. Injecting "call find_tools / do not
    retry" there steers the agent away from the real fix (define the
    variable), so the correction only fires when the missing name is
    *called* like a function.
    """

    async def authors_get_author_details(author_id: int) -> dict:
        return {}

    code = "ad = authors_get_author_details\nprint(formatted_total)"
    result, _ = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={'authors_get_author_details': authors_get_author_details},
        state=mock_state,
        mode='local',
    )

    assert "NameError" in result
    assert "formatted_total" in result
    assert "tool-name correction" not in result


def test_correction_cutoff_rejects_cross_app_junk():
    """Weak cross-app matches must not be offered as suggestions.

    With the old cutoff=0.4, a missing `simple_note_list_notes_get` surfaced
    `spotify_create_playlist_post` (wrong app entirely). At 0.6 such junk is
    filtered and the agent is told to re-query find_tools instead.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.local.local_executor import LocalExecutor

    hint = LocalExecutor._unknown_tool_correction(
        NameError("name 'simple_note_list_notes_get' is not defined", name='simple_note_list_notes_get'),
        available_tools=['spotify_create_playlist_post', 'find_tools'],
        code="result = await simple_note_list_notes_get()",
    )

    assert "tool-name correction" in hint
    assert "spotify_create_playlist_post" not in hint
    assert "find_tools" in hint


def test_correction_warns_about_lookalike_actions():
    """Suggestions carry a caution: a lookalike name may perform a different action.

    difflib ranks `phone_delete_sms_message_sms_delete` above the intended
    read tool for a missing `phone_get_sms_messages_sms_get`; the caution
    keeps the agent from blindly taking the head of the list.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.local.local_executor import LocalExecutor

    hint = LocalExecutor._unknown_tool_correction(
        NameError(
            "name 'phone_get_sms_messages_sms_get' is not defined", name='phone_get_sms_messages_sms_get'
        ),
        available_tools=['phone_delete_sms_message_sms_delete', 'phone_show_sms_messages_sms_get'],
        code="result = await phone_get_sms_messages_sms_get()",
    )

    assert "Did you mean" in hint


def test_correction_ignores_call_shapes_inside_string_literals():
    """A name followed by ``(`` inside a string literal is not a call.

    PR #416 review: the old text search matched phrases like
    ``var = " agent_1("`` and treated the plain-variable NameError as a
    fabricated tool call. The AST check only counts real Call nodes.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.local.local_executor import LocalExecutor

    hint = LocalExecutor._unknown_tool_correction(
        NameError("name 'agent_1' is not defined", name='agent_1'),
        available_tools=['find_tools', 'authors_get_author_details'],
        code='var = " agent_1(x)"\nprint(agent_1)',
    )

    assert hint == ""


def test_correction_suppressed_for_agent_defined_helper():
    """A helper the agent defines in the same code keeps its bare NameError.

    PR #416 review: calling a self-written helper before its ``def`` (or after
    a definition that failed) used to get "call find_tools" guidance — the
    right fix is to repair the helper, not to hunt for a tool.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.local.local_executor import LocalExecutor

    hint = LocalExecutor._unknown_tool_correction(
        NameError("name 'helper1' is not defined", name='helper1'),
        available_tools=['find_tools', 'authors_get_author_details'],
        code="res = await helper1(arg1)\n\nasync def helper1(a):\n    return a\n",
    )

    assert hint == ""


def test_correction_no_close_match_mentions_helper_possibility():
    """With no similar tool, the hint must not assume the name was a tool.

    A lost agent-written helper (defined in an earlier, separate execution)
    reaches this path too; the message covers both repairs — re-include the
    definition, or find_tools if a tool was meant.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.local.local_executor import LocalExecutor

    hint = LocalExecutor._unknown_tool_correction(
        NameError("name 'helper1' is not defined", name='helper1'),
        available_tools=['authors_get_author_details'],
        code="res = await helper1(arg1)",
    )

    assert "tool-name correction" in hint
    assert "helper function" in hint
    assert "find_tools" in hint
    assert "delete vs get" in hint


@pytest.mark.asyncio
async def test_syntax_error_blocked_before_exec(mock_state, monkeypatch):
    """Invalid Python is rejected before sandbox exec with an actionable hint."""
    monkeypatch.setattr(settings.skills, "enabled", False)
    code = 'phase2_md = f"""\n# report\n'

    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={},
        state=mock_state,
        mode='local',
    )

    assert "Python syntax error" in result
    assert "unterminated" in result.lower() or "f-string" in result.lower()
    assert "write_file" in result
    assert new_vars == {}
    assert "Traceback" not in result


@pytest.mark.asyncio
async def test_syntax_error_blocked_before_exec_in_skills_relaxed_mode(mock_state):
    """Skills/relaxed execution loosens import checks but must still reject unparseable code
    before exec() — regression test for the syntax guard being skipped alongside the
    security checks it was bundled with."""
    mock_state.reflection_skills_enabled = True
    code = 'phase2_md = f"""\n# report\n'

    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={},
        state=mock_state,
        mode='local',
    )

    assert "Python syntax error" in result
    assert new_vars == {}
    assert "Traceback" not in result


def test_format_syntax_error_hints_for_unterminated_fstring():
    code = 'x = f"""\nhello\n'
    try:
        compile(code, "<code>", "exec")
    except SyntaxError as exc:
        msg = SecurityValidator.format_syntax_error(code, exc)
    else:
        raise AssertionError("expected SyntaxError")

    assert "Python syntax error at line" in msg
    assert "write_file" in msg


@pytest.mark.asyncio
async def test_dangerous_import_blocked(mock_state, monkeypatch):
    """Test that dangerous imports are blocked."""
    monkeypatch.setattr(settings.skills, "enabled", False)
    code = "import os\nos.system('echo hello')"

    with pytest.raises(ImportError) as exc_info:
        result, new_vars = await CodeExecutor.eval_with_tools_async(
            code=code,
            _locals={},
            state=mock_state,
            mode='local',
        )

    assert "not allowed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_skills_mode_allows_non_allowlisted_import(mock_state, monkeypatch):
    """Skills mode skips import allowlist so skill workflows can use extra packages."""
    mock_state.reflection_skills_enabled = True
    code = "import pathlib\np = pathlib.Path('.')\nprint(p.name or '.')"
    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={"x": 1},
        state=mock_state,
        mode="local",
    )
    assert "." in result


def test_skills_relaxed_skips_wrapped_code_validation():
    token = set_skills_relaxed_execution(True)
    try:
        SecurityValidator.validate_wrapped_code("open('/tmp/x')")
    finally:
        reset_skills_relaxed_execution(token)


def test_wrapped_code_validation_active_without_skills():
    token = set_skills_relaxed_execution(False)
    try:
        with pytest.raises(PermissionError, match="Security violation"):
            SecurityValidator.validate_wrapped_code("open('/tmp/x')")
    finally:
        reset_skills_relaxed_execution(token)


@pytest.mark.asyncio
async def test_allowed_import_works(mock_state):
    """Test that allowed imports work."""
    code = "import json\ndata = json.dumps({'key': 'value'})\nprint(data)"
    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={},
        state=mock_state,
        mode='local',
    )

    assert '"key"' in result or "'key'" in result
    assert 'data' in new_vars


@pytest.mark.asyncio
async def test_pandas_support(mock_state):
    """Test pandas support if available."""
    try:
        code = "import pandas as pd\ndf = pd.DataFrame({'a': [1, 2, 3]})\nprint(len(df))"
        result, new_vars = await CodeExecutor.eval_with_tools_async(
            code=code,
            _locals={},
            state=mock_state,
            mode='local',
        )

        assert "3" in result
        assert 'df' in new_vars
    except ImportError:
        pytest.skip("pandas not installed")


@pytest.mark.asyncio
async def test_variable_reordering(mock_state):
    """Test that printed variables are moved to end."""
    code = "x = 5\ny = 10\nz = 15\nprint(y)"
    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={},
        state=mock_state,
        mode='local',
    )

    # y should be moved to the end since it appears in print statement
    # and is more than 3 characters (actually it's 1, so it won't be moved)
    # Let's test with a longer variable name
    assert 'x' in new_vars and 'y' in new_vars and 'z' in new_vars


@pytest.mark.asyncio
async def test_variable_reordering_long_name(mock_state):
    """Test that printed variables with long names are moved to end."""
    code = "short = 5\nlonger_name = 10\nanother = 15\nprint(longer_name)"
    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={},
        state=mock_state,
        mode='local',
    )

    var_names = list(new_vars.keys())
    # longer_name should be moved to the end since it's > 3 chars and in print
    assert var_names[-1] == 'longer_name'


@pytest.mark.asyncio
async def test_timeout_handling(mock_state):
    """Test that timeouts are handled properly."""
    code = "import asyncio\nawait asyncio.sleep(100)"
    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={},
        state=mock_state,
        mode='local',
    )

    assert "timeout" in result.lower() or "error" in result.lower()


@pytest.mark.asyncio
async def test_expression_auto_print(mock_state):
    """Test that final expressions are auto-printed."""
    code = "x = 5\nx * 2"
    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={},
        state=mock_state,
        mode='local',
    )

    assert "10" in result


@pytest.mark.asyncio
async def test_mode_auto_detection(mock_state):
    """Test that mode is auto-detected from settings."""
    code = "x = 42"
    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={},
        state=mock_state,
    )

    assert 'x' in new_vars
    assert new_vars['x'] == 42
