import {
  localDateAt,
  nextDueOccurrence,
  normalizeScheduleProjection,
} from "./schedule.js";

const MAX_SCHEDULES = 100;
const MAX_BODY_BYTES = 512;
const WAKE_COOLDOWN_MS = 60_000;
const OWNER_ID_PATTERN = /^[A-Za-z0-9_-]{1,128}$/;
const REPOSITORY_PATTERN = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const WORKFLOW_PATTERN = /^[A-Za-z0-9_.-]{1,160}$/;
const REF_PATTERN = /^[A-Za-z0-9._/-]{1,200}$/;

function jsonResponse(payload, status = 200, headers = {}) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      ...headers,
    },
  });
}

function genericFailure(status = 400, cors = {}) {
  return jsonResponse({ error: "The private cloud clock request could not be completed." }, status, cors);
}

function allowedOrigin(env, origin) {
  return Boolean(origin && env.TDN_ALLOWED_ORIGIN && origin === env.TDN_ALLOWED_ORIGIN);
}

function corsHeaders(env, origin) {
  if (!allowedOrigin(env, origin)) return {};
  return {
    "access-control-allow-origin": origin,
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-allow-headers": "authorization, content-type",
    "access-control-max-age": "600",
    vary: "Origin",
  };
}

async function readSmallJson(request) {
  const length = Number(request.headers.get("content-length") || "0");
  if (!Number.isFinite(length) || length > MAX_BODY_BYTES) {
    throw new TypeError("body is too large");
  }
  const raw = await request.text();
  if (raw.length > MAX_BODY_BYTES) throw new TypeError("body is too large");
  const value = raw ? JSON.parse(raw) : {};
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("body must be an object");
  }
  if (Object.keys(value).length !== 1 || value.schemaVersion !== 1) {
    throw new TypeError("body is invalid");
  }
  return value;
}

function tokenSubject(request) {
  const authorization = request.headers.get("authorization") || "";
  if (!authorization.startsWith("Bearer ")) throw new TypeError("missing bearer token");
  const token = authorization.slice("Bearer ".length).trim();
  const parts = token.split(".");
  if (parts.length !== 3 || !parts.every(Boolean)) throw new TypeError("invalid bearer token");
  let payload;
  try {
    const padded = parts[1].replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(parts[1].length / 4) * 4, "=");
    payload = JSON.parse(atob(padded));
  } catch (_error) {
    throw new TypeError("invalid bearer token");
  }
  const uid = String(payload?.sub || "");
  if (!OWNER_ID_PATTERN.test(uid)) throw new TypeError("invalid bearer token");
  return { token, uid };
}

function firestoreDocumentUrl(projectId, path) {
  const escapedPath = path.split("/").map(encodeURIComponent).join("/");
  return `https://firestore.googleapis.com/v1/projects/${encodeURIComponent(projectId)}/databases/(default)/documents/${escapedPath}`;
}

async function firestoreGet(url, token) {
  const response = await fetch(url, {
    headers: { authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw new TypeError("firebase authorization failed");
  return response.json();
}

function fieldValue(field) {
  if (!field || typeof field !== "object") return undefined;
  if (Object.hasOwn(field, "stringValue")) return String(field.stringValue);
  if (Object.hasOwn(field, "booleanValue")) return field.booleanValue === true;
  if (Object.hasOwn(field, "integerValue")) return Number(field.integerValue);
  if (field.arrayValue && typeof field.arrayValue === "object") {
    return (field.arrayValue.values || []).map(fieldValue);
  }
  return undefined;
}

function firestoreProjection(document) {
  const fields = document?.fields || {};
  return normalizeScheduleProjection({
    scheduleId: fieldValue(fields.scheduleId),
    enabled: fieldValue(fields.enabled),
    timezone: fieldValue(fields.timezone),
    startTime: fieldValue(fields.startTime),
    weekdays: fieldValue(fields.weekdays),
  });
}

async function authenticateOwner(request, env) {
  const { token, uid } = tokenSubject(request);
  if (!env.TDN_OWNER_UID || uid !== env.TDN_OWNER_UID) {
    throw new TypeError("owner authorization failed");
  }
  const projectId = String(env.TDN_FIREBASE_PROJECT_ID || "");
  if (!projectId) throw new TypeError("worker configuration failed");
  // Firestore validates the Firebase ID token and its owner-only security rule.
  // The unverified JWT subject above is used only to construct this path.
  await firestoreGet(firestoreDocumentUrl(projectId, `owners/${uid}`), token);
  return { token, uid, projectId };
}

async function loadClockSchedules(auth) {
  const url = new URL(
    firestoreDocumentUrl(
      auth.projectId,
      `users/${auth.uid}/clockSchedules`,
    ),
  );
  url.searchParams.set("pageSize", String(MAX_SCHEDULES));
  const response = await firestoreGet(url.toString(), auth.token);
  const projections = (response.documents || []).map(firestoreProjection);
  if (projections.length > MAX_SCHEDULES) throw new TypeError("too many schedules");
  return projections;
}

function clockStub(env) {
  const id = env.TDN_CLOUD_CLOCK.idFromName("owner-clock-v1");
  return env.TDN_CLOUD_CLOCK.get(id);
}

async function clockRequest(env, command, payload = {}) {
  const response = await clockStub(env).fetch("https://clock.internal/v1/command", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ command, ...payload }),
  });
  if (!response.ok) throw new TypeError("clock operation failed");
  return response.json();
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("origin");
    const cors = corsHeaders(env, origin);
    if (request.method === "OPTIONS") {
      return allowedOrigin(env, origin)
        ? new Response(null, { status: 204, headers: cors })
        : genericFailure(403);
    }
    if (!allowedOrigin(env, origin)) return genericFailure(403);
    try {
      const url = new URL(request.url);
      const auth = await authenticateOwner(request, env);
      if (request.method === "POST" && url.pathname === "/v1/sync") {
        await readSmallJson(request);
        const schedules = await loadClockSchedules(auth);
        const result = await clockRequest(env, "sync", { schedules });
        return jsonResponse({ status: "synchronized", ...result }, 200, cors);
      }
      if (request.method === "POST" && url.pathname === "/v1/wake") {
        await readSmallJson(request);
        const result = await clockRequest(env, "wake");
        return jsonResponse(result, 202, cors);
      }
      if (request.method === "GET" && url.pathname === "/v1/status") {
        const result = await clockRequest(env, "status");
        return jsonResponse(result, 200, cors);
      }
      return genericFailure(404, cors);
    } catch (_error) {
      // Never expose bearer tokens, Firestore paths, or GitHub responses.
      return genericFailure(401, cors);
    }
  },
};

function validDispatchConfig(env) {
  const repository = String(env.TDN_GITHUB_REPOSITORY || "");
  const workflow = String(env.TDN_GITHUB_WORKFLOW || "private-cloud-runner.yml");
  const ref = String(env.TDN_GITHUB_REF || "main");
  if (!REPOSITORY_PATTERN.test(repository) || !WORKFLOW_PATTERN.test(workflow) || !REF_PATTERN.test(ref)) {
    throw new TypeError("dispatch configuration is invalid");
  }
  if (!env.TDN_GITHUB_DISPATCH_TOKEN) throw new TypeError("dispatch configuration is incomplete");
  return { repository, workflow, ref };
}

function repositoryPath(repository) {
  return repository.split("/").map(encodeURIComponent).join("/");
}

export class CloudClock {
  constructor(state, env) {
    this.state = state;
    this.env = env;
  }

  async schedules() {
    return (await this.state.storage.get("schedules")) || [];
  }

  async persistSchedules(schedules) {
    await this.state.storage.put("schedules", schedules);
  }

  async rearm(schedules = null) {
    const allSchedules = schedules || await this.schedules();
    const occurrences = allSchedules
      .map((schedule) => ({ schedule, occurrence: nextDueOccurrence(schedule) }))
      .filter((item) => item.occurrence);
    if (!occurrences.length) {
      await this.state.storage.deleteAlarm();
      return null;
    }
    const next = occurrences.reduce((best, current) => (
      current.occurrence.at < best.occurrence.at ? current : best
    ));
    await this.state.storage.setAlarm(next.occurrence.at);
    return next.occurrence.at;
  }

  async dispatch(inputs = {}) {
    const { repository, workflow, ref } = validDispatchConfig(this.env);
    const response = await fetch(
      `https://api.github.com/repos/${repositoryPath(repository)}/actions/workflows/${encodeURIComponent(workflow)}/dispatches`,
      {
        method: "POST",
        headers: {
          accept: "application/vnd.github+json",
          authorization: `Bearer ${this.env.TDN_GITHUB_DISPATCH_TOKEN}`,
          "content-type": "application/json",
          "user-agent": "the-daily-nexus-cloud-clock",
          "x-github-api-version": "2022-11-28",
        },
        body: JSON.stringify({ ref, inputs }),
      },
    );
    if (response.status !== 204) throw new TypeError("workflow dispatch failed");
    await this.state.storage.put("lastDispatchAt", Date.now());
  }

  async sync(schedules) {
    const previous = new Map((await this.schedules()).map((item) => [item.scheduleId, item]));
    const retained = schedules.map((incoming) => ({
      ...incoming,
      lastDispatchedDate: previous.get(incoming.scheduleId)?.lastDispatchedDate || "",
    }));
    await this.persistSchedules(retained);
    const nextAlarmAt = await this.rearm(retained);
    return { activeScheduleCount: retained.filter((item) => item.enabled).length, nextAlarmAt };
  }

  async status() {
    const schedules = await this.schedules();
    const nextAlarmAt = await this.rearm(schedules);
    return {
      activeScheduleCount: schedules.filter((item) => item.enabled).length,
      nextAlarmAt,
      lastDispatchAt: (await this.state.storage.get("lastDispatchAt")) || null,
    };
  }

  async wake() {
    const previous = (await this.state.storage.get("lastWakeAt")) || 0;
    if (Date.now() - previous < WAKE_COOLDOWN_MS) {
      return { status: "already-requested" };
    }
    await this.dispatch();
    await this.state.storage.put("lastWakeAt", Date.now());
    return { status: "dispatched" };
  }

  async fetch(request) {
    if (request.method !== "POST") return genericFailure(405);
    try {
      const body = await request.json();
      if (!body || typeof body !== "object" || Array.isArray(body)) throw new TypeError("invalid command");
      if (body.command === "sync") {
        if (!Array.isArray(body.schedules) || body.schedules.length > MAX_SCHEDULES) {
          throw new TypeError("invalid schedules");
        }
        const schedules = body.schedules.map(normalizeScheduleProjection);
        return jsonResponse(await this.sync(schedules));
      }
      if (body.command === "wake") return jsonResponse(await this.wake());
      if (body.command === "status") return jsonResponse(await this.status());
      throw new TypeError("unknown command");
    } catch (_error) {
      return genericFailure(400);
    }
  }

  async alarm() {
    const schedules = await this.schedules();
    const now = Date.now();
    let changed = false;
    const due = schedules
      .map((schedule) => ({ schedule, occurrence: nextDueOccurrence(schedule, now) }))
      .filter((item) => item.occurrence && item.occurrence.at <= now + 2_000)
      .sort((first, second) => first.occurrence.at - second.occurrence.at);
    for (const item of due) {
      await this.dispatch({
        clock_schedule_id: item.schedule.scheduleId,
        clock_schedule_date: item.occurrence.localDate,
        clock_source: "cloud-clock",
      });
      item.schedule.lastDispatchedDate = item.occurrence.localDate;
      changed = true;
      // Persist after every accepted dispatch. A rare duplicate delivery is
      // still harmless because the private runner has an execution claim.
      await this.persistSchedules(schedules);
    }
    if (changed || !due.length) await this.rearm(schedules);
  }
}
