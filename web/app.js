import { initializeApp } from "https://www.gstatic.com/firebasejs/12.19.0/firebase-app.js";
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
} from "https://www.gstatic.com/firebasejs/12.19.0/firebase-auth.js";
import {
  addDoc,
  collection,
  doc,
  getDoc,
  getDocFromServer,
  getDocs,
  getFirestore,
  limit,
  onSnapshot,
  orderBy,
  query,
  serverTimestamp,
  startAfter,
  setDoc,
  writeBatch,
} from "https://www.gstatic.com/firebasejs/12.19.0/firebase-firestore.js";

const IDLE_LIMIT_MS = 15 * 60 * 1000;
const SESSION_MAX_MS = 60 * 60 * 1000;
const CONSOLE_SESSION_KEY = "tdn-console-session-v1";
const ARCHIVE_PAGE_SIZE = 100;
const EDITION_ZOOM_STEPS = Object.freeze([0.75, 1, 1.25, 1.5, 1.75]);
// One allowlist for GEN, SCHED and favorites; timing and identity are not parameters.
const GENERATION_PARAMETER_DEFAULTS = Object.freeze({
  runName: "", gmailLabel: "", hostCount: 1, soloName: "Dalia",
  dialogueStyle: "broadcast", primaryVoice: "af_heart", primaryTone: "warm",
  secondaryVoice: "am_michael", secondaryTone: "dry_wit", dateMode: "today",
  includeTih: true, includeNewspaper: true, editionScale: "standard", evidenceMode: "newsletter_first",
});
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
  authEpoch: 0,
  schedules: new Map(),
  subscriptions: [],
  idleAt: Date.now() + IDLE_LIMIT_MS,
  installPrompt: null,
  firebaseHosts: new Set(),
  runRequests: [],
  runRequestRows: new Map(),
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
  dismissAlert();
  byId("global-alert-message").textContent = message;
  globalAlert.setAttribute("role", isError ? "alert" : "status");
  globalAlert.style.background = isError ? "#4b160c" : "#2d190e";
  globalAlert.style.borderColor = isError ? "var(--error)" : "var(--line)";
  globalAlert.hidden = false;
  if (!isError) {
    const version = appState.alertVersion;
    appState.alertTimer = window.setTimeout(() => {
      if (version === appState.alertVersion) dismissAlert();
    }, 7000);
  }
}

function dismissAlert() {
  window.clearTimeout(appState.alertTimer);
  appState.alertTimer = null;
  appState.alertVersion = (appState.alertVersion || 0) + 1;
  byId("global-alert-message").textContent = "";
  globalAlert.hidden = true;
}

function clearSubscriptions() {
  for (const unsubscribe of appState.subscriptions) {
    unsubscribe();
  }
  appState.subscriptions = [];
  appState.schedules.clear();
}

function clearPrivateInterface() {
  clearPlaybackSession();
  clearFavoriteSession();
  clearConsoleSession();
  clearPrivateForms();
  appState.authorized = false;
  clearSubscriptions();
  const audio = byId("episode-audio");
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  byId("player-details").replaceChildren();
  byId("edition-pages").replaceChildren();
  byId("edition-pdf-link").removeAttribute("href");
  byId("edition-pdf-link").hidden = true;
  clearEditionControls();
  byId("mini-player").hidden = true;
  appState.episodeRecords = [];
  appState.olderEpisodes = new Map();
  appState.archiveCursor = null;
  appState.archiveHasMore = false;
  appState.episodes = [];
  appState.lastTranscriptSegment = null;
  appState.readerToken = null;
  byId("player-title").textContent = "SELECT AN EPISODE";
  byId("mini-player-title").textContent = "THE DAILY NEXUS";
  byId("mini-player-edition").textContent = "";
  byId("edition-title").textContent = "SELECT AN EDITION";
  appState.archiveLoading = false;
  updateArchiveButtons();
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
  byId("monitor-scope").textContent = "LATEST 100 REQUEST WINDOW";
  for (const id of ["monitor-date-filter", "monitor-status-filter", "monitor-query-filter"]) byId(id).value = "";
  byId("monitor-sort-filter").value = "newest";
  byId("runner-status").textContent = "RUNNER STATUS UNKNOWN";
  byId("runner-status").parentElement.classList.remove("running", "error");
  byId("runner-detail").textContent = "Awaiting the private cloud runner status.";
  appState.runRequests = [];
  appState.runRequestRows = new Map();
  appState.wakeStates = new Map();
  appState.wakePromise = null;
  appState.generationSubmitting = false;
  appState.requeuingRequests = new Set();
  appState.runner = null;
  appState.resourceProfile = null;
  appState.resourceReadUnavailable = false;
  byId("resource-summary").replaceChildren();
  appState.monitorRefreshedAt = null;
  appState.activeEpisode = null;
  appState.activeEdition = null;
  appState.audioSelectionToken = null;
  appState.editionSelectionToken = null;
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
  setAuthStatus("Sign in to open your console. Automatic lock: 15 minutes idle or one hour after sign-in.");
}

function clearPrivateForms() {
  for (const form of [generationForm, scheduleForm]) {
    form.reset();
    setSections(form, []);
    syncParameterControls(form);
  }
  generationForm.elements.requestedDate.value = generationForm.elements.requestedDate.max;
  byId("profile-name").value = "";
  byId("profile-picker").replaceChildren(element("option", "", "LOAD FAVORITE…"));
  byId("remember-favorites").checked = false;
  byId("update-profile-button").disabled = true;
  byId("delete-profile-button").disabled = true;
  byId("cancel-edit-button").hidden = true;
  dismissAlert();
}

function clearConsoleSession() {
  try { window.sessionStorage.removeItem(CONSOLE_SESSION_KEY); } catch { /* No retained browser state. */ }
  appState.sessionAuthenticatedAt = null;
  appState.sessionEndsAt = null;
}

function persistConsoleSession() {
  try {
    window.sessionStorage.setItem(CONSOLE_SESSION_KEY, JSON.stringify({
      authenticatedAt: appState.sessionAuthenticatedAt,
      idleAt: appState.idleAt,
    }));
  } catch { /* Absolute expiry still derives from Firebase authentication time. */ }
}

async function consoleSessionFor(user) {
  const result = await user.getIdTokenResult();
  // authTime is the original sign-in time; token refresh must not extend it.
  // This protects console behavior, not server-side revocation of copied tokens.
  const authenticatedAt = Date.parse(result.authTime);
  const now = Date.now();
  if (!Number.isFinite(authenticatedAt) || authenticatedAt > now + 5000) {
    throw new Error("The sign-in time could not be verified. Please sign in again.");
  }
  const sessionEndsAt = authenticatedAt + SESSION_MAX_MS;
  let saved = null;
  try { saved = JSON.parse(window.sessionStorage.getItem(CONSOLE_SESSION_KEY)); } catch { /* No saved idle state. */ }
  const idleAt = saved?.authenticatedAt === authenticatedAt && Number.isFinite(saved.idleAt)
    ? Math.min(saved.idleAt, sessionEndsAt)
    : Math.min(authenticatedAt + IDLE_LIMIT_MS, sessionEndsAt);
  if (now >= sessionEndsAt || now >= idleAt) {
    throw new Error("Your console session expired. Please sign in again.");
  }
  return { sessionAuthenticatedAt: authenticatedAt, sessionEndsAt, idleAt };
}

function showApp(user, session) {
  Object.assign(appState, session);
  appState.user = user;
  appState.authorized = true;
  authScreen.hidden = true;
  appShell.hidden = false;
  byId("signed-in-user").textContent = user.email || "Verified Google account";
  persistConsoleSession();
  restoreFavoritePreference();
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
  appState.authEpoch += 1;
  showAuth();
  let detail = reason;
  try {
    await signOut(appState.auth);
  } catch {
    detail = "Console locked locally. Sign-out could not be confirmed; close this tab before signing in again.";
  } finally {
    setAuthStatus(detail);
  }
}

async function verifyOwner(user) {
  const owner = await getDoc(doc(appState.db, "owners", user.uid));
  if (!owner.exists()) {
    throw new Error("This Google account is authenticated but is not an authorized owner.");
  }
}

function resetIdleTimer() {
  if (!appState.authorized) {
    return;
  }
  const now = Date.now();
  const audio = byId("episode-audio");
  const listening = Boolean(audio.src && !audio.paused && !audio.ended);
  if (now >= appState.sessionEndsAt || (now >= appState.idleAt && !listening)) {
    void signOutUser("Your console session expired. Please sign in again.");
    return;
  }
  appState.idleAt = Math.min(now + IDLE_LIMIT_MS, appState.sessionEndsAt);
  persistConsoleSession();
}

async function handleAuthState(user) {
  const epoch = ++appState.authEpoch;
  if (!user) {
    showAuth();
    return;
  }
  setAuthStatus("Verifying private owner access…");
  try {
    const session = await consoleSessionFor(user);
    if (epoch !== appState.authEpoch) return;
    await verifyOwner(user);
    if (epoch !== appState.authEpoch) return;
    if (Date.now() >= session.sessionEndsAt || Date.now() >= session.idleAt) {
      throw new Error("Your console session expired. Please sign in again.");
    }
    showApp(user, session);
  } catch (error) {
    if (epoch !== appState.authEpoch) return;
    await signOutUser(error.message || firebaseErrorMessage(error));
  }
}

async function checkIdleTimer() {
  if (!appState.authorized) {
    return;
  }
  const audio = byId("episode-audio");
  const listening = Boolean(audio.src && !audio.paused && !audio.ended);
  if (listening && Date.now() < appState.sessionEndsAt) resetIdleTimer();
  const remaining = Math.max(0, (listening ? appState.sessionEndsAt : appState.idleAt) - Date.now());
  const minutes = Math.floor(remaining / 60000);
  const seconds = Math.floor((remaining % 60000) / 1000);
  byId("session-countdown").textContent =
    `${listening ? "LISTENING // SESSION LIMIT" : "AUTO SIGN-OUT"} // ${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  if (remaining === 0) {
    await signOutUser("The private session reached its inactivity or one-hour security limit.");
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

function reorderSections(sections, moved, before = "") {
  if (!sections.includes(moved) || before === moved || (before && !sections.includes(before))) {
    return [...sections];
  }
  const ordered = sections.filter((section) => section !== moved);
  ordered.splice(before ? ordered.indexOf(before) : ordered.length, 0, moved);
  return ordered;
}

function sectionDropBefore(chips, clientX, clientY) {
  const rows = [];
  for (const chip of chips) {
    const rect = chip.getBoundingClientRect();
    const row = rows.at(-1);
    if (row && Math.abs(row.top - rect.top) < 8) {
      row.chips.push({ chip, rect });
      row.bottom = Math.max(row.bottom, rect.bottom);
    } else {
      rows.push({ top: rect.top, bottom: rect.bottom, chips: [{ chip, rect }] });
    }
  }
  const rowIndex = rows.findIndex((row) => clientY <= row.bottom + 4);
  if (rowIndex < 0) return null;
  return rows[rowIndex].chips.find(({ rect }) => clientX < rect.left + rect.width / 2)?.chip
    || rows[rowIndex + 1]?.chips[0].chip || null;
}

function moveSection(form, section, direction) {
  if (direction !== -1 && direction !== 1) return;
  const sections = sectionValues(form);
  const index = sections.indexOf(section);
  const destination = index + direction;
  if (index < 0 || destination < 0 || destination >= sections.length) return;
  [sections[index], sections[destination]] = [sections[destination], sections[index]];
  sectionEditor(form).querySelector("textarea").value = sections.join("\n");
  renderSectionTokens(form, section, direction < 0 ? "earlier" : "later");
  sectionEditor(form).querySelector(".section-order-status").textContent =
    `${section} moved to position ${destination + 1} of ${sections.length}.`;
}

function renderSectionTokens(form, focusSection = "", focusAction = "") {
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
    const next = sectionDropBefore(chips, event.clientX, event.clientY);
    list.dataset.dropBefore = next?.dataset.section || "";
    for (const chip of chips) {
      chip.classList.toggle("drop-target", chip === next);
    }
    list.classList.toggle("drop-at-end", !next && chips.length > 0);
  };
  list.ondrop = (event) => {
    event.preventDefault();
    const moved = event.dataTransfer.getData("text/plain");
    const ordered = reorderSections(parseSections(textarea.value), moved, list.dataset.dropBefore);
    textarea.value = ordered.join("\n");
    clearDropHint();
    renderSectionTokens(form, moved, "earlier");
  };
  list.ondragleave = (event) => {
    if (event.relatedTarget && list.contains(event.relatedTarget)) return;
    clearDropHint();
  };
  for (const [index, section] of sections.entries()) {
    const chip = element("span", "section-chip");
    chip.draggable = true;
    chip.dataset.section = section;
    chip.tabIndex = -1;
    chip.setAttribute("role", "group");
    chip.setAttribute("aria-label", `${section}, position ${index + 1} of ${sections.length}`);
    chip.append(element("span", "section-chip-label", section));
    const actions = element("span", "section-chip-actions");
    for (const [action, direction] of [["earlier", -1], ["later", 1]]) {
      const button = element("button", "section-chip-move");
      button.type = "button";
      button.dataset.action = action;
      button.setAttribute("aria-label", `Move ${section} ${action}`);
      button.title = `Move ${action}`;
      button.disabled = index + direction < 0 || index + direction >= sections.length;
      const icon = element("span", `section-order-icon ${action}`);
      icon.setAttribute("aria-hidden", "true");
      button.append(icon);
      button.addEventListener("click", () => moveSection(form, section, direction));
      actions.append(button);
    }
    const remove = element("button", "section-chip-remove", "x");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove ${section}`);
    remove.addEventListener("click", () => {
      textarea.value = parseSections(textarea.value).filter((item) => item !== section).join("\n");
      renderSectionTokens(form);
      editor.querySelector(".section-token-input").focus();
      editor.querySelector(".section-order-status").textContent = `${section} removed.`;
    });
    actions.append(remove);
    chip.append(actions);
    chip.addEventListener("keydown", (event) => {
      if (!event.altKey || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      moveSection(form, section, event.key === "ArrowLeft" ? -1 : 1);
    });
    chip.addEventListener("dragstart", (event) => {
      if (event.target.closest("button")) { event.preventDefault(); return; }
      event.dataTransfer.setData("text/plain", section);
      event.dataTransfer.effectAllowed = "move";
      chip.classList.add("dragging");
      const ghost = chip.cloneNode(true);
      ghost.className = "section-drag-ghost";
      document.body.append(ghost);
      event.dataTransfer.setDragImage(ghost, ghost.offsetWidth / 2, ghost.offsetHeight / 2);
      window.setTimeout(() => ghost.remove(), 0);
    });
    chip.addEventListener("dragend", () => { chip.classList.remove("dragging"); clearDropHint(); });
    list.append(chip);
    if (section === focusSection) {
      const button = [...actions.querySelectorAll("button")].find((item) => item.dataset.action === focusAction);
      (button && !button.disabled ? button : chip).focus();
    }
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
    const ordered = reorderSections(parseSections(textarea.value), moved);
    textarea.value = ordered.join("\n");
    renderSectionTokens(form, moved, "earlier");
  });
  list.append(endDrop);
}

function setSections(form, sections) {
  const editor = sectionEditor(form);
  if (!editor) return;
  editor.querySelector("textarea").value = Array.isArray(sections) ? sections.join("\n") : "";
  editor.querySelector(".section-order-status").textContent = "";
  renderSectionTokens(form);
}

function setupSectionEditor(form) {
  const editor = sectionEditor(form);
  if (!editor) return;
  const input = editor.querySelector(".section-token-input");
  const textarea = editor.querySelector("textarea");
  input.addEventListener("input", () => input.removeAttribute("aria-invalid"));
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.isComposing) return;
    event.preventDefault();
    const candidate = input.value.trim();
    if (!candidate) return;
    try {
      textarea.value = parseSections([textarea.value, candidate].filter(Boolean).join("\n")).join("\n");
      input.value = "";
      renderSectionTokens(form);
      input.removeAttribute("aria-invalid");
      editor.querySelector(".section-order-status").textContent = `${candidate} added.`;
    } catch (error) {
      input.setAttribute("aria-invalid", "true");
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
  // Read configured values, including temporarily inactive host/paper controls.
  // FormData omits disabled controls and would erase their saved preferences.
  const parameters = { sections: sectionValues(form), publish: true };
  for (const [name, fallback] of Object.entries(GENERATION_PARAMETER_DEFAULTS)) {
    const control = form.elements.namedItem(name);
    parameters[name] = typeof fallback === "boolean" ? (control ? control.checked : fallback)
      : typeof fallback === "number" ? Number(control?.value || fallback)
        : String(control?.value ?? fallback).trim();
  }
  return parameters;
}

function validateParameters(parameters) {
  if (typeof parameters.includeNewspaper !== "boolean") {
    throw new Error("Choose whether to generate a newspaper.");
  }
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

function restoreFavoritePreference() {
  let remember = false;
  try {
    const key = profileStorageKey();
    // Preserve deliberately saved legacy favorites; show their device retention.
    remember = window.localStorage.getItem(`${key}:remember`) === "true"
      || window.localStorage.getItem(key) !== null;
  } catch { /* Default to session-only favorites. */ }
  appState.rememberFavorites = remember;
  byId("remember-favorites").checked = remember;
}

function clearFavoriteSession() {
  try { window.sessionStorage.removeItem(profileStorageKey()); } catch { /* No retained favorites. */ }
  appState.rememberFavorites = false;
}

function storeProfiles(profiles) {
  if (!appState.authorized || !appState.user) throw new Error("Sign in before saving favorites.");
  const storage = appState.rememberFavorites ? window.localStorage : window.sessionStorage;
  storage.setItem(profileStorageKey(), JSON.stringify(profiles.slice(0, 20)));
}

function changeFavoriteStorage() {
  if (!appState.authorized || !appState.user) return;
  const previous = Boolean(appState.rememberFavorites);
  const profiles = savedProfiles();
  const remember = byId("remember-favorites").checked;
  try {
    const key = profileStorageKey();
    const target = remember ? window.localStorage : window.sessionStorage;
    const old = previous ? window.localStorage : window.sessionStorage;
    target.setItem(key, JSON.stringify(profiles));
    if (remember) window.localStorage.setItem(`${key}:remember`, "true");
    else window.localStorage.removeItem(`${key}:remember`);
    if (old !== target) old.removeItem(key);
    appState.rememberFavorites = remember;
    showAlert(remember
      ? "Favorites will remain on this device after sign-out. No passwords or tokens are stored with them."
      : "Favorites are now session-only and will be removed when you sign out.");
  } catch {
    byId("remember-favorites").checked = previous;
    showAlert("Favorite storage could not be changed. Your existing favorites were kept.", true);
  }
}

function savedProfiles() {
  if (!appState.authorized || !appState.user) return [];
  try {
    const storage = appState.rememberFavorites ? window.localStorage : window.sessionStorage;
    const raw = storage.getItem(profileStorageKey());
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

function syncNewspaperControls(form) {
  form.elements.editionScale.disabled = !form.elements.includeNewspaper.checked;
}

function syncParameterControls(form) {
  syncHostControls(form);
  syncNewspaperControls(form);
}

function setupParameterForm(form) {
  if (form.dataset.parameterFormReady === "true") return;
  setupSectionEditor(form);
  for (const name of ["hostCount", "soloName", "includeNewspaper"]) {
    form.elements.namedItem(name).addEventListener("change", () => syncParameterControls(form));
  }
  syncParameterControls(form);
  form.dataset.parameterFormReady = "true";
}

function applyParametersToForm(form, parameters) {
  if (!parameters || typeof parameters !== "object" || Array.isArray(parameters)) {
    throw new Error("Saved preferences are not a valid parameter record.");
  }
  if (parameters.sections !== undefined && !Array.isArray(parameters.sections)) {
    throw new Error("Saved podcast sections must be a list.");
  }
  if ((parameters.sections || []).some(section => typeof section !== "string")) {
    throw new Error("Saved podcast sections must contain text names.");
  }
  const sections = parseSections((parameters.sections || []).join("\n"));
  const prepared = [];
  for (const [name, fallback] of Object.entries(GENERATION_PARAMETER_DEFAULTS)) {
    const control = form.elements.namedItem(name);
    if (!control) continue;
    const defaultOption = control.options
      ? [...control.options].find(option => option.defaultSelected) || control.options[0] : null;
    const value = parameters[name] ?? defaultOption?.value ?? fallback;
    const type = typeof fallback;
    if ((type === "boolean" && typeof value !== "boolean")
        || (type !== "boolean" && typeof value !== "string" && typeof value !== "number")
        || (control.options && ![...control.options].some(option => option.value === String(value)))) {
      throw new Error(`Saved preferences contain an unavailable value for ${name}.`);
    }
    prepared.push({ control, value, type });
  }
  // Preflight the complete record before replacing any currently edited values.
  for (const { control, value, type } of prepared) {
    if (type === "boolean") control.checked = value;
    else control.value = String(value);
  }
  setSections(form, sections);
  syncParameterControls(form);
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
    storeProfiles(profiles);
    byId("profile-name").value = name;
    renderProfiles();
    byId("profile-picker").value = name;
    syncFavoriteActions();
    showAlert(appState.rememberFavorites ? "Favorite saved on this device for this owner." : "Favorite saved for this console session only.");
  } catch (error) {
    showAlert(error.message || "Favorite could not be saved.", true);
  }
}

function loadFavoriteProfile() {
  const name = byId("profile-picker").value;
  const profile = savedProfiles().find((item) => item.name === name);
  if (profile?.parameters) {
    try { applyParametersToForm(generationForm, profile.parameters); }
    catch (error) { showAlert(error.message || "Favorite could not be loaded.", true); return; }
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
    storeProfiles(profiles);
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
  try { storeProfiles(profiles); }
  catch { showAlert("The favorite could not be removed. Please try again.", true); return; }
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
  renderResourceSummary();
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
          `${data.parameters?.gmailLabel || "NO LABEL"} // ${sections}\n` +
          (data.parameters?.includeNewspaper === false ? "PODCAST ONLY" : "PODCAST + PAPER"),
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
  const parameters = data.parameters || {};
  try {
    if (typeof parameters !== "object" || Array.isArray(parameters)) {
      throw new Error("Schedule preferences are not a valid parameter record.");
    }
    applyParametersToForm(scheduleForm, { ...parameters,
      runName: parameters.runName || data.name || "Morning Nexus", gmailLabel: parameters.gmailLabel || "" });
  } catch (error) {
    showAlert(error.message || "Schedule preferences could not be loaded.", true);
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
  scheduleForm.elements.enabled.checked = Boolean(data.enabled);
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
  syncParameterControls(scheduleForm);
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
  if (!appState.authorized || appState.generationSubmitting) return;
  const epoch = appState.authEpoch;
  appState.generationSubmitting = true;
  const button = generationForm.querySelector('button[type="submit"]');
  if (button) button.disabled = true;
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
    const queued = await addDoc(
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
    if (appState.authorized && epoch === appState.authEpoch) await requestRunnerWake(queued.id);
  } catch (error) {
    if (appState.authorized && epoch === appState.authEpoch) {
      showAlert(error.message || firebaseErrorMessage(error), true);
    }
  } finally {
    if (epoch === appState.authEpoch) {
      appState.generationSubmitting = false;
      if (button) button.disabled = false;
    }
  }
}

async function requestRunnerWake(requestId) {
  if (!appState.authorized) return;
  const epoch = appState.authEpoch;
  appState.wakeStates ||= new Map();
  appState.wakeStates.set(requestId, "requesting");
  renderRunRequestList();
  const pending = appState.wakePromise || cloudClockRequest("/v1/wake", {
    body: { schemaVersion: 1 },
  });
  appState.wakePromise = pending;
  try {
    const wake = await pending;
    if (!appState.authorized || appState.authEpoch !== epoch) return;
    const confirmed = ["dispatched", "already-requested"].includes(wake?.status);
    appState.wakeStates.set(requestId, confirmed ? "confirmed" : "unconfirmed");
    showAlert(wake?.status === "dispatched"
      ? "Request saved. Runner wake accepted; generation starts when GitHub assigns a runner."
      : wake?.status === "already-requested"
        ? "Request saved. A recent wake was accepted; this does not confirm the runner has started."
        : "Request saved, but wake is not confirmed. Check Cloud Clock, then use WAKE RUNNER on this queued item—not GENERATE again.", !confirmed);
  } catch (_error) {
    if (!appState.authorized || appState.authEpoch !== epoch) return;
    appState.wakeStates.set(requestId, "unconfirmed");
    showAlert("Request saved, but wake is not confirmed. Use WAKE RUNNER on the queued item to retry without creating another episode.", true);
  } finally {
    if (epoch === appState.authEpoch) {
      if (appState.wakePromise === pending) appState.wakePromise = null;
      renderRunRequestList();
    }
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

function runEstimateRange(runName = "") {
  const samples = appState.runRequests.filter(item =>
    ["published", "completed"].includes(item.status) &&
    (!runName || item.parameters?.runName === runName)).map(item => {
      const start = dateValue(item.startedAt), finish = dateValue(item.finishedAt);
      return start && finish ? finish - start : 0;
    }).filter(value => value >= 60000 && value <= SESSION_MAX_MS).sort((a, b) => a - b);
  if (samples.length < 3) return { low: 20 * 60000, high: 55 * 60000, observed: false };
  return { low: samples[Math.floor((samples.length - 1) * 0.2)],
    high: samples[Math.ceil((samples.length - 1) * 0.8)], observed: true };
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
    const estimate = runEstimateRange(data.activeTask);
    const remainingLow = Math.max(0, estimate.low - elapsed);
    const remainingHigh = Math.max(0, estimate.high - elapsed);
    detail.textContent = [
      `ACTIVE // ${data.activeTask || "PRIVATE GENERATION"}`,
      `ELAPSED ${durationText(elapsed)}`,
      data.progress ? `STAGE ${data.progress.stage}/8 // ${data.progress.label}` : "",
      data.progress?.counters?.voice_total
        ? `VOICE ${data.progress.counters.voice_blocks || 0}/${data.progress.counters.voice_total}` : "",
      data.progress?.counters?.newsletters ? `NEWSLETTERS ${data.progress.counters.newsletters}` : "",
      data.progress?.counters?.selected_stories ? `SELECTED STORIES ${data.progress.counters.selected_stories}` : "",
      data.progress?.counters?.word_count ? `SCRIPT ${data.progress.counters.word_count} WORDS` : "",
      data.progress ? `LAST PROGRESS ${timeText(data.checkedAt)}` : "",
      data.progress?.failure_code ? `LAST VALIDATION ${data.progress.failure_code.replaceAll("_", " ").toUpperCase()}` : "",
      `${estimate.observed ? "RECENT RUN RANGE" : "REFERENCE RANGE"} ${durationText(estimate.low)}–${durationText(estimate.high)}`,
      remainingHigh > 0 ? `EST. ${durationText(remainingLow)}–${durationText(remainingHigh)} REMAINING; NOT A DEADLINE` : "PAST ESTIMATE // ONE-HOUR LIMIT",
    ].filter(Boolean).join(" // ");
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

function metricNumber(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1e12
    ? value : null;
}

function renderResourceProfile(snapshot) {
  appState.resourceReadUnavailable = false;
  appState.resourceProfile = snapshot.exists() ? snapshot.data() : null;
  renderResourceSummary();
}

function renderResourceSummary() {
  const container = byId("resource-summary");
  container.replaceChildren();
  const profile = appState.resourceProfile;
  const add = text => container.append(element("p", "item-meta", text));
  const secondsText = value => durationText(value * 1000);
  if (appState.resourceReadUnavailable) add("RESOURCE REFRESH UNAVAILABLE // Previous measurements may be stale; runner and queue refresh are independent.");
  if (!profile) {
    add("NO TERMINAL MEASUREMENT YET // Recorded after the next normal generation; no test episode is needed.");
  } else {
    const total = metricNumber(profile.elapsed_seconds);
    const state = ["completed", "failed", "interrupted"].includes(profile.status) ? profile.status.toUpperCase() : "UNKNOWN";
    add(`LAST MEASUREMENT ${timestampText(profile.at)} // ${state}${total !== null ? ` // GENERATION ${secondsText(total)}` : ""}`);
    const reasons = { time_budget: "PROTECTED TIME BUDGET EXHAUSTED", cost_guard: "NO-SPEND SAFETY GUARD",
      verification_failure: "VERIFICATION FAILED", model_failure: "MODEL FAILURE", audio_failure: "AUDIO FAILURE",
      publish_failure: "PUBLICATION FAILURE", interrupted: "INTERRUPTED", no_content: "NO APPROVED CONTENT",
      subprocess_timeout: "OPERATION TIMEOUT", other: "OTHER FAILURE", sync_pending: "PUBLISHED; METADATA SYNC PENDING" };
    if (Object.hasOwn(reasons, profile.reason)) add(`OUTCOME DETAIL // ${reasons[profile.reason]}`);
    const measurements = profile.measurements || {};
    const labels = { start_delay_seconds: "START DELAY (INCLUDES SETUP)", setup_seconds: "JOB SETUP BEFORE GENERATION",
      job_observed_seconds: "JOB TIME OBSERVED SO FAR (EXCLUDES FINAL CLEANUP)", protected_remaining_seconds: "PROTECTED TIME LEFT AT FINISH" };
    for (const [key, label] of Object.entries(labels)) {
      const value = metricNumber(measurements[key]);
      if (value !== null) add(`${label} // ${secondsText(value)}`);
    }
    if (profile.ready_by_at) {
      const late = metricNumber(measurements.ready_by_late_seconds);
      add(`READY-BY TARGET ${timestampText(profile.ready_by_at)} // ${state !== "COMPLETED" ? "NOT COMPLETED; NO AUTOMATIC RETRY" : late === null ? "NOT MEASURED" : late > 0 ? `COMPLETED ${secondsText(late)} AFTER TARGET` : "COMPLETED WITHIN TARGET"}`);
    }
    const stageLabels = ["Preparation", "Newsletters", "Articles", "Extraction", "Script", "Verification", "Paper", "Audio", "Publication"];
    const stages = Object.entries(profile.stage_seconds || {}).filter(([key, value]) =>
      /^[0-8]$/.test(key) && metricNumber(value) !== null && value >= 1).sort((a, b) => Number(a[0]) - Number(b[0]));
    if (stages.length) add(`STAGE TIMES // ${stages.map(([key, value]) => `${stageLabels[Number(key)]} ${secondsText(value)}`).join(" · ")}`);
    const operations = ["model", "speech", "voice_model", "audio_encode", "paper_render"]
      .filter(key => metricNumber(profile.operations?.[key]?.seconds) !== null);
    if (operations.length) add(`OPERATIONS (ALREADY INCLUDED IN STAGES) // ${operations.map(key =>
      `${key.replaceAll("_", " ")} ${secondsText(profile.operations[key].seconds)}`).join(" · ")}`);
    const samples = (Array.isArray(profile.recent) ? profile.recent : [profile]).slice(-20);
    const valid = samples.filter(sample => sample && ["completed", "failed", "interrupted"].includes(sample.status) && metricNumber(sample.elapsed_seconds) !== null);
    const completed = valid.filter(sample => sample.status === "completed");
    if (valid.length) add(`LAST ${valid.length} TASK SAMPLES // ${completed.length} completed · ${valid.length - completed.length} unsuccessful // ${secondsText(valid.reduce((sum, sample) => sum + sample.elapsed_seconds, 0))} generation time, INCLUDING FAILED ATTEMPTS; NOT BILLING USAGE`);
    if (completed.length >= 3) {
      const weekly = [...appState.schedules.values()].filter(schedule => schedule.enabled)
        .reduce((sum, schedule) => sum + new Set((schedule.weekdays || []).filter(day => Number.isInteger(day) && day >= 0 && day <= 6)).size, 0);
      const mean = completed.reduce((sum, sample) => sum + sample.elapsed_seconds, 0) / completed.length;
      if (weekly) add(`7-DAY GENERATION REFERENCE // ${weekly} scheduled editions ≈ ${Math.ceil(weekly * mean / 60)} min at the recent content mix. Excludes setup, retries, manual runs and other workflows; NOT remaining allowance.`);
    }
    const resources = profile.resources || {};
    if (metricNumber(resources.retained_audio_count) !== null && metricNumber(resources.retention_episodes) !== null) {
      add(`HOSTED RETENTION // ${resources.retained_audio_count} editions · limit ${resources.retention_episodes} editions, not days`);
    }
    const resourceLabels = { retained_audio_bytes: "CURRENT FEED AUDIO (DECLARED/MEASURED)", new_audio_bytes: "NEW AUDIO", new_paper_bytes: "NEW PDF", new_preview_bytes: "NEW PAPER PREVIEWS", staged_bytes: "STAGED RELEASE FILES ONLY" };
    for (const [key, label] of Object.entries(resourceLabels)) {
      const value = metricNumber(resources[key]);
      if (value !== null) add(`${label} // ${(value / (1024 * 1024)).toFixed(1)} MiB`);
    }
  }
  add("ACCOUNT ACTIONS BALANCE // UNKNOWN. These are task measurements, not billed minutes or a verified monthly balance.");
  add("HOSTING TOTAL STORAGE AND TRANSFER // UNKNOWN. Older releases and actual downloads are not included in file-size measurements. Check provider usage before assuming free-tier headroom.");
  for (const [label, url] of [["GitHub account usage", "https://github.com/settings/billing/usage"], ["Firebase usage console", "https://console.firebase.google.com/"]]) {
    const link = element("a", "item-meta", label);
    link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer";
    container.append(link);
  }
}

function renderRunRequests(snapshot) {
  if (!appState.authorized) return;
  appState.runRequests = snapshot.docs.map((item) => ({
    ...item.data(),
    id: item.id,
  }));
  renderRunRequestList();
}

function updateMonitorText(node, text) {
  if (node.textContent !== text) node.textContent = text;
}

function runRequestTimeline(data, now) {
  const status = String(data.status || "unknown").toUpperCase();
  const requested = dateValue(data.requestedAt), started = dateValue(data.startedAt), finished = dateValue(data.finishedAt);
  return [
    `QUEUED ${timestampText(data.requestedAt)}`,
    data.startedAt ? `STARTED ${timestampText(data.startedAt)}` : "",
    data.finishedAt ? `${status} ${timestampText(data.finishedAt)}` : "",
    !data.finishedAt && data.updatedAt ? `UPDATED ${timestampText(data.updatedAt)}` : "",
    requested && started && started >= requested ? `WAIT ${durationText(started - requested)}` : "",
    requested && !started && data.status === "queued" ? `WAIT SO FAR ${durationText(now - requested)}` : "",
    started && finished && finished >= started ? `DURATION ${durationText(finished - started)}` : "",
  ].filter(Boolean).join(" // ");
}

function runRequestDetail(data, now) {
  if (data.status === "running") {
    const started = dateValue(data.startedAt);
    return `RUNNING ${durationText(started ? now - started.getTime() : 0)} // ONE-HOUR JOB LIMIT`;
  }
  if (data.status === "queued") {
    const wakeState = appState.wakeStates?.get(data.id);
    return cloudClockEndpoint()
      ? `REQUEST SAVED // ${wakeState === "requesting" ? "REQUESTING WAKE" : wakeState === "confirmed" ? "WAKE ACCEPTED; WAITING FOR RUNNER" : "WAKE NOT CONFIRMED IN THIS SESSION"}. WAKE RUNNER RETRIES DISPATCH ONLY.`
      : "QUEUED // CLOUD CLOCK SETUP REQUIRED";
  }
  if (data.status === "published") return "PUBLISHED // AVAILABLE IN THE PRIVATE FEED";
  return data.detail ? String(data.detail) : "";
}

function createRunRequestRow(id) {
  const card = element("article", "request-item");
  card.dataset.requestId = id;
  card.tabIndex = -1;
  const header = element("div", "request-header");
  const title = element("p", "item-meta");
  const timeline = element("p", "request-timeline");
  const detail = element("p", "request-detail");
  header.append(title);
  card.append(header, timeline, detail);
  return { card, header, title, timeline, detail, actionKind: null, actions: null, wake: null, data: null };
}

function updateRunRequestRow(row, data, now) {
  row.data = data;
  const status = String(data.status || "unknown").toUpperCase();
  updateMonitorText(row.title, `${status} // ${data.requestedDate || "NO DATE"} // ${data.parameters?.runName || "UNNAMED RUN"} // ${data.parameters?.gmailLabel || "NO LABEL"}`);
  updateMonitorText(row.timeline, runRequestTimeline(data, now));
  const detail = runRequestDetail(data, now);
  updateMonitorText(row.detail, detail);
  if (row.detail.hidden !== !detail) row.detail.hidden = !detail;
  const kind = ["expired", "failed"].includes(data.status) ? "terminal" : data.status === "queued" ? "queued" : "none";
  if (row.actionKind !== kind) {
    row.actions?.remove();
    row.actions = null;
    row.wake = null;
    row.actionKind = kind;
    // Handlers retain only the immutable document ID, never a stale status or label.
    const id = data.id;
    if (kind === "terminal") {
      row.actions = element("div", "request-actions");
      const retry = element("button", "ghost-button requeue-button", "REQUEUE");
      retry.type = "button";
      retry.addEventListener("click", () => requeueRequest(id));
      const remove = element("button", "danger-button requeue-button", "DELETE");
      remove.type = "button";
      remove.addEventListener("click", () => deleteRunRequest(id));
      row.actions.append(retry, remove);
    } else if (kind === "queued") {
      row.wake = element("button", "ghost-button requeue-button", "WAKE RUNNER");
      row.wake.type = "button";
      row.wake.addEventListener("click", () => requestRunnerWake(id));
      row.actions = row.wake;
    }
    if (row.actions) row.header.append(row.actions);
  }
  if (row.wake) {
    const disabled = appState.wakeStates?.get(data.id) === "requesting";
    if (row.wake.disabled !== disabled) row.wake.disabled = disabled;
  }
}

function updateRunRequestTimes(now = Date.now()) {
  if (!appState.authorized) return;
  // The existing local second timer changes clock text only; no reads or sorting.
  for (const row of appState.runRequestRows?.values() || []) {
    if (!["queued", "running"].includes(row.data.status)) continue;
    updateMonitorText(row.timeline, runRequestTimeline(row.data, now));
    if (row.data.status === "running") updateMonitorText(row.detail, runRequestDetail(row.data, now));
  }
}

function renderRunRequestList() {
  if (!appState.authorized) return;
  const container = byId("run-request-list");
  appState.runRequestRows ||= new Map();
  const queued = appState.runRequests.filter((item) => item.status === "queued").length;
  const running = appState.runRequests.filter((item) => item.status === "running").length;
  updateMonitorText(byId("queue-count"), running ? `${running} RUNNING // ${queued} QUEUED` : `${queued} QUEUED`);
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
  const scope = byId("monitor-scope");
  if (scope) updateMonitorText(scope, appState.runRequests.length
    ? `${visible.length} SHOWN // ${appState.runRequests.length} RECENT REQUESTS LOADED // LATEST ${ARCHIVE_PAGE_SIZE} REQUEST WINDOW`
    : "0 RECENT REQUESTS LOADED");
  visible.sort((left, right) => {
    if (sortMode === "status") return String(left.status || "").localeCompare(String(right.status || ""));
    if (sortMode === "name") return String(left.parameters?.runName || "").localeCompare(String(right.parameters?.runName || ""));
    const leftTime = dateValue(left.updatedAt) || dateValue(left.requestedAt) || new Date(0);
    const rightTime = dateValue(right.updatedAt) || dateValue(right.requestedAt) || new Date(0);
    return sortMode === "oldest" ? leftTime - rightTime : rightTime - leftTime;
  });
  const focused = document.activeElement;
  const hadFocus = focused && container.contains(focused);
  const focusedRow = hadFocus ? focused.closest(".request-item") : null;
  const scrollTop = container.scrollTop;
  const visibleIds = new Set(visible.map(data => data.id));
  for (const [id, row] of appState.runRequestRows) {
    if (visibleIds.has(id)) continue;
    row.card.remove();
    appState.runRequestRows.delete(id);
  }
  const now = Date.now();
  if (visible.length && !container.children.length) updateMonitorText(container, "");
  let index = 0;
  for (const data of visible) {
    let row = appState.runRequestRows.get(data.id);
    if (!row) {
      row = createRunRequestRow(data.id);
      appState.runRequestRows.set(data.id, row);
    }
    updateRunRequestRow(row, data, now);
    if (container.children[index] !== row.card) container.insertBefore(row.card, container.children[index] || null);
    index++;
  }
  const className = visible.length ? "terminal-list" : "terminal-list empty-state";
  if (container.className !== className) container.className = className;
  if (!visible.length) updateMonitorText(container, appState.runRequests.length ? "No requests match these filters." : "No queued tasks.");
  if (container.scrollTop !== scrollTop) container.scrollTop = scrollTop;
  // A reorder may blur a moved button; a removed action returns focus to its row.
  if (hadFocus && document.activeElement !== focused) {
    const target = focused.isConnected ? focused : focusedRow?.isConnected ? focusedRow : container;
    if (target === container) target.tabIndex = -1;
    target.focus({ preventScroll: true });
  }
}

async function requeueRequest(requestId) {
  if (!appState.authorized) return;
  appState.requeuingRequests ||= new Set();
  if (appState.requeuingRequests.has(requestId)) return;
  appState.requeuingRequests.add(requestId);
  const epoch = appState.authEpoch;
  try {
    const original = appState.runRequests.find((item) => item.id === requestId);
    if (!original || !["failed", "expired"].includes(original.status) || !original.parameters || !/^\d{4}-\d{2}-\d{2}$/.test(String(original.requestedDate || ""))) {
      throw new Error("This request is incomplete and cannot be requeued safely.");
    }
    // A retry is a new immutable execution, not a status reset. This keeps the
    // original failure visible and prevents Cloud Clock from reclaiming it.
    const queued = await addDoc(
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
    if (appState.authorized && epoch === appState.authEpoch) await requestRunnerWake(queued.id);
  } catch (error) {
    if (appState.authorized && epoch === appState.authEpoch) showAlert(firebaseErrorMessage(error), true);
  } finally {
    if (epoch === appState.authEpoch) appState.requeuingRequests.delete(requestId);
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
  const latest = snapshot.docs.map((episodeDocument) => ({
    id: episodeDocument.id,
    ...episodeDocument.data(),
  }));
  if (!appState.olderEpisodes?.size) {
    appState.archiveCursor = snapshot.docs.at(-1) || null;
    appState.archiveHasMore = snapshot.docs.length === ARCHIVE_PAGE_SIZE;
  }
  const records = new Map(appState.olderEpisodes || []);
  for (const record of latest) records.set(record.id, record);
  appState.episodeRecords = [...records.values()];
  renderEpisodeArchives();
  updateArchiveButtons();
}

function updateArchiveButtons() {
  for (const id of ["load-older-episodes", "load-older-editions"]) {
    const button = byId(id);
    button.hidden = !appState.archiveHasMore;
    button.disabled = Boolean(appState.archiveLoading);
    button.textContent = appState.archiveLoading ? "LOADING" : "LOAD OLDER";
  }
}

async function loadOlderArchive() {
  if (!appState.authorized || !appState.archiveCursor || appState.archiveLoading) return;
  const uid = appState.user.uid;
  appState.archiveLoading = true;
  updateArchiveButtons();
  try {
    const snapshot = await getDocs(query(collection(appState.db, "users", uid, "episodes"),
      orderBy("episodeDate", "desc"), startAfter(appState.archiveCursor), limit(ARCHIVE_PAGE_SIZE)));
    if (!appState.authorized || appState.user?.uid !== uid) return;
    appState.olderEpisodes ||= new Map();
    for (const item of snapshot.docs) appState.olderEpisodes.set(item.id, {id: item.id, ...item.data()});
    const merged = new Map(appState.episodeRecords.map(item => [item.id, item]));
    for (const [id, item] of appState.olderEpisodes) if (!merged.has(id)) merged.set(id, item);
    appState.episodeRecords = [...merged.values()];
    appState.archiveCursor = snapshot.docs.at(-1) || appState.archiveCursor;
    appState.archiveHasMore = snapshot.docs.length === ARCHIVE_PAGE_SIZE;
    renderEpisodeArchives();
  } catch (error) {
    if (appState.authorized && appState.user?.uid === uid) showAlert(firebaseErrorMessage(error), true);
  } finally {
    appState.archiveLoading = false;
    updateArchiveButtons();
  }
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
  if (/^\d{2}\/\d{2}\/\d{4}\s+The Daily Nexus\s+-\s+.+\s+-\s+\d{3,}$/i.test(title)) {
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
          `${archiveDateTitle(record.episodeDate)} The Daily Nexus - ${archivePublicationLabel(record)} - ${String(sequence).padStart(4, "0")}`,
        );
      });
  }
  return labels;
}

function archiveMediaURL(record, extension) {
  if (record.status !== "published" || record.mediaState === "retired") return null;
  return safePrivateURL(extension === ".mp3" ? record.audioUrl : record.newspaperUrl, extension);
}

async function availableArchiveRecord(id, extension) {
  const epoch = appState.authEpoch;
  if (!appState.authorized || !appState.user) return null;
  const snapshot = await getDocFromServer(doc(appState.db, "users", appState.user.uid, "episodes", id));
  if (!appState.authorized || epoch !== appState.authEpoch) return null;
  const record = snapshot.exists() ? { ...snapshot.data(), id } : null;
  if (!record || !archiveMediaURL(record, extension)) {
    if (record) {
      appState.episodeRecords = appState.episodeRecords.map(item => item.id === id ? record : item);
      if (appState.olderEpisodes?.has(id)) appState.olderEpisodes.set(id, record);
      renderEpisodeArchives();
    }
    throw new Error("This edition is retired or its requested media is unavailable. Choose another edition.");
  }
  return record;
}

async function openArchivedEpisode(episode, autoplay = false) {
  const token = {};
  const epoch = appState.authEpoch;
  appState.audioSelectionToken = token;
  try {
    const record = await availableArchiveRecord(episode.id, ".mp3");
    if (!record || token !== appState.audioSelectionToken) return;
    selectEpisode({ ...episode, audioURL: archiveMediaURL(record, ".mp3") });
    if (autoplay) await byId("episode-audio").play();
  } catch (error) {
    if (appState.authorized && epoch === appState.authEpoch && token === appState.audioSelectionToken) {
      showAlert(error.message || firebaseErrorMessage(error), true);
    }
  }
}

async function openArchivedEdition(edition) {
  const token = {};
  const epoch = appState.authEpoch;
  appState.editionSelectionToken = token;
  try {
    const record = await availableArchiveRecord(edition.id, ".pdf");
    if (!record || token !== appState.editionSelectionToken) return;
    selectEdition({ ...edition, url: archiveMediaURL(record, ".pdf"), pageCount: record.newspaperPages,
      previews: record.newspaperPreviews, revision: record.updatedAt?.toMillis?.(),
      reading: validatedEditionReading(record.newspaperReading) });
  } catch (error) {
    if (appState.authorized && epoch === appState.authEpoch && token === appState.editionSelectionToken) {
      showAlert(error.message || firebaseErrorMessage(error), true);
    }
  }
}

function renderEpisodeArchives() {
  const retiredIds = new Set((appState.episodeRecords || [])
    .filter(record => record.mediaState === "retired").map(record => record.id));
  if (retiredIds.has(appState.activeEpisode?.id)) {
    const audio = byId("episode-audio");
    audio.pause(); audio.removeAttribute("src"); audio.load();
    appState.activeEpisode = null;
    appState.audioSelectionToken = null;
    appState.playbackStopped = true;
    byId("mini-player").hidden = true;
    byId("player-title").textContent = "EDITION RETIRED // SELECT ANOTHER EPISODE";
    byId("player-details").replaceChildren();
    if (typeof navigator !== "undefined" && navigator.mediaSession) {
      navigator.mediaSession.metadata = null;
      navigator.mediaSession.playbackState = "none";
    }
  }
  if (retiredIds.has(appState.activeEdition?.id)) {
    appState.activeEdition = null;
    appState.readerToken = null;
    appState.editionSelectionToken = null;
    byId("edition-pages").replaceChildren();
    clearEditionControls();
    byId("edition-pdf-link").removeAttribute("href");
    byId("edition-pdf-link").hidden = true;
    byId("edition-title").textContent = "EDITION RETIRED // SELECT ANOTHER EDITION";
  }
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
    const audioURL = archiveMediaURL(data, ".mp3");
    const episode = audioURL ? {
      id: episodeId,
      title,
      audioURL,
      episodeDate: data.episodeDate,
      references: Array.isArray(data.references) ? data.references : [],
      transcript: Array.isArray(data.transcript) ? data.transcript : [],
      sourceMix: data.sourceMix && typeof data.sourceMix === "object" ? data.sourceMix : {},
      episodeBudget: data.episodeBudget && typeof data.episodeBudget === "object" ? data.episodeBudget : {},
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
      const selectEpisodeCard = () => openArchivedEpisode(episode);
      playCard.addEventListener("click", selectEpisodeCard);
      playCard.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectEpisodeCard();
        }
      });
    } else {
      playCard.append(element("p", "item-meta", data.mediaState === "retired"
        ? "RETIRED // OUTSIDE HOSTED RETENTION WINDOW" : "AUDIO URL NOT SYNCHRONIZED"));
    }
    play.append(playCard);
  }

  for (const data of sortedEpisodeRecords(
    byId("edition-sort")?.value || "newest",
    readableRecords,
  )) {
    const editionId = String(data.id);
    const title = readTitles.get(editionId) || data.title || `The Daily Nexus // ${data.episodeDate || ""}`;
    const pdfURL = archiveMediaURL(data, ".pdf");
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
      const selectEditionCard = () => openArchivedEdition({ id: editionId, title });
      readCard.addEventListener("click", selectEditionCard);
      readCard.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectEditionCard();
        }
      });
    } else {
      readCard.append(element("p", "item-meta", data.mediaState === "retired"
        ? "RETIRED // OUTSIDE HOSTED RETENTION WINDOW"
        : data.newspaperStatus === "failed"
        ? "NEWSPAPER FAILED // VERIFIED PODCAST AVAILABLE IN PLAY"
        : data.newspaperStatus === "skipped"
          ? "PODCAST ONLY // NEWSPAPER NOT REQUESTED"
          : "PDF URL NOT SYNCHRONIZED"));
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
    const epoch = appState.authEpoch;
    const [runner, requests, profile] = await Promise.all([
      getDoc(doc(appState.db, "users", uid, "runner", "status")),
      getDocs(
        query(
          collection(appState.db, "users", uid, "runRequests"),
          orderBy("requestedAt", "desc"),
          limit(ARCHIVE_PAGE_SIZE),
        ),
      ),
      getDocFromServer(doc(appState.db, "users", uid, "runner", "lastProfile")).catch(() => null),
    ]);
    if (!appState.authorized || appState.user?.uid !== uid || epoch !== appState.authEpoch) return;
    appState.monitorRefreshedAt = new Date();
    renderRunner(runner);
    if (profile) renderResourceProfile(profile);
    else { appState.resourceReadUnavailable = true; renderResourceSummary(); }
    renderRunRequests(requests);
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
  byId("player-pause-button").title = audio.paused ? "Resume" : "Pause";
  byId("mini-player-progress").value = progress;
  setRangeProgress(byId("mini-player-progress"), progress);
  byId("mini-player-current").textContent = formatPlaybackTime(current);
  byId("mini-player-total").textContent = formatPlaybackTime(duration);
  byId("mini-pause-button").classList.toggle("active", !audio.paused && !audio.ended);
  byId("mini-pause-button").setAttribute("aria-label", audio.paused ? "Resume" : "Pause");
  byId("mini-pause-button").title = audio.paused ? "Resume" : "Pause";
  for (const id of ["episode-progress", "mini-player-progress"]) {
    byId(id).setAttribute("aria-valuetext", `${formatPlaybackTime(current)} of ${formatPlaybackTime(duration)}`);
  }
  byId("web-player").classList.toggle("playing", !audio.paused && !audio.ended);
  byId("mini-player").classList.toggle("playing", !audio.paused && !audio.ended);
  byId("mini-player").hidden = !appState.activeEpisode || appState.playbackStopped === true || audio.ended;
  syncMediaSession();
  savePlaybackPosition();
  const chapters = byId("player-chapters");
  if (chapters) {
    let chapterIndex = 0;
    for (const [index, option] of Array.from(chapters.options).entries()) {
      if (Number(option.value) <= current) chapterIndex = index;
    }
    chapters.selectedIndex = chapterIndex;
  }
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
    if (active && active !== appState.lastTranscriptSegment && Date.now() > (appState.transcriptFollowAfter || 0)) {
      const container = byId("player-details");
      const top = active.offsetTop - container.offsetTop;
      if (top < container.scrollTop || top + active.offsetHeight > container.scrollTop + container.clientHeight) {
        container.scrollTo({ top: Math.max(0, top - 20), behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      }
    }
    appState.lastTranscriptSegment = active;
  }
}

function minimizeReferenceURL(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password) return null;
    const credentialKeys = new Set([
      "access_token", "refresh_token", "id_token", "auth_token", "authorization",
      "password", "passwd", "api_key", "apikey", "x-api-key", "token",
      "signature", "sig", "x-amz-signature", "x-goog-signature", "upn",
    ]);
    const trackingKeys = new Set([
      "fbclid", "gclid", "mc_cid", "mc_eid", "mkt_tok", "ref", "referrer",
      "mc_tok", "_hsenc", "_hsmi", "cdlcid", "ncid", "emc", "regi_id",
      "segment_id", "subscriber_id", "subscriber_email", "recipient_id",
      "recipient_email", "email", "email_address", "user_id", "user_email",
    ]);
    for (const key of [...url.searchParams.keys()]) {
      const normalized = key.toLowerCase();
      if (credentialKeys.has(normalized)) return null;
      if (trackingKeys.has(normalized) || /^(utm_|cs_|dfp_)/.test(normalized)) {
        url.searchParams.delete(key);
      }
    }
    url.hash = "";
    return url.href;
  } catch {
    return null;
  }
}

function safeReference(value) {
  if (typeof value !== "string") return null;
  const match = value.match(/https:\/\/[^\s)]+/i);
  if (!match) return null;
  const url = minimizeReferenceURL(match[0].replace(/[.,;:!?]+$/, ""));
  if (!url) return null;
  const description = value
    .slice(0, match.index)
    .replace(/[\s\-–—|:]+$/, "")
    .trim();
  const fallback = new URL(url).hostname.replace(/^www\./i, "");
  return { text: description || fallback, url };
}

function renderPlayerDetails(mode = "references") {
  appState.playerDetailMode = mode;
  appState.lastTranscriptSegment = null;
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
    const budget = episode.episodeBudget;
    if (budget && Number.isFinite(budget.selected_news_stories)) {
      container.append(element("p", "item-meta evidence-mix",
        `AUDIO COVERAGE // ${budget.selected_news_stories} OF ${budget.available_stories} NEWS STORIES // ${budget.represented_newsletters} NEWSLETTERS REPRESENTED // CONTENT-SIZED; NO MINIMUM DURATION`));
    }
    if (Number.isFinite(mix.extracted_story_records)) {
      container.append(element("p", "item-meta evidence-mix",
        `SOURCE LINEAGE // ${mix.extracted_story_records} EXTRACTED RECORDS // ${mix.duplicate_story_records || 0} DUPLICATES MERGED // ${mix.omitted_news_stories || 0} NEWS STORIES OUTSIDE AUDIO SHORTLIST // ${mix.source_passages_cited || 0} ORIGINAL PASSAGES CITED. COUNTS ARE NOT A GUARANTEE OF ACCURACY.`));
    }
    if (!refs.length) {
      container.append(element("p", "empty-state",
        "No public article links were provided. Newsletter evidence can still support this edition."));
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
  savePlaybackPosition(true);
  const audio = byId("episode-audio");
  audio.pause();
  appState.activeEpisode = episode;
  appState.playbackStopped = false;
  appState.positionRestored = false;
  audio.src = episode.audioURL;
  audio.load();
  appState.activeAudio = audio;
  byId("player-title").textContent = episode.title;
  byId("mini-player-title").textContent = episode.title;
  byId("mini-player-title").title = episode.title;
  byId("mini-player-edition").textContent = episode.title.match(/(?:-\s*|\/\/\s*)(\d{3,})$/)?.[1] || "";
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
  renderPlayerChapters();
  updateMediaMetadata();
  syncPlayer();
}

function playbackSessionKey() {
  return appState.user?.uid ? `tdn-playback-session:${appState.user.uid}` : null;
}

function playbackBookmarks() {
  try {
    const key = playbackSessionKey();
    const entries = key ? JSON.parse(sessionStorage.getItem(key) || "[]") : [];
    return Array.isArray(entries) ? entries.filter(item =>
      typeof item?.id === "string" && item.id.length <= 160 &&
      Number.isFinite(item.seconds) && item.seconds > 0).slice(-20) : [];
  } catch { return []; }
}

function savePlaybackPosition(force = false) {
  if (!appState.authorized || !appState.activeEpisode ||
      (!appState.positionRestored && !appState.playbackStopped)) return;
  if (!force && Date.now() - (appState.lastPositionSaved || 0) < 5000) return;
  const audio = byId("episode-audio");
  const remaining = playbackBookmarks().filter(item => item.id !== appState.activeEpisode.id);
  if (!appState.playbackStopped && !audio.ended && Number.isFinite(audio.duration) &&
      audio.currentTime > 0 && audio.currentTime < audio.duration - 3) {
    remaining.push({ id: appState.activeEpisode.id, seconds: audio.currentTime });
  }
  try { sessionStorage.setItem(playbackSessionKey(), JSON.stringify(remaining.slice(-20))); }
  catch { /* Private mode can deny storage; playback still works. */ }
  appState.lastPositionSaved = Date.now();
}

function restorePlaybackPosition() {
  if (!appState.authorized || !appState.activeEpisode || appState.positionRestored) return;
  const audio = byId("episode-audio");
  // Ignore stale metadata from the previously selected episode.
  if (audio.currentSrc !== audio.src || !Number.isFinite(audio.duration)) return;
  const bookmark = playbackBookmarks().find(item => item.id === appState.activeEpisode.id);
  if (!appState.playbackStopped && bookmark && bookmark.seconds < audio.duration - 3) audio.currentTime = bookmark.seconds;
  appState.positionRestored = true;
  appState.lastPositionSaved = Date.now();
}

function clearPlaybackSession() {
  try { const key = playbackSessionKey(); if (key) sessionStorage.removeItem(key); } catch {}
  appState.positionRestored = false;
  appState.playbackStopped = true;
  if (typeof navigator !== "undefined" && navigator.mediaSession) {
    navigator.mediaSession.metadata = null;
    navigator.mediaSession.playbackState = "none";
    try { navigator.mediaSession.setPositionState?.(); } catch {}
    for (const action of ["play", "pause", "stop", "previoustrack", "nexttrack", "seekbackward", "seekforward", "seekto"]) {
      try { navigator.mediaSession.setActionHandler(action, null); } catch {}
    }
  }
  const chapters = byId("player-chapters");
  if (chapters) { chapters.replaceChildren(); chapters.parentElement.hidden = true; }
}

function renderPlayerChapters() {
  const control = byId("player-chapters");
  control.replaceChildren();
  const headings = (appState.activeEpisode?.transcript || []).filter(segment =>
    segment.isHeading === true && Number.isFinite(segment.startMs) && segment.startMs >= 0);
  control.parentElement.hidden = !headings.length;
  if (!headings.length) return;
  const introduction = element("option", "", "Introduction");
  introduction.value = "0";
  control.append(introduction);
  for (const heading of headings) {
    const option = element("option", "", heading.text);
    option.value = String(heading.startMs / 1000);
    control.append(option);
  }
}

function updateMediaMetadata() {
  if (!appState.authorized || !appState.activeEpisode || typeof navigator === "undefined" ||
      !navigator.mediaSession || typeof MediaMetadata === "undefined") return;
  navigator.mediaSession.metadata = new MediaMetadata({
    title: appState.activeEpisode.title, artist: "The Daily Nexus", album: "Private newsletter podcast",
  });
  for (const [action, handler] of Object.entries(appState.mediaActions || {})) {
    try { navigator.mediaSession.setActionHandler(action, details => {
      if (appState.authorized && appState.activeEpisode) handler(details);
    }); } catch {}
  }
}

function syncMediaSession() {
  if (!appState.authorized || !appState.activeEpisode || typeof navigator === "undefined" || !navigator.mediaSession) return;
  const audio = byId("episode-audio");
  navigator.mediaSession.playbackState = appState.playbackStopped || audio.ended ? "none" : audio.paused ? "paused" : "playing";
  if (Number.isFinite(audio.duration) && audio.duration > 0 && !appState.playbackStopped) {
    try {
      navigator.mediaSession.setPositionState?.({ duration: audio.duration,
        playbackRate: audio.playbackRate || 1, position: Math.min(audio.duration, Math.max(0, audio.currentTime || 0)) });
    } catch { /* Unsupported browsers retain ordinary in-app controls. */ }
  } else {
    try { navigator.mediaSession.setPositionState?.(); } catch {}
  }
}

function selectRelativeEpisode(direction) {
  const index = appState.episodes.findIndex((item) => item.id === appState.activeEpisode?.id);
  if (index < 0 || !appState.episodes.length) return;
  const target = appState.episodes[(index + direction + appState.episodes.length) % appState.episodes.length];
  const wasPlaying = !byId("episode-audio").paused;
  openArchivedEpisode(target, wasPlaying);
}

function clearEditionControls() {
  appState.editionModePreference = null;
  byId("edition-reader-note").textContent = "";
  for (const id of ["edition-readable", "edition-facsimile"]) {
    byId(id).disabled = true;
    byId(id).setAttribute("aria-pressed", "false");
  }
}

function validatedEditionReading(value) {
  // No arbitrary markup, URL, internal field or unbounded collection is rendered.
  if (!value || value.version !== 1 || JSON.stringify(value).length > 131072) return null;
  const text = candidate => typeof candidate === "string" && candidate.length <= 40000;
  const texts = (candidate, max) => Array.isArray(candidate) && candidate.length <= max && candidate.every(text);
  const rows = (candidate, max, valid) => Array.isArray(candidate) && candidate.length <= max && candidate.every(item => item && valid(item));
  const item = row => text(row.label) && text(row.value) && text(row.detail);
  if (![value.headline, value.deck, value.lead, value.kicker, value.pullQuote].every(text)
      || !rows(value.articles, 8, row => [row.title, row.section, row.standfirst, row.body].every(text)
        && texts(row.bullets, 20) && texts(row.highlights, 20)) || !value.articles.length
      || !rows(value.executive, 4, item) || !texts(value.briefs, 8) || !texts(value.dataPoints, 30)
      || !rows(value.visuals, 3, row => text(row.kind) && text(row.title) && text(row.caption)
        && rows(row.items, 6, item))) return null;
  const copyItem = row => ({ label: row.label, value: row.value, detail: row.detail });
  return { version: 1, headline: value.headline, deck: value.deck, lead: value.lead,
    kicker: value.kicker, pullQuote: value.pullQuote,
    articles: value.articles.map(row => ({ title: row.title, section: row.section,
      standfirst: row.standfirst, body: row.body, bullets: row.bullets,
      highlights: row.highlights.filter(phrase => phrase && row.body.includes(phrase)) })),
    executive: value.executive.map(copyItem), briefs: value.briefs, dataPoints: value.dataPoints,
    visuals: value.visuals.map(row => ({ kind: row.kind, title: row.title, caption: row.caption,
      items: row.items.map(copyItem) })) };
}

function selectEdition(edition) {
  const { id, title, url } = edition;
  appState.activeEdition = edition;
  for (const card of document.querySelectorAll(".edition-list-item")) {
    card.classList.toggle("selected", card.dataset.editionId === id);
  }
  byId("edition-title").textContent = title;
  const link = byId("edition-pdf-link");
  link.href = url;
  link.hidden = false;
  renderEditionView(appState.editionModePreference || (edition.reading ? "readable" : "pages"));
}

function setEditionMode(mode) {
  if (!appState.authorized || !appState.activeEdition) return;
  if (mode === "readable" && !appState.activeEdition.reading) return;
  appState.editionModePreference = mode;
  renderEditionView(mode);
}

function renderEditionView(mode) {
  const edition = appState.activeEdition;
  if (!edition) return;
  const readable = mode === "readable" && Boolean(edition.reading);
  const pages = byId("edition-pages");
  pages.replaceChildren();
  pages.className = readable ? "edition-pages edition-readable" : "edition-pages";
  pages.scrollTo({ top: 0, left: 0 });
  byId("edition-readable").disabled = !edition.reading;
  byId("edition-facsimile").disabled = false;
  byId("edition-readable").setAttribute("aria-pressed", String(readable));
  byId("edition-facsimile").setAttribute("aria-pressed", String(!readable));
  byId("edition-reader-note").textContent = edition.reading
    ? "Same approved edition: adaptable text or original pages."
    : "This edition has original pages only. Readable text is included with newly published papers.";
  const readerToken = {};
  appState.readerToken = readerToken;
  if (readable) renderReadableEdition(edition.reading, pages);
  else renderEditionPages(edition, pages, readerToken);
  applyEditionZoom();
}

function renderReadableEdition(issue, pages) {
  const paper = element("article", "readable-paper");
  paper.append(element("p", "eyebrow", issue.kicker));
  paper.append(element("h4", "readable-headline", issue.headline));
  paper.append(element("p", "readable-deck", issue.deck));
  paper.append(element("p", "readable-lead", issue.lead));
  const renderItems = (title, items, caption = "") => {
    const section = element("section", "readable-brief");
    section.append(element("h5", "", title));
    const list = element("dl", "readable-facts");
    for (const item of items) {
      list.append(element("dt", "", [item.value, item.label].filter(Boolean).join(" — ")));
      if (item.detail) list.append(element("dd", "", item.detail));
    }
    section.append(list);
    if (caption) section.append(element("p", "readable-caption", caption));
    paper.append(section);
  };
  if (issue.executive.length) renderItems("Executive signal", issue.executive);
  for (const article of issue.articles) {
    const section = element("section", "readable-story");
    section.append(element("p", "eyebrow", article.section));
    section.append(element("h5", "", article.title));
    if (article.standfirst) section.append(element("p", "readable-deck", article.standfirst));
    for (const paragraph of article.body.split(/\n\s*\n/).filter(text => text.trim())) {
      const body = element("p", "", "");
      let rest = paragraph;
      // Exact matching highlights only; all untrusted text stays text, never HTML.
      const phrases = article.highlights.filter(phrase => phrase && rest.includes(phrase));
      while (phrases.some(phrase => rest.includes(phrase))) {
        const phrase = phrases.filter(value => rest.includes(value)).sort((a, b) => rest.indexOf(a) - rest.indexOf(b))[0];
        const index = rest.indexOf(phrase);
        body.append(element("span", "", rest.slice(0, index)));
        body.append(element("strong", "", phrase));
        rest = rest.slice(index + phrase.length);
      }
      body.append(element("span", "", rest));
      section.append(body);
    }
    if (article.bullets.length) {
      const list = element("ul", "");
      for (const bullet of article.bullets) list.append(element("li", "", bullet));
      section.append(list);
    }
    paper.append(section);
  }
  for (const visual of issue.visuals) renderItems(visual.title, visual.items, visual.caption);
  for (const [title, items] of [["Also inside", issue.briefs], ["In figures", issue.dataPoints]]) {
    if (!items.length) continue;
    const section = element("section", "readable-brief");
    section.append(element("h5", "", title));
    const list = element("ul", "");
    for (const text of items) list.append(element("li", "", text));
    section.append(list);
    paper.append(section);
  }
  if (issue.pullQuote) paper.append(element("blockquote", "", issue.pullQuote));
  pages.append(paper);
}

function renderEditionPages(edition, pages, readerToken) {
  const { url, title } = edition;
  const count = [1, 2, 3].includes(edition.pageCount) ? edition.pageCount : 3;
  const expected = Array.from({ length: count }, (_, index) => {
    const value = new URL(url);
    value.pathname = value.pathname.replace(/\.pdf$/i, `-${index + 1}.png`);
    return value.href;
  });
  const explicit = Array.isArray(edition.previews) && edition.previews.length === count
    && edition.previews.every((value, index) => value === expected[index]);
  const sources = explicit ? edition.previews : expected;
  let settled = 0;
  let loaded = 0;
  const finish = () => {
    settled++;
    if (settled !== count) return;
    if (!loaded) {
      pages.className = "edition-pages empty-state";
      pages.textContent = "The in-app preview is unavailable. Use OPEN PDF to read the original.";
    } else if (loaded < count && [1, 2, 3].includes(edition.pageCount)) {
      pages.append(element("p", "item-meta", "Some pages could not load. OPEN PDF contains the original edition."));
    }
  };
  for (let number = 1; number <= count; number++) {
    const preview = element("img", "edition-page");
    preview.alt = `${title}, page ${number}`;
    const previewURL = new URL(sources[number - 1]);
    if (Number.isSafeInteger(edition.revision) && edition.revision > 0) {
      previewURL.searchParams.set("_tdn_revision", String(edition.revision));
    }
    preview.src = previewURL.href;
    preview.referrerPolicy = "no-referrer";
    preview.addEventListener("load", () => {
      if (appState.readerToken !== readerToken) return;
      loaded++;
      finish();
    });
    preview.addEventListener("error", () => {
      if (appState.readerToken !== readerToken) return;
      preview.remove();
      finish();
    });
    pages.append(preview);
  }
}

function applyEditionZoom() {
  const pages = byId("edition-pages");
  const percent = Math.round(appState.editionZoom * 100);
  byId("edition-zoom-value").textContent = `${percent}%`;
  byId("edition-zoom-out").disabled = appState.editionZoom <= EDITION_ZOOM_STEPS[0];
  byId("edition-zoom-in").disabled = appState.editionZoom >= EDITION_ZOOM_STEPS[EDITION_ZOOM_STEPS.length - 1];
  pages.style.setProperty("--reading-scale", String(appState.editionZoom));
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
  for (const id of ["load-older-episodes", "load-older-editions"]) {
    byId(id).addEventListener("click", loadOlderArchive);
  }
  for (const eventName of ["wheel", "touchstart", "pointerdown"]) {
    byId("player-details").addEventListener(eventName, () => {
      appState.transcriptFollowAfter = Date.now() + 8000;
      appState.lastTranscriptSegment = null;
    }, { passive: true });
  }
  const audio = byId("episode-audio");
  const togglePlayback = async () => {
    if (!appState.authorized || !audio.src) return;
    appState.playbackStopped = false;
    if (audio.paused) await audio.play(); else audio.pause();
  };
  const play = () => {
    if (!appState.authorized || !audio.src) return;
    appState.playbackStopped = false;
    return audio.play().catch(() => showAlert("Playback could not start in this browser.", true));
  };
  const stop = () => {
    appState.playbackStopped = true;
    audio.pause(); audio.currentTime = 0; savePlaybackPosition(true); syncPlayer();
  };
  byId("mini-levels-button").addEventListener("click", () => {
    const opened = byId("mini-player").classList.toggle("levels-open");
    byId("mini-levels-button").setAttribute("aria-expanded", String(opened));
  });
  byId("player-chapters").addEventListener("change", (event) => {
    if (!audio.duration) return;
    audio.currentTime = Math.min(audio.duration, Number(event.target.value) || 0);
    savePlaybackPosition(true); syncPlayer();
  });
  if (typeof navigator !== "undefined" && navigator.mediaSession) {
    const actions = { play, pause: () => audio.pause(), stop,
      previoustrack: () => selectRelativeEpisode(-1), nexttrack: () => selectRelativeEpisode(1),
      seekbackward: details => { audio.currentTime = Math.max(0, audio.currentTime - (details.seekOffset || 15)); syncPlayer(); },
      seekforward: details => { audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + (details.seekOffset || 15)); syncPlayer(); },
      seekto: details => { if (Number.isFinite(details.seekTime)) audio.currentTime = Math.min(audio.duration || 0, Math.max(0, details.seekTime)); syncPlayer(); },
    };
    appState.mediaActions = actions;
    // Installed on episode selection; sign-out removes every OS action handler.
  }
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
  byId("edition-readable").addEventListener("click", () => setEditionMode("readable"));
  byId("edition-facsimile").addEventListener("click", () => setEditionMode("pages"));
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
    audio.addEventListener(eventName, () => {
      if (eventName === "loadedmetadata") restorePlaybackPosition();
      if (eventName === "pause" || eventName === "ended") savePlaybackPosition(true);
      syncPlayer();
    });
  }
}

function subscribeToPrivateData(uid) {
  clearSubscriptions();
  const epoch = appState.authEpoch;
  const privateSnapshot = callback => snapshot => {
    if (appState.authorized && appState.user?.uid === uid && epoch === appState.authEpoch) callback(snapshot);
  };
  appState.subscriptions.push(
    onSnapshot(
      query(
        collection(appState.db, "users", uid, "schedules"),
        orderBy("name"),
        limit(100),
      ),
      privateSnapshot(renderSchedules),
      (error) => showAlert(firebaseErrorMessage(error), true),
    ),
    onSnapshot(
      query(
        collection(appState.db, "users", uid, "runRequests"),
        orderBy("requestedAt", "desc"),
        limit(100),
      ),
      privateSnapshot(renderRunRequests),
      (error) => showAlert(firebaseErrorMessage(error), true),
    ),
    onSnapshot(
      query(
        collection(appState.db, "users", uid, "episodes"),
        orderBy("episodeDate", "desc"),
        limit(100),
      ),
      privateSnapshot(renderEpisodes),
      (error) => showAlert(firebaseErrorMessage(error), true),
    ),
    onSnapshot(
      doc(appState.db, "users", uid, "runner", "status"),
      privateSnapshot(renderRunner),
      (error) => showAlert(firebaseErrorMessage(error), true),
    ),
    onSnapshot(
      doc(appState.db, "users", uid, "runner", "lastProfile"),
      privateSnapshot(renderResourceProfile),
      () => {
        if (appState.authorized && appState.user?.uid === uid && epoch === appState.authEpoch) {
          appState.resourceReadUnavailable = true;
          renderResourceSummary();
        }
      },
    ),
  );
}

function setupStickyInsets() {
  const nav = document.querySelector(".mode-nav");
  const mini = byId("mini-player");
  const measure = () => {
    const navHeight = nav.getBoundingClientRect().height;
    if (navHeight > 0) document.documentElement.style.setProperty("--mode-nav-height", `${navHeight}px`);
    document.documentElement.style.setProperty("--mini-player-height", `${mini.getBoundingClientRect().height}px`);
  };
  if (typeof ResizeObserver !== "undefined") {
    const observer = new ResizeObserver(measure);
    observer.observe(nav);
    observer.observe(mini);
  }
  window.addEventListener("resize", measure, { passive: true });
  measure();
}

function setupNavigation() {
  for (const button of document.querySelectorAll(".mode-button")) {
    button.addEventListener("click", () => {
      for (const item of document.querySelectorAll(".mode-button")) {
        item.classList.toggle("active", item === button);
        if (item === button) item.setAttribute("aria-current", "page");
        else item.removeAttribute("aria-current");
      }
      for (const view of document.querySelectorAll(".view")) {
        view.classList.toggle("active", view.id === `view-${button.dataset.view}`);
      }
      byId("mini-player").classList.toggle("context-hidden", button.dataset.view === "play");
      window.scrollTo({ top: 0, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
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
    if (!appState.authorized || document.visibilityState === "hidden") return;
    if (appState.runner?.state === "running") updateRunnerDetail();
    updateRunRequestTimes();
  }, 1000);
}

async function initialize() {
  setupStickyInsets();
  setupNavigation();
  setupInstall();
  setupActivityTracking();
  setupTimezonePicker();
  byId("dismiss-alert-button").addEventListener("click", dismissAlert);
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
  byId("remember-favorites").addEventListener("change", changeFavoriteStorage);
  byId("profile-picker").addEventListener("change", loadFavoriteProfile);
  for (const form of [generationForm, scheduleForm]) {
    setupParameterForm(form);
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
    onAuthStateChanged(appState.auth, handleAuthState);
  } catch (error) {
    setAuthStatus(
      "The secure web configuration is not ready. Complete the V4 Firebase setup first.",
      true,
    );
    byId("sign-in-button").disabled = true;
  }
}

initialize();
