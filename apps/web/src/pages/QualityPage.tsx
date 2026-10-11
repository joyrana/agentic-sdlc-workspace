import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Tab,
  TabList,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
  Tooltip,
  makeStyles,
  tokens,
} from "@fluentui/react-components";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { useEvidence, useLatestRun, type DefectT, type TestRunT, type Verdict } from "../api/hooks";
import { LogView } from "../components/Diff";
import { EmptyState, ErrorNotice, Loading, PageHeader, StatusBadge } from "../components/common";

const useStyles = makeStyles({
  mono: { fontFamily: tokens.fontFamilyMonospace, fontSize: tokens.fontSizeBase200 },
  muted: { color: tokens.colorNeutralForeground3, fontSize: tokens.fontSizeBase200 },
  card: {
    backgroundColor: tokens.colorNeutralBackground1,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    padding: tokens.spacingHorizontalL,
    marginBottom: tokens.spacingVerticalL,
    maxWidth: "860px",
    display: "flex",
    flexDirection: "column",
    gap: tokens.spacingVerticalXS,
  },
  list: { margin: 0, paddingLeft: tokens.spacingHorizontalL },
});

type TraceRow = {
  requirement: string;
  criterion: string;
  criterion_text: string;
  source: { document: string; version: number; block_id: string } | null;
  backlog_items: string[];
  verification: { status: string; attempt: number; tests: { nodeid: string; outcome: string }[] } | null;
  defects: { title: string; status: string }[];
  release: { version: string; status: string }[];
};

export function QualityPage() {
  const s = useStyles();
  const { projectId = "" } = useParams();
  const { run, isLoading } = useLatestRun(projectId);
  const [tab, setTab] = useState<"verification" | "tests" | "defects" | "trace">("verification");
  const verdicts = useEvidence<Verdict[]>(projectId, `verification?run_id=${run?.id}`, !!run);
  const tests = useEvidence<TestRunT[]>(projectId, `test-runs?run_id=${run?.id}`, !!run);
  const defects = useEvidence<DefectT[]>(projectId, `defects?run_id=${run?.id}`, !!run);
  const trace = useEvidence<{ rows: TraceRow[] }>(projectId, `traceability${run ? `?run_id=${run.id}` : ""}`);
  const [logFor, setLogFor] = useState<string | null>(null);
  if (isLoading) return <Loading />;
  const attempts = Array.from(new Set((verdicts.data ?? []).map((v) => v.attempt))).sort();
  const criteria = Array.from(new Set((verdicts.data ?? []).map((v) => v.criterion_key))).sort();
  return (
    <>
      <PageHeader
        title="Verification and QA"
        description="Acceptance is decided from QA’s independent test run for the exact commit, criterion by criterion. Unverifiable criteria are shown, never counted as passed."
      />
      <TabList
        selectedValue={tab}
        onTabSelect={(_, d) => setTab(d.value as typeof tab)}
        style={{ marginBottom: 12 }}
      >
        <Tab value="verification">Criteria</Tab>
        <Tab value="tests">Test runs</Tab>
        <Tab value="defects">Defects {defects.data ? `(${defects.data.length})` : ""}</Tab>
        <Tab value="trace">Traceability</Tab>
      </TabList>
      {!run && <EmptyState title="No delivery run yet" />}
      {[verdicts.error, tests.error, defects.error, trace.error].filter(Boolean).map((e, i) => (
        <ErrorNotice key={i} error={e} />
      ))}
      {tab === "verification" &&
        run &&
        (criteria.length === 0 ? (
          <EmptyState title="Not verified yet">Verification runs after QA tests the change.</EmptyState>
        ) : (
          <Table size="small" aria-label="Criterion verification by attempt">
            <TableHeader>
              <TableRow>
                <TableHeaderCell>Criterion</TableHeaderCell>
                <TableHeaderCell>Priority</TableHeaderCell>
                {attempts.map((a) => (
                  <TableHeaderCell key={a}>Attempt {a}</TableHeaderCell>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {criteria.map((k) => {
                const row = (verdicts.data ?? []).filter((v) => v.criterion_key === k);
                const first = row[0];
                return (
                  <TableRow key={k}>
                    <TableCell>
                      <span className={s.mono}>{k}</span>
                      <div className={s.muted}>{first?.criterion}</div>
                    </TableCell>
                    <TableCell>{first?.priority}</TableCell>
                    {attempts.map((a) => {
                      const v = row.find((x) => x.attempt === a);
                      return (
                        <TableCell key={a}>
                          {v ? (
                            <Tooltip
                              content={v.gaps.join("; ") || `confidence ${v.confidence}`}
                              relationship="description"
                            >
                              <span>
                                <StatusBadge status={v.status} />
                              </span>
                            </Tooltip>
                          ) : (
                            "—"
                          )}
                        </TableCell>
                      );
                    })}
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        ))}
      {tab === "tests" && (
        <Table size="small" aria-label="Test runs">
          <TableHeader>
            <TableRow>
              <TableHeaderCell>Suite</TableHeaderCell>
              <TableHeaderCell>Attempt</TableHeaderCell>
              <TableHeaderCell>Status</TableHeaderCell>
              <TableHeaderCell>Results</TableHeaderCell>
              <TableHeaderCell>Commit</TableHeaderCell>
              <TableHeaderCell>Sandbox</TableHeaderCell>
              <TableHeaderCell>Log</TableHeaderCell>
            </TableRow>
          </TableHeader>
          <TableBody>
            {tests.data?.map((t) => (
              <TableRow key={t.id}>
                <TableCell>{t.suite}</TableCell>
                <TableCell>{t.attempt}</TableCell>
                <TableCell>
                  <StatusBadge status={t.status} />
                </TableCell>
                <TableCell>
                  {t.totals.passed}/{t.totals.total} passed
                  {t.cases
                    .filter((c) => c.outcome !== "passed")
                    .map((c) => (
                      <div key={c.nodeid} className={s.muted}>
                        {c.nodeid.split("::").pop()} [{c.criteria.join(", ")}]
                      </div>
                    ))}
                </TableCell>
                <TableCell className={s.mono}>{t.commit?.slice(0, 12)}</TableCell>
                <TableCell className={s.muted}>
                  {String(t.sandbox.backend)} · {String(t.sandbox.duration_s)}s ·{" "}
                  {String(t.sandbox.exit_meaning)}
                </TableCell>
                <TableCell>
                  <Button size="small" onClick={() => setLogFor(t.id)}>
                    View log
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {tab === "defects" &&
        (defects.data?.length ? (
          defects.data.map((d) => (
            <article key={d.id} className={s.card} aria-label={d.title}>
              <Text weight="semibold">{d.title}</Text>
              <Text>
                <StatusBadge status={d.status} /> {d.severity} · found in attempt {d.found_in_attempt}
                {d.fixed_in_attempt ? ` · fixed in attempt ${d.fixed_in_attempt}` : ""} · backlog{" "}
                <span className={s.mono}>{d.backlog_key}</span>
              </Text>
              <Text>
                <strong>Expected:</strong> {d.expected}
              </Text>
              <Text>
                <strong>Actual:</strong> {d.actual}
              </Text>
              <ol className={s.list}>
                {d.reproduction_steps.map((st, i) => (
                  <li key={i} className={s.mono}>
                    {st}
                  </li>
                ))}
              </ol>
            </article>
          ))
        ) : (
          <EmptyState title="No defects recorded" />
        ))}
      {tab === "trace" && trace.data && (
        <Table size="small" aria-label="Traceability matrix">
          <TableHeader>
            <TableRow>
              <TableHeaderCell>Source</TableHeaderCell>
              <TableHeaderCell>Requirement → criterion</TableHeaderCell>
              <TableHeaderCell>Backlog</TableHeaderCell>
              <TableHeaderCell>Verification</TableHeaderCell>
              <TableHeaderCell>Defects</TableHeaderCell>
              <TableHeaderCell>Release</TableHeaderCell>
            </TableRow>
          </TableHeader>
          <TableBody>
            {trace.data.rows.map((r) => (
              <TableRow key={r.criterion}>
                <TableCell className={s.muted}>
                  {r.source ? `${r.source.document} v${r.source.version} · ${r.source.block_id}` : "human"}
                </TableCell>
                <TableCell>
                  <span className={s.mono}>
                    {r.requirement} → {r.criterion}
                  </span>
                </TableCell>
                <TableCell className={s.mono}>{r.backlog_items.join(", ")}</TableCell>
                <TableCell>{r.verification ? <StatusBadge status={r.verification.status} /> : "—"}</TableCell>
                <TableCell>{r.defects.map((d) => d.status).join(", ") || "—"}</TableCell>
                <TableCell>
                  {r.release.map((x) => `${x.version} (${x.status.replace(/_/g, " ")})`).join(", ") || "—"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {logFor && <LogDialog projectId={projectId} id={logFor} onClose={() => setLogFor(null)} />}
    </>
  );
}

function LogDialog({ projectId, id, onClose }: { projectId: string; id: string; onClose: () => void }) {
  const run = useEvidence<TestRunT>(projectId, `test-runs/${id}`);
  return (
    <Dialog open onOpenChange={(_, d) => !d.open && onClose()}>
      <DialogSurface style={{ maxWidth: 900 }}>
        <DialogBody>
          <DialogTitle>
            {run.data ? `${run.data.suite} run, attempt ${run.data.attempt}` : "Test log"}
          </DialogTitle>
          <DialogContent>
            {run.isLoading && <Loading />}
            {run.data && (
              <>
                <Text size={200}>{run.data.command.join(" ")}</Text>
                <LogView text={run.data.log ?? ""} />
              </>
            )}
          </DialogContent>
          <DialogActions>
            <Button onClick={onClose}>Close</Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  );
}
