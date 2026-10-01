import re
from pathlib import PurePosixPath

from pydantic import BaseModel

_COPYRIGHT_LINE = re.compile(
    r"(?im)^[\s#*/\-]*((?:copyright|©|\(c\))[^\n]*?\b(?:19|20)\d{2}\b[^\n]*?)\s*$"
)
_YEARS = re.compile(r"\b(?:19|20)\d{2}(?:\s*[-–,]\s*(?:19|20)?\d{2,4})*\b")
_NOTICE_FILE = re.compile(r"(?i)^(licen[sc]e|copying|notice|copyright)")


class LicenseCheck(BaseModel, frozen=True):
    checkable: bool  # False when the original names no holder we can look for
    preserved: bool  # every original holder is still named somewhere in the candidate
    missing: list[str]  # original copyright lines whose holder is absent
    has_notice_text: bool  # candidate has a LICENSE, COPYING or NOTICE style file
    files_checked: list[str]


def extract_copyright_lines(text: str) -> list[str]:
    """Real copyright lines (they carry a year). Skips the 'above copyright notice' sentence
    and templates like 'Copyright [yyyy] [name]'."""
    return [m.group(1).strip() for m in _COPYRIGHT_LINE.finditer(text)]


def _squash(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text.lower()).split())


def _holder_key(line: str) -> str:
    text = line.lower().replace("(c)", " ").replace("©", " ").replace("all rights reserved", " ")
    text = text.replace("copyright", " ")
    return _squash(_YEARS.sub(" ", text))


def check_attribution(original_lines: list[str], candidate_files: dict[str, str]) -> LicenseCheck:
    """Deterministic attribution check.

    A holder counts as preserved if their name appears anywhere in the candidate's license,
    notice or README text, even with a new year or reworded line. That leans toward 'clear':
    a wrong accusation harms a third party, a missed one only costs a rescan.
    """
    keyed = [(line, _holder_key(line)) for line in original_lines]
    keyed = [(line, key) for line, key in keyed if key]
    files = list(candidate_files)
    has_notice = any(_NOTICE_FILE.match(PurePosixPath(name).name) for name in files)
    if not keyed:
        return LicenseCheck(
            checkable=False, preserved=False, missing=[], has_notice_text=has_notice, files_checked=files
        )
    haystack = f" {_squash(' '.join(candidate_files.values()))} "
    missing = [line for line, key in keyed if f" {key} " not in haystack]
    return LicenseCheck(
        checkable=True,
        preserved=not missing,
        missing=missing,
        has_notice_text=has_notice,
        files_checked=files,
    )
