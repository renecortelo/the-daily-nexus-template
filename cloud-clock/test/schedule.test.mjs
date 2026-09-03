import assert from "node:assert/strict";
import test from "node:test";

import { localDateAt, nextDueOccurrence, normalizeScheduleProjection } from "../src/schedule.js";

const projection = Object.freeze({
  scheduleId: "job-0123456789abcdef0123",
  enabled: true,
  timezone: "America/New_York",
  startTime: "04:45",
  weekdays: [0, 1, 2, 3, 4],
});

test("normalizes only the opaque timing projection", () => {
  assert.deepEqual(normalizeScheduleProjection(projection), projection);
  assert.throws(
    () => normalizeScheduleProjection({ ...projection, gmailLabel: "private" }),
    /unexpected field/,
  );
});

test("finds the next weekday occurrence in the selected IANA timezone", () => {
  const now = Date.parse("2026-08-03T07:00:00Z"); // Monday, 03:00 in New York.
  const next = nextDueOccurrence(projection, now);
  assert.equal(next.localDate, "2026-08-03");
  assert.equal(new Date(next.at).toISOString(), "2026-08-03T08:45:00.000Z");
});

test("catches up once when a newly synchronized schedule is already due", () => {
  const now = Date.parse("2026-08-03T10:00:00Z");
  const next = nextDueOccurrence(projection, now);
  assert.equal(next.localDate, "2026-08-03");
  assert.equal(next.at, now + 1_000);
  assert.equal(localDateAt("America/New_York", now), "2026-08-03");
});

test("does not schedule the same local day after dispatch", () => {
  const now = Date.parse("2026-08-03T10:00:00Z");
  const next = nextDueOccurrence({ ...projection, lastDispatchedDate: "2026-08-03" }, now);
  assert.equal(next.localDate, "2026-08-04");
});

test("uses the first valid minute for a skipped DST time", () => {
  const sunday = {
    ...projection,
    startTime: "02:30",
    weekdays: [6],
  };
  const next = nextDueOccurrence(sunday, Date.parse("2026-03-07T12:00:00Z"));
  assert.equal(next.localDate, "2026-03-08");
  assert.equal(new Date(next.at).toISOString(), "2026-03-08T07:00:00.000Z");
});

test("runs only once at the first occurrence of a repeated DST time", () => {
  const sunday = {
    ...projection,
    startTime: "01:30",
    weekdays: [6],
  };
  const next = nextDueOccurrence(sunday, Date.parse("2026-10-31T12:00:00Z"));
  assert.equal(next.localDate, "2026-11-01");
  assert.equal(new Date(next.at).toISOString(), "2026-11-01T05:30:00.000Z");
});
