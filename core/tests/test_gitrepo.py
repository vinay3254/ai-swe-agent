from pathlib import Path

import pytest
from gitfixtures import git, make_remote

from swe_agent.gitrepo import GitError, GitRepo


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    return make_remote(tmp_path)


@pytest.fixture
def repo(tmp_path: Path, remote: Path) -> GitRepo:
    return GitRepo.clone(str(remote), tmp_path / "work", token=None)


def test_clone_reports_default_branch_and_base_sha(repo: GitRepo, remote: Path) -> None:
    assert repo.default_branch() == "main"
    assert repo.head_sha() == git("rev-parse", "main", cwd=remote)


def test_diff_since_base_includes_edits_new_files_and_agent_commits(
    repo: GitRepo, tmp_path: Path
) -> None:
    base = repo.head_sha()
    repo.create_branch("ai-fix/issue-1")
    work = tmp_path / "work"
    (work / "app.py").write_text("def parse(s):\n    return s[:1]\n")
    (work / "new.py").write_text("x = 1\n")
    git("add", "new.py", cwd=work)
    git("commit", "-m", "agent committed itself", cwd=work)
    (work / "app.py").write_text("def parse(s):\n    return s[:2]\n")

    diff = repo.diff_since(base)

    assert "+    return s[:2]" in diff
    assert "+x = 1" in diff


def test_diff_since_base_is_empty_when_nothing_changed(repo: GitRepo) -> None:
    repo.create_branch("ai-fix/issue-1")
    assert repo.diff_since(repo.head_sha()) == ""


def test_commit_and_push_publish_the_branch(repo: GitRepo, tmp_path: Path, remote: Path) -> None:
    repo.create_branch("ai-fix/issue-1")
    (tmp_path / "work" / "app.py").write_text("changed\n")

    assert repo.commit_all("Fix #1") is True
    repo.push("ai-fix/issue-1")

    assert "Fix #1" in git("log", "-1", "--format=%s", "ai-fix/issue-1", cwd=remote)
    assert git("rev-parse", "main", cwd=remote) != git("rev-parse", "ai-fix/issue-1", cwd=remote)


def test_commit_all_returns_false_when_there_is_nothing_to_commit(repo: GitRepo) -> None:
    repo.create_branch("ai-fix/issue-1")
    assert repo.commit_all("nothing") is False


def test_push_refuses_default_branch_and_foreign_branch_names(repo: GitRepo) -> None:
    with pytest.raises(GitError, match="default branch"):
        repo.push("main")
    with pytest.raises(GitError, match="ai-fix/"):
        repo.push("feature/x")


def test_create_branch_refuses_default_branch_name(repo: GitRepo) -> None:
    with pytest.raises(GitError, match="default branch"):
        repo.create_branch("main")


def test_failed_git_command_raises_with_stderr(tmp_path: Path) -> None:
    with pytest.raises(GitError, match="clone"):
        GitRepo.clone(str(tmp_path / "does-not-exist.git"), tmp_path / "w", token=None)
