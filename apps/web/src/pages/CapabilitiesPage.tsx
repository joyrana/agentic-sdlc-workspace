import {
  Badge,
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
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap } from "../api/client";
import { ErrorNotice, Loading, PageHeader } from "../components/common";

type Skill = {
  id: string;
  version: string;
  name: string;
  description: string;
  category: string;
  agents: string[];
  tools: string[];
  strategies: string[];
  approval_level: string;
  timeout_s: number;
  postconditions: string[];
};
type Model = {
  model_id: string;
  revision: string;
  license: string;
  license_verified: boolean;
  context_window: number;
  tasks: string[];
  privacy: string;
  available: boolean;
  is_mock: boolean;
  deployment: Record<string, unknown>;
  observed: { calls?: number; success_rate?: number; mean_latency_ms?: number };
};
type Agent = { name: string; skills: string[]; tools: string[] };

const useStyles = makeStyles({
  page: {
    padding: `${tokens.spacingVerticalXXL} ${tokens.spacingHorizontalXXL}`,
    width: "100%",
    maxWidth: "1280px",
    boxSizing: "border-box",
  },
  mono: { fontFamily: tokens.fontFamilyMonospace },
  muted: { color: tokens.colorNeutralForeground3 },
  chips: { display: "flex", flexWrap: "wrap", gap: tokens.spacingHorizontalXS },
});

type EvalRun = {
  id: string;
  suite: string;
  dataset_version: string;
  config: Record<string, unknown>;
  metrics: Record<string, number>;
  passed: boolean;
  notes: string;
  created_at: string;
  failing_cases: string[];
};

export function CapabilitiesPage() {
  const s = useStyles();
  const [tab, setTab] = useState<"skills" | "agents" | "models" | "evaluations">("skills");
  const skills = useQuery({
    queryKey: ["skills"],
    queryFn: () => unwrap(api.GET("/api/v1/skills")) as Promise<unknown> as Promise<Skill[]>,
  });
  const agents = useQuery({
    queryKey: ["agents"],
    queryFn: () => unwrap(api.GET("/api/v1/agents")) as Promise<unknown> as Promise<Record<string, Agent>>,
  });
  const models = useQuery({
    queryKey: ["models"],
    queryFn: () => unwrap(api.GET("/api/v1/models")) as Promise<unknown> as Promise<Model[]>,
  });
  const evals = useQuery({
    queryKey: ["evaluations"],
    queryFn: () => unwrap(api.GET("/api/v1/evaluations")) as Promise<unknown> as Promise<EvalRun[]>,
    enabled: tab === "evaluations",
  });
  const err = skills.error ?? agents.error ?? models.error ?? evals.error;
  return (
    <main id="main" className={s.page}>
      <PageHeader
        title="Capabilities"
        description="Skills are loaded only from the repository. Agents can call a tool only when both the agent and the skill allow it."
      />
      <TabList
        selectedValue={tab}
        onTabSelect={(_, d) => setTab(d.value as typeof tab)}
        style={{ marginBottom: 12 }}
      >
        <Tab value="skills">Skills</Tab>
        <Tab value="agents">Agents</Tab>
        <Tab value="models">Models</Tab>
        <Tab value="evaluations">Evaluations</Tab>
      </TabList>
      {err && <ErrorNotice error={err} />}
      {tab === "skills" &&
        (skills.data ? (
          <Table aria-label="Skills" size="small">
            <TableHeader>
              <TableRow>
                <TableHeaderCell>Skill</TableHeaderCell>
                <TableHeaderCell>Version</TableHeaderCell>
                <TableHeaderCell>Agents</TableHeaderCell>
                <TableHeaderCell>Strategies</TableHeaderCell>
                <TableHeaderCell>Tools</TableHeaderCell>
                <TableHeaderCell>Checks</TableHeaderCell>
              </TableRow>
            </TableHeader>
            <TableBody>
              {skills.data.map((k) => (
                <TableRow key={k.id}>
                  <TableCell>
                    <Tooltip content={k.description} relationship="description">
                      <Text weight="semibold">{k.name}</Text>
                    </Tooltip>
                    <div className={`${s.mono} ${s.muted}`}>{k.id}</div>
                  </TableCell>
                  <TableCell className={s.mono}>{k.version}</TableCell>
                  <TableCell>{k.agents.join(", ")}</TableCell>
                  <TableCell>
                    <div className={s.chips}>
                      {k.strategies.map((x) => (
                        <Badge key={x} appearance="outline" size="small">
                          {x}
                        </Badge>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell className={s.mono}>{k.tools.join(", ") || "none"}</TableCell>
                  <TableCell>{k.postconditions.join(", ") || "schema only"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <Loading />
        ))}
      {tab === "agents" && agents.data && (
        <Table aria-label="Agents" size="small">
          <TableHeader>
            <TableRow>
              <TableHeaderCell>Agent</TableHeaderCell>
              <TableHeaderCell>Skills</TableHeaderCell>
              <TableHeaderCell>Allowed tools</TableHeaderCell>
            </TableRow>
          </TableHeader>
          <TableBody>
            {Object.entries(agents.data).map(([id, a]) => (
              <TableRow key={id}>
                <TableCell>
                  <Text weight="semibold">{a.name}</Text>
                </TableCell>
                <TableCell>{a.skills.join(", ") || <span className={s.muted}>none yet</span>}</TableCell>
                <TableCell className={s.mono}>{a.tools.join(", ")}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {tab === "models" && models.data && (
        <Table aria-label="Models" size="small">
          <TableHeader>
            <TableRow>
              <TableHeaderCell>Model</TableHeaderCell>
              <TableHeaderCell>Availability</TableHeaderCell>
              <TableHeaderCell>License</TableHeaderCell>
              <TableHeaderCell>Context</TableHeaderCell>
              <TableHeaderCell>Tasks</TableHeaderCell>
              <TableHeaderCell>Observed</TableHeaderCell>
            </TableRow>
          </TableHeader>
          <TableBody>
            {models.data.map((m) => (
              <TableRow key={m.model_id}>
                <TableCell>
                  <Text weight="semibold">{m.model_id}</Text>{" "}
                  {m.is_mock && (
                    <Badge color="severe" appearance="outline" size="small">
                      mock
                    </Badge>
                  )}
                  <div className={`${s.mono} ${s.muted}`}>rev {m.revision}</div>
                </TableCell>
                <TableCell>
                  <Badge color={m.available ? "success" : "subtle"} appearance="tint">
                    {m.available ? "available" : "not available"}
                  </Badge>
                </TableCell>
                <TableCell>
                  {m.license}{" "}
                  {!m.license_verified && (
                    <Tooltip
                      content="Reported by the publisher; not yet verified against the model card."
                      relationship="description"
                    >
                      <Badge color="warning" appearance="outline" size="small">
                        unverified
                      </Badge>
                    </Tooltip>
                  )}
                </TableCell>
                <TableCell>{m.context_window.toLocaleString()}</TableCell>
                <TableCell>{m.tasks.join(", ")}</TableCell>
                <TableCell>
                  {m.observed.calls
                    ? `${m.observed.calls} calls · ${Math.round((m.observed.success_rate ?? 0) * 100)}% ok · ${m.observed.mean_latency_ms} ms`
                    : "no calls yet"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {tab === "evaluations" &&
        evals.data &&
        (evals.data.length === 0 ? (
          <Text className={s.muted}>
            No evaluation runs recorded. Run <code>make evals</code> with <code>--record</code> to add one.
          </Text>
        ) : (
          <Table aria-label="Evaluation runs" size="small">
            <TableHeader>
              <TableRow>
                <TableHeaderCell>Suite</TableHeaderCell>
                <TableHeaderCell>Result</TableHeaderCell>
                <TableHeaderCell>Metrics</TableHeaderCell>
                <TableHeaderCell>Recorded</TableHeaderCell>
              </TableRow>
            </TableHeader>
            <TableBody>
              {evals.data.map((e) => (
                <TableRow key={e.id}>
                  <TableCell>
                    <Text weight="semibold">{e.suite}</Text>
                    <div className={`${s.mono} ${s.muted}`}>
                      dataset {e.dataset_version} · {String(e.config.strategy)}
                    </div>
                  </TableCell>
                  <TableCell>
                    <Badge color={e.passed ? "success" : "danger"} appearance="tint">
                      {e.passed ? "within baseline" : "below baseline"}
                    </Badge>
                    {e.failing_cases.length > 0 && (
                      <div className={s.muted}>failing: {e.failing_cases.join(", ")}</div>
                    )}
                  </TableCell>
                  <TableCell className={s.mono}>
                    {Object.entries(e.metrics).map(([k, v]) => (
                      <div key={k}>
                        {k} {v}
                      </div>
                    ))}
                  </TableCell>
                  <TableCell>{e.created_at.replace("T", " ").slice(0, 16)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ))}
    </main>
  );
}
