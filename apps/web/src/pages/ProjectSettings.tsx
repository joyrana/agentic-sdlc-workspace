import {
  Button,
  Dropdown,
  Field,
  Input,
  Option,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
  makeStyles,
  tokens,
} from "@fluentui/react-components";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useOutletContext, useParams } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api/client";
import { useAuth } from "../auth";
import { ErrorNotice, Loading, PageHeader, SampleBadge } from "../components/common";

const ROLES = ["project_owner", "engineer", "reviewer", "qa", "release_approver", "auditor"];

const useStyles = makeStyles({
  section: {
    marginBottom: tokens.spacingVerticalXXL,
    display: "flex",
    flexDirection: "column",
    gap: tokens.spacingVerticalM,
  },
  form: { display: "flex", gap: tokens.spacingHorizontalM, alignItems: "flex-end", flexWrap: "wrap" },
});

const LIMITS: { key: string; label: string; hint: string }[] = [
  {
    key: "max_repair_attempts",
    label: "Repair attempts",
    hint: "Defect → fix cycles before a person takes over",
  },
  {
    key: "max_architecture_revisions",
    label: "Architecture revisions",
    hint: "Change requests before escalation",
  },
  { key: "max_model_calls", label: "Model calls per run", hint: "Across all agents" },
  { key: "max_tokens", label: "Tokens per run", hint: "Input plus output" },
  { key: "max_cost_usd", label: "Cost per run (USD)", hint: "Self-hosted models report 0" },
  { key: "max_wall_clock_s", label: "Active time per run (s)", hint: "Excludes time waiting for people" },
];

function LimitsForm({ projectId }: { projectId: string }) {
  const s = useStyles();
  const qc = useQueryClient();
  const { project } = useOutletContext<Schemas["ProjectSummary"]>();
  const { rolesIn } = useAuth();
  const [values, setValues] = useState<Record<string, string>>(
    Object.fromEntries(Object.entries(project.limits ?? {}).map(([k, v]) => [k, String(v)])),
  );
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PATCH("/api/v1/projects/{project_id}", {
          params: { path: { project_id: projectId } },
          body: {
            expected_version: project.version,
            limits: Object.fromEntries(
              Object.entries(values)
                .filter(([, v]) => v.trim() !== "")
                .map(([k, v]) => [k, Number(v)]),
            ),
          },
        }),
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["project", projectId] }),
  });
  const canEdit = rolesIn(projectId).includes("project_owner");
  return (
    <section className={s.section} aria-labelledby="limits-h">
      <Text as="h2" id="limits-h" size={400} weight="semibold">
        Workflow limits
      </Text>
      <Text>Empty fields use the workspace defaults. Exceeding a limit escalates the run to a person.</Text>
      <form
        className={s.form}
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        {LIMITS.map((l) => (
          <Field key={l.key} label={l.label} hint={l.hint}>
            <Input
              type="number"
              min={0}
              value={values[l.key] ?? ""}
              disabled={!canEdit}
              onChange={(_, d) => setValues((v) => ({ ...v, [l.key]: d.value }))}
            />
          </Field>
        ))}
        {canEdit && (
          <Button type="submit" disabled={save.isPending}>
            Save limits
          </Button>
        )}
      </form>
      {save.error && <ErrorNotice error={save.error} />}
    </section>
  );
}

export function ProjectSettings() {
  const s = useStyles();
  const { projectId = "" } = useParams();
  const { rolesIn } = useAuth();
  const canManage = rolesIn(projectId).includes("project_owner");
  const qc = useQueryClient();
  const path = { params: { path: { project_id: projectId } } };
  const members = useQuery({
    queryKey: ["project", projectId, "members"],
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/members", path)),
  });
  const repos = useQuery({
    queryKey: ["project", projectId, "repositories"],
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/repositories", path)),
  });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("engineer");
  const add = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/projects/{project_id}/members", { ...path, body: { email, role } })),
    onSuccess: () => {
      setEmail("");
      void qc.invalidateQueries({ queryKey: ["project", projectId, "members"] });
    },
  });
  const [repoName, setRepoName] = useState("sample-task-service");
  const addRepo = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/projects/{project_id}/repositories", {
          ...path,
          body: { name: repoName, kind: "fixture", location: repoName, default_branch: "main" },
        }),
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["project", projectId, "repositories"] }),
  });

  return (
    <>
      <PageHeader
        title="Settings"
        description="People, roles, repositories and workflow limits for this project."
      />
      <section className={s.section} aria-labelledby="members-h">
        <Text as="h2" id="members-h" size={400} weight="semibold">
          Members
        </Text>
        {members.isLoading && <Loading />}
        {members.error && <ErrorNotice error={members.error} />}
        {members.data && (
          <Table aria-label="Members" size="small">
            <TableHeader>
              <TableRow>
                <TableHeaderCell>Name</TableHeaderCell>
                <TableHeaderCell>Email</TableHeaderCell>
                <TableHeaderCell>Role</TableHeaderCell>
              </TableRow>
            </TableHeader>
            <TableBody>
              {members.data.map((m) => (
                <TableRow key={`${m.user_id}-${m.role}`}>
                  <TableCell>{m.display_name}</TableCell>
                  <TableCell>{m.email}</TableCell>
                  <TableCell>{m.role.replace(/_/g, " ")}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {canManage && (
          <form
            className={s.form}
            onSubmit={(e) => {
              e.preventDefault();
              add.mutate();
            }}
          >
            <Field label="Email">
              <Input value={email} onChange={(_, d) => setEmail(d.value)} type="email" />
            </Field>
            <Field label="Role">
              <Dropdown
                value={role.replace(/_/g, " ")}
                selectedOptions={[role]}
                onOptionSelect={(_, d) => setRole(d.optionValue ?? "engineer")}
              >
                {ROLES.map((r) => (
                  <Option key={r} value={r}>
                    {r.replace(/_/g, " ")}
                  </Option>
                ))}
              </Dropdown>
            </Field>
            <Button type="submit" disabled={!email || add.isPending}>
              Add member
            </Button>
          </form>
        )}
        {add.error && <ErrorNotice error={add.error} />}
      </section>
      <section className={s.section} aria-labelledby="repos-h">
        <Text as="h2" id="repos-h" size={400} weight="semibold">
          Repositories
        </Text>
        {repos.data && repos.data.length === 0 && (
          <Text>No repositories yet. Agents need one to analyze and change code.</Text>
        )}
        {repos.data && repos.data.length > 0 && (
          <Table aria-label="Repositories" size="small">
            <TableHeader>
              <TableRow>
                <TableHeaderCell>Name</TableHeaderCell>
                <TableHeaderCell>Kind</TableHeaderCell>
                <TableHeaderCell>Default branch</TableHeaderCell>
              </TableRow>
            </TableHeader>
            <TableBody>
              {repos.data.map((r) => (
                <TableRow key={r.id}>
                  <TableCell>
                    {r.name} {r.is_sample && <SampleBadge />}
                  </TableCell>
                  <TableCell>{r.kind}</TableCell>
                  <TableCell>{r.default_branch}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {canManage && (
          <form
            className={s.form}
            onSubmit={(e) => {
              e.preventDefault();
              addRepo.mutate();
            }}
          >
            <Field
              label="Sample repository"
              hint="Shipped fixtures from samples/repositories. Clearly marked as sample data."
            >
              <Input value={repoName} onChange={(_, d) => setRepoName(d.value)} />
            </Field>
            <Button type="submit" disabled={!repoName || addRepo.isPending}>
              Register sample repository
            </Button>
          </form>
        )}
        {addRepo.error && <ErrorNotice error={addRepo.error} />}
      </section>
      <LimitsForm projectId={projectId} />
    </>
  );
}
