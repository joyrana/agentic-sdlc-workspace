import { Badge, Text, Tooltip, makeStyles, mergeClasses, tokens } from "@fluentui/react-components";
import { useQuery } from "@tanstack/react-query";
import { NavLink, Outlet, useParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { useProjectEvents } from "../api/events";
import { DeliveryRail } from "../components/DeliveryRail";
import { ErrorNotice, Loading, SampleBadge } from "../components/common";
import { PROJECT_NAV } from "../routes";

const useStyles = makeStyles({
  frame: {
    display: "flex",
    width: "100%",
    minWidth: 0,
    "@media (max-width: 760px)": { flexDirection: "column" },
  },
  nav: {
    width: "220px",
    flexShrink: 0,
    padding: `${tokens.spacingVerticalL} ${tokens.spacingHorizontalS}`,
    backgroundColor: tokens.colorNeutralBackground1,
    borderRight: `1px solid ${tokens.colorNeutralStroke2}`,
    display: "flex",
    flexDirection: "column",
    gap: "2px",
    "@media (max-width: 760px)": {
      width: "auto",
      flexDirection: "row",
      overflowX: "auto",
      borderRight: "none",
      borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
    },
  },
  link: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: tokens.spacingHorizontalS,
    padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalM}`,
    borderRadius: tokens.borderRadiusMedium,
    color: tokens.colorNeutralForeground2,
    textDecoration: "none",
    whiteSpace: "nowrap",
    ":hover": { backgroundColor: tokens.colorNeutralBackground1Hover, color: tokens.colorNeutralForeground1 },
    ":focus-visible": { outline: `2px solid ${tokens.colorStrokeFocus2}`, outlineOffset: "-2px" },
  },
  active: {
    backgroundColor: tokens.colorBrandBackground2,
    color: tokens.colorBrandForeground2,
    fontWeight: tokens.fontWeightSemibold,
  },
  body: { flexGrow: 1, minWidth: 0, display: "flex", flexDirection: "column" },
  head: {
    padding: `${tokens.spacingVerticalL} ${tokens.spacingHorizontalXXL}`,
    backgroundColor: tokens.colorNeutralBackground1,
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
    display: "flex",
    flexDirection: "column",
    gap: tokens.spacingVerticalM,
    "@media (max-width: 760px)": { padding: `${tokens.spacingVerticalM} 16px` },
  },
  titleRow: { display: "flex", alignItems: "baseline", gap: tokens.spacingHorizontalM, flexWrap: "wrap" },
  key: { fontFamily: tokens.fontFamilyMonospace, color: tokens.colorNeutralForeground3 },
  content: {
    padding: `${tokens.spacingVerticalXL} ${tokens.spacingHorizontalXXL}`,
    maxWidth: "1280px",
    width: "100%",
    boxSizing: "border-box",
    "@media (max-width: 760px)": { padding: `${tokens.spacingVerticalL} 16px` },
  },
  live: { marginLeft: "auto" },
});

export function ProjectLayout() {
  const s = useStyles();
  const { projectId } = useParams();
  const summary = useQuery({
    queryKey: ["project", projectId, "summary"],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/summary", { params: { path: { project_id: projectId! } } }),
      ),
    enabled: !!projectId,
  });
  const stream = useProjectEvents(projectId);

  if (summary.isLoading) return <Loading />;
  if (summary.error || !summary.data)
    return (
      <div className={s.content}>
        <ErrorNotice error={summary.error} />
      </div>
    );
  const { project, active_run, pending_approvals, open_defects } = summary.data;

  return (
    <div className={s.frame}>
      <nav className={s.nav} aria-label="Project">
        {PROJECT_NAV.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            end={item.path === ""}
            className={({ isActive }) => mergeClasses(s.link, isActive && s.active)}
          >
            <span>{item.label}</span>
            {item.path === "approvals" && pending_approvals > 0 && (
              <Badge size="small" color="warning">
                {pending_approvals}
              </Badge>
            )}
            {item.path === "quality" && open_defects > 0 && (
              <Badge size="small" color="danger">
                {open_defects}
              </Badge>
            )}
          </NavLink>
        ))}
      </nav>
      <div className={s.body}>
        <div className={s.head}>
          <div className={s.titleRow}>
            <Text as="h1" size={500} weight="semibold">
              {project.name}
            </Text>
            <span className={s.key}>{project.key}</span>
            {project.is_sample && <SampleBadge />}
            <Tooltip relationship="description" content="Live updates from the workflow event stream">
              <Badge className={s.live} appearance="ghost" color={stream === "live" ? "success" : "warning"}>
                {stream === "live" ? "Live" : stream === "closed" ? "Offline" : "Reconnecting"}
              </Badge>
            </Tooltip>
          </div>
          <DeliveryRail
            stage={active_run?.stage as string | undefined}
            status={active_run?.status as string | undefined}
          />
        </div>
        <main id="main" className={s.content}>
          <Outlet context={summary.data} />
        </main>
      </div>
    </div>
  );
}
