# sample-task-service (SAMPLE FIXTURE)

A deliberately small in-memory task service used by the Agentic SDLC Workspace demo and tests.
The workspace copies this directory into its data folder, initializes it as a git repository, and
agents only ever change disposable clones of that copy.

```python
from task_service import TaskService

service = TaskService()
task = service.create_task("Buy milk")
service.complete_task(task.id)
```

Run tests: `python -m pytest -q`
