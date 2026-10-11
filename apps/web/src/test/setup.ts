import "@testing-library/jest-dom/vitest";

// jsdom does not implement ResizeObserver; Fluent's TabList and overflow utilities need it.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;
