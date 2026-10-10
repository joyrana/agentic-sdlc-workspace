import { lazy, Suspense, type ReactNode } from "react";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { useAuth } from "./auth";
import { AppShell } from "./components/AppShell";
import { Loading } from "./components/common";
import { ProjectLayout } from "./pages/ProjectLayout";
import { ProjectOverview } from "./pages/ProjectOverview";
import { ProjectsPage } from "./pages/ProjectsPage";
import { SignIn } from "./pages/SignIn";

const ActivityPage = lazy(() => import("./pages/ActivityPage").then((m) => ({ default: m.ActivityPage })));
const ApprovalsPage = lazy(() => import("./pages/ApprovalsPage").then((m) => ({ default: m.ApprovalsPage })));
const ArchitecturePage = lazy(() =>
  import("./pages/ArchitecturePage").then((m) => ({ default: m.ArchitecturePage })),
);
const BacklogPage = lazy(() => import("./pages/BacklogPage").then((m) => ({ default: m.BacklogPage })));
const CapabilitiesPage = lazy(() =>
  import("./pages/CapabilitiesPage").then((m) => ({ default: m.CapabilitiesPage })),
);
const RequirementsPage = lazy(() =>
  import("./pages/RequirementsPage").then((m) => ({ default: m.RequirementsPage })),
);
const SourcesPage = lazy(() => import("./pages/SourcesPage").then((m) => ({ default: m.SourcesPage })));
const ProjectSettings = lazy(() =>
  import("./pages/ProjectSettings").then((m) => ({ default: m.ProjectSettings })),
);

const page = (node: ReactNode) => <Suspense fallback={<Loading />}>{node}</Suspense>;

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <ProjectsPage /> },
      { path: "capabilities", element: page(<CapabilitiesPage />) },
      {
        path: "projects/:projectId",
        element: <ProjectLayout />,
        children: [
          { index: true, element: <ProjectOverview /> },
          { path: "sources", element: page(<SourcesPage />) },
          { path: "requirements", element: page(<RequirementsPage />) },
          { path: "backlog", element: page(<BacklogPage />) },
          { path: "activity", element: page(<ActivityPage />) },
          { path: "architecture", element: page(<ArchitecturePage />) },
          { path: "approvals", element: page(<ApprovalsPage />) },
          { path: "settings", element: page(<ProjectSettings />) },
        ],
      },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);

export function App() {
  const { token, me, loading } = useAuth();
  if (!token) return <SignIn />;
  if (loading || !me) return <Loading label="Signing in" />;
  return <RouterProvider router={router} />;
}
