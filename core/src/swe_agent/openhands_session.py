# pyright: basic
"""OpenHands-backed `AgentSession`.

OpenHands is an optional extra (`uv sync --extra openhands`), so its imports stay inside
functions. The agent runs in a Docker sandbox with only the job's clone mounted: no GitHub
token, no host environment. The LLM key does reach the agent server, because the agent
loop runs there. Remote conversations take an iteration cap but no cost cap, so
`max_iterations` is the only budget.
"""

from pathlib import Path
from typing import Any

from swe_agent.runner import AgentRun
from swe_agent.testcmd import detect_test_command

# Not /workspace itself: the agent server keeps its conversation state under /workspace, and
# that must stay out of the clone so it cannot end up in the diff.
CONTAINER_DIR = "/workspace/project"
DEFAULT_SERVER_IMAGE = "ghcr.io/openhands/agent-server:latest-python"


def open_for_sandbox(workdir: Path) -> None:
    """The server image runs as uid 10001, not the host user. Make the clone writable for it.
    Git tracks only the executable bit, so adding rw on files does not show up in the diff."""
    for path in (workdir, *workdir.rglob("*")):
        if path.is_symlink():
            continue
        path.chmod(path.stat().st_mode | 0o666 | (0o111 if path.is_dir() else 0))


def agent_completed(status: str) -> bool:
    """Only a conversation that reached `finished` is complete. Stuck, error, paused,
    or still running (iteration or budget cap hit) all count as incomplete."""
    return str(getattr(status, "value", status)) == "finished"


class OpenHandsSession:
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        test_command: str | None,
        max_iterations: int = 100,
        server_image: str = DEFAULT_SERVER_IMAGE,
        test_timeout_s: float = 900.0,
        mcp_config: dict[str, Any] | None = None,
        base_url: str | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._test_command = test_command
        self._max_iterations = max_iterations
        self._server_image = server_image
        self._test_timeout_s = test_timeout_s
        self._mcp_config = mcp_config or {}

    def build_agent(self) -> Any:
        from openhands.sdk import LLM, Agent, Tool
        from openhands.tools.file_editor import FileEditorTool
        from openhands.tools.terminal import TerminalTool

        return Agent(
            llm=LLM(model=self._model, api_key=self._api_key, base_url=self._base_url),
            tools=[Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name)],
            mcp_config=self._mcp_config,
        )

    def run(self, workdir: Path, prompt: str) -> AgentRun:
        from openhands.sdk import Conversation
        from openhands.sdk.conversation import get_agent_final_response
        from openhands.workspace import DockerWorkspace

        test_command = self._test_command or detect_test_command(workdir)
        open_for_sandbox(workdir)
        with DockerWorkspace(
            server_image=self._server_image,
            working_dir=CONTAINER_DIR,
            volumes=[f"{workdir}:{CONTAINER_DIR}:rw"],
        ) as workspace:
            conversation = Conversation(
                agent=self.build_agent(),
                workspace=workspace,
                max_iteration_per_run=self._max_iterations,
            )
            try:
                conversation.send_message(prompt)
                conversation.run()
                completed = agent_completed(conversation.state.execution_status)
                transcript = get_agent_final_response(conversation.state.events)
            finally:
                conversation.close()
            if test_command is None:
                return AgentRun(completed, False, f"{transcript}\n\n[no test command detected]")
            result = workspace.execute_command(
                test_command, cwd=CONTAINER_DIR, timeout=self._test_timeout_s
            )
        passed = result.exit_code == 0 and not result.timeout_occurred
        return AgentRun(completed, passed, f"{transcript}\n\n[tests: exit {result.exit_code}]")
