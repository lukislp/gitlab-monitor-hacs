"""Direct unit tests for GitLabProjectEntity (custom_components/gitlab_monitor/entity.py).

Deliberately bypasses full HA entity-platform setup: `project`/`available` are pure
property logic over `coordinator.data`, and a Mock coordinator is enough to construct
the entity (its __init__ only reads `coordinator.data[project_key].info` and
`coordinator.config_entry.entry_id`) and then mutate `.data` afterwards to exercise
both "coordinator hasn't refreshed yet" (data is None) and "this project dropped out
of the data" (key missing) - two different early-return branches in `project`.
"""
from __future__ import annotations

from unittest.mock import Mock

from custom_components.gitlab_monitor.coordinator import GitLabProjectData
from custom_components.gitlab_monitor.entity import GitLabProjectEntity

from .conftest import TEST_PROJECT, TEST_PROJECT_ID, make_project_info


def _make_entity() -> tuple[GitLabProjectEntity, Mock]:
    coordinator = Mock()
    coordinator.config_entry.entry_id = "entry123"
    coordinator.data = {
        TEST_PROJECT: GitLabProjectData(key=TEST_PROJECT, info=make_project_info())
    }
    entity = GitLabProjectEntity(coordinator, TEST_PROJECT, "suffix")
    return entity, coordinator


def test_project_and_available_when_data_present() -> None:
    entity, _coordinator = _make_entity()
    assert entity.project is not None
    assert entity.project.info["id"] == TEST_PROJECT_ID
    assert entity.available is True


def test_project_returns_none_when_coordinator_data_is_none() -> None:
    """Coordinator hasn't completed its first refresh yet - data is None, not {}."""
    entity, coordinator = _make_entity()
    coordinator.data = None
    assert entity.project is None
    assert entity.available is False


def test_project_returns_none_when_project_key_missing() -> None:
    """This project dropped out of coordinator.data (e.g. all its fetches failed)."""
    entity, coordinator = _make_entity()
    coordinator.data = {}
    assert entity.project is None
    assert entity.available is False
