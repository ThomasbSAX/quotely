import * as cp from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { runTests } from "@vscode/test-electron";

const ROOT = path.resolve(__dirname, "..", "..", "..");
const URL = "http://127.0.0.1:7331";

async function backendReady(): Promise<boolean> {
  try {
    const res = await fetch(`${URL}/health`, { headers: { "X-Quotely-Client": "test" } });
    const h = (await res.json()) as { indexed_documents: number; indexing: { running: boolean } };
    return h.indexed_documents > 0 && !h.indexing.running;
  } catch {
    return false;
  }
}

async function ensureBackend(): Promise<cp.ChildProcess | null> {
  if (await backendReady()) return null;
  const py = path.join(ROOT, "backend", ".venv", "bin", "python3");
  const proc = cp.spawn(py, ["main.py"], { cwd: path.join(ROOT, "backend"), stdio: "ignore" });
  for (let i = 0; i < 600 && !(await backendReady()); i++) await new Promise((r) => setTimeout(r, 1000));
  return proc;
}

async function main(): Promise<void> {
  const backend = await ensureBackend();
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), "quotely-ws-"));
  for (const f of ["sample.tex", "sample.md"]) fs.copyFileSync(path.join(__dirname, "..", "..", "test-fixtures", f), path.join(ws, f));
  fs.mkdirSync(path.join(ws, ".vscode"));
  fs.writeFileSync(path.join(ws, ".vscode", "settings.json"), JSON.stringify({
    "quotely.projectPath": ROOT,
    "quotely.assistant.delay": 200,
  }));
  try {
    await runTests({
      extensionDevelopmentPath: path.resolve(__dirname, "..", ".."),
      extensionTestsPath: path.resolve(__dirname, "suite"),
      launchArgs: [ws, "--disable-extensions", "--user-data-dir", fs.mkdtempSync(path.join(os.tmpdir(), "quotely-user-"))],
      extensionTestsEnv: { QUOTELY_TEST_WORKSPACE: ws },
    });
  } finally {
    backend?.kill();
    fs.rmSync(ws, { recursive: true, force: true });
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
