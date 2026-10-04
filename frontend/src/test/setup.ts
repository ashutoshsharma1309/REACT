import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup, configure } from "@testing-library/react";

// The case page renders several panels from one fixture set; allow for a busy CI machine.
configure({ asyncUtilTimeout: 4000 });

afterEach(() => {
  cleanup();
  try { localStorage.clear(); } catch { /* jsdom storage unavailable */ }
});
