import {
  Avatar,
  Badge,
  Button,
  Menu,
  MenuItem,
  MenuList,
  MenuPopover,
  MenuTrigger,
  Text,
  Tooltip,
  makeStyles,
  tokens,
} from "@fluentui/react-components";
import { SignOut20Regular } from "@fluentui/react-icons";
import { useQuery } from "@tanstack/react-query";
import { Link, Outlet } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { useAuth } from "../auth";

const useStyles = makeStyles({
  root: {
    minHeight: "100vh",
    display: "flex",
    flexDirection: "column",
    backgroundColor: tokens.colorNeutralBackground2,
  },
  bar: {
    display: "flex",
    alignItems: "center",
    gap: tokens.spacingHorizontalL,
    padding: `0 ${tokens.spacingHorizontalL}`,
    height: "48px",
    backgroundColor: tokens.colorNeutralBackground1,
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
  },
  brand: {
    display: "flex",
    alignItems: "center",
    gap: tokens.spacingHorizontalS,
    color: tokens.colorNeutralForeground1,
    textDecoration: "none",
    fontWeight: tokens.fontWeightSemibold,
  },
  mark: {
    width: "18px",
    height: "18px",
    transform: "rotate(45deg)",
    backgroundColor: tokens.colorBrandBackground,
    borderRadius: "3px",
  },
  spacer: { flexGrow: 1 },
  navLink: {
    color: tokens.colorNeutralForeground2,
    textDecoration: "none",
    ":hover": { textDecoration: "underline" },
  },
  main: { flexGrow: 1, display: "flex", minHeight: 0 },
  skip: {
    position: "absolute",
    left: "-999px",
    ":focus": { left: tokens.spacingHorizontalM, top: tokens.spacingVerticalS, zIndex: 10 },
  },
});

export function AppShell() {
  const s = useStyles();
  const { me, signOut } = useAuth();
  const info = useQuery({ queryKey: ["system-info"], queryFn: () => unwrap(api.GET("/api/v1/system/info")) });
  return (
    <div className={s.root}>
      <a href="#main" className={s.skip}>
        Skip to content
      </a>
      <header className={s.bar}>
        <Link to="/" className={s.brand}>
          <span className={s.mark} aria-hidden />
          Agentic SDLC Workspace
        </Link>
        {info.data?.model_inference === "unavailable" && (
          <Tooltip
            relationship="description"
            content="No model endpoint is configured. Skills run with rule-based strategies and every result is labeled with how it was produced."
          >
            <Badge appearance="outline" color="warning">
              No model inference
            </Badge>
          </Tooltip>
        )}
        {info.data && info.data.app_env !== "production" && (
          <Badge appearance="tint" color="informative">
            {info.data.app_env}
          </Badge>
        )}
        <span className={s.spacer} />
        <Link to="/capabilities" className={s.navLink}>
          Capabilities
        </Link>
        {me && (
          <Menu>
            <MenuTrigger disableButtonEnhancement>
              <Button appearance="subtle" icon={<Avatar name={me.display_name} size={24} />}>
                <Text>{me.display_name}</Text>
              </Button>
            </MenuTrigger>
            <MenuPopover>
              <MenuList>
                <MenuItem disabled>{me.email}</MenuItem>
                <MenuItem icon={<SignOut20Regular />} onClick={signOut}>
                  Sign out
                </MenuItem>
              </MenuList>
            </MenuPopover>
          </Menu>
        )}
      </header>
      <div className={s.main}>
        <Outlet />
      </div>
    </div>
  );
}
