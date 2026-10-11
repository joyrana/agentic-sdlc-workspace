from task_service.errors import ValidationError
from task_service.models import Task


class TaskService:
    """In-memory task store."""

    def __init__(self) -> None:
        self._tasks: dict[int, Task] = {}
        self._next_id = 1

    def create_task(self, title: str, description: str = "") -> Task:
        task = Task(id=self._next_id, title=title, description=description)
        self._tasks[task.id] = task
        self._next_id += 1
        return task

    def list_tasks(self) -> list[Task]:
        return list(self._tasks.values())

    def complete_task(self, task_id: int) -> Task:
        if task_id not in self._tasks:
            raise ValidationError(f"unknown task {task_id}")
        task = self._tasks[task_id]
        task.done = True
        return task
