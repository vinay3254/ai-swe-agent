from swe_agent.watchdog.fingerprint import build_fingerprints, match_files

ORIGINAL = {
    "src/core.py": (
        "import os\n"
        "def compute_distinctive_checksum(payload, salt):\n"
        "    return hashlib.sha256(payload + salt + b'swe-agent-demo').hexdigest()\n"
        "x = 1\n"
    ),
    "src/util.py": "def helper_that_is_long_enough_to_be_distinctive_enough(value): return value * 31\n",
    "LICENSE": "Copyright (c) 2024 Jane Doe\n",
}


def test_distinctive_lines_are_long_unique_and_exclude_license_text() -> None:
    fp = build_fingerprints(ORIGINAL)

    assert "def compute_distinctive_checksum(payload, salt):" in fp.lines
    assert all(len(line) >= 40 for line in fp.lines)
    assert not any("Copyright" in line for line in fp.lines)
    assert "import os" not in fp.lines


def test_repeated_lines_are_not_distinctive() -> None:
    dup = "    raise NotImplementedError('this line repeats in many places')"
    fp = build_fingerprints({"a.py": f"{dup}\n{dup}\n", "b.py": f"{dup}\n"})

    assert dup.strip() not in fp.lines


def test_line_limit_and_length_cap() -> None:
    text = "\n".join(f"def function_number_{i:03d}_with_a_long_enough_name(a, b): pass" for i in range(20))
    fp = build_fingerprints({"a.py": text}, line_limit=5)
    assert len(fp.lines) == 5

    long = "x" * 400
    capped = build_fingerprints({"a.py": f"value = '{long}'\n"})
    assert all(len(line) <= 120 for line in capped.lines)


def test_identical_file_matches_by_hash_even_with_crlf_and_trailing_spaces() -> None:
    fp = build_fingerprints(ORIGINAL)
    candidate = {"lib/core.py": ORIGINAL["src/core.py"].replace("\n", "  \r\n")}

    matches = match_files(fp, candidate)

    assert [(m.path, m.kind) for m in matches] == [("lib/core.py", "hash")]


def test_partial_copy_matches_when_two_distinctive_lines_survive() -> None:
    fp = build_fingerprints(ORIGINAL)
    candidate = {
        "x.py": (
            "def compute_distinctive_checksum(payload, salt):\n"
            "    pass\n"
            "def helper_that_is_long_enough_to_be_distinctive_enough(value): return value * 31\n"
        )
    }

    matches = match_files(fp, candidate)

    assert [(m.path, m.kind) for m in matches] == [("x.py", "lines")]


def test_one_shared_line_is_not_enough() -> None:
    fp = build_fingerprints(ORIGINAL)
    candidate = {"x.py": "def compute_distinctive_checksum(payload, salt):\n    pass\n"}

    assert match_files(fp, candidate) == []


def test_unrelated_files_do_not_match() -> None:
    fp = build_fingerprints(ORIGINAL)

    assert match_files(fp, {"a.py": "print('hello')\n"}) == []
