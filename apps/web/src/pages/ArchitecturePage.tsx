import {
  Badge,
  Button,
  Checkbox,
  Dropdown,
  Field,
  MessageBar,
  MessageBarBody,
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
import { useParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { useEvidence } from "../api/hooks";
import { useAuth } from "../auth";
import { Mermaid } from "../components/Mermaid";
import {
  EmptyState,
  ErrorNotice,
  Loading,
  PageHeader,
  StatusBadge,
  StrategyBadge,
} from "../components/common";

type OptionT = {
  name: string;
  summary: string;
  pros: string[];
  cons: string[];
  risks: string[];
  files: string[];
};
type ProposalContent = {
  title: string;
  context: string;
  options: OptionT[];
  recommended_option: string;
  decision_rationale: string;
  components: { name: string; file: string; change: string }[];
  failure_modes: string[];
  security: string[];
  testing_strategy: string[];
  deployment: string[];
  open_risks: string[];
  diagram_mermaid: string;
  traceability: { requirement_key: string; component: string; approach: string; verification?: string }[];
  review_feedback?: string[];
  [k: string]: unknown;
};
type Revision = {
  version: number;
  content_hash: string;
  reason: string;
  author: string | null;
  created_at: string;
  content: ProposalContent;
};
type Detail = {
  proposal: {
    id: string;
    status: string;
    version: number;
    content: ProposalContent;
    content_hash: string;
    generated_by: Record<string, string>;
  };
  revisions: Revision[];
  adr: {
    number: number;
    title: string;
    status: string;
    version: number;
    content: Record<string, unknown>;
  } | null;
  plan: {
    id: string;
    status: string;
    version: number;
    content: {
      approach: string;
      in_scope: string[];
      out_of_scope: { key: string; reason: string }[];
      steps: { order: number; backlog_key: string; description: string; files: string[]; tests: string[] }[];
      quality_gates: string[];
      rollback: string;
    };
  } | null;
};

const useStyles = makeStyles({
  grid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
    gap: tokens.spacingHorizontalM,
  },
  option: {
    backgroundColor: tokens.colorNeutralBackground1,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    padding: tokens.spacingHorizontalM,
    display: "flex",
    flexDirection: "column",
    gap: tokens.spacingVerticalXS,
  },
  chosen: { border: `2px solid ${tokens.colorBrandStroke1}` },
  section: {
    marginTop: tokens.spacingVerticalXL,
    display: "flex",
    flexDirection: "column",
    gap: tokens.spacingVerticalS,
  },
  prose: { maxWidth: "75ch" },
  list: { margin: 0, paddingLeft: tokens.spacingHorizontalL },
  mono: { fontFamily: tokens.fontFamilyMonospace, fontSize: tokens.fontSizeBase200 },
  row: { display: "flex", gap: tokens.spacingHorizontalS, alignItems: "center", flexWrap: "wrap" },
  muted: { color: tokens.colorNeutralForeground3 },
  changed: { backgroundColor: tokens.colorPaletteYellowBackground1 },
});

export function ArchitecturePage() {
  const s = useStyles();
  const { projectId = "" } = useParams();
  const proposals = useQuery({
    queryKey: ["project", projectId, "architecture"],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/architecture", {
          params: { path: { project_id: projectId } },
        }),
      ),
  });
  const latest = proposals.data?.[0];
  const detail = useEvidence<Detail>(projectId, `architecture/${latest?.id}`, !!latest);
  if (proposals.isLoading) return <Loading />;
  if (proposals.error) return <ErrorNotice error={proposals.error} />;
  if (!latest)
    return (
      <>
        <PageHeader title="Architecture" />
        <EmptyState title="No proposal yet">
          The Architect agent proposes a design once a delivery run reaches the design stage.
        </EmptyState>
      </>
    );
  const d = detail.data;
  const c = d?.proposal.content;
  return (
    <>
      <PageHeader
        title={c?.title ?? "Architecture"}
        description={
          d && (
            <span className={s.row}>
              <StatusBadge status={d.proposal.status} /> v{d.proposal.version}
              <span className={s.mono}>#{d.proposal.content_hash.slice(0, 12)}</span>
              <StrategyBadge
                strategy={d.proposal.generated_by.strategy ?? "heuristic"}
                model={d.proposal.generated_by.model_ref}
              />
            </span>
          )
        }
      />
      {detail.isLoading && <Loading />}
      {c && d && (
        <>
          <Text className={s.prose} as="p">
            {c.context}
          </Text>
          <section className={s.section} aria-labelledby="opt-h">
            <Text as="h2" id="opt-h" size={400} weight="semibold">
              Options
            </Text>
            <div className={s.grid}>
              {c.options.map((o) => (
                <div
                  key={o.name}
                  className={o.name === c.recommended_option ? `${s.option} ${s.chosen}` : s.option}
                >
                  <div className={s.row}>
                    <Text weight="semibold">{o.name}</Text>
                    {o.name === c.recommended_option && (
                      <Badge color="brand" size="small">
                        recommended
                      </Badge>
                    )}
                  </div>
                  <Text>{o.summary}</Text>
                  <Text size={200} weight="semibold">
                    For
                  </Text>
                  <ul className={s.list}>
                    {o.pros.map((x) => (
                      <li key={x}>{x}</li>
                    ))}
                  </ul>
                  <Text size={200} weight="semibold">
                    Against
                  </Text>
                  <ul className={s.list}>
                    {o.cons.map((x) => (
                      <li key={x}>{x}</li>
                    ))}
                  </ul>
                  {o.risks.length > 0 && (
                    <>
                      <Text size={200} weight="semibold">
                        Risks
                      </Text>
                      <ul className={s.list}>
                        {o.risks.map((x) => (
                          <li key={x}>{x}</li>
                        ))}
                      </ul>
                    </>
                  )}
                  <Text className={s.mono}>{o.files.join(", ")}</Text>
                </div>
              ))}
            </div>
            <Text className={s.prose}>
              <strong>Rationale.</strong> {c.decision_rationale}
            </Text>
          </section>
          <section className={s.section} aria-labelledby="diag-h">
            <Text as="h2" id="diag-h" size={400} weight="semibold">
              Design
            </Text>
            <Mermaid source={c.diagram_mermaid} label="Architecture diagram" />
            <Table size="small" aria-label="Components">
              <TableHeader>
                <TableRow>
                  <TableHeaderCell>Component</TableHeaderCell>
                  <TableHeaderCell>File</TableHeaderCell>
                  <TableHeaderCell>Change</TableHeaderCell>
                </TableRow>
              </TableHeader>
              <TableBody>
                {c.components.map((x) => (
                  <TableRow key={x.name}>
                    <TableCell>{x.name}</TableCell>
                    <TableCell className={s.mono}>{x.file}</TableCell>
                    <TableCell>{x.change}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </section>
          <section className={s.section} aria-labelledby="trace-h">
            <Text as="h2" id="trace-h" size={400} weight="semibold">
              Requirement traceability
            </Text>
            <Table size="small" aria-label="Traceability">
              <TableHeader>
                <TableRow>
                  <TableHeaderCell>Requirement</TableHeaderCell>
                  <TableHeaderCell>Component</TableHeaderCell>
                  <TableHeaderCell>Verification</TableHeaderCell>
                </TableRow>
              </TableHeader>
              <TableBody>
                {c.traceability.map((t) => (
                  <TableRow key={t.requirement_key}>
                    <TableCell className={s.mono}>{t.requirement_key}</TableCell>
                    <TableCell>{t.component}</TableCell>
                    <TableCell>{t.verification ?? ""}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </section>
          <section className={s.section} aria-labelledby="risk-h">
            <Text as="h2" id="risk-h" size={400} weight="semibold">
              Failure modes, security and deployment
            </Text>
            <ul className={s.list}>
              {[...c.failure_modes, ...c.security, ...c.deployment, ...c.open_risks].map((x, i) => (
                <li key={i}>{x}</li>
              ))}
            </ul>
          </section>
          {d.adr && (
            <section className={s.section} aria-labelledby="adr-h">
              <Text as="h2" id="adr-h" size={400} weight="semibold">
                ADR-{String(d.adr.number).padStart(4, "0")}: {d.adr.title}{" "}
                <StatusBadge status={d.adr.status} />
              </Text>
              <Text className={s.prose}>{String(d.adr.content.decision)}</Text>
              <ul className={s.list}>
                {(d.adr.content.alternatives as { name: string; why_not: string }[]).map((x) => (
                  <li key={x.name}>
                    Not chosen: {x.name} — {x.why_not}
                  </li>
                ))}
              </ul>
            </section>
          )}
          <EditProposal projectId={projectId} detail={d} />
          <Revisions revisions={d.revisions} />
          {d.plan && <PlanPanel projectId={projectId} plan={d.plan} />}
        </>
      )}
    </>
  );
}

function EditProposal({ projectId, detail }: { projectId: string; detail: Detail }) {
  const s = useStyles();
  const { rolesIn } = useAuth();
  const qc = useQueryClient();
  const c = detail.proposal.content;
  const [option, setOption] = useState(c.recommended_option);
  const [rationale, setRationale] = useState(c.decision_rationale);
  const canEdit =
    rolesIn(projectId).some((r) => r === "project_owner" || r === "engineer") &&
    ["in_review", "changes_requested"].includes(detail.proposal.status);
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PATCH("/api/v1/projects/{project_id}/architecture/{proposal_id}", {
          params: { path: { project_id: projectId, proposal_id: detail.proposal.id } },
          body: {
            expected_version: detail.proposal.version,
            content: { ...c, recommended_option: option, decision_rationale: rationale },
          },
        }),
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["project", projectId] }),
  });
  if (!canEdit) return null;
  return (
    <section className={s.section} aria-labelledby="edit-h">
      <Text as="h2" id="edit-h" size={400} weight="semibold">
        Revise the decision
      </Text>
      <MessageBar intent="info">
        <MessageBarBody>
          Saving creates version {detail.proposal.version + 1}. Any pending approval for v
          {detail.proposal.version} becomes stale, and you can’t approve a revision you authored.
        </MessageBarBody>
      </MessageBar>
      <Field label="Recommended option">
        <Dropdown
          value={option}
          selectedOptions={[option]}
          onOptionSelect={(_, d) => setOption(d.optionValue ?? option)}
        >
          {c.options.map((o) => (
            <Option key={o.name} value={o.name}>
              {o.name}
            </Option>
          ))}
        </Dropdown>
      </Field>
      <Field label="Rationale">
        <Textarea value={rationale} onChange={(_, d) => setRationale(d.value)} resize="vertical" rows={4} />
      </Field>
      <div>
        <Button
          disabled={save.isPending || (option === c.recommended_option && rationale === c.decision_rationale)}
          onClick={() => save.mutate()}
        >
          Save revision
        </Button>
      </div>
      {save.error && <ErrorNotice error={save.error} />}
    </section>
  );
}

function Revisions({ revisions }: { revisions: Revision[] }) {
  const s = useStyles();
  const [a, setA] = useState(Math.max(0, revisions.length - 2));
  const [b, setB] = useState(revisions.length - 1);
  if (revisions.length < 2) return null;
  const ra = revisions[a];
  const rb = revisions[b];
  if (!ra || !rb) return null;
  const keys = Array.from(new Set([...Object.keys(ra.content), ...Object.keys(rb.content)])).sort();
  const changed = keys.filter((k) => JSON.stringify(ra.content[k]) !== JSON.stringify(rb.content[k]));
  return (
    <section className={s.section} aria-labelledby="rev-h">
      <Text as="h2" id="rev-h" size={400} weight="semibold">
        Compare versions
      </Text>
      <div className={s.row}>
        {[
          ["From", a, setA],
          ["To", b, setB],
        ].map(([label, value, set]) => (
          <Field key={label as string} label={label as string}>
            <Dropdown
              value={`v${revisions[value as number]?.version}`}
              selectedOptions={[String(value)]}
              onOptionSelect={(_, d) => (set as (n: number) => void)(Number(d.optionValue))}
            >
              {revisions.map((r, i) => (
                <Option key={r.version} value={String(i)} text={`v${r.version}`}>
                  v{r.version} — {r.reason}
                  {r.author ? ` (${r.author})` : ""}
                </Option>
              ))}
            </Dropdown>
          </Field>
        ))}
      </div>
      {changed.length === 0 ? (
        <Text className={s.muted}>No differences.</Text>
      ) : (
        <Table size="small" aria-label="Changed fields">
          <TableHeader>
            <TableRow>
              <TableHeaderCell>Field</TableHeaderCell>
              <TableHeaderCell>v{ra.version}</TableHeaderCell>
              <TableHeaderCell>v{rb.version}</TableHeaderCell>
            </TableRow>
          </TableHeader>
          <TableBody>
            {changed.map((k) => (
              <TableRow key={k} className={s.changed}>
                <TableCell className={s.mono}>{k}</TableCell>
                <TableCell>{summarize(ra.content[k])}</TableCell>
                <TableCell>{summarize(rb.content[k])}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </section>
  );
}

function summarize(v: unknown): string {
  if (typeof v === "string") return v.length > 240 ? v.slice(0, 240) + "…" : v;
  const json = JSON.stringify(v);
  return json && json.length > 240 ? json.slice(0, 240) + "…" : (json ?? "");
}

function PlanPanel({ projectId, plan }: { projectId: string; plan: NonNullable<Detail["plan"]> }) {
  const s = useStyles();
  const { rolesIn } = useAuth();
  const qc = useQueryClient();
  const [remove, setRemove] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  const narrow = useMutation({
    mutationFn: () =>
      unwrap(
        api.PATCH("/api/v1/projects/{project_id}/implementation-plans/{plan_id}/scope", {
          params: { path: { project_id: projectId, plan_id: plan.id } },
          body: { expected_version: plan.version, remove, reason },
        }),
      ),
    onSuccess: () => {
      setRemove([]);
      void qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });
  const canNarrow = rolesIn(projectId).includes("project_owner") && plan.status === "in_review";
  return (
    <section className={s.section} aria-labelledby="plan-h">
      <Text as="h2" id="plan-h" size={400} weight="semibold">
        Implementation plan <StatusBadge status={plan.status} /> v{plan.version}
      </Text>
      <Table size="small" aria-label="Implementation steps">
        <TableHeader>
          <TableRow>
            {canNarrow && <TableHeaderCell>Remove</TableHeaderCell>}
            <TableHeaderCell>#</TableHeaderCell>
            <TableHeaderCell>Item</TableHeaderCell>
            <TableHeaderCell>Description</TableHeaderCell>
            <TableHeaderCell>Tests</TableHeaderCell>
          </TableRow>
        </TableHeader>
        <TableBody>
          {plan.content.steps.map((st) => (
            <TableRow key={st.backlog_key}>
              {canNarrow && (
                <TableCell>
                  <Checkbox
                    aria-label={`Remove ${st.backlog_key} from scope`}
                    checked={remove.includes(st.backlog_key)}
                    onChange={(_, d) =>
                      setRemove((r) =>
                        d.checked ? [...r, st.backlog_key] : r.filter((x) => x !== st.backlog_key),
                      )
                    }
                  />
                </TableCell>
              )}
              <TableCell>{st.order}</TableCell>
              <TableCell className={s.mono}>{st.backlog_key}</TableCell>
              <TableCell>{st.description}</TableCell>
              <TableCell>{st.tests.length}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {plan.content.out_of_scope.length > 0 && (
        <Text>Out of scope: {plan.content.out_of_scope.map((o) => `${o.key} (${o.reason})`).join("; ")}</Text>
      )}
      <Text>Quality gates: {plan.content.quality_gates.join("; ")}.</Text>
      {canNarrow && remove.length > 0 && (
        <div className={s.row}>
          <Field label="Why narrow the scope?" style={{ flexGrow: 1 }}>
            <Textarea value={reason} onChange={(_, d) => setReason(d.value)} />
          </Field>
          <Button disabled={!reason.trim() || narrow.isPending} onClick={() => narrow.mutate()}>
            Remove {remove.length} item(s) from scope
          </Button>
        </div>
      )}
      {narrow.error && <ErrorNotice error={narrow.error} />}
    </section>
  );
}
