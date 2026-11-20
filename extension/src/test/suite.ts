import * as assert from "assert";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

const ws = process.env.QUOTELY_TEST_WORKSPACE!;

async function waitFor<T>(what: string, probe: () => Promise<T | undefined>, timeoutMs = 60000): Promise<T> {
  const until = Date.now() + timeoutMs;
  while (Date.now() < until) {
    const v = await probe();
    if (v) return v;
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(`Timed out waiting for ${what}`);
}

async function citeFirstSuggestion(file: string, needle: string): Promise<{ doc: vscode.TextDocument; key: string }> {
  const doc = await vscode.workspace.openTextDocument(path.join(ws, file));
  const editor = await vscode.window.showTextDocument(doc);
  const at = doc.positionAt(doc.getText().indexOf(needle) + 5);
  editor.selection = new vscode.Selection(at, at);
  const action = await waitFor(`a Quotely citation for ${file}`, async () => {
    const actions = await vscode.commands.executeCommand<vscode.CodeAction[]>(
      "vscode.executeCodeActionProvider", doc.uri, new vscode.Range(at, at));
    return actions?.find((a) => a.command?.command === "quotely.assist.cite");
  });
  const hovers = await vscode.commands.executeCommand<vscode.Hover[]>("vscode.executeHoverProvider", doc.uri, at);
  const hoverText = hovers.flatMap((h) => h.contents.map((c) => (c as vscode.MarkdownString).value ?? String(c))).join("\n");
  assert.ok(hoverText.includes("Source possible") && hoverText.includes("quotely.assist.cite"), "hover offers the source");
  const key = action.title.match(/citer (\S+)/)![1];
  await vscode.commands.executeCommand(action.command!.command, ...(action.command!.arguments ?? []));
  return { doc, key };
}

export async function run(): Promise<void> {
  const ext = vscode.extensions.getExtension("quotely.quotely")!;
  const api = (await ext.activate()) as { workspaceState: vscode.Memento };
  await api.workspaceState.update("quotely.bibPermission", { [ws]: "always" });
  const bib = path.join(ws, "references.bib");

  const tex = await citeFirstSuggestion("sample.tex", "La part des ouvriers");
  await waitFor("\\cite in the .tex", async () => tex.doc.getText().includes(`~\\cite{${tex.key}}.`));
  assert.ok(tex.doc.getText().includes("\\bibliography{references}"), "bibliography declared in the .tex");
  assert.ok(fs.readFileSync(bib, "utf8").includes(`{${tex.key},`), "entry written to references.bib");

  const md = await citeFirstSuggestion("sample.md", "Every concept class");
  await waitFor("[@…] in the .md", async () => md.doc.getText().includes(`[@${md.key}].`));
  assert.ok(/^---\nbibliography: references\.bib\n---/.test(md.doc.getText()), "bibliography declared in the YAML header");
  assert.ok(fs.readFileSync(bib, "utf8").includes(`{${md.key},`), "second entry appended to references.bib");
  console.log(`[Quotely test] cited ${tex.key} in sample.tex and ${md.key} in sample.md`);
}
