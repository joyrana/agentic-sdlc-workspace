import { FluentProvider } from "@fluentui/react-components";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { StrategyBadge } from "../components/common";
import { lightTheme } from "../theme";
import { ApprovalsPage } from "./ApprovalsPage";

const base = {
  run_id: "r1",
  requested_action: "Approve the architecture proposal and ADR before implementation planning.",
  artifact_type: "architecture_proposal",
  artifact_id: "a1",
  artifact_version: 2,
  content_hash: "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
  required_roles: ["reviewer", "project_owner"],
  status: "pending",
  expires_at: null,
  created_at: "2026-10-10T10:00:00Z",
  decision: null,
};

function renderWith(approvals: unknown[]) {
  globalThis.fetch = vi.fn(
    async () => new Response(JSON.stringify(approvals), { status: 200 }),
  ) as typeof fetch;
  return render(
    <FluentProvider theme={lightTheme}>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/projects/p1/approvals"]}>
          <Routes>
            <Route path="/projects/:projectId/approvals" element={<ApprovalsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </FluentProvider>,
  );
}

describe("ApprovalsPage", () => {
  it("explains why the user cannot decide and offers no decision buttons", async () => {
    renderWith([
      {
        ...base,
        id: "x1",
        gate: "architecture",
        can_decide: false,
        cannot_decide_reason: "you authored this revision",
      },
    ]);
    expect(await screen.findByText(/You can’t decide this: you authored this revision/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve/ })).not.toBeInTheDocument();
  });

  it("names the exact version being approved", async () => {
    renderWith([{ ...base, id: "x2", gate: "architecture", can_decide: true, cannot_decide_reason: null }]);
    expect(await screen.findByRole("button", { name: "Approve v2" })).toBeInTheDocument();
    expect(screen.getByText("#abcdef012345")).toBeInTheDocument();
    // Rejecting requires a comment, so the button starts disabled.
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
  });
});

describe("StrategyBadge", () => {
  it("labels non-model output honestly", () => {
    render(
      <FluentProvider theme={lightTheme}>
        <StrategyBadge strategy="scripted" />
        <StrategyBadge strategy="heuristic" />
        <StrategyBadge strategy="mock" />
      </FluentProvider>,
    );
    expect(screen.getByText("Scripted fixture")).toBeInTheDocument();
    expect(screen.getByText("Rule-based")).toBeInTheDocument();
    expect(screen.getByText("Mock model")).toBeInTheDocument();
  });
});
