from pathlib import Path

import pytest

from swe_agent.testcmd import detect_test_command


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"pyproject.toml": ""}, "python -m pytest -q"),
        ({"pytest.ini": ""}, "python -m pytest -q"),
        ({"package.json": "{}"}, "npm test --silent"),
        ({"Cargo.toml": ""}, "cargo test"),
        ({"go.mod": ""}, "go test ./..."),
        ({"README.md": ""}, None),
    ],
)
def test_detects_the_test_command_from_project_files(
    tmp_path: Path, files: dict[str, str], expected: str | None
) -> None:
    for name, text in files.items():
        (tmp_path / name).write_text(text)

    assert detect_test_command(tmp_path) == expected


def test_package_json_wins_only_when_no_python_project_files(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "pyproject.toml").write_text("")

    assert detect_test_command(tmp_path) == "python -m pytest -q"
