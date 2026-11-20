import * as vscode from "vscode";
import * as fs from "fs";
import * as path from "path";

type Flavor = "latex" | "markdown" | "plain";

const PERMISSION_KEY = "quotely.bibPermission";
const DEFAULT_BIB = "references.bib";

let state: vscode.Memento;

export function initBibliography(context: vscode.ExtensionContext): void {
  state = context.workspaceState;
}

export function flavorOf(doc: vscode.TextDocument): Flavor {
  if (doc.languageId === "latex" || doc.languageId === "tex" || doc.fileName.endsWith(".tex")) return "latex";
  if (doc.languageId === "markdown" || doc.fileName.endsWith(".md")) return "markdown";
  return "plain";
}

export function citedKeys(doc: vscode.TextDocument): string[] {
  const text = doc.getText();
  const keys = new Set<string>();
  for (const m of text.matchAll(/\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{([^}]+)\}/g)) {
    m[1].split(",").forEach((k) => keys.add(k.trim()));
  }
  if (flavorOf(doc) === "markdown") {
    for (const m of text.matchAll(/(?:^|[\s\[;-])@([A-Za-z0-9_:\-.]+)/g)) keys.add(m[1].replace(/[.:]+$/, ""));
  }
  return [...keys].filter(Boolean);
}

export function bibFiles(doc: vscode.TextDocument): string[] {
  const text = doc.getText();
  const dir = path.dirname(doc.fileName);
  const names: string[] = [];
  for (const m of text.matchAll(/\\(?:bibliography|addbibresource)\{([^}]+)\}/g)) {
    m[1].split(",").forEach((n) => names.push(n.trim()));
  }
  const yaml = text.match(/^---\n([\s\S]*?)\n---/);
  if (yaml) {
    const single = yaml[1].match(/^bibliography:\s*["']?([^"'\n\[]+)["']?\s*$/m);
    if (single) names.push(single[1].trim());
    for (const m of yaml[1].matchAll(/^\s*-\s*["']?([^"'\n]+\.bib)["']?\s*$/gm)) names.push(m[1].trim());
  }
  return names.map((n) => path.resolve(dir, n.endsWith(".bib") ? n : `${n}.bib`));
}

export function bibHasKey(file: string, key: string): number {
  if (!fs.existsSync(file)) return -1;
  const lines = fs.readFileSync(file, "utf8").split("\n");
  const re = new RegExp(`^\\s*@\\w+\\s*\\{\\s*${key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*,`);
  return lines.findIndex((l) => re.test(l));
}

async function askPermission(doc: vscode.TextDocument, what: string): Promise<boolean> {
  const remembered = state.get<Record<string, string>>(PERMISSION_KEY, {});
  const scope = vscode.workspace.getWorkspaceFolder(doc.uri)?.uri.fsPath ?? doc.fileName;
  if (remembered[scope] === "always") return true;
  const choice = await vscode.window.showInformationMessage(
    `Quotely voudrait ${what}. On y va ?`,
    { modal: false },
    "Oui",
    "Toujours pour ce projet",
    "Non"
  );
  if (choice === "Toujours pour ce projet") {
    await state.update(PERMISSION_KEY, { ...remembered, [scope]: "always" });
    return true;
  }
  return choice === "Oui";
}

function endOfDocument(doc: vscode.TextDocument): vscode.Position {
  return doc.lineAt(doc.lineCount - 1).range.end;
}

async function setupLatex(editor: vscode.TextEditor, bibName: string): Promise<boolean> {
  const doc = editor.document;
  const text = doc.getText();
  const stem = bibName.replace(/\.bib$/, "");
  const biblatex = /\\usepackage(\[[^\]]*\])?\{biblatex\}/.test(text);
  const endDoc = text.lastIndexOf("\\end{document}");
  const at = endDoc >= 0 ? doc.positionAt(endDoc) : endOfDocument(doc);
  return editor.edit((eb) => {
    if (biblatex) {
      const pkg = text.match(/\\usepackage(\[[^\]]*\])?\{biblatex\}.*\n/);
      eb.insert(doc.positionAt(pkg!.index! + pkg![0].length), `\\addbibresource{${bibName}}\n`);
      eb.insert(at, "\\printbibliography\n\n");
    } else {
      eb.insert(at, `\\bibliographystyle{plain}\n\\bibliography{${stem}}\n\n`);
    }
  });
}

async function setupMarkdown(editor: vscode.TextEditor, bibName: string): Promise<boolean> {
  const doc = editor.document;
  const text = doc.getText();
  const yaml = text.match(/^---\n([\s\S]*?)\n---/);
  return editor.edit((eb) => {
    if (yaml) eb.insert(doc.positionAt(4 + yaml[1].length), `\nbibliography: ${bibName}`);
    else eb.insert(new vscode.Position(0, 0), `---\nbibliography: ${bibName}\n---\n\n`);
    if (!/^#{1,3}\s+(Références|References|Bibliographie)\s*$/m.test(text)) {
      eb.insert(endOfDocument(doc), "\n\n## Références\n");
    }
  });
}

function plainReference(entry: string): string {
  const field = (name: string) => entry.match(new RegExp(`${name}\\s*=\\s*\\{+([^}]*)\\}+`, "i"))?.[1] ?? "";
  const authors = field("author").replace(/\s+and\s+/g, ", ") || "Anonyme";
  const year = field("year") || "s.d.";
  const doi = field("doi");
  return `${authors} (${year}). ${field("title")}.${doi ? ` https://doi.org/${doi}` : ""}`;
}

async function addToPlain(editor: vscode.TextEditor, key: string, entry: string): Promise<boolean> {
  const doc = editor.document;
  const text = doc.getText();
  if (text.includes(`[${key}]`)) return true;
  const hasSection = /^(Bibliographie|Références|References)\s*$/m.test(text);
  if (!hasSection && !(await askPermission(doc, "ajouter une section « Bibliographie » à la fin du document"))) return false;
  const line = `[${key}] ${plainReference(entry)}\n`;
  return editor.edit((eb) => eb.insert(endOfDocument(doc), hasSection ? `\n${line}` : `\n\nBibliographie\n\n${line}`));
}

/** Make sure `key` is resolvable from the document; returns false when the user declined. */
export async function ensureEntry(editor: vscode.TextEditor, key: string, entry: string): Promise<boolean> {
  const doc = editor.document;
  const flavor = flavorOf(doc);
  if (flavor === "plain") return addToPlain(editor, key, entry);

  let files = bibFiles(doc);
  if (files.some((f) => bibHasKey(f, key) >= 0)) return true;
  if (files.length === 0) {
    const what = flavor === "latex"
      ? `créer ${DEFAULT_BIB} et ajouter la bibliographie à la fin du document`
      : `créer ${DEFAULT_BIB}, la déclarer dans l'en-tête YAML et ajouter une section « Références »`;
    if (!(await askPermission(doc, what))) return false;
    const ok = flavor === "latex" ? await setupLatex(editor, DEFAULT_BIB) : await setupMarkdown(editor, DEFAULT_BIB);
    if (!ok) return false;
    files = [path.resolve(path.dirname(doc.fileName), DEFAULT_BIB)];
  }
  const target = files.find((f) => fs.existsSync(f)) ?? files[0];
  const prefix = fs.existsSync(target) && fs.readFileSync(target, "utf8").trim() ? "\n\n" : "";
  fs.appendFileSync(target, `${prefix}${entry.trim()}\n`);
  return true;
}

export function locateEntry(doc: vscode.TextDocument, key: string): { file: string; line: number } | null {
  for (const file of bibFiles(doc)) {
    const line = bibHasKey(file, key);
    if (line >= 0) return { file, line };
  }
  const lines = doc.getText().split("\n");
  const inDoc = lines.findIndex((l) => l.includes(`{${key},`) || l.startsWith(`[${key}]`));
  return inDoc >= 0 ? { file: doc.fileName, line: inDoc } : null;
}

export function citeCommand(flavor: Flavor, key: string): string {
  return flavor === "latex" ? `\\cite{${key}}` : flavor === "markdown" ? `[@${key}]` : `[${key}]`;
}

/** Insert a citation at the end of the sentence [start, end), merging with an adjacent one. */
export async function insertCitation(editor: vscode.TextEditor, start: vscode.Position, end: vscode.Position, key: string): Promise<boolean> {
  const doc = editor.document;
  const flavor = flavorOf(doc);
  const sentence = doc.getText(new vscode.Range(start, end));
  const trailing = sentence.match(/([.!?…:;]+["»”)]*)\s*$/);
  const anchorOffset = doc.offsetAt(end) - (trailing ? trailing[0].length : 0);
  const before = doc.getText(new vscode.Range(start, doc.positionAt(anchorOffset)));
  const latexTail = before.match(/\\cite\{([^}]*)\}\s*$/);
  const mdTail = before.match(/\[(@[^\]]*)\]\s*$/);
  return editor.edit((eb) => {
    if (flavor === "latex" && latexTail) {
      eb.insert(doc.positionAt(anchorOffset - latexTail[0].length + latexTail[0].lastIndexOf("}")), `,${key}`);
    } else if (flavor === "markdown" && mdTail) {
      eb.insert(doc.positionAt(anchorOffset - mdTail[0].length + mdTail[0].lastIndexOf("]")), `; @${key}`);
    } else {
      const sep = flavor === "latex" ? "~" : " ";
      eb.insert(doc.positionAt(anchorOffset), `${sep}${citeCommand(flavor, key)}`);
    }
  });
}

export async function insertNote(editor: vscode.TextEditor, at: vscode.Position, key: string, page: number | null, passage: string): Promise<boolean> {
  const doc = editor.document;
  const flavor = flavorOf(doc);
  const excerpt = passage.length > 280 ? `${passage.slice(0, 277)}…` : passage;
  const where = page ? `${key}, p. ${page}` : key;
  const body = `Quotely ▸ [${where}] « ${excerpt.replace(/-->/g, "—>")} »`;
  const line = doc.lineAt(at.line);
  const indent = line.text.match(/^\s*/)?.[0] ?? "";
  const note = flavor === "latex" ? `${indent}% ${body}` : `${indent}<!-- ${body} -->`;
  return editor.edit((eb) => eb.insert(line.range.end, `\n${note}`));
}
