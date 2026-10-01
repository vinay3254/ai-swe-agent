import hashlib
from collections import Counter
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel

MIN_HASH_CHARS = 100  # tiny files (empty __init__.py) match everywhere
_SKIP_NAMES = ("license", "licence", "copying", "notice", "readme", "copyright")
_COMMENT_PREFIXES = ("#", "//", "*", "/*", "--", "<!--")


class Fingerprints(BaseModel, frozen=True):
    file_hashes: dict[str, str]  # sha256 of normalized content -> original path
    lines: list[str]  # distinctive stripped lines, safe to use as code-search queries


class FileMatch(BaseModel, frozen=True):
    path: str  # path in the candidate repo
    kind: Literal["hash", "lines"]
    original_path: str | None = None


def _normalize(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def _hash(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode()).hexdigest()


def _is_boilerplate_file(path: str) -> bool:
    return PurePosixPath(path).name.lower().startswith(_SKIP_NAMES)


def build_fingerprints(
    files: dict[str, str], *, line_limit: int = 5, min_len: int = 40, max_len: int = 120
) -> Fingerprints:
    sources = {p: t for p, t in files.items() if not _is_boilerplate_file(p)}
    hashes = {
        _hash(text): path for path, text in sources.items() if len(_normalize(text)) >= MIN_HASH_CHARS
    }
    counts: Counter[str] = Counter()
    first_seen: dict[str, tuple[str, int]] = {}
    for path in sorted(sources):
        for number, raw in enumerate(sources[path].splitlines()):
            line = raw.strip()
            lowered = line.lower()
            if (
                len(line) < min_len
                or line.startswith(_COMMENT_PREFIXES)
                or "copyright" in lowered
                or "license" in lowered
            ):
                continue
            line = line[:max_len]
            counts[line] += 1
            first_seen.setdefault(line, (path, number))
    unique = [line for line, n in counts.items() if n == 1]
    unique.sort(key=lambda line: (-len(line), first_seen[line]))
    return Fingerprints(file_hashes=hashes, lines=unique[:line_limit])


def match_files(
    fp: Fingerprints, candidate_files: dict[str, str], *, min_line_hits: int = 2
) -> list[FileMatch]:
    """Candidate files that are a copy of an original file, or keep several of its signature lines."""
    matches: list[FileMatch] = []
    for path, text in candidate_files.items():
        digest = _hash(text)
        if digest in fp.file_hashes:
            matches.append(FileMatch(path=path, kind="hash", original_path=fp.file_hashes[digest]))
            continue
        stripped = "\n".join(line.strip() for line in text.splitlines())
        if sum(1 for line in fp.lines if line in stripped) >= min_line_hits:
            matches.append(FileMatch(path=path, kind="lines"))
    return matches
