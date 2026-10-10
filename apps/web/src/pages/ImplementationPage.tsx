import { Badge, Text, makeStyles, tokens } from "@fluentui/react-components";
import { useParams } from "react-router-dom";
import { useEvidence, useLatestRun, type CodeChange, type TestRunT } from "../api/hooks";
import { Diff } from "../components/Diff";
import {
  EmptyState,
  ErrorNotice,
  Loading,
  PageHeader,
  StatusBadge,
  StrategyBadge,
} from "../components/common";

const useStyles = makeStyles({
  change: {
    backgroundColor: tokens.colorNeutralBackground1,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    padding: tokens.spacingHorizontalL,
    marginBottom: tokens.spacingVerticalXL,
    display: "flex",
    flexDirection: "column",
    gap: tokens.spacingVerticalS,
  },
  row: { display: "flex", gap: tokens.spacingHorizontalS, alignItems: "center", flexWrap: "wrap" },
  mono: { fontFamily: tokens.fontFamilyMonospace, fontSize: tokens.fontSizeBase200 },
  muted: { color: tokens.colorNeutralForeground3 },
  list: { margin: 0, paddingLeft: tokens.spacingHorizontalL },
});

export function ImplementationPage() {
  const s = useStyles();
  const { projectId = "" } = useParams();
  const { run, isLoading } = useLatestRun(projectId);
  const changes = useEvidence<CodeChange[]>(
    projectId,
    `code-changes?run_id=${run?.id}&include_diff=true`,
    !!run,
  );
  const tests = useEvidence<TestRunT[]>(projectId, `test-runs?run_id=${run?.id}`, !!run);
  if (isLoading || changes.isLoading) return <Loading />;
  return (
    <>
      <PageHeader
        title="Implementation"
        description="Each attempt is applied in a disposable workspace, reviewed against policy and the approved plan, and pushed to an agent branch. Nothing is merged without review."
      />
      {changes.error && <ErrorNotice error={changes.error} />}
      {(!changes.data || changes.data.length === 0) && (
        <EmptyState title="No code changes yet">
          Changes appear after the implementation scope is approved.
        </EmptyState>
      )}
      {changes.data?.map((c) => {
        const dev = tests.data?.find((t) => t.suite === "developer" && t.attempt === c.attempt);
        return (
          <article key={c.id} className={s.change} aria-label={`Attempt ${c.attempt}`}>
            <div className={s.row}>
              <Text weight="semibold" size={400}>
                Attempt {c.attempt}
              </Text>
              <StatusBadge status={c.status} />
              <StrategyBadge strategy={c.coding_agent.strategy} model={c.coding_agent.model_ref} />
              <span className={s.mono}>
                {c.branch} @ {c.head_commit?.slice(0, 12) ?? "—"} (base {c.base_commit.slice(0, 12)})
              </span>
            </div>
            <Text>{c.summary}</Text>
            {c.coding_agent.notes?.map((n) => (
              <Text key={n} className={s.muted}>
                {n}
              </Text>
            ))}
            <div className={s.row}>
              <Badge appearance="tint" color={c.review.ok ? "success" : "danger"}>
                policy review {c.review.ok ? "passed" : "failed"}
              </Badge>
              <Text className={s.muted}>
                +{c.review.added_lines} −{c.review.removed_lines} in {c.files.length} files
              </Text>
              {dev && (
                <>
                  <Text className={s.muted}>developer tests:</Text>
                  <StatusBadge status={dev.status} />
                  <Text className={s.muted}>
                    {dev.totals.passed}/{dev.totals.total} passed (developer evidence is not used for
                    acceptance)
                  </Text>
                </>
              )}
            </div>
            {[...c.review.violations, ...c.review.warnings].length > 0 && (
              <ul className={s.list}>
                {c.review.violations.map((v) => (
                  <li key={v}>Violation: {v}</li>
                ))}
                {c.review.warnings.map((v) => (
                  <li key={v}>Warning: {v}</li>
                ))}
              </ul>
            )}
            {c.diff && <Diff text={c.diff} />}
          </article>
        );
      })}
    </>
  );
}
