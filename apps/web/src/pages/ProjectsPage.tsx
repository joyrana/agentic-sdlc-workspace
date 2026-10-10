import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  DialogTrigger,
  Field,
  Input,
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
import { Add20Regular } from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { EmptyState, ErrorNotice, Loading, PageHeader, SampleBadge } from "../components/common";

const useStyles = makeStyles({
  page: {
    padding: `${tokens.spacingVerticalXXL} ${tokens.spacingHorizontalXXL}`,
    width: "100%",
    maxWidth: "1200px",
  },
  form: { display: "flex", flexDirection: "column", gap: tokens.spacingVerticalM },
  name: { fontWeight: tokens.fontWeightSemibold, color: tokens.colorBrandForeground1 },
  key: { fontFamily: tokens.fontFamilyMonospace, color: tokens.colorNeutralForeground3 },
});

export function ProjectsPage() {
  const s = useStyles();
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => unwrap(api.GET("/api/v1/projects")) });

  return (
    <main id="main" className={s.page}>
      <PageHeader
        title="Projects"
        description="Each project carries its sources, requirements, backlog and delivery runs."
        actions={<CreateProjectDialog />}
      />
      {projects.isLoading && <Loading />}
      {projects.error && <ErrorNotice error={projects.error} onRetry={() => void projects.refetch()} />}
      {projects.data && projects.data.items.length === 0 && (
        <EmptyState title="No projects yet" action={<CreateProjectDialog />}>
          Create a project, then upload a requirements document to start.
        </EmptyState>
      )}
      {projects.data && projects.data.items.length > 0 && (
        <Table aria-label="Projects">
          <TableHeader>
            <TableRow>
              <TableHeaderCell>Name</TableHeaderCell>
              <TableHeaderCell>Key</TableHeaderCell>
              <TableHeaderCell>Goals</TableHeaderCell>
              <TableHeaderCell>Created</TableHeaderCell>
            </TableRow>
          </TableHeader>
          <TableBody>
            {projects.data.items.map((p) => (
              <TableRow key={p.id}>
                <TableCell>
                  <Link to={`/projects/${p.id}`} className={s.name}>
                    {p.name}
                  </Link>{" "}
                  {p.is_sample && <SampleBadge />}
                </TableCell>
                <TableCell>
                  <span className={s.key}>{p.key}</span>
                </TableCell>
                <TableCell>
                  <Text truncate wrap={false}>
                    {p.goals || "—"}
                  </Text>
                </TableCell>
                <TableCell>{new Date(p.created_at).toLocaleDateString()}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </main>
  );
}

function CreateProjectDialog() {
  const s = useStyles();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [goals, setGoals] = useState("");
  const create = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/projects", { body: { name, goals, description: "", constraints: {} } })),
    onSuccess: (p) => {
      void qc.invalidateQueries({ queryKey: ["projects"] });
      void qc.invalidateQueries({ queryKey: ["me"] });
      setOpen(false);
      navigate(`/projects/${p.id}`);
    },
  });
  return (
    <Dialog open={open} onOpenChange={(_, d) => setOpen(d.open)}>
      <DialogTrigger disableButtonEnhancement>
        <Button appearance="primary" icon={<Add20Regular />}>
          Create project
        </Button>
      </DialogTrigger>
      <DialogSurface>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <DialogBody>
            <DialogTitle>Create project</DialogTitle>
            <DialogContent className={s.form}>
              <Field label="Name" required>
                <Input value={name} onChange={(_, d) => setName(d.value)} />
              </Field>
              <Field label="Goals" hint="What should this delivery achieve? You can change this later.">
                <Textarea value={goals} onChange={(_, d) => setGoals(d.value)} resize="vertical" />
              </Field>
              {create.error && <ErrorNotice error={create.error} />}
            </DialogContent>
            <DialogActions>
              <DialogTrigger disableButtonEnhancement>
                <Button>Cancel</Button>
              </DialogTrigger>
              <Button appearance="primary" type="submit" disabled={!name.trim() || create.isPending}>
                Create project
              </Button>
            </DialogActions>
          </DialogBody>
        </form>
      </DialogSurface>
    </Dialog>
  );
}
