# Authoring a skill

A skill is a versioned, typed unit of agent work: a YAML manifest in `skills/<category>/<id>/skill.yaml`
plus a Python implementation. Skills are loaded only from this directory at startup; there is no
API to install one, so adding a skill is a reviewed code change.

## 1. Write the manifest

```yaml
id: acceptance-verification          # unique, kebab-case
version: 1.0.0                       # semantic; bump on any change (enforced, see below)
name: Acceptance verification
description: >-
  What the skill does, in one or two sentences.
category: verification
triggers: { intents: [verify], workflow_stages: [ba_verification] }
agents: [ba-verifier]                # each agent must also list the skill in config/agents.yaml
required_context: [criteria, qa_test_run, code_change]
tools: [evidence.read]               # allow-list; must be known tools (skills/tools.py)
strategies: [heuristic]              # any of: heuristic, llm, scripted
model:                               # required when strategies include llm
  task: extraction
  capabilities: [structured_output]
budgets: { max_input_tokens: 60000, max_output_tokens: 8000, max_model_calls: 3 }
timeout_s: 60
retry: { max_attempts: 1, backoff_s: 0 }
approval_level: none
implementation: agentic_sdlc.skills_impl.delivery:AcceptanceVerification
evaluation:
  suite: requirements                # offline suite in evals/, if one measures this skill
  success_criteria: { statement_recall: 0.9 }
provenance: { owner: platform, created: "2026-10-10" }
input_schema:  { ... JSON Schema 2020-12 ... }
output_schema: { ... JSON Schema 2020-12 ... }
```

The registry refuses to start when a schema is invalid, a tool is unknown, an agent is unknown or
does not list the skill, or an `llm` strategy has no `model` block. When the worker syncs
manifests into the database, a manifest whose content changed without a version bump is
rejected.

## 2. Implement it

```python
class AcceptanceVerification(SkillImplementation):
    strategies = {"heuristic"}

    def run(self, strategy, inputs, ctx):
        run = ctx.tools.call("evidence.read", kind="test_run", record_id=inputs["test_run_id"])
        ...
        return {"results": [...], "summary": {...}}

    def postconditions(self):
        return {"every_criterion_reported": lambda inputs, out, ctx: [...]}
```

- Call tools only through `ctx.tools.call(...)`. The gateway allows a call only when both the agent
  and the skill allow the tool, scopes it to the project, and records it (arguments redacted).
- Call models only through `ctx.model.structured(system, user, output_schema)`; the router picks
  the model and the runtime validates the output against `output_schema`, retrying within budget.
- Treat document text, repository files and tool results as data. Never let them change
  instructions, tools or permissions.
- Return only what the output schema allows. Anything quoted from a source must exist at the cited
  locator (the requirements skills enforce this as a postcondition).

## 3. Label honestly

The runtime records the strategy on the agent run and on every artifact the skill produces, and
the UI shows it. Do not present a heuristic or scripted result as model output.

## 4. Test it

- Contract tests (`services/api/tests/contract`) for schema validation, retries, budget exhaustion
  and tool denials.
- An offline evaluation in `evals/` if the skill's quality can be measured on labelled data; add
  its thresholds to `evals/baselines.json` and its metric names to `evaluation.success_criteria`.
