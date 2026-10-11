import {
  Badge,
  Button,
  Field,
  Tab,
  TabList,
  Text,
  Textarea,
  Tooltip,
  makeStyles,
  tokens,
} from "@fluentui/react-components";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { useApprovals, type Approval } from "../api/hooks";
import { EmptyState, ErrorNotice, Loading, PageHeader, StatusBadge } from "../components/common";

const useStyles = makeStyles({
  list: { display: "flex", flexDirection: "column", gap: tokens.spacingVerticalL, maxWidth: "860px" },
  card: {
    backgroundColor: tokens.colorNeutralBackground1,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    padding: tokens.spacingHorizontalL,
    display: "flex",
    flexDirection: "column",
    gap: tokens.spacingVerticalS,
  },
  waiting: { borderLeft: `4px solid ${tokens.colorPaletteMarigoldBorderActive}` },
  row: { display: "flex", gap: tokens.spacingHorizontalS, alignItems: "center", flexWrap: "wrap" },
  mono: { fontFamily: tokens.fontFamilyMonospace, fontSize: tokens.fontSizeBase200 },
  muted: { color: tokens.colorNeutralForeground3 },
});

const GATE: Record<string, { title: string; link: string }> = {
  architecture: { title: "Architecture proposal", link: "../architecture" },
  implementation_scope: { title: "Implementation scope", link: "../architecture" },
  release_staging: { title: "Release to staging", link: "../releases" },
  release_production: { title: "Release to production", link: "../releases" },
};

export function ApprovalsPage() {
  const s = useStyles();
  const { projectId = "" } = useParams();
  const [tab, setTab] = useState<"pending" | "all">("pending");
  const approvals = useApprovals(projectId, tab);
  return (
    <>
      <PageHeader
        title="Approvals"
        description="Agents cannot approve their own work. Each decision is bound to the exact version you review; if the artifact changes, the request becomes stale."
      />
      <TabList
        selectedValue={tab}
        onTabSelect={(_, d) => setTab(d.value as "pending" | "all")}
        style={{ marginBottom: 12 }}
      >
        <Tab value="pending">Waiting for a decision</Tab>
        <Tab value="all">History</Tab>
      </TabList>
      {approvals.isLoading && <Loading />}
      {approvals.error && <ErrorNotice error={approvals.error} />}
      {approvals.data?.length === 0 && (
        <EmptyState title={tab === "pending" ? "Nothing is waiting for you" : "No approvals yet"} />
      )}
      <div className={s.list}>
        {approvals.data?.map((a) => (
          <ApprovalCard key={a.id} a={a} projectId={projectId} />
        ))}
      </div>
    </>
  );
}

function ApprovalCard({ a, projectId }: { a: Approval; projectId: string }) {
  const s = useStyles();
  const qc = useQueryClient();
  const [comment, setComment] = useState("");
  const decide = useMutation({
    mutationFn: (decision: "approved" | "rejected" | "changes_requested") =>
      unwrap(
        api.POST("/api/v1/projects/{project_id}/approvals/{request_id}/decision", {
          params: { path: { project_id: projectId, request_id: a.id } },
          body: { decision, comment },
        }),
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["project", projectId] }),
  });
  const gate = GATE[a.gate] ?? { title: a.gate, link: "." };
  const d = a.decision as {
    decision: string;
    comment: string;
    approver: string;
    role: string;
    at: string;
  } | null;
  return (
    <article className={a.status === "pending" ? `${s.card} ${s.waiting}` : s.card} aria-label={gate.title}>
      <div className={s.row}>
        <Text weight="semibold" size={400}>
          {gate.title}
        </Text>
        <StatusBadge status={a.status} />
        <Badge appearance="outline" size="small">
          v{a.artifact_version}
        </Badge>
        <Tooltip content={`Content hash ${a.content_hash}`} relationship="description">
          <span className={s.mono}>#{a.content_hash.slice(0, 12)}</span>
        </Tooltip>
      </div>
      <Text>{a.requested_action}</Text>
      <Text className={s.muted}>
        Decided by: {a.required_roles.map((r) => r.replace(/_/g, " ")).join(" or ")} ·{" "}
        <Link to={gate.link}>Review the artifact</Link>
      </Text>
      {d && (
        <Text>
          <strong>{d.decision.replace(/_/g, " ")}</strong> by {d.approver} ({d.role.replace(/_/g, " ")})
          {d.comment ? `: “${d.comment}”` : ""}
        </Text>
      )}
      {a.status === "pending" &&
        (a.can_decide ? (
          <>
            <Field label="Comment" hint="Required when rejecting or requesting changes">
              <Textarea value={comment} onChange={(_, v) => setComment(v.value)} resize="vertical" />
            </Field>
            <div className={s.row}>
              <Button
                appearance="primary"
                disabled={decide.isPending}
                onClick={() => decide.mutate("approved")}
              >
                Approve v{a.artifact_version}
              </Button>
              {a.gate === "architecture" && (
                <Button
                  disabled={decide.isPending || !comment.trim()}
                  onClick={() => decide.mutate("changes_requested")}
                >
                  Request changes
                </Button>
              )}
              <Button
                disabled={decide.isPending || !comment.trim()}
                onClick={() => decide.mutate("rejected")}
              >
                Reject
              </Button>
            </div>
            {decide.error && <ErrorNotice error={decide.error} />}
          </>
        ) : (
          <Text className={s.muted}>You can’t decide this: {a.cannot_decide_reason}.</Text>
        ))}
    </article>
  );
}
