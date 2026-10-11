"""SAMPLE independent acceptance suite for the title-validation BRD.

Owned by QA, not by the developer agent: it is copied into the sandbox only for the acceptance run
and is never part of the change under test. Each test names the acceptance criterion it verifies.
"""

import pytest

from task_service import TaskService, ValidationError


@pytest.mark.criterion("FR-001-AC1")
def test_title_of_121_characters_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TaskService().create_task("a" * 121)


@pytest.mark.criterion("FR-001-AC2")
def test_title_of_exactly_120_characters_is_accepted() -> None:
    task = TaskService().create_task("a" * 120)
    assert len(task.title) == 120


@pytest.mark.criterion("FR-002-AC1")
def test_surrounding_whitespace_is_trimmed() -> None:
    assert TaskService().create_task("  Buy milk  ").title == "Buy milk"


@pytest.mark.criterion("FR-003-AC1")
def test_title_of_only_spaces_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TaskService().create_task("     ")


@pytest.mark.criterion("NFR-002-AC1")
def test_error_names_the_title_field_with_a_readable_message() -> None:
    with pytest.raises(ValidationError) as err:
        TaskService().create_task("")
    assert err.value.field == "title"
    assert len(err.value.message.split()) >= 3


@pytest.mark.criterion("FR-001-AC1")
@pytest.mark.criterion("FR-003-AC1")
def test_rejected_titles_are_not_stored() -> None:
    service = TaskService()
    for bad in ("", "   ", "b" * 500):
        with pytest.raises(ValidationError):
            service.create_task(bad)
    assert service.list_tasks() == []
