"""Atheris fuzz harness for the small parsers between GitLab / the user and the entities.

Contract under test: the config-flow URL normalizer and project-list splitter, the sensor
timestamp parser and state truncation, and the project-path encoder never raise for any
input and keep their documented shape (scheme prefix, no trailing slash, unique non-empty
keys, at most 255 characters, no unescaped slash).

Run locally (Linux, needs the atheris wheel):
    pip install --require-hashes -r requirements_test.txt -r requirements_fuzz.txt
    PYTHONPATH=. python fuzz/fuzz_parsers.py -max_total_time=60
CI runs the same harness for a short, fixed time budget (see .github/workflows/ci-cd.yml).
"""

from __future__ import annotations

import sys

import atheris

from custom_components.gitlab_monitor.api import GitLabClient
from custom_components.gitlab_monitor.config_flow import (
    _normalize_url,
    _parse_custom_projects,
)
from custom_components.gitlab_monitor.sensor import _parse_ts, _truncate


def test_one_input(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)

    url = _normalize_url(fdp.ConsumeUnicodeNoSurrogates(64))
    if not url.startswith(("http://", "https://")) or (
        len(url) > 8 and url.endswith("/")
    ):
        raise AssertionError(f"_normalize_url produced {url!r}")

    projects = _parse_custom_projects(fdp.ConsumeUnicodeNoSurrogates(128))
    if len(set(projects)) != len(projects) or any(
        not p or p != p.strip().strip("/") for p in projects
    ):
        raise AssertionError(f"_parse_custom_projects produced {projects!r}")

    _parse_ts(fdp.ConsumeUnicodeNoSurrogates(40) if fdp.ConsumeBool() else None)

    truncated = _truncate(
        fdp.ConsumeUnicodeNoSurrogates(400) if fdp.ConsumeBool() else None
    )
    if truncated is not None and len(truncated) > 255:
        raise AssertionError(f"_truncate returned {len(truncated)} characters")

    project = (
        fdp.ConsumeInt(4) if fdp.ConsumeBool() else fdp.ConsumeUnicodeNoSurrogates(64)
    )
    if "/" in GitLabClient._encode_project(project):
        raise AssertionError(f"_encode_project left a slash in {project!r}")


if __name__ == "__main__":
    atheris.instrument_all()
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()
