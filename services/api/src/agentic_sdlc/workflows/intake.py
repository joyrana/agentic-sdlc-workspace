"""Intake workflow: normalize a document version, run the Business Analyst skill, persist results.

normalize ──► extract (skill + persist, one transaction) ──► finalize

Nodes are idempotent: a re-run after a crash skips work already committed (status checks).
"""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from sqlalchemy import delete, select

from agentic_sdlc.audit import service as audit
from agentic_sdlc.core.db import session_scope
from agentic_sdlc.core.settings import get_settings
from agentic_sdlc.core.storage import get_storage
from agentic_sdlc.documents.normalize import normalize_docx, normalize_text
from agentic_sdlc.events import service as events
from agentic_sdlc.orm import DocumentBlock, DocumentVersion, SourceDocument, User
from agentic_sdlc.requirements.service import persist_extraction
from agentic_sdlc.seed import agent_email
from agentic_sdlc.skills.base import BudgetTracker, ExecutionContext, Limits, SkillError
from agentic_sdlc.skills.runtime import SkillRuntime
from agentic_sdlc.workflows.checkpoint import checkpointer


class IntakeState(TypedDict, total=False):
    document_version_id: str
    project_id: str
    status: str
    error: str | None
    summary: dict[str, Any]


def _load(db: Any, version_id: str) -> tuple[DocumentVersion, SourceDocument]:
    version = db.get(DocumentVersion, uuid.UUID(version_id))
    doc = db.get(SourceDocument, version.document_id)
    return version, doc


def normalize(state: IntakeState) -> IntakeState:
    with session_scope() as db:
        version, doc = _load(db, state["document_version_id"])
        if version.status in ("processed", "failed"):
            return {"status": version.status}
        if version.normalized is None:
            data = get_storage().get(version.storage_key)
            try:
                if version.original_filename.lower().endswith(".docx"):
                    normalized = normalize_docx(data)
                else:
                    normalized = normalize_text(data, kind=doc.kind)
            except Exception as exc:  # malformed content inside a structurally valid file
                version.status = "failed"
                version.warnings = [*version.warnings, f"Could not read the document: {type(exc).__name__}"]
                events.emit(
                    db,
                    type="document.failed",
                    project_id=doc.project_id,
                    payload={"document_id": str(doc.id), "version": version.version},
                )
                return {"status": "failed", "error": type(exc).__name__}
            version.normalized = normalized
            version.warnings = [*version.warnings, *normalized.get("warnings", [])]
            db.execute(delete(DocumentBlock).where(DocumentBlock.document_version_id == version.id))
            for b in normalized["blocks"]:
                db.add(
                    DocumentBlock(
                        document_version_id=version.id,
                        project_id=doc.project_id,
                        block_id=b["block_id"],
                        kind=b["kind"],
                        ordinal=b["ordinal"],
                        section_path=b.get("section_path", []),
                        text=b["text"],
                    )
                )
        version.status = "processing"
        events.emit(
            db,
            type="document.normalized",
            project_id=doc.project_id,
            payload={
                "document_id": str(doc.id),
                "version": version.version,
                "blocks": len(version.normalized["blocks"]),
            },
        )
        return {"status": "processing"}


def extract(state: IntakeState) -> IntakeState:
    if state.get("status") != "processing":
        return {}
    s = get_settings()
    try:
        with session_scope() as db:
            version, doc = _load(db, state["document_version_id"])
            if version.status == "processed":
                return {"status": "processed", "summary": version.extraction_summary}
            skill = "transcript-analysis" if doc.kind == "transcript" else "brd-analysis"
            ctx = ExecutionContext(
                db=db,
                project_id=doc.project_id,
                agent="business-analyst",
                stage="intake",
                budget=BudgetTracker(
                    Limits(
                        max_model_calls=10,
                        max_tokens=s.max_tokens,
                        max_cost_usd=s.max_cost_usd,
                        max_wall_clock_s=s.max_wall_clock_s,
                    )
                ),
            )
            result = SkillRuntime().execute(
                skill, {"document_version_id": str(version.id), "document_kind": doc.kind}, ctx
            )
            agent_user = db.scalar(select(User).where(User.email == agent_email("business-analyst")))
            summary = persist_extraction(
                db,
                project_id=doc.project_id,
                version=version,
                document=doc,
                extraction=result.output,
                method=result.strategy,
                model_ref=result.model_ref,
                agent_user_id=agent_user.id if agent_user else None,
            )
            summary["agent_run_id"] = str(result.agent_run_id)
            summary["skill"] = skill
            summary["is_mock"] = result.is_mock
            summary["fallback_reason"] = (result.routing or {}).get("fallback_reason")
            version.extraction_summary = summary
            version.status = "processed"
            audit.record(
                db,
                action="document.processed",
                actor_id=agent_user.id if agent_user else None,
                actor_type="agent",
                project_id=doc.project_id,
                target_type="document_version",
                target_id=version.id,
                details={
                    "created": len(summary["created"]),
                    "updated": len(summary["updated"]),
                    "strategy": result.strategy,
                },
            )
            return {"status": "processed", "summary": summary}
    except SkillError as exc:
        with session_scope() as db:
            version, doc = _load(db, state["document_version_id"])
            version.status = "failed"
            version.warnings = [*version.warnings, f"Extraction failed: {exc}"[:500]]
            events.emit(
                db,
                type="document.failed",
                project_id=doc.project_id,
                payload={"document_id": str(doc.id), "version": version.version},
            )
        return {"status": "failed", "error": str(exc)[:500]}


def finalize(state: IntakeState) -> IntakeState:
    if state.get("status") != "processed":
        return {}
    with session_scope() as db:
        version, doc = _load(db, state["document_version_id"])
        summary = version.extraction_summary
        events.emit(
            db,
            type="document.processed",
            project_id=doc.project_id,
            payload={
                "document_id": str(doc.id),
                "version": version.version,
                "requirements_created": len(summary.get("created", [])),
                "requirements_updated": len(summary.get("updated", [])),
                "questions_added": summary.get("questions_added", 0),
                "method": summary.get("method"),
            },
            artifact_refs=[{"type": "document_version", "id": str(version.id), "version": version.version}],
        )
    return {}


def build_graph() -> StateGraph[IntakeState]:
    g = StateGraph(IntakeState)
    g.add_node("normalize", normalize)
    g.add_node("extract", extract)
    g.add_node("finalize", finalize)
    g.add_edge(START, "normalize")
    g.add_edge("normalize", "extract")
    g.add_edge("extract", "finalize")
    g.add_edge("finalize", END)
    return g


def handle(payload: dict[str, Any], run_id: uuid.UUID | None) -> None:
    version_id = payload["document_version_id"]
    with checkpointer() as cp:
        graph = build_graph().compile(checkpointer=cp)
        config: RunnableConfig = {"configurable": {"thread_id": f"intake:{version_id}"}}
        state = graph.get_state(config)
        if state.next:  # resume an interrupted run from its last checkpoint
            graph.invoke(None, config)
        elif not state.values:
            graph.invoke({"document_version_id": version_id, "project_id": payload["project_id"]}, config)
