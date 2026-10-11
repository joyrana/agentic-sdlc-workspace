import {
  Button,
  Field,
  MessageBar,
  MessageBarBody,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
  Textarea,
  Tooltip,
  makeStyles,
  tokens,
} from "@fluentui/react-components";
import { CheckmarkCircle16Filled, DismissCircle16Filled, Warning16Filled } from "@fluentui/react-icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { tokenStore } from "../api/client";
import { useEvidence } from "../api/hooks";
import { useAuth } from "../auth";
import { EmptyState, ErrorNotice, Loading, PageHeader, StatusBadge } from "../components/common";

type Check = { name: string; status: "pass" | "warn" | "fail"; detail: string };
type Dep = {
  id: string;
  environment: string;
  adapter: string;
  status: string;
  started_at: string | null;
  smoke: {
    checks?: { name: string; ok: boolean; output?: string; detail?: string }[];
    integrity?: { ok: boolean; detail: string };
  };
  health: {
    checks?: { name: string; ok: boolean; output?: string }[];
    verdict?: { recommendation: string; failed_checks: string[] };
  };
  log: string;
  rollback: { performed?: boolean; reason?: string };
};
type ReleaseT = {
  id: string;
  version: string;
  commit: string;
  artifact_sha256: string;
  status: string;
  readiness: { eligible: boolean; blockers: string[]; warnings: string[]; checks: Check[] };
  deployments: Dep[];
  production_deploy_enabled: boolean;
};

const useStyles = makeStyles({
  card: {
    backgroundColor: tokens.colorNeutralBackground1,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    padding: tokens.spacingHorizontalL,
    marginBottom: tokens.spacingVerticalXL,
    display: "flex",
    flexDirection: "column",
    gap: tokens.spacingVerticalM,
  },
  row: { display: "flex", gap: tokens.spacingHorizontalS, alignItems: "center", flexWrap: "wrap" },
  mono: { fontFamily: tokens.fontFamilyMonospace, fontSize: tokens.fontSizeBase200 },
  muted: { color: tokens.colorNeutralForeground3 },
  evidence: {
    display: "flex",
    flexDirection: "column",
    gap: tokens.spacingVerticalXXS,
    marginTop: tokens.spacingVerticalXS,
  },
  line: { display: "flex", gap: tokens.spacingHorizontalXS, alignItems: "center" },
  pass: { color: tokens.colorPaletteGreenForeground1 },
  warn: { color: tokens.colorPaletteMarigoldForeground1 },
  fail: { color: tokens.colorPaletteRedForeground1 },
});

export function ReleasesPage() {
  const { projectId = "" } = useParams();
  const releases = useEvidence<ReleaseT[]>(projectId, "releases");
  if (releases.isLoading) return <Loading />;
  return (
    <>
      <PageHeader
        title="Releases"
        description="Eligibility is computed from recorded evidence. Deployment requires a release approver and is re-verified by the deployment adapter."
      />
      {releases.error && <ErrorNotice error={releases.error} />}
      {releases.data?.length === 0 && <EmptyState title="No release candidates yet" />}
      {releases.data?.map((r) => (
        <ReleaseCard key={r.id} r={r} projectId={projectId} />
      ))}
    </>
  );
}

function ReleaseCard({ r, projectId }: { r: ReleaseT; projectId: string }) {
  const s = useStyles();
  const { rolesIn } = useAuth();
  const qc = useQueryClient();
  const [reason, setReason] = useState("");
  const isApprover = rolesIn(projectId).includes("release_approver");
  const active = r.deployments.find((d) => d.environment === "staging" && d.status === "succeeded");
  const rollback = useMutation({
    mutationFn: async () => {
      const token = tokenStore.get();
      const res = await fetch(`/api/v1/projects/${projectId}/releases/${r.id}/rollback`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ reason }),
      });
      if (!res.ok) throw new Error((await res.json()).error?.message ?? "Rollback failed");
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["project", projectId] }),
  });
  const icon = (st: Check["status"]) =>
    st === "pass" ? (
      <CheckmarkCircle16Filled className={s.pass} aria-label="pass" />
    ) : st === "warn" ? (
      <Warning16Filled className={s.warn} aria-label="warning" />
    ) : (
      <DismissCircle16Filled className={s.fail} aria-label="fail" />
    );
  return (
    <article className={s.card} aria-label={`Release ${r.version}`}>
      <div className={s.row}>
        <Text weight="semibold" size={500}>
          {r.version}
        </Text>
        <StatusBadge status={r.status} />
        <Tooltip content={`sha256 ${r.artifact_sha256}`} relationship="description">
          <span className={s.mono}>artifact {r.artifact_sha256.slice(0, 12)}</span>
        </Tooltip>
        <span className={s.mono}>commit {r.commit.slice(0, 12)}</span>
      </div>
      {!r.readiness.eligible && (
        <MessageBar intent="error">
          <MessageBarBody>Blocked: {r.readiness.blockers.join("; ")}</MessageBarBody>
        </MessageBar>
      )}
      {r.status === "candidate" && (
        <MessageBar intent="warning">
          <MessageBarBody>
            Waiting for a release approver. <Link to="../approvals">Open approvals</Link>
          </MessageBarBody>
        </MessageBar>
      )}
      <Table size="small" aria-label="Readiness checks">
        <TableHeader>
          <TableRow>
            <TableHeaderCell>Check</TableHeaderCell>
            <TableHeaderCell>Result</TableHeaderCell>
          </TableRow>
        </TableHeader>
        <TableBody>
          {r.readiness.checks.map((c) => (
            <TableRow key={c.name}>
              <TableCell>
                <span className={s.row}>
                  {icon(c.status)} {c.name}
                </span>
              </TableCell>
              <TableCell>{c.detail}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {r.deployments.map((d) => (
        <section key={d.id} aria-label={`${d.environment} deployment`}>
          <div className={s.row}>
            <Text weight="semibold">
              {d.environment} via {d.adapter.replace(/_/g, " ")}
            </Text>
            <StatusBadge status={d.status} />
            {d.started_at && <span className={s.muted}>{d.started_at.replace("T", " ").slice(0, 19)}</span>}
          </div>
          <div className={s.evidence}>
            {d.smoke.integrity && (
              <Text className={s.line}>
                {icon(d.smoke.integrity.ok ? "pass" : "fail")} {d.smoke.integrity.detail}
              </Text>
            )}
            {[
              ...(d.smoke.checks ?? []).map((c) => ({ ...c, kind: "smoke" })),
              ...(d.health.checks ?? []).map((c) => ({ ...c, kind: "health" })),
            ].map((c) => (
              <Text key={`${c.kind}-${c.name}`} className={s.line}>
                {icon(c.ok ? "pass" : "fail")} {c.kind}: {c.name}
                <span className={s.mono}>{(c.output ?? "").trim().slice(-80)}</span>
              </Text>
            ))}
            {d.rollback.performed && <Text>Rolled back: {d.rollback.reason}</Text>}
          </div>
        </section>
      ))}
      {isApprover && active && (
        <div className={s.row}>
          <Field label="Rollback reason" style={{ flexGrow: 1 }}>
            <Textarea value={reason} onChange={(_, v) => setReason(v.value)} />
          </Field>
          <Button disabled={!reason.trim() || rollback.isPending} onClick={() => rollback.mutate()}>
            Roll back staging
          </Button>
        </div>
      )}
      {rollback.error && <ErrorNotice error={rollback.error} />}
      {r.status === "deployed_staging" && (
        <Text className={s.muted}>
          {r.production_deploy_enabled
            ? "Production promotion requires a separate release-approver decision."
            : "Production deployment is disabled by policy for this workspace."}
        </Text>
      )}
    </article>
  );
}
