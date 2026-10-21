import assert from "node:assert/strict";
import test from "node:test";
import { observeWorkflow, safeObservation } from "../src/runner-status.js";
import { CloudClock } from "../src/index.js";

const config = { repository: "owner/private", workflow: "private-cloud-runner.yml", ref: "main", token: "synthetic-private" };
const run = { id: 123, head_branch: "main", event: "workflow_dispatch", status: "completed", conclusion: "failure", run_number: 7,
  created_at: "2026-01-01T01:00:00Z", updated_at: "2026-01-01T01:00:03Z" };
const job = { status: "completed", conclusion: "failure", steps: [], check_run_url: "https://api.github.com/repos/owner/private/check-runs/456" };
async function observe(responses, candidate = run) {
  const original = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), method: options.method });
    const next = responses.shift();
    if (next instanceof Error) throw next;
    return next instanceof Response ? next : Response.json(next);
  };
  try {
    const result = await observeWorkflow(config, Date.parse("2026-01-01T02:00:00Z"));
    assert(calls.length <= 3); assert(calls.every(call => call.method === "GET"));
    assert.doesNotMatch(JSON.stringify(result), /owner|private|456|annotation|token/);
    return { result, calls };
  } finally { globalThis.fetch = original; }
}

test("pre-start refusal requires an exact provider annotation before naming billing", async () => {
  const { result } = await observe([{ workflow_runs: [run] }, { total_count: 1, jobs: [job] }, [{ path: ".github", annotation_level: "failure",
    message: "The job was not started because recent account payments have failed or your spending limit needs to be increased." }]]);
  assert.equal(result.state, "not_started"); assert.equal(result.reason, "billing_or_spending_limit");
  assert.equal(result.runNumber, 7);
});

test("denied Checks read, missing annotation or arbitrary failure never implies exhausted quota", async () => {
  for (const annotation of [new Response(null, { status: 403 }), [], [{ path: "src/app.js", annotation_level: "failure", message: "billing" }]]) {
    const { result } = await observe([{ workflow_runs: [run] }, { total_count: 1, jobs: [job] }, annotation]);
    assert.equal(result.state, "not_started"); assert.equal(result.reason, undefined);
  }
  const { result, calls } = await observe([{ workflow_runs: [run] }, { total_count: 1, jobs: [{ ...job, steps: [{ name: "synthetic-private" }] }] }]);
  assert.equal(result.state, "failed"); assert.equal(calls.length, 2);
});

test("refused or malformed provider reads return unknown without retaining errors", async () => {
  for (const response of [new Response(null, { status: 429 }), new Error("synthetic-private"), { workflow_runs: {} }, { workflow_runs: [{ ...run, head_branch: "other" }] }]) {
    const { result } = await observe([response]); assert.equal(result.state, "unknown");
  }
});

test("provider evidence distinguishes queued, starting, running and terminal workflow outcomes", async () => {
  for (const [value, jobs, expected] of [
    [{ ...run, status: "queued" }, null, "queued"],
    [{ ...run, status: "in_progress" }, { total_count: 1, jobs: [job] }, "starting"],
    [{ ...run, status: "in_progress" }, { total_count: 1, jobs: [{ ...job, steps: [{}] }] }, "running"],
    [{ ...run, conclusion: "success" }, null, "completed"],
    [{ ...run, conclusion: "cancelled" }, null, "cancelled"],
  ]) {
    const { result } = await observe([{ workflow_runs: [value] }, jobs]); assert.equal(result.state, expected);
  }
  const { result } = await observe([{ workflow_runs: [] }]); assert.equal(result.state, "none");
});

test("truncated job lists and off-repository annotation URLs are not trusted", async () => {
  for (const check_run_url of ["https://untrusted.example/repos/owner/private/check-runs/1", "https://api.github.com/repos/other/private/check-runs/1", "https://api.github.com/repos/owner/private/check-runs/1?secret=synthetic-private"]) {
    const { result, calls } = await observe([{ workflow_runs: [run] }, { total_count: 1, jobs: [{ ...job, check_run_url }] }]);
    assert.equal(result.state, "not_started"); assert.equal(result.reason, undefined); assert.equal(calls.length, 2);
  }
  const { result } = await observe([{ workflow_runs: [run] }, { total_count: 21, jobs: [job] }]);
  assert.equal(result.state, "failed"); assert.equal(result.reason, undefined);
});

test("oversize provider bodies fail closed before JSON parsing", async () => {
  const { result } = await observe([new Response("x".repeat(131073))]); assert.equal(result.state, "unknown");
});

test("cached/coalesced status reads do not dispatch or persist provider identities", async () => {
  const original = globalThis.fetch; let count = 0;
  const storage = new Map();
  const clock = new CloudClock({ storage: { get: async key => storage.get(key), put: async (key, value) => storage.set(key, value) } }, {
    TDN_GITHUB_REPOSITORY: config.repository, TDN_GITHUB_DISPATCH_TOKEN: config.token,
  });
  globalThis.fetch = async (_url, options) => { count++; assert.equal(options.method, "GET"); await Promise.resolve(); return Response.json({ workflow_runs: [{ ...run, conclusion: "success" }] }); };
  try {
    await Promise.all([clock.providerStatus(), clock.providerStatus()]);
    await clock.providerStatus(); assert.equal(count, 1);
    assert.doesNotMatch(JSON.stringify([...storage.values()]), /owner|private|token|123/);
    storage.set("providerStatus", { state: "running", observedAt: Date.now() + 60000 });
    await clock.providerStatus(); assert.equal(count, 2);
  } finally { globalThis.fetch = original; }
});

test("observation allowlist never retains provider IDs, messages or non-finite fields", () => {
  assert.deepEqual(safeObservation({ state: "untrusted", reason: "billing_or_spending_limit", observedAt: Infinity, secret: "synthetic-private" }), { state: "unknown" });
});
