import { Text, makeStyles, tokens } from "@fluentui/react-components";
import { Link, useOutletContext } from "react-router-dom";
import type { Schemas } from "../api/client";
import { EmptyState, StatusBadge } from "../components/common";

type Summary = Schemas["ProjectSummary"];

const useStyles = makeStyles({
  grid: {
    display: "flex",
    flexWrap: "wrap",
    gap: "1px",
    backgroundColor: tokens.colorNeutralStroke2,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    overflow: "hidden",
    marginBottom: tokens.spacingVerticalXXL,
  },
  cell: {
    flex: "1 1 160px",
    backgroundColor: tokens.colorNeutralBackground1,
    padding: `${tokens.spacingVerticalM} ${tokens.spacingHorizontalL}`,
    display: "flex",
    flexDirection: "column",
    gap: tokens.spacingVerticalXXS,
    textDecoration: "none",
    color: "inherit",
  },
  value: {
    fontSize: tokens.fontSizeBase600,
    fontWeight: tokens.fontWeightSemibold,
    lineHeight: tokens.lineHeightBase600,
  },
  section: {
    display: "flex",
    flexDirection: "column",
    gap: tokens.spacingVerticalS,
    marginBottom: tokens.spacingVerticalXL,
    maxWidth: "72ch",
  },
});

export function ProjectOverview() {
  const s = useStyles();
  const data = useOutletContext<Summary>();
  const { counts, tokens: usage, active_run, project } = data;
  const metrics: { label: string; value: string | number; to: string }[] = [
    { label: "Sources", value: counts.documents ?? 0, to: "sources" },
    { label: "Requirements", value: counts.requirements ?? 0, to: "requirements" },
    { label: "Open questions", value: counts.open_questions ?? 0, to: "requirements" },
    { label: "Backlog items", value: counts.backlog_items ?? 0, to: "backlog" },
    { label: "Awaiting approval", value: data.pending_approvals, to: "approvals" },
    { label: "Open defects", value: data.open_defects, to: "quality" },
    {
      label: usage.includes_estimates ? "Tokens (incl. estimates)" : "Tokens",
      value: (Number(usage.input) + Number(usage.output)).toLocaleString(),
      to: "activity",
    },
  ];

  return (
    <>
      <div className={s.grid}>
        {metrics.map((m) => (
          <Link key={m.label} to={m.to} className={s.cell}>
            <Text size={200}>{m.label}</Text>
            <span className={s.value}>{m.value}</span>
          </Link>
        ))}
      </div>
      <section className={s.section} aria-labelledby="run-h">
        <Text as="h2" id="run-h" size={400} weight="semibold">
          Delivery run
        </Text>
        {active_run ? (
          <Text>
            Latest run is <StatusBadge status={String(active_run.status)} /> at stage{" "}
            <strong>{String(active_run.stage ?? "starting")}</strong>. <Link to="activity">See activity</Link>
          </Text>
        ) : counts.requirements ? (
          <Text>
            Requirements are ready for review. When the acceptance criteria look right,{" "}
            <Link to="activity">start a delivery run</Link>.
          </Text>
        ) : (
          <EmptyState
            title="Start with a source document"
            action={<Link to="sources">Upload a BRD or transcript</Link>}
          >
            The Business Analyst agent extracts requirements, rules and acceptance criteria, each linked to
            the passage it came from.
          </EmptyState>
        )}
      </section>
      {project.goals && (
        <section className={s.section} aria-labelledby="goals-h">
          <Text as="h2" id="goals-h" size={400} weight="semibold">
            Goals
          </Text>
          <Text>{project.goals}</Text>
        </section>
      )}
    </>
  );
}
