import pytest

pytest.importorskip("openhands.sdk")

from swe_agent.openhands_session import OpenHandsSession, agent_completed  # noqa: E402


@pytest.mark.parametrize(
    ("status", "completed"),
    [
        ("finished", True),
        ("stuck", False),
        ("error", False),
        ("running", False),
        ("paused", False),
        ("waiting_for_confirmation", False),
    ],
)
def test_only_a_finished_conversation_counts_as_completed(status: str, completed: bool) -> None:
    assert agent_completed(status) is completed


def test_agent_has_terminal_and_editor_tools_and_passes_mcp_config_through() -> None:
    mcp = {"nav": {"command": "uvx", "args": ["mcp-server-git"]}}
    session = OpenHandsSession(
        model="anthropic/claude-sonnet-5-5",
        api_key="sk-test",
        test_command=None,
        mcp_config=mcp,
    )

    agent = session.build_agent()

    assert [t.name for t in agent.tools] == ["terminal", "file_editor"]
    assert agent.llm.model == "anthropic/claude-sonnet-5-5"
    assert agent.mcp_config["nav"].command == "uvx"
    assert agent.mcp_config["nav"].args == ["mcp-server-git"]


def test_base_url_is_passed_to_the_llm_for_openai_compatible_gateways() -> None:
    session = OpenHandsSession(
        model="openai/auto",
        api_key="omni",
        test_command=None,
        base_url="http://172.17.0.1:20128/v1",
    )

    agent = session.build_agent()

    assert agent.llm.base_url == "http://172.17.0.1:20128/v1"
    assert agent.llm.model == "openai/auto"


def test_base_url_defaults_to_none_for_direct_providers() -> None:
    agent = OpenHandsSession(model="m", api_key="k", test_command=None).build_agent()

    assert agent.llm.base_url is None
