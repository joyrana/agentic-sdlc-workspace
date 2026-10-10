import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { useAuth } from "./auth";
import { AppShell } from "./components/AppShell";
import { BacklogPage } from "./pages/BacklogPage";
import { CapabilitiesPage } from "./pages/CapabilitiesPage";
import { Loading } from "./components/common";
import { ProjectLayout } from "./pages/ProjectLayout";
import { ProjectOverview } from "./pages/ProjectOverview";
import { ProjectSettings } from "./pages/ProjectSettings";
import { ProjectsPage } from "./pages/ProjectsPage";
import { RequirementsPage } from "./pages/RequirementsPage";
import { SourcesPage } from "./pages/SourcesPage";
import { SignIn } from "./pages/SignIn";

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <ProjectsPage /> },
      { path: "capabilities", element: <CapabilitiesPage /> },
      {
        path: "projects/:projectId",
        element: <ProjectLayout />,
        children: [
          { index: true, element: <ProjectOverview /> },
          { path: "sources", element: <SourcesPage /> },
          { path: "requirements", element: <RequirementsPage /> },
          { path: "backlog", element: <BacklogPage /> },
          { path: "settings", element: <ProjectSettings /> },
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
