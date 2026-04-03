/**
 * Prebuild script: copies backend Python files into extension/backend-bundle/
 * so they can be bundled inside the VSIX and extracted on first run.
 */
const fs   = require("fs");
const path = require("path");

const SRC  = path.join(__dirname, "..", "..", "backend");
const DEST = path.join(__dirname, "..", "backend-bundle");

fs.mkdirSync(DEST, { recursive: true });

const INCLUDE = (f) => f.endsWith(".py") || f === "requirements.txt";
const files = fs.readdirSync(SRC).filter(INCLUDE);

for (const file of files) {
  fs.copyFileSync(path.join(SRC, file), path.join(DEST, file));
  console.log(`  bundled: ${file}`);
}

console.log(`[Quotely] Backend bundle ready (${files.length} files) → ${DEST}`);
