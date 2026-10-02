import os

import pytest

# Importing openhands.sdk loads the nearest .env into os.environ, so a developer's real
# credentials would leak into every later test. Start each test from a clean slate.
_PREFIXES = ("SWE_AGENT_", "CORE_API_", "GITHUB_", "TYPESAFE_", "ANTHROPIC_")


@pytest.fixture(autouse=True)
def _clean_credentials_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [n for n in os.environ if n.startswith(_PREFIXES)]:
        monkeypatch.delenv(name)
