import pytest

from task_service import TaskService, ValidationError


def test_create_and_list() -> None:
    s = TaskService()
    t = s.create_task("Buy milk", "2 litres")
    assert t.id == 1 and t.title == "Buy milk"
    assert s.list_tasks() == [t]


def test_ids_increment() -> None:
    s = TaskService()
    assert [s.create_task(x).id for x in "abc"] == [1, 2, 3]


def test_complete_task() -> None:
    s = TaskService()
    t = s.create_task("Write report")
    assert s.complete_task(t.id).done is True


def test_complete_unknown_task() -> None:
    with pytest.raises(ValidationError):
        TaskService().complete_task(42)
