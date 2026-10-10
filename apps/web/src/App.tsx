import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { useAuth } from "./auth";
import { AppShell } from "./components/AppShell";
import { Loading } from "./components/common";
import { ProjectLayout } from "./pages/ProjectLayout";
import { ProjectOverview } from "./pages/ProjectOverview";
import { ProjectSettings } from "./pages/ProjectSettings";
import { ProjectsPage } from "./pages/ProjectsPage";
import { SignIn } from "./pages/SignIn";

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <ProjectsPage /> },
      {
        path: "projects/:projectId",
        element: <ProjectLayout />,
        children: [
          { index: true, element: <ProjectOverview /> },
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
