const SCHEDULE_ID_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$/;
const TIME_PATTERN = /^(?<hour>[01]\d|2[0-3]):(?<minute>[0-5]\d)$/;

function asInteger(value, field) {
  if (!Number.isInteger(value)) {
    throw new TypeError(`${field} must be an integer`);
  }
  return value;
}

function localFormatter(timeZone, includeSeconds = false) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    ...(includeSeconds ? { second: "2-digit" } : {}),
    hourCycle: "h23",
  });
}

function formattedParts(timeZone, instant, includeSeconds = false) {
  const values = Object.fromEntries(
    localFormatter(timeZone, includeSeconds)
      .formatToParts(instant)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  return {
    year: Number(values.year),
    month: Number(values.month),
    day: Number(values.day),
    hour: Number(values.hour),
    minute: Number(values.minute),
    second: includeSeconds ? Number(values.second) : 0,
  };
}

function dateKey(parts) {
  return `${String(parts.year).padStart(4, "0")}-${String(parts.month).padStart(2, "0")}-${String(parts.day).padStart(2, "0")}`;
}

function offsetAt(timeZone, epochMilliseconds) {
  const local = formattedParts(timeZone, new Date(epochMilliseconds), true);
  return Date.UTC(
    local.year,
    local.month - 1,
    local.day,
    local.hour,
    local.minute,
    local.second,
  ) - epochMilliseconds;
}

function utcForLocalTime(timeZone, date, hour, minute) {
  const nominal = Date.UTC(date.year, date.month - 1, date.day, hour, minute);
  const offsets = new Set(
    [-24, -12, 0, 12, 24].map((hours) => offsetAt(timeZone, nominal + hours * 3_600_000)),
  );
  const candidates = [...offsets].map((offset) => nominal - offset);
  const exact = candidates.filter((candidate) => {
    const rendered = formattedParts(timeZone, new Date(candidate));
    return (
      rendered.year === date.year
      && rendered.month === date.month
      && rendered.day === date.day
      && rendered.hour === hour
      && rendered.minute === minute
    );
  });
  if (exact.length) return Math.min(...exact);

  // A skipped local minute occurs at the spring DST transition. Search the
  // small transition window from its earliest candidate and use the first
  // valid later minute on that local day. On a repeated autumn hour, `exact`
  // above deliberately selected the first occurrence, so it still runs once.
  const scanStart = Math.min(...candidates) - 180 * 60_000;
  const scanEnd = Math.max(...candidates) + 180 * 60_000;
  for (let shifted = scanStart; shifted <= scanEnd; shifted += 60_000) {
    const shiftedParts = formattedParts(timeZone, new Date(shifted));
    if (
      dateKey(shiftedParts) === dateKey(date)
      && (shiftedParts.hour > hour
        || (shiftedParts.hour === hour && shiftedParts.minute >= minute))
    ) {
      return shifted;
    }
  }
  return Math.min(...candidates);
}

function addDays(date, days) {
  const value = new Date(Date.UTC(date.year, date.month - 1, date.day + days));
  return {
    year: value.getUTCFullYear(),
    month: value.getUTCMonth() + 1,
    day: value.getUTCDate(),
  };
}

function scheduleWeekday(date) {
  const value = new Date(Date.UTC(date.year, date.month - 1, date.day));
  return (value.getUTCDay() + 6) % 7;
}

export function normalizeScheduleProjection(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("schedule must be an object");
  }
  const keys = Object.keys(value).sort();
  const expected = ["enabled", "scheduleId", "startTime", "timezone", "weekdays"];
  if (keys.length !== expected.length || keys.some((key, index) => key !== expected[index])) {
    throw new TypeError("schedule contains an unexpected field");
  }
  const scheduleId = String(value.scheduleId || "");
  if (!SCHEDULE_ID_PATTERN.test(scheduleId)) {
    throw new TypeError("scheduleId is invalid");
  }
  if (typeof value.enabled !== "boolean") {
    throw new TypeError("enabled must be true or false");
  }
  const timezone = String(value.timezone || "");
  if (!timezone || timezone.length > 80) {
    throw new TypeError("timezone is invalid");
  }
  try {
    localFormatter(timezone).format();
  } catch (_error) {
    throw new TypeError("timezone is invalid");
  }
  const matchedTime = TIME_PATTERN.exec(String(value.startTime || ""));
  if (!matchedTime?.groups) {
    throw new TypeError("startTime must use HH:MM");
  }
  if (!Array.isArray(value.weekdays) || !value.weekdays.length || value.weekdays.length > 7) {
    throw new TypeError("weekdays are invalid");
  }
  const weekdays = [...new Set(value.weekdays.map((item) => asInteger(item, "weekday")))].sort(
    (first, second) => first - second,
  );
  if (weekdays.length !== value.weekdays.length || weekdays.some((item) => item < 0 || item > 6)) {
    throw new TypeError("weekdays are invalid");
  }
  return {
    scheduleId,
    enabled: value.enabled,
    timezone,
    startTime: String(value.startTime),
    weekdays,
  };
}

export function nextDueOccurrence(schedule, nowMilliseconds = Date.now()) {
  if (!schedule.enabled) return null;
  const [hour, minute] = schedule.startTime.split(":").map(Number);
  const localNow = formattedParts(schedule.timezone, new Date(nowMilliseconds));
  for (let offset = 0; offset <= 7; offset += 1) {
    const localDate = addDays(localNow, offset);
    if (!schedule.weekdays.includes(scheduleWeekday(localDate))) continue;
    const localDateKey = dateKey(localDate);
    if (schedule.lastDispatchedDate === localDateKey) continue;
    const occurrence = utcForLocalTime(schedule.timezone, localDate, hour, minute);
    if (occurrence < nowMilliseconds - 30_000) {
      if (offset === 0) {
        return { at: nowMilliseconds + 1_000, localDate: localDateKey };
      }
      continue;
    }
    return { at: occurrence, localDate: localDateKey };
  }
  return null;
}

export function localDateAt(timeZone, epochMilliseconds = Date.now()) {
  return dateKey(formattedParts(timeZone, new Date(epochMilliseconds)));
}
