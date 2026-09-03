"use strict";

const fs = require("node:fs");
const path = require("node:path");

const EXPECTED_FIREBASE_TOOLS = "15.24.0";
let releasePhase = "startup";

function fail(message) {
  throw new Error(message);
}

function argument(name) {
  const index = process.argv.indexOf(name);
  if (index < 0 || index + 1 >= process.argv.length) {
    fail(`Missing required argument: ${name}`);
  }
  return process.argv[index + 1];
}

function safeProjectId(value) {
  if (!/^[a-z][a-z0-9-]{4,28}[a-z0-9]$/.test(value)) {
    fail("Firebase project ID is invalid");
  }
  return value;
}

function cloudClockOrigin(value) {
  if (!value) return "";
  let parsed;
  try {
    parsed = new URL(value);
  } catch (_error) {
    fail("Cloud clock endpoint is invalid");
  }
  if (
    parsed.protocol !== "https:"
    || !parsed.hostname.endsWith(".workers.dev")
    || parsed.username
    || parsed.password
    || parsed.search
    || parsed.hash
    || !["", "/"].includes(parsed.pathname)
  ) {
    fail("Cloud clock endpoint must be a standard HTTPS workers.dev URL");
  }
  return parsed.origin;
}

function loadRemovalPaths(filename) {
  const raw = fs.readFileSync(filename, "utf8").replace(/^\uFEFF/, "");
  const value = JSON.parse(raw);
  if (!Array.isArray(value) || value.length > 200) {
    fail("Removal manifest is invalid");
  }
  return [...new Set(value.map((item) => {
    if (
      typeof item !== "string"
      || item.length > 500
      || !item.startsWith("/p/")
      || item.includes("..")
      || item.includes("\\")
    ) {
      fail("Removal manifest contains an unsafe path");
    }
    return item;
  }))];
}

function listPublicFiles(root) {
  const files = [];
  function visit(directory, prefix) {
    for (const item of fs.readdirSync(directory, { withFileTypes: true })) {
      const absolute = path.join(directory, item.name);
      const relative = prefix ? `${prefix}/${item.name}` : item.name;
      if (item.isSymbolicLink()) {
        fail("Hosting tree contains a symbolic link");
      }
      if (item.isDirectory()) {
        visit(absolute, relative);
      } else if (item.isFile()) {
        files.push(relative);
      } else {
        fail("Hosting tree contains an unsupported filesystem entry");
      }
    }
  }
  visit(root, "");
  return files.sort();
}

function servingConfig(projectRoot, clockOrigin = "") {
  const firebase = JSON.parse(
    fs.readFileSync(path.join(projectRoot, "firebase.json"), "utf8"),
  );
  const hosting = firebase.hosting;
  if (!hosting || Array.isArray(hosting) || typeof hosting !== "object") {
    fail("Exactly one Firebase Hosting configuration is required");
  }
  const allowed = new Set(["public", "ignore", "headers"]);
  for (const key of Object.keys(hosting)) {
    if (!allowed.has(key)) {
      fail(`Unsupported Firebase Hosting configuration: ${key}`);
    }
  }
  const headers = (hosting.headers || []).map((entry) => {
    if (
      !entry
      || typeof entry.source !== "string"
      || !Array.isArray(entry.headers)
    ) {
      fail("Firebase Hosting header configuration is invalid");
    }
    const values = {};
    for (const header of entry.headers) {
      if (
        !header
        || typeof header.key !== "string"
        || typeof header.value !== "string"
      ) {
        fail("Firebase Hosting header value is invalid");
      }
      values[header.key] = header.value;
    }
    if (clockOrigin && typeof values["Content-Security-Policy"] === "string") {
      const csp = values["Content-Security-Policy"];
      if (!csp.includes("connect-src ")) {
        fail("Hosting Content-Security-Policy is missing connect-src");
      }
      values["Content-Security-Policy"] = csp.replace(
        "connect-src ",
        `connect-src ${clockOrigin} `,
      );
    }
    return { glob: entry.source, headers: values };
  });
  return { headers };
}

async function removePaths(client, versionName, paths) {
  for (let index = 0; index < paths.length; index += 1000) {
    const files = Object.fromEntries(
      paths.slice(index, index + 1000).map((item) => [item, ""]),
    );
    await client.post(`/${versionName}:populateFiles`, { files });
  }
}

function extractVersionName(result) {
  if (typeof result === "string") {
    return result;
  }
  if (result && typeof result === "object") {
    if (typeof result.name === "string") return result.name;
    if (result.response && typeof result.response.name === "string") return result.response.name;
    if (result.version && typeof result.version.name === "string") return result.version.name;
  }
  return null;
}

async function main() {
  releasePhase = "validating the private release inputs";
  if (!process.env.FIREBASE_TOKEN) {
    fail("Firebase deployment authorization is unavailable");
  }
  const projectRoot = path.resolve(__dirname, "..");
  const projectId = safeProjectId(argument("--project"));
  const publicRoot = path.resolve(argument("--public"));
  const removalManifest = path.resolve(argument("--remove-manifest"));
  const clockOrigin = cloudClockOrigin(process.env.TDN_CLOUD_CLOCK_URL || "");
  if (
    !publicRoot.startsWith(`${projectRoot}${path.sep}`)
    || !fs.statSync(publicRoot).isDirectory()
  ) {
    fail("Hosting public directory escaped the project");
  }
  const packageInfo = require("firebase-tools/package.json");
  if (packageInfo.version !== EXPECTED_FIREBASE_TOOLS) {
    fail("The pinned Firebase CLI version is not installed");
  }
  const apiv2 = require("firebase-tools/lib/apiv2");
  apiv2.setRefreshToken(process.env.FIREBASE_TOKEN);
  const hostingApi = require("firebase-tools/lib/hosting/api");
  const { Uploader } = require("firebase-tools/lib/deploy/hosting/uploader");
  const { Client } = apiv2;
  const { hostingApiOrigin } = require("firebase-tools/lib/api");
  releasePhase = "reading the live Hosting release";
  let channel;
  try {
    channel = await hostingApi.getChannel(projectId, projectId, "live");
  } catch (_channelError) {
    channel = null;
  }
  let versionName = null;
  const sourceVersion = channel?.release?.version?.name;
  if (typeof sourceVersion === "string" && sourceVersion) {
    releasePhase = "cloning the live Hosting release";
    try {
      const operation = await hostingApi.cloneVersion(
        projectId,
        sourceVersion,
        false,
      );
      versionName = extractVersionName(operation);
    } catch (_cloneError) {
      // Error details can include private Hosting metadata. The phase in the
      // final safe failure message is sufficient for troubleshooting.
      versionName = null;
    }
  }
  if (!versionName) {
    releasePhase = "creating the first Hosting release";
    const created = await hostingApi.createVersion(
      projectId,
      { status: "CREATED" },
    );
    versionName = extractVersionName(created);
  }
  if (
    typeof versionName !== "string"
    || !new RegExp(`^sites/${projectId}/versions/[A-Za-z0-9_-]+$`).test(versionName)
  ) {
    fail("Firebase did not create a safe Hosting version");
  }
  const client = new Client({
    urlPrefix: hostingApiOrigin(),
    apiVersion: "v1beta1",
    auth: true,
  });
  const removals = loadRemovalPaths(removalManifest);
  if (removals.length) {
    releasePhase = "retiring previous private media";
    await removePaths(client, versionName, removals);
  }
  releasePhase = "preparing the local private release";
  const files = listPublicFiles(publicRoot);
  if (!files.length || files.length > 500) {
    fail("Hosting delta contains an invalid number of files");
  }
  const uploader = new Uploader({
    version: versionName,
    files,
    public: publicRoot,
    cwd: projectRoot,
    projectRoot,
  });
  releasePhase = "uploading the changed private files";
  await uploader.start();
  const versionId = versionName.split("/").at(-1);
  releasePhase = "finalizing the private Hosting release";
  await hostingApi.updateVersion(
    projectId,
    versionId,
    {
      status: "FINALIZED",
      config: servingConfig(projectRoot, clockOrigin),
    },
  );
  releasePhase = "activating the private Hosting release";
  await hostingApi.createRelease(
    projectId,
    "live",
    versionName,
    { message: "The Daily Nexus private incremental release" },
  );
  process.stdout.write(
    `Private Firebase release completed with ${files.length} local files`
    + ` and ${removals.length} retired media paths.\n`,
  );
}

main().catch((error) => {
  // A generic error class and phase are enough to diagnose a release without
  // exposing provider metadata in logs that may be shared outside private use.
  const name = error && typeof error.name === "string" ? error.name : "Error";
  process.stderr.write(`Private Firebase release failed (${name} // ${releasePhase}).\n`);
  process.exitCode = 1;
});
