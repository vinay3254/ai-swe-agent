import re
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field

_ISSUE_URL = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/issues/(\d+)/?$")


class IssueRef(BaseModel, frozen=True):
    owner: str
    repo: str
    number: int = Field(gt=0)

    @classmethod
    def parse(cls, url: str) -> Self:
        match = _ISSUE_URL.match(url.strip())
        if match is None:
            raise ValueError(f"not a GitHub issue URL: {url!r}")
        return cls(owner=match[1], repo=match[2], number=int(match[3]))

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.full_name}/issues/{self.number}"


class Issue(BaseModel, frozen=True):
    ref: IssueRef
    title: str
    body: str
    labels: tuple[str, ...] = ()


class JobState(StrEnum):
    RECEIVED = "received"  # job row exists, nothing has run yet
    TRIAGED = "triaged"
    RUNNING = "running"
    REVIEWED = "reviewed"
    DONE = "done"
    ESCALATED = "escalated"
    FAILED = "failed"
