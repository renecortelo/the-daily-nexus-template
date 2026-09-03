import { initializeApp } from "https://www.gstatic.com/firebasejs/12.16.0/firebase-app.js";
import {
  GoogleAuthProvider,
  browserSessionPersistence,
  getAuth,
  getRedirectResult,
  onAuthStateChanged,
  setPersistence,
  signInWithPopup,
  signInWithRedirect,
  signOut,
} from "https://www.gstatic.com/firebasejs/12.16.0/firebase-auth.js";
import {
  addDoc,
  collection,
  doc,
  getDoc,
  getDocs,
  getFirestore,
  limit,
  onSnapshot,
  orderBy,
  query,
  serverTimestamp,
  setDoc,
  writeBatch,
} from "https://www.gstatic.com/firebasejs/12.16.0/firebase-firestore.js";

const IDLE_LIMIT_MS = 15 * 60 * 1000;
const TYPICAL_RUN_MS = (22 * 60 + 40) * 1000;
const EDITION_ZOOM_STEPS = Object.freeze([0.75, 1, 1.25, 1.5, 1.75]);
const LOCAL_VOICE_GENDERS = Object.freeze({
  af_heart: "Female",
  bf_emma: "Female",
  af_bella: "Female",
  am_michael: "Male",
  am_eric: "Male",
  am_puck: "Male",
});
const appState = {
  auth: null,
  db: null,
  user: null,
  authorized: false,
  schedules: new Map(),
  subscriptions: [],
  idleAt: Date.now() + IDLE_LIMIT_MS,
  installPrompt: null,
  firebaseHosts: new Set(),
  runRequests: [],
  runner: null,
  monitorRefreshedAt: null,
  activeEpisode: null,
  activeEdition: null,
  editionZoom: 1,
  activeAudio: null,
  episodes: [],
  episodeRecords: [],
  playerDetailMode: "references",
  clockSyncTimer: null,
  clockSyncing: false,
  clockProjectionSignature: "",
  clockStatus: "",
};

const byId = (id) => document.getElementById(id);
const authScreen = byId("auth-screen");
const appShell = byId("app-shell");
const authStatus = byId("auth-status");
const globalAlert = byId("global-alert");
const scheduleForm = byId("schedule-form");
const generationForm = byId("generation-form");

function setAuthStatus(message, isError = false) {
  authStatus.textContent = message;
  authStatus.style.color = isError ? "var(--error)" : "";
}

function showAlert(message, isError = false) {
  globalAlert.textContent = message;
  globalAlert.style.background = isError ? "#4b160c" : "#2d190e";
  globalAlert.style.borderColor = isError ? "var(--error)" : "var(--line)";
  globalAlert.hidden = false;
  window.setTimeout(() => {
    globalAlert.hidden = true;
  }, 7000);
}

function clearSubscriptions() {
  for (const unsubscribe of appState.subscriptions) {
    unsubscribe();
  }
  appState.subscriptions = [];
  appState.schedules.clear();
}

function clearPrivateInterface() {
  clearSubscriptions();
  if (appState.clockSyncTimer) {
    window.clearTimeout(appState.clockSyncTimer);
    appState.clockSyncTimer = null;
  }
  byId("signed-in-user").textContent = "";
  byId("schedule-list").replaceChildren();
  byId("run-request-list").replaceChildren();
  byId("episode-list").replaceChildren();
  byId("edition-list").replaceChildren();
  byId("schedule-count").textContent = "0";
  byId("queue-count").textContent = "0 QUEUED";
  byId("runner-status").textContent = "RUNNER STATUS UNKNOWN";
  byId("runner-status").parentElement.classList.remove("running", "error");
  byId("runner-detail").textContent = "Awaiting the private cloud runner status.";
  appState.runRequests = [];
  appState.runner = null;
  appState.monitorRefreshedAt = null;
  appState.activeEpisode = null;
  appState.activeEdition = null;
  appState.user = null;
  appState.authorized = false;
  appState.clockProjectionSignature = "";
  appState.clockStatus = "";
  updateCloudClockStatus();
}

function showAuth() {
  clearPrivateInterface();
  appShell.hidden = true;
  authScreen.hidden = false;
  setAuthStatus("Session-only sign-in. Closing this app clears the web session.");
}

function showApp(user) {
  appState.user = user;
  appState.authorized = true;
  authScreen.hidden = true;
  appShell.hidden = false;
  byId("signed-in-user").textContent = user.email || "Verified Google account";
  resetIdleTimer();
  renderProfiles();
  subscribeToPrivateData(user.uid);
  void refreshCloudClockStatus();
}

function firebaseErrorMessage(error) {
  const code = typeof error?.code === "string" ? error.code : "";
  if (code === "auth/popup-closed-by-user") {
    return "Google sign-in was closed before completion.";
  }
  if (code === "auth/unauthorized-domain") {
    return "This Firebase domain is not authorized for Google sign-in.";
  }
  if (code === "auth/operation-not-allowed") {
    return "Google sign-in is not enabled for this Firebase project.";
  }
  if (code === "auth/network-request-failed") {
    return "Google sign-in could not reach Firebase. Check the connection or browser privacy blocking.";
  }
  if (code === "auth/internal-error") {
    return "Firebase rejected the sign-in configuration. Please refresh and try again.";
  }
  if (code === "permission-denied") {
    return "Access denied by the private owner rules.";
  }
  return "The secure request could not be completed. Check Firebase setup and connectivity.";
}

async function signInUser() {
  const button = byId("sign-in-button");
  button.disabled = true;
  setAuthStatus("Opening Google authentication…");
  const provider = new GoogleAuthProvider();
  provider.setCustomParameters({ prompt: "select_account" });
  try {
    const isIOS =
      /iPad|iPhone|iPod/.test(navigator.userAgent) ||
      window.matchMedia("(display-mode: standalone)").matches;
    if (isIOS) {
      await signInWithRedirect(appState.auth, provider);
      return;
    }
    try {
      await signInWithPopup(appState.auth, provider);
    } catch (error) {
      if (
        error?.code === "auth/popup-blocked" ||
        error?.code === "auth/operation-not-supported-in-this-environment"
      ) {
        await signInWithRedirect(appState.auth, provider);
        return;
      }
      throw error;
    }
  } catch (error) {
    setAuthStatus(firebaseErrorMessage(error), true);
  } finally {
    button.disabled = false;
  }
}

async function signOutUser(reason = "You signed out securely.") {
  try {
    await signOut(appState.auth);
  } finally {
    showAuth();
    setAuthStatus(reason);
  }
}

async function verifyOwner(user) {
  const owner = await getDoc(doc(appState.db, "owners", user.uid));
  if (!owner.exists()) {
    await signOut(appState.auth);
    throw new Error("This Google account is authenticated but is not an authorized owner.");
  }
}

function resetIdleTimer() {
  if (!appState.authorized) {
    return;
  }
  appState.idleAt = Date.now() + IDLE_LIMIT_MS;
}

async function checkIdleTimer() {
  if (!appState.authorized) {
    return;
  }
  const remaining = Math.max(0, appState.idleAt - Date.now());
  const minutes = Math.floor(remaining / 60000);
  const seconds = Math.floor((remaining % 60000) / 1000);
  byId("session-countdown").textContent =
    `AUTO SIGN-OUT // ${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  if (remaining === 0) {
    await signOutUser("The private session expired after 15 minutes of inactivity.");
  }
}

function parseSections(value) {
  const sections = value
    .split(/[,\n]/)
    .map((item) => item.replace(/\s+/g, " ").trim())
    .filter(Boolean);
  if (sections.length > 10) {
    throw new Error("Use no more than 10 podcast sections.");
  }
  const seen = new Set();
  for (const section of sections) {
    if (section.length > 60) {
      throw new Error("Podcast section names must be 60 characters or fewer.");
    }
    if (section.includes("/")) {
      throw new Error("Use “and” instead of a slash in podcast section names.");
    }
    const folded = section.toLocaleLowerCase();
    if (seen.has(folded)) {
      throw new Error(`Duplicate podcast section: ${section}`);
    }
    seen.add(folded);
  }
  return sections;
}

function sectionEditor(form) {
  return form.querySelector("[data-sections-editor]");
}

function sectionValues(form) {
  return parseSections(sectionEditor(form).querySelector("textarea").value);
}

function renderSectionTokens(form) {
  const editor = sectionEditor(form);
  if (!editor) return;
  const textarea = editor.querySelector("textarea");
  const list = editor.querySelector(".section-token-list");
  const sections = parseSections(textarea.value);
  textarea.value = sections.join("\n");
  list.replaceChildren();
  const clearDropHint = () => {
    list.dataset.dropBefore = "";
    list.classList.remove("drop-at-end");
    for (const chip of list.querySelectorAll(".section-chip")) chip.classList.remove("drop-target");
  };
  list.ondragover = (event) => {
    event.preventDefault();
    const chips = [...list.querySelectorAll(".section-chip:not(.dragging)")];
    const next = chips.find((chip) => event.clientX < chip.getBoundingClientRect().left + chip.offsetWidth / 2);
    list.dataset.dropBefore = next?.dataset.section || "";
    for (const chip of chips) {
      chip.classList.toggle("drop-target", chip === next);
    }
    list.classList.toggle("drop-at-end", !next && chips.length > 0);
  };
  list.ondrop = (event) => {
    event.preventDefault();
    const moved = event.dataTransfer.getData("text/plain");
    const ordered = parseSections(textarea.value).filter((item) => item !== moved);
    const before = list.dataset.dropBefore;
    const destination = before ? ordered.indexOf(before) : ordered.length;
    ordered.splice(Math.max(0, destination), 0, moved);
    textarea.value = ordered.join("\n");
    clearDropHint();
    renderSectionTokens(form);
  };
  list.ondragleave = (event) => {
    if (event.relatedTarget && list.contains(event.relatedTarget)) return;
    clearDropHint();
  };
  for (const section of sections) {
    const chip = element("span", "section-chip");
    chip.draggable = true;
    chip.dataset.section = section;
    chip.append(document.createTextNode(section));
    const remove = element("button", "section-chip-remove", "x");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove ${section}`);
    remove.addEventListener("click", () => {
      textarea.value = parseSections(textarea.value).filter((item) => item !== section).join("\n");
      renderSectionTokens(form);
    });
    chip.append(remove);
    chip.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData("text/plain", section);
      event.dataTransfer.effectAllowed = "move";
      chip.classList.add("dragging");
      const ghost = chip.cloneNode(true);
      ghost.className = "section-drag-ghost";
      document.body.append(ghost);
      event.dataTransfer.setDragImage(ghost, ghost.offsetWidth / 2, ghost.offsetHeight / 2);
      window.setTimeout(() => ghost.remove(), 0);
    });
    chip.addEventListener("dragend", () => chip.classList.remove("dragging"));
    list.append(chip);
  }
  const endDrop = element("span", "section-drop-end");
  endDrop.setAttribute("aria-hidden", "true");
  endDrop.addEventListener("dragover", (event) => {
    event.preventDefault();
    list.dataset.dropBefore = "";
    list.classList.add("drop-at-end");
    for (const chip of list.querySelectorAll(".section-chip")) chip.classList.remove("drop-target");
  });
  endDrop.addEventListener("drop", (event) => {
    event.preventDefault();
    event.stopPropagation();
    const moved = event.dataTransfer.getData("text/plain");
    const ordered = parseSections(textarea.value).filter((item) => item !== moved);
    ordered.push(moved);
    textarea.value = ordered.join("\n");
    renderSectionTokens(form);
  });
  list.append(endDrop);
}

function setSections(form, sections) {
  const editor = sectionEditor(form);
  if (!editor) return;
  editor.querySelector("textarea").value = Array.isArray(sections) ? sections.join("\n") : "";
  renderSectionTokens(form);
}

function setupSectionEditor(form) {
  const editor = sectionEditor(form);
  if (!editor) return;
  const input = editor.querySelector(".section-token-input");
  const textarea = editor.querySelector("textarea");
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const candidate = input.value.trim();
    if (!candidate) return;
    try {
      textarea.value = parseSections([textarea.value, candidate].filter(Boolean).join("\n")).join("\n");
      input.value = "";
      renderSectionTokens(form);
    } catch (error) {
      showAlert(error.message, true);
    }
  });
  renderSectionTokens(form);
}

function syncHostControls(form) {
  const twoHosts = String(form.elements.hostCount.value) === "2";
  for (const [selector, enabled] of [["[data-solo-control]", !twoHosts], ["[data-duo-control]", twoHosts]]) {
    const label = form.querySelector(selector);
    if (!label) continue;
    label.querySelector("select").disabled = !enabled;
    label.classList.toggle("inactive-control", !enabled);
  }
  for (const label of form.querySelectorAll("[data-secondary-control]")) {
    label.querySelector("select").disabled = !twoHosts;
    label.classList.toggle("inactive-control", !twoHosts);
  }
  const primaryGender = twoHosts || form.elements.soloName.value === "Dalia" ? "Female" : "Male";
  const restrictVoice = (control, gender) => {
    for (const option of control.options) {
      const allowed = option.dataset.gender === gender;
      option.disabled = !allowed;
      option.hidden = !allowed;
    }
    if (control.selectedOptions[0]?.dataset.gender !== gender) {
      control.value = [...control.options].find((option) => option.dataset.gender === gender)?.value || "";
    }
  };
  restrictVoice(form.elements.primaryVoice, primaryGender);
  restrictVoice(form.elements.secondaryVoice, "Male");
}

function parameterData(form) {
  const values = new FormData(form);
  return {
    runName: String(values.get("runName") || "").trim(),
    gmailLabel: String(values.get("gmailLabel") || "").trim(),
    sections: sectionValues(form),
    hostCount: Number(values.get("hostCount")),
    soloName: String(values.get("soloName") || "Dalia"),
    dialogueStyle: String(values.get("dialogueStyle") || "broadcast"),
    primaryVoice: String(values.get("primaryVoice") || "af_heart"),
    primaryTone: String(values.get("primaryTone") || "warm"),
    secondaryVoice: String(values.get("secondaryVoice") || "am_michael"),
    secondaryTone: String(values.get("secondaryTone") || "dry_wit"),
    // The web archive only exists once a verified cloud run is published.
    // Local-only staging remains available through the desktop/CLI workflow.
    publish: true,
    dateMode: String(values.get("dateMode") || "today"),
    includeTih: values.get("includeTih") === "on",
    editionScale: String(values.get("editionScale") || "standard"),
    evidenceMode: String(values.get("evidenceMode") || "newsletter_first"),
  };
}

function validateParameters(parameters) {
  if (!parameters.runName || parameters.runName.length > 80) {
    throw new Error("Give this run a name of up to 80 characters.");
  }
  if (!parameters.gmailLabel || parameters.gmailLabel.length > 225) {
    throw new Error("Enter a valid Gmail label.");
  }
  if (
    parameters.gmailLabel.startsWith("/") ||
    parameters.gmailLabel.endsWith("/") ||
    parameters.gmailLabel.includes("//")
  ) {
    throw new Error("Use slashes only between Gmail label levels.");
  }
  if (![1, 2].includes(parameters.hostCount)) {
    throw new Error("Choose one or two hosts.");
  }
  if (!["focused", "standard", "comprehensive"].includes(parameters.editionScale)) {
    throw new Error("Choose a valid edition scale.");
  }
  if (!["newsletter_first", "newsletter_only"].includes(parameters.evidenceMode)) {
    throw new Error("Choose a valid evidence mode.");
  }
  if (parameters.evidenceMode === "newsletter_only" && parameters.includeTih) {
    throw new Error("Newsletter only mode requires TIH to be turned off.");
  }
  const expectedPrimaryGender = parameters.hostCount === 2 || parameters.soloName === "Dalia" ? "Female" : "Male";
  if (LOCAL_VOICE_GENDERS[parameters.primaryVoice] !== expectedPrimaryGender) {
    throw new Error(`${parameters.hostCount === 2 || parameters.soloName === "Dalia" ? "Dalia" : "Nox"} needs a matching local voice.`);
  }
  if (parameters.hostCount === 2 && (LOCAL_VOICE_GENDERS[parameters.secondaryVoice] !== "Male" || parameters.primaryVoice === parameters.secondaryVoice)) {
    throw new Error("Dalia and Nox need distinct, host-appropriate local voices.");
  }
  return parameters;
}

function profileStorageKey() {
  return appState.user ? `tdn-private-profiles:${appState.user.uid}` : "";
}

function savedProfiles() {
  try {
    const raw = window.localStorage.getItem(profileStorageKey());
    const profiles = raw ? JSON.parse(raw) : [];
    return Array.isArray(profiles) ? profiles : [];
  } catch {
    return [];
  }
}

function renderProfiles() {
  const picker = byId("profile-picker");
  if (!picker) return;
  const selectedName = picker.value;
  picker.replaceChildren(element("option", "", "LOAD FAVORITE…"));
  for (const profile of savedProfiles()) {
    const option = element("option", "", profile.name);
    option.value = profile.name;
    picker.append(option);
  }
  picker.value = [...picker.options].some((option) => option.value === selectedName)
    ? selectedName
    : "";
  syncFavoriteActions();
}

function selectedFavorite() {
  const name = byId("profile-picker")?.value || "";
  return savedProfiles().find((item) => item.name === name) || null;
}

function syncFavoriteActions() {
  const selected = Boolean(selectedFavorite());
  const update = byId("update-profile-button");
  const remove = byId("delete-profile-button");
  if (update) update.disabled = !selected;
  if (remove) remove.disabled = !selected;
}

function applyParametersToForm(form, parameters) {
  for (const name of [
    "runName", "gmailLabel", "hostCount", "soloName", "dialogueStyle",
    "primaryVoice", "primaryTone", "secondaryVoice", "secondaryTone", "dateMode", "editionScale", "evidenceMode",
  ]) {
    if (parameters[name] !== undefined && form.elements.namedItem(name)) {
      form.elements.namedItem(name).value = parameters[name];
    }
  }
  const publish = form.elements.namedItem("publish");
  if (publish instanceof HTMLInputElement) {
    publish.checked = Boolean(parameters.publish);
  }
  form.elements.includeTih.checked = parameters.includeTih !== false;
  setSections(form, parameters.sections || []);
  syncHostControls(form);
}

function saveFavoriteProfile() {
  try {
    const name = byId("profile-name").value.trim() || generationForm.elements.runName.value.trim();
    if (!name || name.length > 60) throw new Error("Name this favorite using up to 60 characters.");
    const parameters = validateParameters(parameterData(generationForm));
    const profiles = savedProfiles();
    if (profiles.some((item) => item.name === name)) {
      throw new Error("A favorite with that name already exists. Load it, then choose UPDATE to edit it.");
    }
    profiles.unshift({ name, parameters });
    window.localStorage.setItem(profileStorageKey(), JSON.stringify(profiles.slice(0, 20)));
    byId("profile-name").value = name;
    renderProfiles();
    byId("profile-picker").value = name;
    syncFavoriteActions();
    showAlert("Favorite saved only in this browser for this signed-in owner.");
  } catch (error) {
    showAlert(error.message || "Favorite could not be saved.", true);
  }
}

function loadFavoriteProfile() {
  const name = byId("profile-picker").value;
  const profile = savedProfiles().find((item) => item.name === name);
  if (profile?.parameters) {
    applyParametersToForm(generationForm, profile.parameters);
    byId("profile-name").value = profile.name;
    syncFavoriteActions();
    showAlert(`Loaded favorite: ${name}.`);
    return;
  }
  byId("profile-name").value = "";
  syncFavoriteActions();
}

function updateFavoriteProfile() {
  try {
    const selected = selectedFavorite();
    if (!selected) throw new Error("Choose a favorite to update.");
    const name = byId("profile-name").value.trim() || selected.name;
    if (!name || name.length > 60) throw new Error("Name this favorite using up to 60 characters.");
    const parameters = validateParameters(parameterData(generationForm));
    if (name !== selected.name && savedProfiles().some((item) => item.name === name)) {
      throw new Error("A different favorite already uses that name. Choose a new name before updating.");
    }
    const profiles = savedProfiles().filter(
      (item) => item.name !== selected.name,
    );
    profiles.unshift({ name, parameters });
    window.localStorage.setItem(profileStorageKey(), JSON.stringify(profiles.slice(0, 20)));
    renderProfiles();
    byId("profile-picker").value = name;
    byId("profile-name").value = name;
    syncFavoriteActions();
    showAlert(`Favorite updated: ${name}.`);
  } catch (error) {
    showAlert(error.message || "Favorite could not be updated.", true);
  }
}

function deleteFavoriteProfile() {
  const selected = selectedFavorite();
  if (!selected) {
    showAlert("Choose a favorite to delete.", true);
    return;
  }
  if (!window.confirm(`Delete the favorite \"${selected.name}\" from this browser?`)) {
    return;
  }
  const profiles = savedProfiles().filter((item) => item.name !== selected.name);
  window.localStorage.setItem(profileStorageKey(), JSON.stringify(profiles));
  byId("profile-name").value = "";
  renderProfiles();
  showAlert(`Favorite deleted: ${selected.name}.`);
}

function weekdayText(days) {
  const labels = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
  return days.map((day) => labels[day]).join(" ");
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

function cloudClockEndpoint() {
  const raw = globalThis.TDN_CLOUD_CLOCK?.endpoint;
  if (typeof raw !== "string" || !raw.trim()) return null;
  try {
    const parsed = new URL(raw);
    if (
      parsed.protocol !== "https:"
      || !parsed.hostname.endsWith(".workers.dev")
      || parsed.username
      || parsed.password
      || parsed.search
      || parsed.hash
    ) {
      return null;
    }
    return parsed.origin;
  } catch (_error) {
    return null;
  }
}

function updateCloudClockStatus(message = "") {
  const chip = byId("cloud-clock-status");
  if (!chip) return;
  if (!appState.authorized) {
    chip.textContent = "CLOUD CLOCK // SIGN-IN REQUIRED";
    return;
  }
  if (!cloudClockEndpoint()) {
    chip.textContent = "CLOUD CLOCK // SETUP REQUIRED";
    return;
  }
  chip.textContent = message || appState.clockStatus || "CLOUD CLOCK // CONNECTING";
}

async function cloudClockRequest(path, { method = "POST", body } = {}) {
  const endpoint = cloudClockEndpoint();
  if (!endpoint) return null;
  if (!appState.user) throw new Error("Sign in before contacting the cloud clock.");
  const token = await appState.user.getIdToken();
  const response = await fetch(`${endpoint}${path}`, {
    method,
    mode: "cors",
    credentials: "omit",
    cache: "no-store",
    referrerPolicy: "no-referrer",
    headers: {
      authorization: `Bearer ${token}`,
      ...(body ? { "content-type": "application/json" } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  if (!response.ok) {
    throw new Error("The private cloud clock could not confirm this request.");
  }
  return response.json();
}

function clockProjection(scheduleId, data) {
  return {
    scheduleId,
    enabled: Boolean(data.enabled),
    timezone: String(data.timezone || "UTC"),
    startTime: String(data.startTime || ""),
    weekdays: [...(data.weekdays || [])].map(Number).sort((first, second) => first - second),
  };
}

function projectionSignature() {
  return JSON.stringify(
    [...appState.schedules.entries()]
      .map(([scheduleId, data]) => clockProjection(scheduleId, data))
      .sort((first, second) => first.scheduleId.localeCompare(second.scheduleId)),
  );
}

async function reconcileClockSchedules() {
  if (!appState.authorized || !appState.user || appState.clockSyncing) return;
  const signature = projectionSignature();
  if (signature === appState.clockProjectionSignature) return;
  appState.clockSyncing = true;
  try {
    const uid = appState.user.uid;
    const existing = await getDocs(
      collection(appState.db, "users", uid, "clockSchedules"),
    );
    const batch = writeBatch(appState.db);
    const wanted = new Set(appState.schedules.keys());
    for (const [scheduleId, data] of appState.schedules) {
      batch.set(
        doc(appState.db, "users", uid, "clockSchedules", scheduleId),
        { ...clockProjection(scheduleId, data), schemaVersion: 1 },
      );
    }
    for (const stale of existing.docs) {
      if (!wanted.has(stale.id)) {
        batch.delete(stale.ref);
      }
    }
    await batch.commit();
    appState.clockProjectionSignature = signature;
    await synchronizeCloudClock();
  } catch (error) {
    // A missing first rules deployment should not silently fall back forever.
    updateCloudClockStatus("CLOUD CLOCK // SYNC NEEDS ATTENTION");
    showAlert(error.message || firebaseErrorMessage(error), true);
  } finally {
    appState.clockSyncing = false;
  }
}

function queueClockReconciliation() {
  if (appState.clockSyncTimer) window.clearTimeout(appState.clockSyncTimer);
  appState.clockSyncTimer = window.setTimeout(() => {
    appState.clockSyncTimer = null;
    void reconcileClockSchedules();
  }, 250);
}

async function synchronizeCloudClock() {
  if (!cloudClockEndpoint()) {
    updateCloudClockStatus();
    return null;
  }
  const result = await cloudClockRequest("/v1/sync", {
    body: { schemaVersion: 1 },
  });
  appState.clockStatus = result?.nextAlarmAt
    ? `CLOUD CLOCK // NEXT ${timeText(new Date(result.nextAlarmAt))}`
    : "CLOUD CLOCK // NO ACTIVE SCHEDULE";
  updateCloudClockStatus();
  return result;
}

async function refreshCloudClockStatus() {
  if (!appState.authorized) {
    updateCloudClockStatus();
    return null;
  }
  if (!cloudClockEndpoint()) {
    updateCloudClockStatus();
    return null;
  }
  try {
    const result = await cloudClockRequest("/v1/status", { method: "GET" });
    appState.clockStatus = result?.nextAlarmAt
      ? `CLOUD CLOCK // NEXT ${timeText(new Date(result.nextAlarmAt))}`
      : "CLOUD CLOCK // NO ACTIVE SCHEDULE";
    updateCloudClockStatus();
    return result;
  } catch (_error) {
    updateCloudClockStatus("CLOUD CLOCK // UNAVAILABLE");
    return null;
  }
}

function renderSchedules(snapshot) {
  const container = byId("schedule-list");
  container.replaceChildren();
  appState.schedules.clear();
  for (const scheduleDocument of snapshot.docs) {
    appState.schedules.set(scheduleDocument.id, scheduleDocument.data());
  }
  queueClockReconciliation();
  byId("schedule-count").textContent = String(snapshot.size);
  if (snapshot.empty) {
    container.className = "schedule-list empty-state";
    container.textContent = "No schedules configured.";
    return;
  }
  container.className = "schedule-list";
  for (const scheduleDocument of snapshot.docs) {
    const data = scheduleDocument.data();
    const card = element("article", "schedule-item");
    card.append(element("h3", "", data.name || "Unnamed schedule"));
    const state = data.enabled ? "ENABLED" : "PAUSED";
    const sections = data.parameters?.sections?.length
      ? data.parameters.sections.join(" · ")
      : "AUTO SECTIONS";
    card.append(
      element(
        "p",
        "item-meta",
        `${state} // ${weekdayText(data.weekdays || [])} // ` +
          `${data.startTime || "--:--"} → READY ${data.readyBy || "--:--"}\n` +
          `${data.parameters?.runName || "UNNAMED RUN"} // ` +
          `${data.parameters?.gmailLabel || "NO LABEL"} // ${sections}`,
      ),
    );
    const actions = element("div", "item-actions");
    const enabled = element("label", "schedule-enable-toggle");
    const enabledInput = document.createElement("input");
    enabledInput.type = "checkbox";
    enabledInput.checked = Boolean(data.enabled);
    enabledInput.setAttribute("aria-label", `Enable ${data.name || "this schedule"}`);
    enabledInput.addEventListener("change", () => {
      void toggleScheduleEnabled(scheduleDocument.id, enabledInput);
    });
    enabled.append(enabledInput, document.createTextNode(" ENABLE"));
    const edit = element("button", "ghost-button", "EDIT");
    edit.type = "button";
    edit.addEventListener("click", () => editSchedule(scheduleDocument.id));
    const remove = element("button", "danger-button", "DELETE");
    remove.type = "button";
    remove.addEventListener("click", () => removeSchedule(scheduleDocument.id));
    actions.append(enabled, edit, remove);
    card.append(actions);
    container.append(card);
  }
}

async function toggleScheduleEnabled(scheduleId, checkbox) {
  const enabled = checkbox.checked;
  checkbox.disabled = true;
  try {
    const batch = writeBatch(appState.db);
    batch.set(
      doc(appState.db, "users", appState.user.uid, "schedules", scheduleId),
      { enabled, updatedAt: serverTimestamp() },
      { merge: true },
    );
    batch.set(
      doc(appState.db, "users", appState.user.uid, "clockSchedules", scheduleId),
      { enabled },
      { merge: true },
    );
    await batch.commit();
    appState.clockProjectionSignature = "";
    let clockSynchronized = true;
    try {
      await synchronizeCloudClock();
    } catch (_error) {
      clockSynchronized = false;
      updateCloudClockStatus("CLOUD CLOCK // SYNC NEEDS ATTENTION");
    }
    showAlert(
      clockSynchronized
        ? enabled
          ? "Schedule enabled and synchronized."
          : "Schedule paused and synchronized."
        : enabled
          ? "Schedule enabled, but Cloud Clock needs attention before it can run."
          : "Schedule paused, but Cloud Clock needs attention.",
      !clockSynchronized,
    );
  } catch (error) {
    checkbox.checked = !enabled;
    showAlert(firebaseErrorMessage(error), true);
  } finally {
    checkbox.disabled = false;
  }
}

function fillSelect(form, name, value) {
  const control = form.elements.namedItem(name);
  if (control) {
    control.value = value;
  }
}

function setupTimezonePicker() {
  const control = scheduleForm.elements.namedItem("timezone");
  if (!(control instanceof HTMLSelectElement)) return;
  const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  let zones = ["UTC"];
  try {
    if (typeof Intl.supportedValuesOf === "function") {
      zones.push(...Intl.supportedValuesOf("timeZone"));
    }
  } catch (_error) {
    zones.push("America/New_York", "Europe/London", "Asia/Tokyo");
  }
  zones.push(browserTimezone);
  zones = [...new Set(zones)].sort((first, second) => first.localeCompare(second));
  control.replaceChildren(
    ...zones.map((timezone) => {
      const option = document.createElement("option");
      option.value = timezone;
      option.textContent = timezone.replaceAll("_", " ");
      option.defaultSelected = timezone === browserTimezone;
      return option;
    }),
  );
  control.value = browserTimezone;
}

function editSchedule(scheduleId) {
  const data = appState.schedules.get(scheduleId);
  if (!data) {
    return;
  }
  scheduleForm.elements.scheduleId.value = scheduleId;
  scheduleForm.elements.name.value = data.name || "";
  scheduleForm.elements.startTime.value = data.startTime || "04:45";
  scheduleForm.elements.readyBy.value = data.readyBy || "06:00";
  fillSelect(scheduleForm, "timezone", data.timezone || "UTC");
  const selectedDays = new Set(data.weekdays || []);
  for (const checkbox of scheduleForm.querySelectorAll('input[name="weekday"]')) {
    checkbox.checked = selectedDays.has(Number(checkbox.value));
  }
  const parameters = data.parameters || {};
  scheduleForm.elements.runName.value = parameters.runName || data.name || "Morning Nexus";
  scheduleForm.elements.gmailLabel.value = parameters.gmailLabel || "";
  setSections(scheduleForm, parameters.sections || []);
  for (const name of [
    "hostCount",
    "soloName",
    "dialogueStyle",
    "primaryVoice",
    "primaryTone",
    "secondaryVoice",
    "secondaryTone",
    "dateMode",
    "editionScale",
    "evidenceMode",
  ]) {
    fillSelect(scheduleForm, name, parameters[name]);
  }
  scheduleForm.elements.enabled.checked = Boolean(data.enabled);
  scheduleForm.elements.includeTih.checked = parameters.includeTih !== false;
  syncHostControls(scheduleForm);
  byId("cancel-edit-button").hidden = false;
  scheduleForm.scrollIntoView({ behavior: "smooth", block: "start" });
}

function resetScheduleForm() {
  scheduleForm.reset();
  scheduleForm.elements.scheduleId.value = "";
  scheduleForm.elements.startTime.value = "04:45";
  scheduleForm.elements.readyBy.value = "06:00";
  for (const checkbox of scheduleForm.querySelectorAll('input[name="weekday"]')) {
    checkbox.checked = Number(checkbox.value) < 5;
  }
  byId("cancel-edit-button").hidden = true;
  setSections(scheduleForm, []);
  syncHostControls(scheduleForm);
}

async function saveSchedule(event) {
  event.preventDefault();
  try {
    const values = new FormData(scheduleForm);
    const weekdays = [...scheduleForm.querySelectorAll('input[name="weekday"]:checked')]
      .map((item) => Number(item.value))
      .sort();
    if (!weekdays.length) {
      throw new Error("Choose at least one weekday.");
    }
    const parameters = validateParameters(parameterData(scheduleForm));
    const existingId = String(values.get("scheduleId") || "");
    const scheduleId =
      existingId || `job-${crypto.randomUUID().replaceAll("-", "").slice(0, 20)}`;
    const existing = appState.schedules.get(scheduleId);
    const payload = {
      name: String(values.get("name") || "").trim(),
      enabled: values.get("enabled") === "on",
      timezone: String(values.get("timezone") || "UTC"),
      startTime: String(values.get("startTime") || ""),
      readyBy: String(values.get("readyBy") || ""),
      weekdays,
      parameters,
      schemaVersion: 1,
      createdAt: existing?.createdAt || serverTimestamp(),
      updatedAt: serverTimestamp(),
    };
    if (!payload.name || payload.name.length > 120) {
      throw new Error("Enter a schedule name up to 120 characters.");
    }
    if (
      !/^\d{2}:\d{2}$/.test(payload.startTime) ||
      !/^\d{2}:\d{2}$/.test(payload.readyBy) ||
      payload.startTime >= payload.readyBy
    ) {
      throw new Error("Start generation must be earlier than the ready-by time.");
    }
    const batch = writeBatch(appState.db);
    batch.set(
      doc(appState.db, "users", appState.user.uid, "schedules", scheduleId),
      payload,
      { merge: false },
    );
    batch.set(
      doc(appState.db, "users", appState.user.uid, "clockSchedules", scheduleId),
      { ...clockProjection(scheduleId, payload), schemaVersion: 1 },
      { merge: false },
    );
    await batch.commit();
    appState.clockProjectionSignature = "";
    let clockSynchronized = true;
    try {
      await synchronizeCloudClock();
    } catch (_error) {
      clockSynchronized = false;
      updateCloudClockStatus("CLOUD CLOCK // SYNC NEEDS ATTENTION");
    }
    resetScheduleForm();
    showAlert(
      !clockSynchronized
        ? "Schedule saved, but the cloud clock needs attention. It will not run until synchronization succeeds."
        : cloudClockEndpoint()
          ? "Schedule saved and synchronized to the private cloud clock."
          : "Schedule saved. Configure Cloud Clock before it can run.",
    );
  } catch (error) {
    showAlert(error.message || firebaseErrorMessage(error), true);
  }
}

async function removeSchedule(scheduleId) {
  if (!window.confirm("Delete this private schedule? This cannot be undone.")) {
    return;
  }
  try {
    const batch = writeBatch(appState.db);
    batch.delete(doc(appState.db, "users", appState.user.uid, "schedules", scheduleId));
    batch.delete(doc(appState.db, "users", appState.user.uid, "clockSchedules", scheduleId));
    await batch.commit();
    appState.clockProjectionSignature = "";
    let clockSynchronized = true;
    try {
      await synchronizeCloudClock();
    } catch (_error) {
      clockSynchronized = false;
      updateCloudClockStatus("CLOUD CLOCK // SYNC NEEDS ATTENTION");
    }
    showAlert(
      clockSynchronized
        ? "Schedule deleted and removed from the private cloud clock."
        : "Schedule deleted. The cloud clock still needs synchronization; reload the app after checking its status.",
      !clockSynchronized,
    );
  } catch (error) {
    showAlert(firebaseErrorMessage(error), true);
  }
}

async function queueGeneration(event) {
  event.preventDefault();
  try {
    const values = new FormData(generationForm);
    const requestedDate = String(values.get("requestedDate") || "");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(requestedDate)) {
      throw new Error("Choose an episode date.");
    }
    if (requestedDate > generationForm.elements.requestedDate.max) {
      throw new Error("Future dates are unavailable. Choose today or an earlier date.");
    }
    const parameters = validateParameters(parameterData(generationForm));
    parameters.dateMode = "today";
    await addDoc(
      collection(appState.db, "users", appState.user.uid, "runRequests"),
      {
        parameters,
        requestedDate,
        status: "queued",
        schemaVersion: 1,
        requestedAt: serverTimestamp(),
        updatedAt: serverTimestamp(),
      },
    );
    let wake = null;
    try {
      wake = await cloudClockRequest("/v1/wake", {
        body: { schemaVersion: 1 },
      });
    } catch (_error) {
      showAlert("Generation queued, but the cloud clock did not confirm a wake-up. Requeue it after checking Cloud Clock status.", true);
      return;
    }
    if (wake?.status === "dispatched") {
      showAlert("Generation queued. The private cloud clock dispatched the runner now.");
    } else if (wake?.status === "already-requested") {
      showAlert("Generation queued. A private runner wake-up is already in progress.");
    } else {
      showAlert("Generation queued. Cloud Clock setup is required before the private runner can start it.", true);
    }
  } catch (error) {
    showAlert(error.message || firebaseErrorMessage(error), true);
  }
}

function dateValue(value) {
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value;
  }
  if (value && typeof value.toDate === "function") {
    return value.toDate();
  }
  if (typeof value === "string") {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }
  return null;
}

function durationText(milliseconds) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return [hours, minutes, remainder].map((item) => String(item).padStart(2, "0")).join(":");
}

function timeText(value) {
  const parsed = dateValue(value);
  if (!parsed) {
    return "TIME PENDING";
  }
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(parsed);
}

function timestampText(value) {
  const parsed = dateValue(value);
  if (!parsed) return "PENDING";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(parsed).toUpperCase();
}

function updateRunnerDetail() {
  const detail = byId("runner-detail");
  const data = appState.runner;
  if (!data) {
    detail.textContent = "Awaiting the private cloud runner status.";
    return;
  }
  const state = String(data.state || "unknown").toLowerCase();
  if (state === "running") {
    const startedAt = dateValue(data.startedAt) || dateValue(data.checkedAt);
    const elapsed = startedAt ? Date.now() - startedAt.getTime() : 0;
    const remaining = Math.max(0, TYPICAL_RUN_MS - elapsed);
    detail.textContent = [
      `ACTIVE // ${data.activeTask || "PRIVATE GENERATION"}`,
      `ELAPSED ${durationText(elapsed)}`,
      `TYPICAL ${durationText(TYPICAL_RUN_MS)}`,
      `EST. ${durationText(remaining)} REMAINING`,
    ].join(" // ");
    return;
  }
  if (state === "error") {
    detail.textContent = `LAST RUN NEEDS ATTENTION // ${data.detail || "Open a new request after reviewing the private run."}`;
    return;
  }
  detail.textContent = [
    `LAST CLOUD CHECK ${timeText(data.checkedAt)}`,
    data.detail || "Private runner ready.",
    appState.monitorRefreshedAt ? `VIEW REFRESHED ${timeText(appState.monitorRefreshedAt)}` : "",
  ].filter(Boolean).join(" // ");
}

function renderRunRequests(snapshot) {
  appState.runRequests = snapshot.docs.map((item) => ({
    id: item.id,
    ...item.data(),
  }));
  renderRunRequestList();
}

function renderRunRequestList() {
  const container = byId("run-request-list");
  container.replaceChildren();
  const queued = appState.runRequests.filter((item) => item.status === "queued").length;
  const running = appState.runRequests.filter((item) => item.status === "running").length;
  byId("queue-count").textContent = running ? `${running} RUNNING // ${queued} QUEUED` : `${queued} QUEUED`;
  if (!appState.runRequests.length) {
    container.className = "terminal-list empty-state";
    container.textContent = "No queued tasks.";
    return;
  }
  container.className = "terminal-list";
  const dateFilter = byId("monitor-date-filter")?.value || "";
  const statusFilter = byId("monitor-status-filter")?.value || "";
  const queryFilter = (byId("monitor-query-filter")?.value || "").trim().toLocaleLowerCase();
  const sortMode = byId("monitor-sort-filter")?.value || "newest";
  const visible = appState.runRequests.filter((item) => {
    if (dateFilter && item.requestedDate !== dateFilter) return false;
    if (statusFilter && item.status !== statusFilter) return false;
    const searchable = `${item.parameters?.runName || ""} ${item.parameters?.gmailLabel || ""}`.toLocaleLowerCase();
    return !queryFilter || searchable.includes(queryFilter);
  });
  visible.sort((left, right) => {
    if (sortMode === "status") return String(left.status || "").localeCompare(String(right.status || ""));
    if (sortMode === "name") return String(left.parameters?.runName || "").localeCompare(String(right.parameters?.runName || ""));
    const leftTime = dateValue(left.updatedAt) || dateValue(left.requestedAt) || new Date(0);
    const rightTime = dateValue(right.updatedAt) || dateValue(right.requestedAt) || new Date(0);
    return sortMode === "oldest" ? leftTime - rightTime : rightTime - leftTime;
  });
  if (!visible.length) {
    container.className = "terminal-list empty-state";
    container.textContent = "No requests match these filters.";
    return;
  }
  for (const data of visible) {
    const card = element("article", "request-item");
    const status = String(data.status || "unknown").toUpperCase();
    const header = element("div", "request-header");
    header.append(
      element(
        "p",
        "item-meta",
        `${status} // ` +
          `${data.requestedDate || "NO DATE"} // ` +
          `${data.parameters?.runName || "UNNAMED RUN"} // ` +
          `${data.parameters?.gmailLabel || "NO LABEL"}`,
      ),
    );
    if (["expired", "failed"].includes(data.status)) {
      const actions = element("div", "request-actions");
      const retry = element("button", "ghost-button requeue-button", "REQUEUE");
      retry.type = "button";
      retry.addEventListener("click", () => requeueRequest(data.id));
      const remove = element("button", "danger-button requeue-button", "DELETE");
      remove.type = "button";
      remove.addEventListener("click", () => deleteRunRequest(data.id));
      actions.append(retry, remove);
      header.append(actions);
    }
    card.append(header);
    const timeline = [
      `QUEUED ${timestampText(data.requestedAt)}`,
      data.startedAt ? `STARTED ${timestampText(data.startedAt)}` : "",
      data.finishedAt ? `${status} ${timestampText(data.finishedAt)}` : "",
      !data.finishedAt && data.updatedAt ? `UPDATED ${timestampText(data.updatedAt)}` : "",
    ].filter(Boolean).join(" // ");
    card.append(element("p", "request-timeline", timeline));
    if (data.status === "running") {
      const startedAt = dateValue(data.startedAt);
      const elapsed = startedAt ? Date.now() - startedAt.getTime() : 0;
      card.append(
        element(
          "p",
          "request-detail",
          `RUNNING ${durationText(elapsed)} // TYPICAL ${durationText(TYPICAL_RUN_MS)}`,
        ),
      );
    } else if (data.status === "queued") {
      card.append(
        element(
          "p",
          "request-detail",
          cloudClockEndpoint()
            ? "QUEUED // PRIVATE CLOUD CLOCK WILL WAKE THE RUNNER"
            : "QUEUED // CLOUD CLOCK SETUP REQUIRED",
        ),
      );
    } else if (data.status === "published") {
      card.append(element("p", "request-detail", "PUBLISHED // AVAILABLE IN THE PRIVATE FEED"));
    } else if (data.detail) {
      card.append(element("p", "request-detail", String(data.detail)));
    }
    container.append(card);
  }
}

async function requeueRequest(requestId) {
  try {
    const original = appState.runRequests.find((item) => item.id === requestId);
    if (!original || !original.parameters || !/^\d{4}-\d{2}-\d{2}$/.test(String(original.requestedDate || ""))) {
      throw new Error("This request is incomplete and cannot be requeued safely.");
    }
    // A retry is a new immutable execution, not a status reset. This keeps the
    // original failure visible and prevents Cloud Clock from reclaiming it.
    await addDoc(
      collection(appState.db, "users", appState.user.uid, "runRequests"),
      {
        parameters: original.parameters,
        requestedDate: original.requestedDate,
        status: "queued",
        schemaVersion: 1,
        requestedAt: serverTimestamp(),
        updatedAt: serverTimestamp(),
      },
    );
    try {
      const wake = await cloudClockRequest("/v1/wake", {
        body: { schemaVersion: 1 },
      });
      showAlert(
        wake?.status === "dispatched"
          ? "Fresh retry queued. The private cloud clock dispatched the runner now."
          : wake?.status === "already-requested"
            ? "Fresh retry queued. A private runner wake-up is already in progress."
            : "Fresh retry queued. Cloud Clock setup is required before the private runner can start it.",
      );
    } catch (_error) {
      showAlert("Fresh retry queued, but the cloud clock did not confirm a wake-up. Check its status, then requeue again.", true);
    }
  } catch (error) {
    showAlert(firebaseErrorMessage(error), true);
  }
}

async function deleteRunRequest(requestId) {
  if (!window.confirm("Remove this failed or expired request from the process monitor? This does not delete an episode or its published feed entry.")) {
    return;
  }
  try {
    const batch = writeBatch(appState.db);
    batch.delete(doc(appState.db, "users", appState.user.uid, "runRequests", requestId));
    await batch.commit();
    showAlert("Request removed from the process monitor.");
  } catch (error) {
    showAlert(firebaseErrorMessage(error), true);
  }
}

function safePrivateURL(value, extension) {
  if (typeof value !== "string") {
    return null;
  }
  try {
    const url = new URL(value);
    const allowedHost = appState.firebaseHosts.has(url.hostname);
    if (
      url.protocol !== "https:" ||
      !allowedHost ||
      !url.pathname.startsWith("/p/") ||
      !url.pathname.toLowerCase().endsWith(extension)
    ) {
      return null;
    }
    return url.href;
  } catch {
    return null;
  }
}

function renderEpisodes(snapshot) {
  appState.episodeRecords = snapshot.docs.map((episodeDocument) => ({
    id: episodeDocument.id,
    ...episodeDocument.data(),
  }));
  renderEpisodeArchives();
}

function sortedEpisodeRecords(mode, sourceRecords = appState.episodeRecords || []) {
  const records = [...sourceRecords];
  return records.sort((left, right) => {
    if (mode === "title") return String(left.title || "").localeCompare(String(right.title || ""));
    if (mode === "duration") return Number(right.durationMinutes || 0) - Number(left.durationMinutes || 0);
    const comparison = String(left.episodeDate || "").localeCompare(String(right.episodeDate || ""));
    return mode === "oldest" ? comparison : -comparison;
  });
}

function archiveDateTitle(episodeDate) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(episodeDate || ""));
  return match ? `${match[2]}/${match[3]}/${match[1]}` : "Unknown date";
}

function storedPublicationTitle(record) {
  const title = String(record.title || "").trim();
  if (/^\d{2}\/\d{2}\/\d{4}\s+The Daily Nexus\s+-\s+.+\s+-\s+\d{3}$/i.test(title)) {
    return title;
  }
  return "";
}

function archivePublicationLabel(record) {
  const label = String(record.publicationLabel || "").trim();
  return label || "Archive";
}

function archiveRecordOrder(left, right) {
  const sequenceDifference = Number(left.publicationSequence || 0) - Number(right.publicationSequence || 0);
  if (sequenceDifference) return sequenceDifference;
  const leftTime = dateValue(left.publishedAt) || dateValue(left.updatedAt) || dateValue(left.createdAt) || new Date(0);
  const rightTime = dateValue(right.publishedAt) || dateValue(right.updatedAt) || dateValue(right.createdAt) || new Date(0);
  if (leftTime.getTime() !== rightTime.getTime()) return leftTime - rightTime;
  return String(left.id).localeCompare(String(right.id));
}

function episodeDisplayTitles(records) {
  const groups = new Map();
  for (const record of records) {
    const standardTitle = storedPublicationTitle(record);
    if (standardTitle) {
      groups.set(`stored\u0000${record.id}`, [record]);
      continue;
    }
    const key = `${record.episodeDate || ""}\u0000${archivePublicationLabel(record)}`;
    const group = groups.get(key) || [];
    group.push(record);
    groups.set(key, group);
  }
  const labels = new Map();
  for (const recordsForTitle of groups.values()) {
    const standardTitle = storedPublicationTitle(recordsForTitle[0]);
    if (standardTitle) {
      labels.set(String(recordsForTitle[0].id), standardTitle);
      continue;
    }
    recordsForTitle
      .slice()
      .sort(archiveRecordOrder)
      .forEach((record, index) => {
        const sequence = Number.isInteger(Number(record.publicationSequence)) && Number(record.publicationSequence) > 0
          ? Number(record.publicationSequence)
          : index + 1;
        labels.set(
          String(record.id),
          `${archiveDateTitle(record.episodeDate)} The Daily Nexus - ${archivePublicationLabel(record)} - ${String(sequence).padStart(3, "0")}`,
        );
      });
  }
  return labels;
}

function renderEpisodeArchives() {
  const play = byId("episode-list");
  const read = byId("edition-list");
  play.replaceChildren();
  read.replaceChildren();
  if (!appState.episodeRecords?.length) {
    appState.episodes = [];
    play.className = "episode-list empty-state";
    read.className = "edition-list empty-state";
    play.textContent = "No synchronized episodes are available yet.";
    read.textContent = "No synchronized editions are available yet.";
    return;
  }
  play.className = "episode-list";
  read.className = "edition-list";
  appState.episodes = [];
  // Each Firestore document represents an intentional edition. Historical
  // collisions are repaired at the media URL level; never hide an edition here.
  const playableRecords = appState.episodeRecords;
  const readableRecords = appState.episodeRecords;
  const playTitles = episodeDisplayTitles(playableRecords);
  const readTitles = episodeDisplayTitles(readableRecords);
  for (const data of sortedEpisodeRecords(
    byId("episode-sort")?.value || "newest",
    playableRecords,
  )) {
    const episodeId = String(data.id);
    const title = playTitles.get(episodeId) || data.title || `The Daily Nexus // ${data.episodeDate || ""}`;
    const audioURL = safePrivateURL(data.audioUrl, ".mp3");
    const pdfURL = safePrivateURL(data.newspaperUrl, ".pdf");
    const episode = audioURL ? {
      id: episodeId,
      title,
      audioURL,
      episodeDate: data.episodeDate,
      references: Array.isArray(data.references) ? data.references : [],
      transcript: Array.isArray(data.transcript) ? data.transcript : [],
      sourceMix: data.sourceMix && typeof data.sourceMix === "object" ? data.sourceMix : {},
    } : null;
    if (episode) appState.episodes.push(episode);

    const playCard = element("article", "episode-list-item");
    playCard.tabIndex = audioURL ? 0 : -1;
    playCard.setAttribute("role", audioURL ? "button" : "article");
    if (audioURL) playCard.dataset.episodeId = episodeId;
    playCard.classList.toggle("selected", appState.activeEpisode?.id === episodeId);
    playCard.append(element("h3", "", title));
    playCard.append(
      element(
        "p",
        "item-meta",
        `${data.episodeDate || "UNKNOWN DATE"} // ${data.durationMinutes || "—"} MIN`,
      ),
    );
    if (audioURL) {
      const selectEpisodeCard = () => selectEpisode(episode);
      playCard.addEventListener("click", selectEpisodeCard);
      playCard.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectEpisodeCard();
        }
      });
    } else {
      playCard.append(element("p", "item-meta", "AUDIO URL NOT SYNCHRONIZED"));
    }
    play.append(playCard);
  }

  for (const data of sortedEpisodeRecords(
    byId("edition-sort")?.value || "newest",
    readableRecords,
  )) {
    const editionId = String(data.id);
    const title = readTitles.get(editionId) || data.title || `The Daily Nexus // ${data.episodeDate || ""}`;
    const pdfURL = data.status === "published"
      ? safePrivateURL(data.newspaperUrl, ".pdf")
      : null;
    const readCard = element("article", "edition-list-item");
    readCard.tabIndex = pdfURL ? 0 : -1;
    readCard.setAttribute("role", pdfURL ? "button" : "article");
    readCard.dataset.editionId = editionId;
    readCard.classList.toggle("selected", appState.activeEdition?.id === editionId);
    readCard.append(element("h3", "", title));
    readCard.append(
      element(
        "p",
        "item-meta",
        `${data.episodeDate || "UNKNOWN DATE"} // EXECUTIVE EDITION`,
      ),
    );
    if (pdfURL) {
      const selectEditionCard = () => selectEdition({ id: editionId, title, url: pdfURL });
      readCard.addEventListener("click", selectEditionCard);
      readCard.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectEditionCard();
        }
      });
    } else {
      readCard.append(element("p", "item-meta", "PDF URL NOT SYNCHRONIZED"));
    }
    read.append(readCard);
  }
}

function renderRunner(snapshot) {
  if (!snapshot.exists()) {
    appState.runner = null;
    byId("runner-status").textContent = "RUNNER NOT PAIRED";
    byId("runner-status").parentElement.classList.remove("running", "error");
    updateRunnerDetail();
    return;
  }
  const data = snapshot.data();
  appState.runner = data;
  const stateValue = String(data.state || "unknown").toLowerCase();
  const state = stateValue.toUpperCase();
  byId("runner-status").textContent = stateValue === "idle" ? "RUNNER AVAILABLE" : `RUNNER ${state}`;
  byId("runner-status").parentElement.classList.toggle("running", stateValue === "running");
  byId("runner-status").parentElement.classList.toggle("error", stateValue === "error");
  updateRunnerDetail();
}

async function refreshMonitor() {
  if (!appState.authorized || !appState.user) {
    return;
  }
  const button = byId("refresh-monitor-button");
  button.disabled = true;
  button.textContent = "REFRESHING";
  try {
    const uid = appState.user.uid;
    const [runner, requests] = await Promise.all([
      getDoc(doc(appState.db, "users", uid, "runner", "status")),
      getDocs(
        query(
          collection(appState.db, "users", uid, "runRequests"),
          orderBy("requestedAt", "desc"),
          limit(20),
        ),
      ),
    ]);
    renderRunner(runner);
    renderRunRequests(requests);
    appState.monitorRefreshedAt = new Date();
    showAlert("Private runner status refreshed.");
  } catch (error) {
    showAlert(firebaseErrorMessage(error), true);
  } finally {
    button.disabled = false;
    button.textContent = "REFRESH";
  }
}

function formatPlaybackTime(seconds) {
  const total = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

function setRangeProgress(control, value = control.value) {
  const progress = Math.max(0, Math.min(100, Number(value) || 0));
  control.style.setProperty("--progress", `${progress}%`);
}

function syncPlayer() {
  const audio = byId("episode-audio");
  const duration = audio.duration || 0;
  const current = audio.currentTime || 0;
  const progress = duration ? String((current / duration) * 100) : "0";
  byId("episode-progress").value = progress;
  setRangeProgress(byId("episode-progress"), progress);
  const playbackText = `${formatPlaybackTime(current)} / ${formatPlaybackTime(duration)}`;
  byId("player-time").textContent = playbackText;
  byId("player-current").textContent = formatPlaybackTime(current);
  byId("player-total").textContent = formatPlaybackTime(duration);
  byId("player-pause-button").classList.toggle("active", !audio.paused && !audio.ended);
  byId("player-pause-button").setAttribute("aria-label", audio.paused ? "Resume" : "Pause");
  byId("mini-player-progress").value = progress;
  setRangeProgress(byId("mini-player-progress"), progress);
  byId("mini-player-current").textContent = formatPlaybackTime(current);
  byId("mini-player-total").textContent = formatPlaybackTime(duration);
  byId("mini-pause-button").classList.toggle("active", !audio.paused && !audio.ended);
  byId("web-player").classList.toggle("playing", !audio.paused && !audio.ended);
  byId("mini-player").classList.toggle("playing", !audio.paused && !audio.ended);
  if (appState.playerDetailMode === "transcript") {
    const currentMs = audio.currentTime * 1000;
    let active = null;
    for (const segment of document.querySelectorAll(".transcript-segment")) {
      const start = Number(segment.dataset.startMs);
      const next = Number(segment.dataset.nextStartMs);
      const isActive = Number.isFinite(start) && start <= currentMs && (!Number.isFinite(next) || currentMs < next);
      segment.classList.toggle("active", isActive);
      if (isActive) active = segment;
    }
    active?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

function safeReference(value) {
  if (typeof value !== "string") return null;
  const match = value.match(/https:\/\/[^\s)]+/i);
  if (!match) return null;
  const url = match[0].replace(/[.,;:!?]+$/, "");
  const description = value
    .slice(0, match.index)
    .replace(/[\s\-–—|:]+$/, "")
    .trim();
  let fallback = "Open source";
  try {
    fallback = new URL(url).hostname.replace(/^www\./i, "");
  } catch {
    // The URL has already passed the private-host safety check upstream.
  }
  return { text: description || fallback, url };
}

function renderPlayerDetails(mode = "references") {
  appState.playerDetailMode = mode;
  const container = byId("player-details");
  const episode = appState.activeEpisode;
  container.replaceChildren();
  if (!episode) {
    container.className = "player-details empty-state";
    container.textContent = "Load an episode to view references.";
    return;
  }
  container.className = "player-details";
  if (mode === "references") {
    const refs = episode.references.map(safeReference).filter(Boolean);
    const mix = episode.sourceMix || {};
    if (Object.keys(mix).length) {
      const modeLabel = mix.mode === "newsletter_only" ? "NEWSLETTER ONLY" : "NEWSLETTER FIRST";
      const summary = `${modeLabel} // ${mix.newsletter_messages || 0} NEWSLETTERS // ${mix.newsletter_backed_stories || 0} NEWSLETTER STORIES // ${mix.safe_articles_retrieved || 0} SAFE ARTICLES // ${mix.research_sources || 0} RESEARCH SOURCES`;
      container.append(element("p", "item-meta evidence-mix", summary));
    }
    if (!refs.length) {
      container.className = "player-details empty-state";
      container.textContent = "References will appear for newly synchronized episodes.";
      return;
    }
    for (const reference of refs) {
      const link = element("a", "reference-link", reference.text);
      link.href = reference.url;
      link.title = reference.url;
      link.setAttribute("aria-label", `${reference.text}. Open source: ${reference.url}`);
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      container.append(link);
    }
    return;
  }
  if (!episode.transcript.length) {
    container.className = "player-details empty-state";
    container.textContent = "Transcript will appear for newly synchronized episodes.";
    return;
  }
  for (const [index, segment] of episode.transcript.entries()) {
    const button = element("button", "transcript-segment", `${segment.host || "HOST"} // ${segment.text || ""}`);
    button.type = "button";
    button.dataset.startMs = String(segment.startMs || 0);
    const nextStart = episode.transcript[index + 1]?.startMs;
    if (Number.isFinite(Number(nextStart))) button.dataset.nextStartMs = String(nextStart);
    button.addEventListener("click", () => {
      const audio = byId("episode-audio");
      if (Number.isFinite(Number(segment.startMs))) audio.currentTime = Number(segment.startMs) / 1000;
      syncPlayer();
    });
    container.append(button);
  }
}

function selectEpisode(episode) {
  const audio = byId("episode-audio");
  audio.pause();
  audio.src = episode.audioURL;
  audio.load();
  appState.activeEpisode = episode;
  appState.activeAudio = audio;
  byId("player-title").textContent = episode.title;
  byId("mini-player-title").textContent = episode.title;
  byId("player-play-button").disabled = false;
  byId("player-pause-button").disabled = false;
  byId("player-stop-button").disabled = false;
  byId("player-previous-button").disabled = false;
  byId("player-next-button").disabled = false;
  for (const card of document.querySelectorAll(".episode-list-item")) {
    card.classList.toggle("selected", card.dataset.episodeId === episode.id);
  }
  byId("mini-player").hidden = false;
  renderPlayerDetails();
  syncPlayer();
}

function selectRelativeEpisode(direction) {
  const index = appState.episodes.findIndex((item) => item.id === appState.activeEpisode?.id);
  if (index < 0 || !appState.episodes.length) return;
  const target = appState.episodes[(index + direction + appState.episodes.length) % appState.episodes.length];
  const wasPlaying = !byId("episode-audio").paused;
  selectEpisode(target);
  if (wasPlaying) byId("episode-audio").play().catch(() => {});
}

function selectEdition(edition) {
  const { id, title, url } = edition;
  appState.activeEdition = edition;
  for (const card of document.querySelectorAll(".edition-list-item")) {
    card.classList.toggle("selected", card.dataset.editionId === id);
  }
  byId("edition-title").textContent = title;
  const pages = byId("edition-pages");
  const link = byId("edition-pdf-link");
  pages.replaceChildren();
  pages.className = "edition-pages";
  link.href = url;
  link.hidden = false;
  let firstPageLoaded = false;
  const previewRequest = Date.now().toString(36);
  for (const number of [1, 2, 3]) {
    const preview = element("img", "edition-page");
    preview.alt = `${title}, page ${number}`;
    const previewURL = new URL(url);
    previewURL.pathname = previewURL.pathname.replace(/\.pdf$/i, `-${number}.png`);
    previewURL.searchParams.set("_tdn_preview", `${previewRequest}-${number}`);
    preview.src = previewURL.href;
    preview.addEventListener("load", () => {
      if (number !== 1 || firstPageLoaded) return;
      firstPageLoaded = true;
      link.href = url;
      link.hidden = false;
    });
    preview.addEventListener("error", () => {
      preview.remove();
      if (number === 1 && !firstPageLoaded) {
        pages.className = "edition-pages empty-state";
        pages.textContent = "The in-app preview is unavailable for this edition. Use OPEN PDF to read the original.";
      }
    });
    pages.append(preview);
  }
  applyEditionZoom();
}

function applyEditionZoom() {
  const pages = byId("edition-pages");
  const percent = Math.round(appState.editionZoom * 100);
  byId("edition-zoom-value").textContent = `${percent}%`;
  byId("edition-zoom-out").disabled = appState.editionZoom <= EDITION_ZOOM_STEPS[0];
  byId("edition-zoom-in").disabled = appState.editionZoom >= EDITION_ZOOM_STEPS[EDITION_ZOOM_STEPS.length - 1];
  for (const preview of pages.querySelectorAll(".edition-page")) {
    preview.style.width = `${percent}%`;
    preview.style.maxWidth = `${58 * appState.editionZoom}rem`;
  }
}

function adjustEditionZoom(direction) {
  const current = EDITION_ZOOM_STEPS.indexOf(appState.editionZoom);
  const next = Math.max(0, Math.min(EDITION_ZOOM_STEPS.length - 1, current + direction));
  appState.editionZoom = EDITION_ZOOM_STEPS[next];
  applyEditionZoom();
}

function setupWebPlayer() {
  const audio = byId("episode-audio");
  const togglePlayback = async () => {
    if (!audio.src) return;
    if (audio.paused) await audio.play(); else audio.pause();
  };
  const play = () => audio.src && audio.play().catch(() => showAlert("Playback could not start in this browser.", true));
  const stop = () => { audio.pause(); audio.currentTime = 0; syncPlayer(); };
  byId("player-play-button").addEventListener("click", play);
  byId("player-pause-button").addEventListener("click", () => { togglePlayback().catch(() => showAlert("Playback could not start in this browser.", true)); });
  byId("mini-play-button").addEventListener("click", play);
  byId("mini-pause-button").addEventListener("click", () => { togglePlayback().catch(() => showAlert("Playback could not start in this browser.", true)); });
  byId("player-stop-button").addEventListener("click", stop);
  byId("mini-stop-button").addEventListener("click", stop);
  byId("player-previous-button").addEventListener("click", () => selectRelativeEpisode(-1));
  byId("player-next-button").addEventListener("click", () => selectRelativeEpisode(1));
  byId("mini-previous-button").addEventListener("click", () => selectRelativeEpisode(-1));
  byId("mini-next-button").addEventListener("click", () => selectRelativeEpisode(1));
  audio.preservesPitch = true;
  audio.webkitPreservesPitch = true;
  for (const id of ["player-volume", "mini-volume"]) byId(id).addEventListener("input", (event) => {
    audio.volume = Number(event.target.value) / 100;
    for (const output of ["player-volume-value", "mini-volume-value"]) byId(output).textContent = `${event.target.value}%`;
    for (const control of ["player-volume", "mini-volume"]) byId(control).value = event.target.value;
  });
  for (const id of ["player-speed", "mini-speed"]) byId(id).addEventListener("input", (event) => {
    const speed = Number(event.target.value) / 100;
    audio.playbackRate = speed;
    const label = `${speed.toFixed(2).replace(/0$/, "")}x`;
    for (const output of ["player-speed-value", "mini-speed-value"]) byId(output).textContent = label;
    for (const control of ["player-speed", "mini-speed"]) byId(control).value = event.target.value;
  });
  for (const mode of ["references", "transcript"]) {
    byId(`show-${mode}`).addEventListener("click", () => {
      byId("show-references").classList.toggle("active", mode === "references");
      byId("show-transcript").classList.toggle("active", mode === "transcript");
      renderPlayerDetails(mode);
      syncPlayer();
    });
  }
  byId("edition-zoom-out").addEventListener("click", () => adjustEditionZoom(-1));
  byId("edition-zoom-in").addEventListener("click", () => adjustEditionZoom(1));
  applyEditionZoom();
  const seekFromPointer = (control, event) => {
    if (!audio.duration) return;
    const bounds = control.getBoundingClientRect();
    if (!bounds.width) return;
    const ratio = Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width));
    const value = String(ratio * 100);
    control.value = value;
    setRangeProgress(control, value);
    audio.currentTime = ratio * audio.duration;
  };
  for (const control of [byId("episode-progress"), byId("mini-player-progress")]) {
    let dragPointerId = null;
    control.addEventListener("input", () => {
      setRangeProgress(control);
      if (audio.duration) audio.currentTime = (Number(control.value) / 100) * audio.duration;
    });
    control.addEventListener("pointerdown", (event) => {
      if (!audio.duration) return;
      dragPointerId = event.pointerId;
      control.setPointerCapture?.(event.pointerId);
      seekFromPointer(control, event);
    });
    control.addEventListener("pointermove", (event) => {
      if (!audio.duration) return;
      const bounds = event.currentTarget.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width));
      event.currentTarget.title = `Seek to ${formatPlaybackTime(ratio * audio.duration)}`;
      if (event.pointerId === dragPointerId) seekFromPointer(control, event);
    });
    for (const eventName of ["pointerup", "pointercancel"]) {
      control.addEventListener(eventName, (event) => {
        if (event.pointerId !== dragPointerId) return;
        dragPointerId = null;
        if (control.hasPointerCapture?.(event.pointerId)) {
          control.releasePointerCapture(event.pointerId);
        }
      });
    }
  }
  for (const eventName of ["timeupdate", "loadedmetadata", "play", "pause", "ended"]) {
    audio.addEventListener(eventName, syncPlayer);
  }
}

function subscribeToPrivateData(uid) {
  clearSubscriptions();
  appState.subscriptions.push(
    onSnapshot(
      query(
        collection(appState.db, "users", uid, "schedules"),
        orderBy("name"),
        limit(100),
      ),
      renderSchedules,
      (error) => showAlert(firebaseErrorMessage(error), true),
    ),
    onSnapshot(
      query(
        collection(appState.db, "users", uid, "runRequests"),
        orderBy("requestedAt", "desc"),
        limit(100),
      ),
      renderRunRequests,
      (error) => showAlert(firebaseErrorMessage(error), true),
    ),
    onSnapshot(
      query(
        collection(appState.db, "users", uid, "episodes"),
        orderBy("episodeDate", "desc"),
        limit(100),
      ),
      renderEpisodes,
      (error) => showAlert(firebaseErrorMessage(error), true),
    ),
    onSnapshot(
      doc(appState.db, "users", uid, "runner", "status"),
      renderRunner,
      (error) => showAlert(firebaseErrorMessage(error), true),
    ),
  );
}

function setupNavigation() {
  for (const button of document.querySelectorAll(".mode-button")) {
    button.addEventListener("click", () => {
      for (const item of document.querySelectorAll(".mode-button")) {
        item.classList.toggle("active", item === button);
      }
      for (const view of document.querySelectorAll(".view")) {
        view.classList.toggle("active", view.id === `view-${button.dataset.view}`);
      }
      byId("mini-player").classList.toggle("context-hidden", button.dataset.view === "play");
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }
}

function setupInstall() {
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    appState.installPrompt = event;
    byId("install-button").hidden = false;
  });
  byId("install-button").addEventListener("click", async () => {
    if (appState.installPrompt) {
      appState.installPrompt.prompt();
      await appState.installPrompt.userChoice;
      appState.installPrompt = null;
      return;
    }
    showAlert("On iPhone: open Safari, tap Share, then Add to Home Screen.");
  });
}

function setupActivityTracking() {
  for (const eventName of ["pointerdown", "keydown", "touchstart"]) {
    window.addEventListener(eventName, resetIdleTimer, { passive: true });
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      checkIdleTimer();
    }
  });
  window.setInterval(checkIdleTimer, 1000);
  window.setInterval(() => {
    if (appState.authorized && appState.runner?.state === "running") {
      updateRunnerDetail();
      renderRunRequestList();
    }
  }, 1000);
}

async function initialize() {
  setupNavigation();
  setupInstall();
  setupActivityTracking();
  setupTimezonePicker();
  byId("sign-in-button").addEventListener("click", signInUser);
  byId("sign-out-button").addEventListener("click", () => signOutUser());
  byId("refresh-monitor-button").addEventListener("click", refreshMonitor);
  byId("cancel-edit-button").addEventListener("click", resetScheduleForm);
  for (const id of ["monitor-date-filter", "monitor-status-filter", "monitor-query-filter", "monitor-sort-filter"]) {
    byId(id).addEventListener("input", renderRunRequestList);
    byId(id).addEventListener("change", renderRunRequestList);
  }
  for (const id of ["episode-sort", "edition-sort"]) {
    byId(id).addEventListener("change", renderEpisodeArchives);
  }
  scheduleForm.addEventListener("submit", saveSchedule);
  generationForm.addEventListener("submit", queueGeneration);
  setupWebPlayer();
  byId("save-profile-button").addEventListener("click", saveFavoriteProfile);
  byId("update-profile-button").addEventListener("click", updateFavoriteProfile);
  byId("delete-profile-button").addEventListener("click", deleteFavoriteProfile);
  byId("profile-picker").addEventListener("change", loadFavoriteProfile);
  for (const form of [generationForm, scheduleForm]) {
    setupSectionEditor(form);
    form.elements.hostCount.addEventListener("change", () => syncHostControls(form));
    form.elements.soloName.addEventListener("change", () => syncHostControls(form));
    syncHostControls(form);
  }
  const localToday = new Date();
  localToday.setMinutes(localToday.getMinutes() - localToday.getTimezoneOffset());
  generationForm.elements.requestedDate.max = localToday.toISOString().slice(0, 10);
  generationForm.elements.requestedDate.value = generationForm.elements.requestedDate.max;

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/service-worker.js").catch(() => {
      // Authentication still works if offline-shell registration is unavailable.
    });
  }

  try {
    const response = await fetch("/__/firebase/init.json", {
      cache: "no-store",
      credentials: "same-origin",
    });
    if (!response.ok) {
      throw new Error("Firebase web configuration is unavailable.");
    }
    const firebaseConfig = await response.json();
    if (
      typeof firebaseConfig.projectId !== "string" ||
      !/^[a-z][a-z0-9-]{4,28}[a-z0-9]$/.test(firebaseConfig.projectId)
    ) {
      throw new Error("Firebase returned an invalid project identity.");
    }
    appState.firebaseHosts = new Set([
      `${firebaseConfig.projectId}.web.app`,
      `${firebaseConfig.projectId}.firebaseapp.com`,
    ]);
    if (!appState.firebaseHosts.has(window.location.hostname)) {
      throw new Error("The app is not running on its dedicated Firebase origin.");
    }
    if (appState.firebaseHosts.has(window.location.hostname)) {
      // Keep redirect authentication on the same trusted Hosting origin. This
      // avoids modern browsers blocking Firebase's cross-site redirect storage.
      firebaseConfig.authDomain = window.location.hostname;
    }
    const firebaseApp = initializeApp(firebaseConfig);
    appState.auth = getAuth(firebaseApp);
    appState.db = getFirestore(firebaseApp);
    await setPersistence(appState.auth, browserSessionPersistence);
    await getRedirectResult(appState.auth);
    onAuthStateChanged(appState.auth, async (user) => {
      if (!user) {
        showAuth();
        return;
      }
      setAuthStatus("Verifying private owner access…");
      try {
        await verifyOwner(user);
        showApp(user);
      } catch (error) {
        showAuth();
        setAuthStatus(error.message || firebaseErrorMessage(error), true);
      }
    });
  } catch (error) {
    setAuthStatus(
      "The secure web configuration is not ready. Complete the V4 Firebase setup first.",
      true,
    );
    byId("sign-in-button").disabled = true;
  }
}

initialize();
