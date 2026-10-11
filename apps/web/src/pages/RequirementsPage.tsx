import {
  Badge,
  Button,
  Field,
  MessageBar,
  MessageBarBody,
  Tab,
  TabList,
  Text,
  Textarea,
  Tooltip,
  makeStyles,
  tokens,
} from "@fluentui/react-components";
import { Checkmark16Regular, Edit16Regular } from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api/client";
import { useAuth } from "../auth";
import {
  EmptyState,
  ErrorNotice,
  Loading,
  PageHeader,
  StatusBadge,
  StrategyBadge,
} from "../components/common";

type Requirement = Schemas["RequirementOut"];
type Criterion = Schemas["CriterionOut"];
type Question = Schemas["QuestionOut"];

const useStyles = makeStyles({
  list: { display: "flex", flexDirection: "column", gap: tokens.spacingVerticalL },
  req: {
    backgroundColor: tokens.colorNeutralBackground1,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    padding: `${tokens.spacingVerticalM} ${tokens.spacingHorizontalL}`,
    display: "grid",
    gridTemplateColumns: "minmax(0, 1fr) minmax(0, 0.8fr)",
    gap: tokens.spacingHorizontalXL,
    "@media (max-width: 960px)": { gridTemplateColumns: "1fr" },
  },
  head: { display: "flex", alignItems: "baseline", gap: tokens.spacingHorizontalS, flexWrap: "wrap" },
  key: { fontFamily: tokens.fontFamilyMonospace, fontWeight: tokens.fontWeightSemibold },
  statement: { marginTop: tokens.spacingVerticalXS, marginBottom: tokens.spacingVerticalS, maxWidth: "70ch" },
  source: {
    borderLeft: `3px solid ${tokens.colorBrandStroke2}`,
    paddingLeft: tokens.spacingHorizontalM,
    display: "flex",
    flexDirection: "column",
    gap: tokens.spacingVerticalXXS,
  },
  quote: { fontStyle: "italic", color: tokens.colorNeutralForeground2 },
  meta: { color: tokens.colorNeutralForeground3, fontSize: tokens.fontSizeBase200 },
  criteria: {
    listStyle: "none",
    padding: 0,
    margin: 0,
    display: "flex",
    flexDirection: "column",
    gap: tokens.spacingVerticalS,
  },
  crit: {
    display: "grid",
    gridTemplateColumns: "auto 1fr auto",
    gap: tokens.spacingHorizontalS,
    alignItems: "start",
  },
  critKey: {
    fontFamily: tokens.fontFamilyMonospace,
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase200,
  },
  question: {
    backgroundColor: tokens.colorNeutralBackground1,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    padding: tokens.spacingHorizontalL,
    display: "flex",
    flexDirection: "column",
    gap: tokens.spacingVerticalS,
    maxWidth: "80ch",
  },
  blocking: { borderLeft: `4px solid ${tokens.colorPaletteRedBorderActive}` },
  row: { display: "flex", gap: tokens.spacingHorizontalS, alignItems: "center", flexWrap: "wrap" },
});

export function RequirementsPage() {
  const s = useStyles();
  const { projectId = "" } = useParams();
  const [tab, setTab] = useState<"requirements" | "questions" | "rules">("requirements");
  const path = { params: { path: { project_id: projectId } } };
  const reqs = useQuery({
    queryKey: ["project", projectId, "requirements"],
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/requirements", path)),
  });
  const questions = useQuery({
    queryKey: ["project", projectId, "questions"],
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/questions", path)),
  });
  const rules = useQuery({
    queryKey: ["project", projectId, "rules"],
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/business-rules", path)),
  });
  const blocking = questions.data?.filter((q) => q.blocking).length ?? 0;

  return (
    <>
      <PageHeader
        title="Requirements"
        description="Review what the Business Analyst agent extracted. Edit acceptance criteria until they are testable."
      />
      <TabList
        selectedValue={tab}
        onTabSelect={(_, d) => setTab(d.value as typeof tab)}
        style={{ marginBottom: 16 }}
      >
        <Tab value="requirements">Requirements {reqs.data ? `(${reqs.data.length})` : ""}</Tab>
        <Tab value="questions">
          Open questions {questions.data ? `(${questions.data.length})` : ""}{" "}
          {blocking > 0 && (
            <Badge color="danger" size="small">
              {blocking} blocking
            </Badge>
          )}
        </Tab>
        <Tab value="rules">Business rules {rules.data ? `(${rules.data.length})` : ""}</Tab>
      </TabList>

      {tab === "requirements" && (
        <>
          {reqs.isLoading && <Loading />}
          {reqs.error && <ErrorNotice error={reqs.error} />}
          {reqs.data?.length === 0 && (
            <EmptyState title="No requirements yet">Upload a source document first.</EmptyState>
          )}
          <div className={s.list}>
            {reqs.data?.map((r) => (
              <RequirementCard key={r.id} req={r} projectId={projectId} />
            ))}
          </div>
        </>
      )}
      {tab === "questions" && (
        <div className={s.list}>
          {questions.data?.length === 0 && <EmptyState title="No open questions" />}
          {questions.data?.map((q) => (
            <QuestionCard key={q.id} q={q} projectId={projectId} />
          ))}
        </div>
      )}
      {tab === "rules" && (
        <div className={s.list}>
          {rules.data?.length === 0 && <EmptyState title="No business rules extracted" />}
          {rules.data?.map((r) => (
            <div key={r.id} className={s.question}>
              <div className={s.head}>
                <span className={s.key}>{r.key}</span>
                <StrategyBadge strategy={r.extraction_method} />
              </div>
              <Text>{r.statement}</Text>
              <SourceRef locator={r.locator} quote={r.source_text} />
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function SourceRef({ locator, quote }: { locator: Record<string, unknown>; quote: string }) {
  const s = useStyles();
  const path = (locator.section_path as string[] | undefined) ?? [];
  return (
    <div className={s.source}>
      <Text className={s.meta}>
        {String(locator.document_title ?? "Source")} v{String(locator.document_version ?? "?")} ·{" "}
        {locator.speaker
          ? `${String(locator.speaker)} ${locator.timestamp ? `at ${String(locator.timestamp)}` : ""} · `
          : ""}
        {path.length ? path.join(" › ") : "document root"} · {String(locator.block_id ?? "")}
      </Text>
      {quote && <Text className={s.quote}>“{quote}”</Text>}
    </div>
  );
}

function RequirementCard({ req, projectId }: { req: Requirement; projectId: string }) {
  const s = useStyles();
  const { rolesIn } = useAuth();
  const canEdit = rolesIn(projectId).some((r) => ["project_owner", "engineer", "reviewer"].includes(r));
  const qc = useQueryClient();
  const [adding, setAdding] = useState("");
  const add = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/projects/{project_id}/requirements/{requirement_id}/criteria", {
          params: { path: { project_id: projectId, requirement_id: req.id } },
          body: { text: adding },
        }),
      ),
    onSuccess: () => {
      setAdding("");
      void qc.invalidateQueries({ queryKey: ["project", projectId, "requirements"] });
    },
  });
  return (
    <article className={s.req} aria-labelledby={`req-${req.id}`}>
      <div>
        <div className={s.head}>
          <span className={s.key} id={`req-${req.id}`}>
            {req.key}
          </span>
          <Text weight="semibold">{req.title}</Text>
          <Badge appearance="outline" size="small">
            {req.priority}
          </Badge>
          <Badge appearance="outline" size="small">
            {req.kind.replace("_", "-")}
          </Badge>
          {req.validation_status === "changed" && (
            <Tooltip
              content="The source document changed this requirement. Linked backlog items may need re-planning."
              relationship="description"
            >
              <Badge color="warning" size="small">
                changed in source
              </Badge>
            </Tooltip>
          )}
          {(req.open_questions ?? 0) > 0 && (
            <Badge color="warning" appearance="tint" size="small">
              {req.open_questions} open question{req.open_questions === 1 ? "" : "s"}
            </Badge>
          )}
        </div>
        <Text as="p" className={s.statement}>
          {req.statement}
        </Text>
        <SourceRef locator={req.locator} quote={req.source_text} />
        <Text className={s.meta}>
          v{req.version} ·{" "}
          <StrategyBadge
            strategy={req.origin === "human" ? "human" : req.extraction_method}
            model={req.model_ref}
          />{" "}
          {req.origin === "inferred" && "inferred, not stated in the source"}
        </Text>
      </div>
      <div>
        <Text weight="semibold" size={300}>
          Acceptance criteria
        </Text>
        <ul className={s.criteria}>
          {req.criteria.map((c) => (
            <CriterionRow key={c.id} c={c} projectId={projectId} canEdit={canEdit} />
          ))}
        </ul>
        {canEdit && (
          <form
            className={s.row}
            style={{ marginTop: 8 }}
            onSubmit={(e) => {
              e.preventDefault();
              if (adding.trim()) add.mutate();
            }}
          >
            <Textarea
              value={adding}
              onChange={(_, d) => setAdding(d.value)}
              placeholder="Add a criterion: Given …, when …, then …"
              resize="vertical"
              style={{ flexGrow: 1 }}
              aria-label={`New acceptance criterion for ${req.key}`}
            />
            <Button type="submit" size="small" disabled={!adding.trim() || add.isPending}>
              Add criterion
            </Button>
          </form>
        )}
        {add.error && <ErrorNotice error={add.error} />}
      </div>
    </article>
  );
}

function CriterionRow({ c, projectId, canEdit }: { c: Criterion; projectId: string; canEdit: boolean }) {
  const s = useStyles();
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(c.text);
  const save = useMutation({
    mutationFn: (body: { text?: string; status?: "draft" | "approved" | "rejected" }) =>
      unwrap(
        api.PATCH("/api/v1/projects/{project_id}/acceptance-criteria/{criterion_id}", {
          params: { path: { project_id: projectId, criterion_id: c.id } },
          body: { expected_version: c.version, ...body },
        }),
      ),
    onSuccess: () => {
      setEditing(false);
      void qc.invalidateQueries({ queryKey: ["project", projectId, "requirements"] });
    },
  });
  return (
    <li className={s.crit}>
      <span className={s.critKey}>{c.key.split("-").pop()}</span>
      <div>
        {editing ? (
          <Field
            validationMessage={save.error ? (save.error as Error).message : undefined}
            validationState={save.error ? "error" : "none"}
          >
            <Textarea
              value={text}
              onChange={(_, d) => setText(d.value)}
              resize="vertical"
              aria-label={`Edit ${c.key}`}
            />
          </Field>
        ) : (
          <Text>{c.text}</Text>
        )}
        <div className={s.row}>
          <StrategyBadge
            strategy={c.origin === "human" ? "human" : c.extraction_method}
            model={c.model_ref}
          />
          {c.origin === "inferred" && <Text className={s.meta}>inferred — review before relying on it</Text>}
          {c.status !== "draft" && <StatusBadge status={c.status} />}
          <Text className={s.meta}>v{c.version}</Text>
        </div>
      </div>
      {canEdit && (
        <div className={s.row}>
          {editing ? (
            <>
              <Button
                size="small"
                appearance="primary"
                onClick={() => save.mutate({ text })}
                disabled={save.isPending}
              >
                Save
              </Button>
              <Button
                size="small"
                onClick={() => {
                  setEditing(false);
                  setText(c.text);
                  save.reset();
                }}
              >
                Cancel
              </Button>
            </>
          ) : (
            <>
              <Tooltip content="Edit criterion" relationship="label">
                <Button
                  size="small"
                  appearance="subtle"
                  icon={<Edit16Regular />}
                  onClick={() => setEditing(true)}
                />
              </Tooltip>
              {c.status !== "approved" && (
                <Tooltip content="Mark as reviewed and approved" relationship="label">
                  <Button
                    size="small"
                    appearance="subtle"
                    icon={<Checkmark16Regular />}
                    onClick={() => save.mutate({ status: "approved" })}
                  />
                </Tooltip>
              )}
            </>
          )}
        </div>
      )}
    </li>
  );
}

function QuestionCard({ q, projectId }: { q: Question; projectId: string }) {
  const s = useStyles();
  const { rolesIn } = useAuth();
  const canResolve = rolesIn(projectId).some((r) => ["project_owner", "engineer", "reviewer"].includes(r));
  const qc = useQueryClient();
  const [resolution, setResolution] = useState("");
  const resolve = useMutation({
    mutationFn: (status: "resolved" | "dismissed") =>
      unwrap(
        api.POST("/api/v1/projects/{project_id}/questions/{question_id}/resolve", {
          params: { path: { project_id: projectId, question_id: q.id } },
          body: { resolution, status },
        }),
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["project", projectId] }),
  });
  const label: Record<string, string> = {
    ambiguity: "Ambiguous",
    missing_detail: "Missing detail",
    contradiction: "Contradiction",
    assumption: "Assumption",
    injection_warning: "Instructions in source",
  };
  return (
    <section className={q.blocking ? `${s.question} ${s.blocking}` : s.question}>
      <div className={s.row}>
        <Badge
          appearance="tint"
          color={q.kind === "contradiction" || q.kind === "injection_warning" ? "danger" : "warning"}
        >
          {label[q.kind] ?? q.kind}
        </Badge>
        {q.blocking && <Text weight="semibold">Blocks delivery until resolved</Text>}
      </div>
      <Text>{q.question}</Text>
      {q.source_text && <SourceRef locator={q.locator} quote={q.source_text} />}
      {q.kind === "injection_warning" && (
        <MessageBar intent="warning">
          <MessageBarBody>
            This passage was excluded from extraction. Documents can never change agent permissions or
            approvals.
          </MessageBarBody>
        </MessageBar>
      )}
      {canResolve && (
        <>
          <Field label="Resolution">
            <Textarea value={resolution} onChange={(_, d) => setResolution(d.value)} resize="vertical" />
          </Field>
          <div className={s.row}>
            <Button
              appearance="primary"
              disabled={!resolution.trim() || resolve.isPending}
              onClick={() => resolve.mutate("resolved")}
            >
              Resolve
            </Button>
            <Button
              disabled={!resolution.trim() || resolve.isPending}
              onClick={() => resolve.mutate("dismissed")}
            >
              Dismiss
            </Button>
          </div>
          {resolve.error && <ErrorNotice error={resolve.error} />}
        </>
      )}
    </section>
  );
}
