from pathlib import Path

# First match wins. A repo can be polyglot; the earlier entry is the more common test entry point.
_MARKERS: tuple[tuple[str, str], ...] = (
    ("pyproject.toml", "python -m pytest -q"),
    ("pytest.ini", "python -m pytest -q"),
    ("setup.py", "python -m pytest -q"),
    ("package.json", "npm test --silent"),
    ("Cargo.toml", "cargo test"),
    ("go.mod", "go test ./..."),
)


def detect_test_command(workdir: Path) -> str | None:
    """Guess the repo's test command from its files. None means we cannot tell."""
    for marker, command in _MARKERS:
        if (workdir / marker).exists():
            return command
    return None
