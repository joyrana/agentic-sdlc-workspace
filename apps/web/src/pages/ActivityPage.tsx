import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  DialogTrigger,
  Dropdown,
  Field,
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
  Option,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
  Textarea,
  makeStyles,
  tokens,
} from "@fluentui/react-components";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { useRunDetail, useRuns } from "../api/hooks";
import { useAuth } from "../auth";
import {
  EmptyState,
  ErrorNotice,
  Loading,
  PageHeader,
  StatusBadge,
  StrategyBadge,
} from "../components/common";

const useStyles = makeStyles({
  stats: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
    gap: tokens.spacingHorizontalM,
    marginBottom: tokens.spacingVerticalL,
  },
  stat: {
    backgroundColor: tokens.colorNeutralBackground1,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusMedium,
    padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalM}`,
    display: "flex",
    flexDirection: "column",
  },
  value: {
    fontSize: tokens.fontSizeBase500,
    fontWeight: tokens.fontWeightSemibold,
    fontVariantNumeric: "tabular-nums",
  },
  muted: { color: tokens.colorNeutralForeground3, fontSize: tokens.fontSizeBase200 },
  section: {
    marginTop: tokens.spacingVerticalXL,
    display: "flex",
    flexDirection: "column",
    gap: tokens.spacingVerticalS,
  },
  timeline: {
    listStyle: "none",
    padding: 0,
    margin: 0,
    borderLeft: `2px solid ${tokens.colorNeutralStroke2}`,
  },
  event: {
    padding: `${tokens.spacingVerticalXXS} 0 ${tokens.spacingVerticalXXS} ${tokens.spacingHorizontalM}`,
    display: "flex",
    gap: tokens.spacingHorizontalM,
  },
  ts: { fontVariantNumeric: "tabular-nums", color: tokens.colorNeutralForeground3, minWidth: "72px" },
  mono: { fontFamily: tokens.fontFamilyMonospace, fontSize: tokens.fontSizeBase200 },
  form: {
    display: "flex",
    gap: tokens.spacingHorizontalM,
    alignItems: "flex-end",
    flexWrap: "wrap",
    marginBottom: tokens.spacingVerticalL,
  },
});

const EVENT_TEXT: Record<string, (p: Record<string, unknown>) => string> = {
  "run.queued": () => "Run queued",
  "workflow.stage_started": (p) => `Stage started: ${String(p.stage).replace(/_/g, " ")}`,
  "backlog.generated": (p) => `Backlog generated: ${p.stories} stories (${p.strategy})`,
  "architecture.proposed": (p) => `Architecture proposal v${p.version}`,
  "architecture.edited": (p) => `Proposal edited by a person (v${p.version})`,
  "approval.requested": (p) => `Approval requested: ${String(p.gate).replace(/_/g, " ")}`,
  "approval.decided": (p) =>
    `Approval ${String(p.decision).replace(/_/g, " ")}: ${String(p.gate).replace(/_/g, " ")}`,
  "approval.stale": (p) => `Approval request became stale (${p.reason})`,
  "clarification.required": () => "Waiting for blocking questions to be resolved",
  "code.applied": (p) => `Change applied (attempt ${p.attempt}, ${p.strategy}) at ${p.head}`,
  "tests.completed": (p) => `${p.suite} tests ${p.status} (attempt ${p.attempt})`,
  "verification.completed": (p) =>
    `Verification: ${p.verified} verified, ${p.failed} failed, ${p.unverifiable} unverifiable`,
  "defect.created": (p) => `Defect: ${p.title}`,
  "release.candidate": (p) => `Release candidate ${p.version}${p.eligible ? "" : " (blocked)"}`,
  "deployment.completed": (p) => `Deployed to ${p.environment}: ${p.status}`,
  "deployment.rolled_back": (p) => `Rolled back: ${p.reason}`,
  "run.escalated": (p) => `Escalated at ${String(p.stage).replace(/_/g, " ")}: ${p.reason}`,
  "run.completed": () => "Run completed",
  "run.cancelled": (p) => `Run cancelled: ${p.reason}`,
  "run.rejected": (p) => `Rejected at ${p.gate}`,
};

export function ActivityPage() {
  const s = useStyles();
  const { projectId = "" } = useParams();
  const { rolesIn } = useAuth();
  const roles = rolesIn(projectId);
  const runs = useRuns(projectId);
  const [selected, setSelected] = useState<string | undefined>();
  const runId = selected ?? runs.data?.[0]?.id;
  const detail = useRunDetail(projectId, runId);
  const r = detail.data;
  const counters = (r?.counters ?? {}) as Record<string, number>;
  const escalation = (r?.outcome as { escalation?: { stage: string; reason: string } } | undefined)
    ?.escalation;
  const active = r && ["queued", "running", "awaiting_approval"].includes(r.status);

  return (
    <>
      <PageHeader
        title="Agent activity"
        description="What each agent did, with which skill and strategy, and what it cost."
        actions={
          <>
            {(roles.includes("project_owner") || roles.includes("engineer")) && !active && (
              <StartRun projectId={projectId} />
            )}
            {roles.includes("project_owner") && active && r && (
              <CancelRun projectId={projectId} runId={r.id} />
            )}
          </>
        }
      />
      {runs.isLoading && <Loading />}
      {runs.error && <ErrorNotice error={runs.error} />}
      {runs.data?.length === 0 && (
        <EmptyState title="No delivery runs yet">
          Register a repository in Settings, review the requirements, then start a run.
        </EmptyState>
      )}
      {runs.data && runs.data.length > 1 && (
        <Field label="Run" style={{ maxWidth: 420, marginBottom: 12 }}>
          <Dropdown
            value={
              runs.data
                .find((x) => x.id === runId)
                ?.created_at.slice(0, 19)
                .replace("T", " ") ?? ""
            }
            selectedOptions={runId ? [runId] : []}
            onOptionSelect={(_, d) => setSelected(d.optionValue)}
          >
            {runs.data.map((x) => (
              <Option key={x.id} value={x.id} text={x.created_at.slice(0, 19).replace("T", " ")}>
                {x.created_at.slice(0, 19).replace("T", " ")} — {x.status.replace(/_/g, " ")}
              </Option>
            ))}
          </Dropdown>
        </Field>
      )}
      {r && (
        <>
          {escalation && (
            <MessageBar intent="error" style={{ marginBottom: 12 }}>
              <MessageBarBody>
                <MessageBarTitle>Needs a person: {escalation.stage.replace(/_/g, " ")}</MessageBarTitle>
                {escalation.reason}
              </MessageBarBody>
            </MessageBar>
          )}
          {r.status === "awaiting_approval" && (
            <MessageBar intent="warning" style={{ marginBottom: 12 }}>
              <MessageBarBody>
                Waiting at <strong>{r.current_stage?.replace(/_/g, " ")}</strong>.{" "}
                {r.current_stage === "clarification_gate" ? (
                  <Link to="../requirements">Resolve blocking questions</Link>
                ) : (
                  <Link to="../approvals">Review the approval</Link>
                )}
              </MessageBarBody>
            </MessageBar>
          )}
          <div className={s.stats}>
            <Stat label="Status" value={<StatusBadge status={r.status} />} />
            <Stat label="Stage" value={r.current_stage?.replace(/_/g, " ") ?? "—"} />
            <Stat label="Attempt" value={counters.attempt ?? 1} />
            <Stat label="Model calls" value={counters.model_calls ?? 0} />
            <Stat label="Tokens" value={(counters.tokens ?? 0).toLocaleString()} />
            <Stat label="Cost" value={`$${(counters.cost_usd ?? 0).toFixed(4)}`} />
            <Stat label="Active time" value={`${Math.round(counters.active_s ?? 0)}s`} />
          </div>
          <section className={s.section} aria-labelledby="agents-h">
            <Text as="h2" id="agents-h" size={400} weight="semibold">
              Agent runs
            </Text>
            <Table size="small" aria-label="Agent runs">
              <TableHeader>
                <TableRow>
                  <TableHeaderCell>Stage</TableHeaderCell>
                  <TableHeaderCell>Agent</TableHeaderCell>
                  <TableHeaderCell>Skill</TableHeaderCell>
                  <TableHeaderCell>Produced by</TableHeaderCell>
                  <TableHeaderCell>Status</TableHeaderCell>
                  <TableHeaderCell>Attempts</TableHeaderCell>
                  <TableHeaderCell>Duration</TableHeaderCell>
                </TableRow>
              </TableHeader>
              <TableBody>
                {r.agent_runs.map((a) => (
                  <TableRow key={a.id}>
                    <TableCell>{a.stage?.replace(/_/g, " ")}</TableCell>
                    <TableCell>{a.agent}</TableCell>
                    <TableCell>
                      <span className={s.mono}>
                        {a.skill_id}@{a.skill_version}
                      </span>
                    </TableCell>
                    <TableCell>
                      <StrategyBadge strategy={a.is_mock ? "mock" : a.strategy} model={a.model_ref} />
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={a.status} />
                      {a.error && <div className={s.muted}>{a.error.slice(0, 160)}</div>}
                    </TableCell>
                    <TableCell>{a.attempts}</TableCell>
                    <TableCell>
                      {a.duration_ms != null ? `${(a.duration_ms / 1000).toFixed(2)}s` : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </section>
          {r.tool_invocations.length > 0 && (
            <section className={s.section} aria-labelledby="tools-h">
              <Text as="h2" id="tools-h" size={400} weight="semibold">
                Tool calls
              </Text>
              <Table size="small" aria-label="Tool calls">
                <TableHeader>
                  <TableRow>
                    <TableHeaderCell>Agent</TableHeaderCell>
                    <TableHeaderCell>Tool</TableHeaderCell>
                    <TableHeaderCell>Policy</TableHeaderCell>
                    <TableHeaderCell>Status</TableHeaderCell>
                    <TableHeaderCell>Arguments (redacted)</TableHeaderCell>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {r.tool_invocations.slice(-40).map((t, i) => (
                    <TableRow key={i}>
                      <TableCell>{String(t.agent)}</TableCell>
                      <TableCell className={s.mono}>{String(t.tool)}</TableCell>
                      <TableCell>
                        <StatusBadge status={t.policy_decision === "allowed" ? "approved" : "rejected"} />
                      </TableCell>
                      <TableCell>{String(t.status)}</TableCell>
                      <TableCell className={s.mono}>{JSON.stringify(t.arguments).slice(0, 120)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </section>
          )}
          <section className={s.section} aria-labelledby="tl-h">
            <Text as="h2" id="tl-h" size={400} weight="semibold">
              Timeline
            </Text>
            <ol className={s.timeline}>
              {r.timeline.map((e) => (
                <li key={String(e.seq)} className={s.event}>
                  <span className={s.ts}>{String(e.ts).slice(11, 19)}</span>
                  <Text>
                    {(EVENT_TEXT[String(e.type)] ?? (() => String(e.type)))(
                      e.payload as Record<string, unknown>,
                    )}
                  </Text>
                </li>
              ))}
            </ol>
          </section>
        </>
      )}
    </>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  const s = useStyles();
  return (
    <div className={s.stat}>
      <span className={s.muted}>{label}</span>
      <span className={s.value}>{value}</span>
    </div>
  );
}

function StartRun({ projectId }: { projectId: string }) {
  const s = useStyles();
  const qc = useQueryClient();
  const repos = useQuery({
    queryKey: ["project", projectId, "repositories"],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/repositories", {
          params: { path: { project_id: projectId } },
        }),
      ),
  });
  const [repo, setRepo] = useState<string | undefined>();
  const chosen = repo ?? repos.data?.[0]?.id;
  const start = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/projects/{project_id}/runs", {
          params: { path: { project_id: projectId } },
          body: { repository_id: chosen! },
        }),
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["project", projectId] }),
  });
  if (!repos.data?.length) return <Link to="../settings">Register a repository to start a run</Link>;
  return (
    <div className={s.form}>
      <Field label="Repository">
        <Dropdown
          value={repos.data.find((x) => x.id === chosen)?.name ?? ""}
          selectedOptions={chosen ? [chosen] : []}
          onOptionSelect={(_, d) => setRepo(d.optionValue)}
        >
          {repos.data.map((x) => (
            <Option key={x.id} value={x.id}>
              {x.name}
            </Option>
          ))}
        </Dropdown>
      </Field>
      <Button appearance="primary" onClick={() => start.mutate()} disabled={start.isPending}>
        Start delivery run
      </Button>
      {start.error && <ErrorNotice error={start.error} />}
    </div>
  );
}

function CancelRun({ projectId, runId }: { projectId: string; runId: string }) {
  const qc = useQueryClient();
  const [reason, setReason] = useState("");
  const cancel = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/projects/{project_id}/runs/{run_id}/cancel", {
          params: { path: { project_id: projectId, run_id: runId } },
          body: { reason },
        }),
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["project", projectId] }),
  });
  return (
    <Dialog>
      <DialogTrigger disableButtonEnhancement>
        <Button>Cancel run</Button>
      </DialogTrigger>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>Cancel this delivery run?</DialogTitle>
          <DialogContent>
            <Text>
              Pending approvals are cancelled and no further agent work happens. Recorded evidence is kept.
            </Text>
            <Field label="Reason" required>
              <Textarea value={reason} onChange={(_, d) => setReason(d.value)} />
            </Field>
            {cancel.error && <ErrorNotice error={cancel.error} />}
          </DialogContent>
          <DialogActions>
            <DialogTrigger disableButtonEnhancement>
              <Button>Keep running</Button>
            </DialogTrigger>
            <Button
              appearance="primary"
              disabled={!reason.trim() || cancel.isPending}
              onClick={() => cancel.mutate()}
            >
              Cancel run
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  );
}
