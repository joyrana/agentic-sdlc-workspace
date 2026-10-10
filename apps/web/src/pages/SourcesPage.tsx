import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  Radio,
  RadioGroup,
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
import { ArrowUpload20Regular } from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api, unwrap, uploadForm, type Schemas } from "../api/client";
import { useAuth } from "../auth";
import {
  EmptyState,
  ErrorNotice,
  Loading,
  PageHeader,
  SampleBadge,
  StatusBadge,
  StrategyBadge,
} from "../components/common";

type Doc = Schemas["DocumentDetail"];

const useStyles = makeStyles({
  upload: {
    display: "flex",
    gap: tokens.spacingHorizontalL,
    alignItems: "flex-end",
    flexWrap: "wrap",
    padding: tokens.spacingHorizontalL,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1,
    marginBottom: tokens.spacingVerticalXL,
  },
  warnings: { margin: 0, paddingLeft: tokens.spacingHorizontalL },
  blocks: {
    display: "flex",
    flexDirection: "column",
    gap: tokens.spacingVerticalXS,
    maxHeight: "60vh",
    overflowY: "auto",
  },
  block: { display: "grid", gridTemplateColumns: "64px 1fr", gap: tokens.spacingHorizontalM },
  blockId: { fontFamily: tokens.fontFamilyMonospace, color: tokens.colorNeutralForeground3 },
  path: { color: tokens.colorNeutralForeground3, fontSize: tokens.fontSizeBase200 },
  heading: { fontWeight: tokens.fontWeightSemibold },
});

export function SourcesPage() {
  const s = useStyles();
  const { projectId = "" } = useParams();
  const { rolesIn } = useAuth();
  const canUpload = rolesIn(projectId).some((r) => r === "project_owner" || r === "engineer");
  const docs = useQuery({
    queryKey: ["project", projectId, "documents"],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/documents", { params: { path: { project_id: projectId } } }),
      ),
    refetchInterval: (q) =>
      q.state.data?.some((d) => d.versions.some((v) => v.status === "pending" || v.status === "processing"))
        ? 2000
        : false,
  });
  const [viewing, setViewing] = useState<{ doc: Doc; version: number } | null>(null);

  return (
    <>
      <PageHeader
        title="Sources"
        description="Business documents and meeting transcripts. Every requirement links back to the passage it came from."
      />
      {canUpload && <UploadPanel projectId={projectId} />}
      {docs.isLoading && <Loading />}
      {docs.error && <ErrorNotice error={docs.error} onRetry={() => void docs.refetch()} />}
      {docs.data?.length === 0 && (
        <EmptyState title="No sources yet">
          Upload a BRD (.docx) or a meeting transcript (.txt, .vtt). Try{" "}
          <code>samples/documents/task-title-validation-brd.docx</code>.
        </EmptyState>
      )}
      {docs.data && docs.data.length > 0 && (
        <Table aria-label="Source documents">
          <TableHeader>
            <TableRow>
              <TableHeaderCell>Document</TableHeaderCell>
              <TableHeaderCell>Kind</TableHeaderCell>
              <TableHeaderCell>Version</TableHeaderCell>
              <TableHeaderCell>Status</TableHeaderCell>
              <TableHeaderCell>Extracted</TableHeaderCell>
              <TableHeaderCell>Warnings</TableHeaderCell>
            </TableRow>
          </TableHeader>
          <TableBody>
            {docs.data.map((d) => {
              const v = d.versions[d.versions.length - 1];
              if (!v) return null;
              const summary = v.extraction_summary as {
                created?: string[];
                updated?: string[];
                method?: string;
                model_ref?: string | null;
                questions_added?: number;
              };
              return (
                <TableRow key={d.id}>
                  <TableCell>
                    <Button
                      appearance="transparent"
                      onClick={() => setViewing({ doc: d, version: v.version })}
                    >
                      {d.title}
                    </Button>
                    {d.is_sample && <SampleBadge />}
                  </TableCell>
                  <TableCell>{d.kind}</TableCell>
                  <TableCell>v{v.version}</TableCell>
                  <TableCell>
                    <StatusBadge status={v.status} />
                  </TableCell>
                  <TableCell>
                    {v.status === "processed" && (
                      <Text>
                        {summary.created?.length ?? 0} new, {summary.updated?.length ?? 0} changed,{" "}
                        {summary.questions_added ?? 0} questions{" "}
                        {summary.method && (
                          <StrategyBadge strategy={summary.method} model={summary.model_ref} />
                        )}
                      </Text>
                    )}
                  </TableCell>
                  <TableCell>
                    {v.warnings.length > 0 ? (
                      <ul className={s.warnings}>
                        {v.warnings.map((w, i) => (
                          <li key={i}>
                            <Text size={200}>{String(w)}</Text>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
      {viewing && (
        <BlocksDialog
          projectId={projectId}
          doc={viewing.doc}
          version={viewing.version}
          onClose={() => setViewing(null)}
        />
      )}
    </>
  );
}

function UploadPanel({ projectId }: { projectId: string }) {
  const s = useStyles();
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState("brd");
  const upload = useMutation({
    mutationFn: () => {
      const form = new FormData();
      form.append("file", file!);
      form.append("kind", kind);
      return uploadForm<Schemas["UploadOut"]>(`/api/v1/projects/${projectId}/documents`, form);
    },
    onSuccess: () => {
      setFile(null);
      if (fileRef.current) fileRef.current.value = "";
      void qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });
  return (
    <form
      className={s.upload}
      onSubmit={(e) => {
        e.preventDefault();
        if (file) upload.mutate();
      }}
    >
      <Field label="File" hint=".docx, .txt, .md or .vtt — up to 10 MB">
        <input
          ref={fileRef}
          type="file"
          accept=".docx,.txt,.md,.vtt"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          aria-label="Choose a file to upload"
        />
      </Field>
      <Field label="Kind">
        <RadioGroup layout="horizontal" value={kind} onChange={(_, d) => setKind(d.value)}>
          <Radio value="brd" label="Requirements document" />
          <Radio value="transcript" label="Meeting transcript" />
          <Radio value="spec" label="Specification" />
        </RadioGroup>
      </Field>
      <Button
        appearance="primary"
        icon={<ArrowUpload20Regular />}
        type="submit"
        disabled={!file || upload.isPending}
      >
        {upload.isPending ? "Uploading" : "Upload and extract"}
      </Button>
      {upload.error && <ErrorNotice error={upload.error} />}
      {upload.isSuccess && (
        <MessageBar intent="success">
          <MessageBarBody>Uploaded. The Business Analyst agent is extracting requirements.</MessageBarBody>
        </MessageBar>
      )}
    </form>
  );
}

function BlocksDialog({
  projectId,
  doc,
  version,
  onClose,
}: {
  projectId: string;
  doc: Doc;
  version: number;
  onClose: () => void;
}) {
  const s = useStyles();
  const [q, setQ] = useState("");
  const blocks = useQuery({
    queryKey: ["project", projectId, "blocks", doc.id, version, q],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/documents/{document_id}/versions/{version}/blocks", {
          params: { path: { project_id: projectId, document_id: doc.id, version }, query: q ? { q } : {} },
        }),
      ),
  });
  return (
    <Dialog open onOpenChange={(_, d) => !d.open && onClose()}>
      <DialogSurface style={{ maxWidth: "860px" }}>
        <DialogBody>
          <DialogTitle>
            {doc.title} · v{version}
          </DialogTitle>
          <DialogContent>
            <Field label="Search this document">
              <Input value={q} onChange={(_, d) => setQ(d.value)} placeholder="e.g. whitespace" />
            </Field>
            {blocks.isLoading && <Loading />}
            {blocks.data && (
              <div className={s.blocks} role="list">
                {blocks.data.map((b) => (
                  <div key={b.block_id} className={s.block} role="listitem" id={b.block_id}>
                    <span className={s.blockId}>{b.block_id}</span>
                    <div>
                      {b.section_path.length > 0 && (
                        <div className={s.path}>{b.section_path.join(" › ")}</div>
                      )}
                      <Text className={b.kind === "heading" ? s.heading : undefined}>{b.text}</Text>
                    </div>
                  </div>
                ))}
                {blocks.data.length === 0 && <Text>No passages match.</Text>}
              </div>
            )}
          </DialogContent>
          <DialogActions>
            <Button onClick={onClose}>Close</Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  );
}
