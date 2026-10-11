import { FluentProvider } from "@fluentui/react-components";
import { render, screen } from "@testing-library/react";
import { DeliveryRail, RAIL, railStates } from "./DeliveryRail";
import { lightTheme } from "../theme";

describe("railStates", () => {
  it("is all todo without a run", () => {
    expect(railStates(null, null).every((s) => s === "todo")).toBe(true);
  });

  it("marks earlier steps done and the gate as waiting while awaiting approval", () => {
    const states = railStates("architecture_gate", "awaiting_approval");
    const gateIdx = RAIL.findIndex((s) => s.id === "arch_gate");
    expect(states.slice(0, gateIdx).every((s) => s === "done")).toBe(true);
    expect(states[gateIdx]).toBe("waiting");
    expect(states[gateIdx + 1]).toBe("todo");
  });

  it("marks the current step blocked when escalated", () => {
    const idx = RAIL.findIndex((s) => s.id === "verify");
    expect(railStates("create_defects", "escalated")[idx]).toBe("blocked");
  });

  it("marks everything done when completed", () => {
    expect(railStates("finalize", "completed").every((s) => s === "done")).toBe(true);
  });
});

describe("DeliveryRail", () => {
  it("describes the current step in text, not only color", () => {
    render(
      <FluentProvider theme={lightTheme}>
        <DeliveryRail stage="release_gate" status="awaiting_approval" />
      </FluentProvider>,
    );
    expect(screen.getByText("Release approval — waiting for a human decision")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Delivery progress" })).toBeInTheDocument();
  });
});
