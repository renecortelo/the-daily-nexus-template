"use strict";

const fs = require("node:fs");
const path = require("node:path");

const WEB_FILES = [
  "index.html",
  "styles.css",
  "app.js",
  "cloud-clock-config.js",
  "manifest.webmanifest",
  "service-worker.js",
];
const ASSET_FILES = ["tdn-icon.png", "tdn-icon-transparent.png", "google-g.png"];

function fail(message) {
  throw new Error(message);
}

function argument(name) {
  const index = process.argv.indexOf(name);
  if (index < 0 || index + 1 >= process.argv.length) fail(`Missing required argument: ${name}`);
  return process.argv[index + 1];
}

function cloudClockEndpoint(value) {
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

function copyAllowedFile(source, destination) {
  const details = fs.lstatSync(source);
  if (!details.isFile() || details.isSymbolicLink()) fail("Web release source is unsafe");
  fs.copyFileSync(source, destination);
}

function main() {
  const projectRoot = path.resolve(__dirname, "..");
  const output = path.resolve(argument("--output"));
  const allowedPrefix = `${path.join(projectRoot, "tmp")}${path.sep}`;
  if (!output.startsWith(allowedPrefix)) fail("Release output must stay inside this project's tmp directory");
  const endpoint = cloudClockEndpoint(process.env.TDN_CLOUD_CLOCK_URL || "");
  fs.rmSync(output, { recursive: true, force: true });
  fs.mkdirSync(path.join(output, "assets"), { recursive: true });
  for (const name of WEB_FILES) {
    copyAllowedFile(path.join(projectRoot, "web", name), path.join(output, name));
  }
  for (const name of ASSET_FILES) {
    copyAllowedFile(path.join(projectRoot, "assets", name), path.join(output, "assets", name));
  }
  fs.writeFileSync(
    path.join(output, "cloud-clock-config.js"),
    "// Generated for this private Hosting release.\n"
      + `window.TDN_CLOUD_CLOCK = Object.freeze({ endpoint: ${JSON.stringify(endpoint)} });\n`,
    "utf8",
  );
  process.stdout.write(`Prepared private web release${endpoint ? " with cloud clock" : " without cloud clock"}.\n`);
}

main();
