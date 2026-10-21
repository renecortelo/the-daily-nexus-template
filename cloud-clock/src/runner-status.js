// Read-only provider evidence. Never return repository identities or annotation prose.
const STATES = new Set(["unknown", "none", "queued", "starting", "running", "completed", "failed", "not_started", "cancelled"]);
const MAX_BYTES = 131072;

function instant(value) {
  const number = typeof value === "string" ? Date.parse(value) : value;
  return Number.isSafeInteger(number) && number > 0 && number < 8640000000000000 ? number : null;
}

export function safeObservation(raw) {
  const result = { state: STATES.has(raw?.state) ? raw.state : "unknown" };
  for (const key of ["observedAt", "createdAt", "updatedAt"]) {
    const value = instant(raw?.[key]);
    if (value !== null) result[key] = value;
  }
  if (Number.isSafeInteger(raw?.runNumber) && raw.runNumber > 0 && raw.runNumber <= 1e9) result.runNumber = raw.runNumber;
  if (result.state === "not_started" && raw?.reason === "billing_or_spending_limit") result.reason = raw.reason;
  return result;
}

export async function observeWorkflow({ repository, workflow, ref, token }, now = Date.now()) {
  const root = `/repos/${repository.split("/").map(encodeURIComponent).join("/")}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  const report = { state: "unknown", observedAt: now };
  async function read(path) {
    const response = await fetch(`https://api.github.com${path}`, {
      method: "GET", redirect: "error", signal: controller.signal,
      headers: { accept: "application/vnd.github+json", authorization: `Bearer ${token}`,
        "user-agent": "the-daily-nexus-cloud-clock", "x-github-api-version": "2022-11-28" },
    });
    if (!response.ok) throw new Error("provider_read_unavailable");
    if (Number(response.headers.get("content-length")) > MAX_BYTES) throw new Error("provider_shape_unavailable");
    const reader = response.body.getReader();
    const chunks = []; let size = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > MAX_BYTES) throw new Error("provider_shape_unavailable");
        chunks.push(value);
      }
    } finally { await reader.cancel(); }
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return JSON.parse(new TextDecoder().decode(bytes));
  }
  try {
    const path = `${root}/actions/workflows/${encodeURIComponent(workflow)}/runs?per_page=1&branch=${encodeURIComponent(ref)}&event=workflow_dispatch`;
    const data = await read(path);
    if (!Array.isArray(data.workflow_runs) || data.workflow_runs.length > 1) return report;
    if (!data.workflow_runs.length) return { ...report, state: "none" };
    const run = data.workflow_runs[0];
    if (!Number.isSafeInteger(run.id) || run.id <= 0 || run.head_branch !== ref || run.event !== "workflow_dispatch") return report;
    Object.assign(report, { createdAt: instant(run.created_at), updatedAt: instant(run.updated_at), runNumber: run.run_number });
    if (["queued", "waiting", "pending", "requested"].includes(run.status)) return safeObservation({ ...report, state: "queued" });
    if (run.status === "completed") {
      if (run.conclusion === "success") return safeObservation({ ...report, state: "completed" });
      if (run.conclusion === "cancelled") return safeObservation({ ...report, state: "cancelled" });
      if (!["failure", "timed_out", "startup_failure", "action_required"].includes(run.conclusion)) return safeObservation(report);
      report.state = "failed";
    } else if (run.status !== "in_progress") return safeObservation(report);
    const jobs = await read(`${root}/actions/runs/${run.id}/jobs?per_page=20&filter=latest`);
    if (!Array.isArray(jobs.jobs) || !Number.isInteger(jobs.total_count) || jobs.total_count !== jobs.jobs.length || jobs.jobs.length > 20) return safeObservation(report);
    const started = jobs.jobs.some(job => Array.isArray(job.steps) && job.steps.length > 0);
    if (run.status === "in_progress") return safeObservation({ ...report, state: started ? "running" : "starting" });
    // A failed job with no steps is not an editorial crash. Do not infer its cause.
    if (jobs.jobs.length !== 1 || !jobs.jobs.every(job => job.status === "completed" && job.conclusion === "failure" && Array.isArray(job.steps) && job.steps.length === 0)) return safeObservation(report);
    report.state = "not_started";
    const check = new URL(jobs.jobs[0].check_run_url);
    const prefix = `${root}/check-runs/`;
    if (check.origin !== "https://api.github.com" || check.search || check.hash || check.username || check.password || !check.pathname.startsWith(prefix) || !/^\d+$/.test(check.pathname.slice(prefix.length))) return safeObservation(report);
    // Optional Checks read may be denied by the existing token; never widen scopes.
    try {
      const annotations = await read(`${check.pathname}/annotations?per_page=20`);
      if (Array.isArray(annotations) && annotations.some(item => item.annotation_level === "failure" && item.path === ".github" && typeof item.message === "string" && /^The job was not started because /i.test(item.message) && /(?:spending limit|spending budget|payments have failed|billing)/i.test(item.message))) report.reason = "billing_or_spending_limit";
    } catch (_error) { /* Unknown reason stays unknown; no raw provider text. */ }
    return safeObservation(report);
  } catch (_error) { return safeObservation(report); }
  finally { clearTimeout(timer); }
}
