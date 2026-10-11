import { useQuery } from "@tanstack/react-query";
import { api, ApiError, tokenStore, unwrap, type Schemas } from "./client";

export type Run = Schemas["RunOut"];
export type RunDetail = Schemas["RunDetail"];
export type Approval = Schemas["ApprovalOut"];

const p = (projectId: string) => ({ params: { path: { project_id: projectId } } });

export function useRuns(projectId: string) {
  return useQuery({
    queryKey: ["project", projectId, "runs"],
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/runs", p(projectId))),
  });
}

/** The most recent delivery run, if any. */
export function useLatestRun(projectId: string) {
  const runs = useRuns(projectId);
  return { ...runs, run: runs.data?.[0] };
}

export function useRunDetail(projectId: string, runId: string | undefined) {
  return useQuery({
    queryKey: ["project", projectId, "run", runId],
    enabled: !!runId,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/runs/{run_id}", {
          params: { path: { project_id: projectId, run_id: runId! } },
        }),
      ),
  });
}

export function useApprovals(projectId: string, status: "pending" | "all" = "pending") {
  return useQuery({
    queryKey: ["project", projectId, "approvals", status],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/approvals", {
          params: { path: { project_id: projectId }, query: { status } },
        }),
      ),
  });
}

/** Evidence endpoints return loosely typed JSON; these are the shapes the UI relies on. */
export type CodeChange = {
  id: string;
  attempt: number;
  branch: string;
  base_commit: string;
  head_commit: string | null;
  files: string[];
  summary: string;
  status: string;
  diff?: string;
  coding_agent: { strategy: string; model_ref?: string | null; notes?: string[] };
  review: {
    ok: boolean;
    violations: string[];
    warnings: string[];
    added_lines: number;
    removed_lines: number;
  };
};
export type TestCase = { nodeid: string; outcome: string; criteria: string[]; message: string };
export type TestRunT = {
  id: string;
  suite: string;
  attempt: number;
  status: string;
  totals: Record<string, number>;
  cases: TestCase[];
  command: string[];
  sandbox: Record<string, unknown>;
  commit: string | null;
  log?: string;
};
export type Verdict = {
  criterion_key: string;
  criterion: string;
  requirement_key: string;
  priority: string;
  attempt: number;
  status: string;
  confidence: number;
  gaps: string[];
};
export type DefectT = {
  id: string;
  title: string;
  severity: string;
  status: string;
  criterion_key: string | null;
  backlog_key: string | null;
  reproduction_steps: string[];
  expected: string;
  actual: string;
  found_in_attempt: number;
  fixed_in_attempt: number | null;
};

async function getJson<T>(url: string): Promise<T> {
  const token = tokenStore.get();
  const res = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  const body = await res.json().catch(() => ({}));
  if (!res.ok)
    throw new ApiError(
      res.status,
      body?.error?.code ?? "http_error",
      body?.error?.message ?? "Request failed",
    );
  return body as T;
}

export function useEvidence<T>(projectId: string, path: string, enabled = true) {
  return useQuery({
    queryKey: ["project", projectId, "evidence", path],
    enabled,
    queryFn: () => getJson<T>(`/api/v1/projects/${projectId}/${path}`),
  });
}
