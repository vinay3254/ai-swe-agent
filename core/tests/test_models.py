import pytest

from swe_agent.models import IssueRef


def test_parse_issue_url() -> None:
    ref = IssueRef.parse("https://github.com/octo/app.js/issues/42")
    assert (ref.owner, ref.repo, ref.number) == ("octo", "app.js", 42)
    assert ref.full_name == "octo/app.js"
    assert ref.url == "https://github.com/octo/app.js/issues/42"


def test_parse_accepts_trailing_slash_and_whitespace() -> None:
    assert IssueRef.parse(" https://github.com/octo/app/issues/7/\n").number == 7


@pytest.mark.parametrize(
    "bad",
    [
        "https://github.com/octo/app/pull/7",
        "https://gitlab.com/octo/app/issues/7",
        "https://github.com/octo/app/issues/0x",
        "https://github.com/octo/app/issues/7/comments",
        "octo/app#7",
        "",
    ],
)
def test_parse_rejects_other_urls(bad: str) -> None:
    with pytest.raises(ValueError):
        IssueRef.parse(bad)
