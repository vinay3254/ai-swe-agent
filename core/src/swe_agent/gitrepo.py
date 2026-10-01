import base64
import os
import subprocess
from pathlib import Path
from typing import Self

BRANCH_PREFIX = "ai-fix/"


class GitError(Exception):
    pass


class GitRepo:
    """A local clone driven through the git CLI.

    The GitHub token reaches git only through environment variables, so it never lands
    in `.git/config` or in a process argument list.
    """

    def __init__(
        self,
        path: Path,
        *,
        token: str | None,
        author_name: str = "ai-swe-agent",
        author_email: str = "ai-swe-agent@users.noreply.github.com",
    ) -> None:
        self.path = path
        self._token = token
        self._identity = ["-c", f"user.name={author_name}", "-c", f"user.email={author_email}"]

    @classmethod
    def clone(cls, url: str, dest: Path, *, token: str | None) -> Self:
        repo = cls(dest, token=token)
        repo._run("clone", url, str(dest), cwd=dest.parent)
        return repo

    def _env(self) -> dict[str, str]:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        if self._token is not None:
            auth = base64.b64encode(f"x-access-token:{self._token}".encode()).decode()
            env.update(
                GIT_CONFIG_COUNT="1",
                GIT_CONFIG_KEY_0="http.extraheader",
                GIT_CONFIG_VALUE_0=f"Authorization: Basic {auth}",
            )
        return env

    def _run(self, *args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(
            ["git", *self._identity, *args],
            cwd=cwd or self.path,
            env=self._env(),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise GitError(f"git {args[0]} failed: {result.stderr.strip()}")
        return result.stdout.strip()

    def default_branch(self) -> str:
        ref = self._run("symbolic-ref", "--short", "refs/remotes/origin/HEAD")
        return ref.removeprefix("origin/")

    def head_sha(self) -> str:
        return self._run("rev-parse", "HEAD")

    def create_branch(self, name: str) -> None:
        if name == self.default_branch():
            raise GitError(f"refusing to work on the default branch {name!r}")
        self._run("checkout", "-b", name)

    def diff_since(self, base_sha: str) -> str:
        """Everything the agent changed since `base_sha`, committed or not, new files included."""
        self._run("add", "-A")
        return self._run("diff", "--cached", base_sha)

    def commit_all(self, message: str) -> bool:
        """Commit leftover changes. False when the tree is already clean."""
        self._run("add", "-A")
        if not self._run("status", "--porcelain"):
            return False
        self._run("commit", "-m", message)
        return True

    def push(self, branch: str) -> None:
        if branch == self.default_branch():
            raise GitError(f"refusing to push the default branch {branch!r}")
        if not branch.startswith(BRANCH_PREFIX):
            raise GitError(f"refusing to push {branch!r}: agent branches start with {BRANCH_PREFIX!r}")
        self._run("push", "origin", f"refs/heads/{branch}:refs/heads/{branch}")
