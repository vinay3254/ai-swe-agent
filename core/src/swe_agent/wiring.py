from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from swe_agent.config import Settings
from swe_agent.github import GitHubClient
from swe_agent.jev_gate.client import JevClient
from swe_agent.jev_gate.questions import JevGate
from swe_agent.openhands_session import OpenHandsSession
from swe_agent.pipeline import Pipeline
from swe_agent.runner import GitRunner
from swe_agent.service import JobService
from swe_agent.store import Store
from swe_agent.watchdog.cases import CaseStore
from swe_agent.watchdog.gate import JevWatchGate
from swe_agent.watchdog.github_watch import WatchGitHubClient
from swe_agent.watchdog.scan import Watchdog


class ConfigError(Exception):
    pass


def build_pipeline(settings: Settings) -> tuple[Pipeline, Store]:
    """Composition root: real Jev, GitHub, git and OpenHands behind the pipeline's ports."""
    llm_key = settings.effective_llm_api_key()
    missing = [
        name
        for name, present in (
            ("GITHUB_TOKEN", settings.github_token is not None),
            ("ANTHROPIC_API_KEY or SWE_AGENT_LLM_API_KEY", llm_key is not None),
        )
        if not present
    ]
    if missing:
        raise ConfigError(f"missing environment: {', '.join(missing)}")
    assert settings.github_token is not None and llm_key is not None

    store = Store(settings.db_path)
    jev = JevClient(
        api_key=settings.typesafe_api_key.get_secret_value(),
        model=settings.jev_model,
        base_url=settings.jev_base_url,
        timeout_s=settings.jev_timeout_s,
        max_attempts=settings.jev_max_attempts,
    )
    gate = JevGate(
        jev, max_issue_chars=settings.max_issue_chars, max_diff_chars=settings.max_diff_chars
    )
    github_token = settings.github_token.get_secret_value()
    runner = GitRunner(
        session=OpenHandsSession(
            model=settings.agent_model,
            api_key=llm_key,
            base_url=settings.llm_base_url,
            test_command=settings.test_command,
            max_iterations=settings.max_iterations,
            server_image=settings.server_image,
            mcp_config=settings.mcp_servers or None,
        ),
        clone_url=lambda ref: f"https://github.com/{ref.full_name}.git",
        token=github_token,
        workspace_root=settings.workspace_root,
    )
    pipeline = Pipeline(
        store=store,
        github=GitHubClient(token=github_token),
        runner=runner,
        gate=gate,
        thresholds=settings.thresholds,
    )
    return pipeline, store


def build_service(settings: Settings) -> JobService:
    pipeline, store = build_pipeline(settings)
    return JobService(
        pipeline=pipeline,
        store=store,
        allowlist=settings.repo_allowlist,
        executor=ThreadPoolExecutor(max_workers=settings.max_concurrent_jobs),
    )


@dataclass(frozen=True)
class WatchdogRuntime:
    watchdog: Watchdog
    cases: CaseStore
    github: WatchGitHubClient


def build_watchdog(settings: Settings) -> WatchdogRuntime:
    if settings.github_token is None:
        raise ConfigError("missing environment: GITHUB_TOKEN")
    github = WatchGitHubClient(token=settings.github_token.get_secret_value())
    jev = JevClient(
        api_key=settings.typesafe_api_key.get_secret_value(),
        model=settings.jev_model,
        base_url=settings.jev_base_url,
        timeout_s=settings.jev_timeout_s,
        max_attempts=settings.jev_max_attempts,
    )
    cases = CaseStore(settings.db_path)
    watchdog = Watchdog(
        github=github,
        gate=JevWatchGate(jev, max_chars=settings.max_diff_chars),
        cases=cases,
        thresholds=settings.thresholds,
    )
    return WatchdogRuntime(watchdog=watchdog, cases=cases, github=github)
