"""Static packaging guardrails; these are not a Docker build/run test.

Docker negations match parents too, so reopening a directory must be
followed by a descendant exclusion before individual files are allowed.
Semantics: moby/patternmatcher MatchesOrParentMatches.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("parent,allowed", [
    ("tools", "!tools/parse_stream.py"),
    ("backend", "!backend/jana/"),
    ("backend/jana", "!backend/jana/*.py"),
    ("site", "!site/index.html"),
    ("site/src", "!site/src/*.mjs"),
    ("data", "!data/public/"),
])
def test_reopened_directories_do_not_admit_all_descendants(parent, allowed):
    rules = ROOT.joinpath(".dockerignore").read_text(encoding="utf-8").splitlines()
    index = rules.index(f"!{parent}/")
    assert rules[index + 1] == f"{parent}/**"
    assert index + 1 < rules.index(allowed)


def test_image_includes_only_streaming_cli_copy():
    copies = [line for line in ROOT.joinpath("Dockerfile").read_text().splitlines()
              if line.startswith("COPY tools")]
    assert copies == ["COPY tools/parse_stream.py /app/tools/parse_stream.py"]
