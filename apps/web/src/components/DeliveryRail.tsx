import { makeStyles, mergeClasses, shorthands, Text, tokens, Tooltip } from "@fluentui/react-components";

/**
 * The delivery rail: the lifecycle as a sequence of stage segments with human gates as diamonds.
 * It renders backend state only (run stage + status); it never decides transitions.
 */

export type RailStep = { id: string; label: string; kind: "stage" | "gate"; stages: string[] };

export const RAIL: RailStep[] = [
  {
    id: "plan",
    label: "Plan",
    kind: "stage",
    stages: ["snapshot_inputs", "clarification_gate", "plan_backlog"],
  },
  { id: "design", label: "Design", kind: "stage", stages: ["analyze_repository", "propose_architecture"] },
  { id: "arch_gate", label: "Architecture approval", kind: "gate", stages: ["architecture_gate"] },
  { id: "scope_gate", label: "Scope approval", kind: "gate", stages: ["implementation_gate"] },
  { id: "build", label: "Build", kind: "stage", stages: ["implement"] },
  {
    id: "verify",
    label: "Verify",
    kind: "stage",
    stages: ["qa_tests", "ba_verification", "create_defects", "regression"],
  },
  { id: "ready", label: "Readiness", kind: "stage", stages: ["release_readiness"] },
  { id: "release_gate", label: "Release approval", kind: "gate", stages: ["release_gate"] },
  {
    id: "staging",
    label: "Staging",
    kind: "stage",
    stages: ["deploy_staging", "post_deploy_verification", "finalize"],
  },
];

export type StepState = "done" | "current" | "waiting" | "blocked" | "todo";

export function railStates(stage: string | null | undefined, status: string | null | undefined): StepState[] {
  if (!stage) return RAIL.map(() => "todo");
  const idx = RAIL.findIndex((s) => s.stages.includes(stage));
  const finished = status === "completed";
  return RAIL.map((_, i) => {
    if (finished || i < idx) return "done";
    if (i > idx || idx < 0) return "todo";
    if (status === "awaiting_approval") return "waiting";
    if (status === "escalated" || status === "failed" || status === "cancelled") return "blocked";
    return "current";
  });
}

const useStyles = makeStyles({
  rail: { display: "flex", alignItems: "center", gap: "2px", width: "100%", minWidth: 0 },
  stage: {
    flexGrow: 1,
    flexBasis: 0,
    minWidth: "36px",
    height: "6px",
    borderRadius: tokens.borderRadiusCircular,
    backgroundColor: tokens.colorNeutralStroke2,
  },
  gate: {
    flexShrink: 0,
    width: "12px",
    height: "12px",
    transform: "rotate(45deg)",
    ...shorthands.margin("0", "4px"),
    ...shorthands.border("2px", "solid", tokens.colorNeutralStroke1),
    backgroundColor: tokens.colorNeutralBackground1,
  },
  done: {
    backgroundColor: tokens.colorBrandBackground,
    ...shorthands.borderColor(tokens.colorBrandBackground),
  },
  current: {
    backgroundImage: `linear-gradient(90deg, ${tokens.colorBrandBackground} 50%, ${tokens.colorBrandBackground2} 50%)`,
    ...shorthands.borderColor(tokens.colorBrandBackground),
  },
  waiting: {
    backgroundColor: tokens.colorPaletteMarigoldBackground3,
    ...shorthands.borderColor(tokens.colorPaletteMarigoldBorderActive),
    "@media (prefers-reduced-motion: no-preference)": {
      animationName: {
        "0%": { boxShadow: "0 0 0 0 rgba(234,163,0,.55)" },
        "100%": { boxShadow: "0 0 0 8px rgba(234,163,0,0)" },
      },
      animationDuration: "1.6s",
      animationIterationCount: "infinite",
    },
  },
  blocked: {
    backgroundColor: tokens.colorPaletteRedBackground3,
    ...shorthands.borderColor(tokens.colorPaletteRedBorderActive),
  },
  labels: { display: "flex", justifyContent: "space-between", marginTop: tokens.spacingVerticalXS },
});

const STATE_TEXT: Record<StepState, string> = {
  done: "complete",
  current: "in progress",
  waiting: "waiting for a human decision",
  blocked: "stopped",
  todo: "not started",
};

export function DeliveryRail({ stage, status }: { stage?: string | null; status?: string | null }) {
  const s = useStyles();
  const states = railStates(stage, status);
  const currentIdx = states.findIndex((x) => x === "current" || x === "waiting" || x === "blocked");
  const current = currentIdx >= 0 ? RAIL[currentIdx] : undefined;
  return (
    <div>
      <div className={s.rail} role="list" aria-label="Delivery progress">
        {RAIL.map((step, i) => {
          const st = states[i] ?? "todo";
          return (
            <Tooltip key={step.id} content={`${step.label}: ${STATE_TEXT[st]}`} relationship="label">
              <div
                role="listitem"
                tabIndex={0}
                aria-current={st === "current" || st === "waiting" ? "step" : undefined}
                className={mergeClasses(step.kind === "gate" ? s.gate : s.stage, st !== "todo" && s[st])}
              />
            </Tooltip>
          );
        })}
      </div>
      <div className={s.labels}>
        <Text size={200}>
          {!stage
            ? "No delivery run yet"
            : status === "completed"
              ? "Delivered to staging"
              : current
                ? `${current.label} — ${STATE_TEXT[states[currentIdx] ?? "todo"]}`
                : stage}
        </Text>
      </div>
    </div>
  );
}
