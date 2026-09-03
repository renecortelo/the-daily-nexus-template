"use strict";

const path = require("path");

const toolsRoot = path.join(
  process.env.LOCALAPPDATA,
  "AudioDigest",
  "node-tools",
  "node_modules",
);
const Configstore = require(path.join(toolsRoot, "configstore"));
const firebasePackage = require(
  path.join(toolsRoot, "firebase-tools", "package.json"),
);
const store = new Configstore(firebasePackage.name);

store.set("usage", false);
store.set("gemini", false);

if (store.get("usage") !== false || store.get("gemini") !== false) {
  throw new Error("Firebase CLI privacy preferences could not be enforced.");
}
