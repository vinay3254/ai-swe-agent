from swe_agent.watchdog.license_check import check_attribution, extract_copyright_lines

MIT = """MIT License

Copyright (c) 2024 Jane Doe

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software...

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
"""

APACHE_APPENDIX = """Copyright [yyyy] [name of copyright owner]

Licensed under the Apache License, Version 2.0
"""


def test_extracts_only_real_copyright_lines() -> None:
    assert extract_copyright_lines(MIT) == ["Copyright (c) 2024 Jane Doe"]


def test_boilerplate_and_notice_sentences_are_not_copyright_lines() -> None:
    assert extract_copyright_lines(APACHE_APPENDIX) == []


def test_extracts_multiple_holders_and_comment_prefixed_lines() -> None:
    text = "# Copyright 2020-2023 Acme Corp\n * Copyright (C) 2019 Bob Ray\n"
    assert extract_copyright_lines(text) == [
        "Copyright 2020-2023 Acme Corp",
        "Copyright (C) 2019 Bob Ray",
    ]


def test_preserved_when_holder_appears_with_updated_year() -> None:
    check = check_attribution(
        ["Copyright (c) 2024 Jane Doe"],
        {"LICENSE": "MIT License\n\nCopyright (c) 2023-2025 Jane Doe\nand friends\n"},
    )

    assert check.preserved is True
    assert check.missing == []
    assert check.has_notice_text is True


def test_missing_when_license_names_a_different_holder() -> None:
    check = check_attribution(
        ["Copyright (c) 2024 Jane Doe"],
        {"LICENSE": "MIT License\n\nCopyright (c) 2025 Mallory Smith\n"},
    )

    assert check.preserved is False
    assert check.missing == ["Copyright (c) 2024 Jane Doe"]
    assert check.has_notice_text is True


def test_missing_and_no_notice_text_when_candidate_has_no_license_files() -> None:
    check = check_attribution(["Copyright (c) 2024 Jane Doe"], {"README.md": "# My cool lib\n"})

    assert check.preserved is False
    assert check.has_notice_text is False
    assert check.files_checked == ["README.md"]


def test_holder_named_in_readme_counts_as_preserved_to_avoid_false_accusations() -> None:
    check = check_attribution(
        ["Copyright (c) 2024 Jane Doe"],
        {"README.md": "Based on code by Jane Doe, used under the MIT license.\n"},
    )

    assert check.preserved is True


def test_matching_ignores_case_punctuation_and_symbols() -> None:
    check = check_attribution(
        ["Copyright (c) 2024 Jane Doe"], {"NOTICE": "COPYRIGHT © 2024 jane  doe."}
    )

    assert check.preserved is True


def test_lines_without_a_holder_cannot_be_checked_and_are_skipped() -> None:
    check = check_attribution(["Copyright 2024"], {"LICENSE": "nothing"})

    assert check.checkable is False


def test_no_original_lines_means_not_checkable() -> None:
    assert check_attribution([], {"LICENSE": "x"}).checkable is False
