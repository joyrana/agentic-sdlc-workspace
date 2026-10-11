import {
  Badge,
  Button,
  MessageBar,
  MessageBarBody,
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
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
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

type Item = Schemas["BacklogItemOut"];

const useStyles = makeStyles({
  summary: {
    display: "flex",
    flexWrap: "wrap",
    gap: tokens.spacingHorizontalXXL,
    marginBottom: tokens.spacingVerticalL,
    padding: `${tokens.spacingVerticalM} ${tokens.spacingHorizontalL}`,
    backgroundColor: tokens.colorNeutralBackground1,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
  },
  stat: { display: "flex", flexDirection: "column" },
  statValue: { fontSize: tokens.fontSizeBase500, fontWeight: tokens.fontWeightSemibold },
  key: { fontFamily: tokens.fontFamilyMonospace, whiteSpace: "nowrap" },
  title: { display: "flex", alignItems: "center", gap: tokens.spacingHorizontalS },
  indent1: { paddingLeft: tokens.spacingHorizontalXL },
  indent2: { paddingLeft: `calc(${tokens.spacingHorizontalXL} * 2)` },
  num: { textAlign: "right", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" },
  muted: { color: tokens.colorNeutralForeground3, fontSize: tokens.fontSizeBase200 },
  board: {
    display: "grid",
    gridTemplateColumns: "repeat(4, minmax(200px, 1fr))",
    gap: tokens.spacingHorizontalM,
    overflowX: "auto",
  },
  column: {
    backgroundColor: tokens.colorNeutralBackground3,
    borderRadius: tokens.borderRadiusLarge,
    padding: tokens.spacingHorizontalS,
    display: "flex",
    flexDirection: "column",
    gap: tokens.spacingVerticalS,
    minHeight: "200px",
  },
  card: {
    backgroundColor: tokens.colorNeutralBackground1,
    borderRadius: tokens.borderRadiusMedium,
    padding: tokens.spacingHorizontalM,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    display: "flex",
    flexDirection: "column",
    gap: tokens.spacingVerticalXS,
  },
  path: { display: "flex", gap: tokens.spacingHorizontalXS, alignItems: "center", flexWrap: "wrap" },
});

const KIND_COLOR: Record<string, "brand" | "informative" | "success" | "danger" | "warning"> = {
  epic: "brand",
  feature: "informative",
  story: "success",
  bug: "danger",
  enhancement: "warning",
  task: "informative",
};

function fmtDuration(s: number): string {
  return s < 90 ? `${s}s` : s < 5400 ? `${Math.round(s / 60)}m` : `${(s / 3600).toFixed(1)}h`;
}

export function BacklogPage() {
  const s = useStyles();
  const { projectId = "" } = useParams();
  const { rolesIn } = useAuth();
  const canEdit = rolesIn(projectId).some((r) => r === "project_owner" || r === "engineer");
  const qc = useQueryClient();
  const [view, setView] = useState<"tree" | "board">("tree");
  const backlog = useQuery({
    queryKey: ["project", projectId, "backlog"],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/backlog", { params: { path: { project_id: projectId } } }),
      ),
  });
  const generate = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/projects/{project_id}/backlog/generate", {
          params: { path: { project_id: projectId } },
        }),
      ),
    onSuccess: () =>
      setTimeout(() => void qc.invalidateQueries({ queryKey: ["project", projectId, "backlog"] }), 1500),
  });

  const ordered = useMemo(() => {
    const items = backlog.data?.items ?? [];
    const children = new Map<string | null, Item[]>();
    for (const i of items) {
      const k = i.parent_id ?? null;
      children.set(k, [...(children.get(k) ?? []), i]);
    }
    const out: { item: Item; depth: number }[] = [];
    const walk = (parent: string | null, depth: number) => {
      for (const i of children.get(parent) ?? []) {
        out.push({ item: i, depth });
        walk(i.id, depth + 1);
      }
    };
    walk(null, 0);
    return out;
  }, [backlog.data]);

  const analysis = backlog.data?.analysis as
    | {
        valid: boolean;
        cycle: string[] | null;
        critical_path?: string[];
        milestones?: string[][];
        totals?: Record<string, number>;
      }
    | undefined;
  const stories = ordered.filter((o) => o.item.kind !== "epic" && o.item.kind !== "feature");
  const route = stories[0]?.item.model_route as
    | {
        coding_agent?: { kind: string; reason?: string; model_ref?: string };
        generated_by?: { strategy: string; model_ref?: string };
      }
    | undefined;

  return (
    <>
      <PageHeader
        title="Backlog"
        description="Story points measure relative complexity. Token, cost and time estimates are separate, and marked as estimates until measured."
        actions={
          canEdit && (
            <Button appearance="primary" onClick={() => generate.mutate()} disabled={generate.isPending}>
              {backlog.data?.items.length ? "Plan new requirements" : "Generate backlog"}
            </Button>
          )
        }
      />
      {generate.error && <ErrorNotice error={generate.error} />}
      {backlog.isLoading && <Loading />}
      {backlog.error && <ErrorNotice error={backlog.error} />}
      {backlog.data?.items.length === 0 && (
        <EmptyState title="No backlog yet">
          Generate a backlog from the extracted requirements, or start a delivery run, which plans
          automatically.
        </EmptyState>
      )}
      {analysis && !analysis.valid && (
        <MessageBar intent="error">
          <MessageBarBody>Dependency cycle: {analysis.cycle?.join(" → ")}</MessageBarBody>
        </MessageBar>
      )}
      {analysis?.totals && (
        <div className={s.summary}>
          <div className={s.stat}>
            <Text className={s.muted}>Story points</Text>
            <span className={s.statValue}>{analysis.totals.story_points}</span>
          </div>
          <div className={s.stat}>
            <Text className={s.muted}>Tokens (estimated)</Text>
            <span className={s.statValue}>{Number(analysis.totals.tokens_estimated).toLocaleString()}</span>
          </div>
          <div className={s.stat}>
            <Text className={s.muted}>Tokens (measured)</Text>
            <span className={s.statValue}>{Number(analysis.totals.tokens_actual).toLocaleString()}</span>
          </div>
          {analysis.critical_path && (
            <div className={s.stat}>
              <Text className={s.muted}>Critical path</Text>
              <span className={s.path}>
                {analysis.critical_path.map((k, i) => (
                  <span key={k}>
                    <span className={s.key}>{k}</span>
                    {i < analysis.critical_path!.length - 1 ? " → " : ""}
                  </span>
                ))}
              </span>
            </div>
          )}
          {route?.coding_agent && (
            <div className={s.stat}>
              <Text className={s.muted}>Coding agent</Text>
              <Tooltip
                content={route.coding_agent.reason ?? route.coding_agent.model_ref ?? ""}
                relationship="description"
              >
                <span>
                  <StrategyBadge
                    strategy={route.coding_agent.kind === "none" ? "unavailable" : route.coding_agent.kind}
                  />
                </span>
              </Tooltip>
            </div>
          )}
          {route?.generated_by && (
            <div className={s.stat}>
              <Text className={s.muted}>Planned by</Text>
              <span>
                <StrategyBadge strategy={route.generated_by.strategy} model={route.generated_by.model_ref} />
              </span>
            </div>
          )}
        </div>
      )}
      {backlog.data && backlog.data.items.length > 0 && (
        <>
          <TabList selectedValue={view} onTabSelect={(_, d) => setView(d.value as "tree" | "board")}>
            <Tab value="tree">Hierarchy</Tab>
            <Tab value="board">Board</Tab>
          </TabList>
          {view === "tree" ? (
            <Table aria-label="Backlog hierarchy" size="small">
              <TableHeader>
                <TableRow>
                  <TableHeaderCell>Item</TableHeaderCell>
                  <TableHeaderCell>Requirements</TableHeaderCell>
                  <TableHeaderCell>Depends on</TableHeaderCell>
                  <TableHeaderCell>Status</TableHeaderCell>
                  <TableHeaderCell className={s.num}>Points</TableHeaderCell>
                  <TableHeaderCell>Uncertainty</TableHeaderCell>
                  <TableHeaderCell className={s.num}>Tokens (est.)</TableHeaderCell>
                  <TableHeaderCell className={s.num}>Time (est.)</TableHeaderCell>
                  <TableHeaderCell className={s.num}>Measured</TableHeaderCell>
                </TableRow>
              </TableHeader>
              <TableBody>
                {ordered.map(({ item, depth }) => {
                  const est = item.estimate as {
                    tokens_in?: number;
                    tokens_out?: number;
                    duration_s?: number;
                  };
                  const act = item.actuals as { tokens_total?: number; tokens_estimated?: boolean };
                  return (
                    <TableRow key={item.id}>
                      <TableCell>
                        <div className={depth === 1 ? s.indent1 : depth >= 2 ? s.indent2 : undefined}>
                          <span className={s.title}>
                            <Badge
                              appearance="tint"
                              color={KIND_COLOR[item.kind] ?? "informative"}
                              size="small"
                            >
                              {item.kind}
                            </Badge>
                            <span className={s.key}>{item.key}</span>
                            <Text weight={depth === 0 ? "semibold" : "regular"}>{item.title}</Text>
                          </span>
                        </div>
                      </TableCell>
                      <TableCell>
                        <span className={s.key}>{item.requirement_keys?.join(", ")}</span>
                      </TableCell>
                      <TableCell>
                        {item.depends_on?.map((d) => (
                          <Tooltip
                            key={String(d.id)}
                            content={String(d.reason ?? "")}
                            relationship="description"
                          >
                            <span className={s.key}>{String(d.key)} </span>
                          </Tooltip>
                        ))}
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={item.status} />
                      </TableCell>
                      <TableCell className={s.num}>{item.story_points ?? ""}</TableCell>
                      <TableCell>{item.uncertainty ?? ""}</TableCell>
                      <TableCell className={s.num}>
                        {est.tokens_in != null
                          ? ((est.tokens_in ?? 0) + (est.tokens_out ?? 0)).toLocaleString()
                          : ""}
                      </TableCell>
                      <TableCell className={s.num}>
                        {est.duration_s != null ? fmtDuration(est.duration_s) : ""}
                      </TableCell>
                      <TableCell className={s.num}>
                        {act.tokens_total
                          ? `${act.tokens_total.toLocaleString()}${act.tokens_estimated ? " (est.)" : ""}`
                          : ""}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          ) : (
            <div className={s.board} role="region" aria-label="Backlog board">
              {(["todo", "in_progress", "blocked", "done"] as const).map((status) => (
                <section key={status} className={s.column} aria-label={status.replace("_", " ")}>
                  <Text weight="semibold">{status.replace("_", " ")}</Text>
                  {stories
                    .filter((o) => o.item.status === status)
                    .map(({ item }) => (
                      <div key={item.id} className={s.card}>
                        <span className={s.key}>{item.key}</span>
                        <Text>{item.title}</Text>
                        <Text className={s.muted}>
                          {item.story_points ?? "?"} pts · {item.uncertainty ?? "?"} uncertainty
                        </Text>
                      </div>
                    ))}
                </section>
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}
