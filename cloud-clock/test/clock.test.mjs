import assert from "node:assert/strict";
import test from "node:test";

import cloudWorker, { CloudClock } from "../src/index.js";

class MemoryStorage {
  values = new Map();
  alarm = null;

  async get(key) {
    return this.values.get(key);
  }

  async put(key, value) {
    this.values.set(key, value);
  }

  async setAlarm(value) {
    this.alarm = value;
  }

  async deleteAlarm() {
    this.alarm = null;
  }
}

function clockEnvironment() {
  return {
    TDN_GITHUB_DISPATCH_TOKEN: "test-token",
    TDN_GITHUB_REPOSITORY: "owner/private-repository",
    TDN_GITHUB_WORKFLOW: "private-cloud-runner.yml",
    TDN_GITHUB_REF: "main",
  };
}

function timingProjection() {
  return {
    scheduleId: "job-0123456789abcdef0123",
    enabled: true,
    timezone: "UTC",
    startTime: "04:45",
    weekdays: [0, 1, 2, 3, 4],
  };
}

test("clock state retains timing only", async () => {
  const storage = new MemoryStorage();
  const clock = new CloudClock({ storage }, clockEnvironment());
  const response = await clock.fetch(new Request("https://clock.internal/v1/command", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ command: "sync", schedules: [timingProjection()] }),
  }));

  assert.equal(response.status, 200);
  const stored = await storage.get("schedules");
  assert.deepEqual(Object.keys(stored[0]).sort(), [
    "enabled",
    "lastDispatchedDate",
    "scheduleId",
    "startTime",
    "timezone",
    "weekdays",
  ]);
  assert.doesNotMatch(JSON.stringify(stored), /gmail|newsletter|parameter|voice|token/i);
  assert.equal(typeof storage.alarm, "number");
});

test("manual wake dispatches only the locked private workflow", async () => {
  const storage = new MemoryStorage();
  const clock = new CloudClock({ storage }, clockEnvironment());
  const originalFetch = globalThis.fetch;
  let request = null;
  globalThis.fetch = async (url, options) => {
    request = { url: String(url), options };
    return new Response(null, { status: 204 });
  };
  try {
    const response = await clock.fetch(new Request("https://clock.internal/v1/command", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ command: "wake" }),
    }));
    assert.equal(response.status, 200);
    assert.match(request.url, /\/actions\/workflows\/private-cloud-runner\.yml\/dispatches$/);
    assert.deepEqual(JSON.parse(request.options.body), { ref: "main", inputs: {} });
    assert.equal(request.options.headers.authorization, "Bearer test-token");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("browser clock endpoint rejects an untrusted origin before authentication", async () => {
  const response = await cloudWorker.fetch(
    new Request("https://clock.example.workers.dev/v1/status", {
      headers: { origin: "https://untrusted.example" },
    }),
    { TDN_ALLOWED_ORIGIN: "https://trusted.example" },
  );
  assert.equal(response.status, 403);
});
