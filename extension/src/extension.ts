import * as vscode from "vscode";
import * as path from "path";
import * as os from "os";
import * as cp from "child_process";
import * as fs from "fs";
import { CitationCompletionProvider, CitationHoverProvider } from "./provider";
import { checkHealth, listPapers, getSuggestions, searchDocuments, ingestFolder, reindexAll } from "./api";
import { QuotelyDecorationProvider } from "./decoration";
import { CitationLinkProvider, updateCitationDecorations } from "./citationLinks";

const SUPPORTED_LANGUAGES = ["latex", "markdown", "plaintext", "tex"];

let statusBarItem: vscode.StatusBarItem;
let completionProvider: CitationCompletionProvider;
let backendProcess: cp.ChildProcess | null = null;
let decorationProvider: QuotelyDecorationProvider;

// TextEditorDecorationType for [n] superscript badges on \cite{KEY}
let citationDecorationType: vscode.TextEditorDecorationType;

export async function activate(context: vscode.ExtensionContext) {
  statusBarItem = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    100
  );
  statusBarItem.command = "quotely.listPapers";
  statusBarItem.text = "$(book) Quotely";
  statusBarItem.tooltip = "Quotely: click to list indexed papers";
  statusBarItem.show();
  context.subscriptions.push(statusBarItem);

  // Check backend health and update status bar
  const projectPath = await ensureProjectPath(context);
  await startBackendIfOffline(projectPath);
  await updateStatusBar();
  setInterval(updateStatusBar, 15000);

  // File decoration provider — green ✓ badge on indexed files in the Explorer
  decorationProvider = new QuotelyDecorationProvider();
  context.subscriptions.push(
    vscode.window.registerFileDecorationProvider(decorationProvider)
  );
  // Initial decoration fetch + refresh every 30 s (only when backend is up)
  await decorationProvider.refresh();
  setInterval(() => decorationProvider.refresh(), 30000);

  // Auto-index current workspace on activation + when workspace changes
  autoIndexWorkspace(context);
  context.subscriptions.push(
    vscode.workspace.onDidChangeWorkspaceFolders(() => autoIndexWorkspace(context))
  );

  // [n] superscript badges on \cite{KEY} — created once, disposed on deactivate
  citationDecorationType = vscode.window.createTextEditorDecorationType({
    color: new vscode.ThemeColor("textLink.foreground"),
    textDecoration: "underline",
    cursor: "pointer",
  });
  context.subscriptions.push(citationDecorationType);

  // Register DocumentLinkProvider so \cite{KEY} is a clickable link → bibliography
  for (const lang of ["latex", "markdown"]) {
    context.subscriptions.push(
      vscode.languages.registerDocumentLinkProvider(
        { language: lang },
        new CitationLinkProvider()
      )
    );
  }

  // Update [n] badges whenever the active editor changes or content changes
  const refreshCitationBadges = (editor: vscode.TextEditor | undefined) => {
    if (editor) updateCitationDecorations(editor, citationDecorationType);
  };
  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor(refreshCitationBadges),
    vscode.workspace.onDidChangeTextDocument((e) => {
      const editor = vscode.window.activeTextEditor;
      if (editor && editor.document === e.document) {
        updateCitationDecorations(editor, citationDecorationType);
      }
    })
  );
  if (vscode.window.activeTextEditor) {
    refreshCitationBadges(vscode.window.activeTextEditor);
  }

  // Command: jump to bibliography entry (used by \cite{KEY} document links)
  context.subscriptions.push(
    vscode.commands.registerCommand(
      "quotely.goToBibEntry",
      async (filePath: string, line: number) => {
        const doc = await vscode.workspace.openTextDocument(filePath);
        const editor = await vscode.window.showTextDocument(doc);
        const pos = new vscode.Position(line, 0);
        editor.selection = new vscode.Selection(pos, pos);
        editor.revealRange(
          new vscode.Range(pos, pos),
          vscode.TextEditorRevealType.InCenter
        );
      }
    )
  );

  completionProvider = new CitationCompletionProvider();
  const hoverProvider = new CitationHoverProvider(completionProvider);

  // Register inline completion for supported languages
  for (const lang of SUPPORTED_LANGUAGES) {
    context.subscriptions.push(
      vscode.languages.registerInlineCompletionItemProvider({ language: lang }, completionProvider)
    );
    context.subscriptions.push(
      vscode.languages.registerHoverProvider({ language: lang }, hoverProvider)
    );
  }

  // Command: search across all indexed documents
  context.subscriptions.push(
    vscode.commands.registerCommand("quotely.search", async () => {
      const query = await vscode.window.showInputBox({
        prompt: "Quotely — Recherche dans les documents indexés",
        placeHolder: "Ex: tensor spectral norm, graphes, réseaux de neurones…",
      });
      if (!query?.trim()) return;

      const mode = await vscode.window.showQuickPick(
        [
          { label: "$(search) Mot-clé exact", description: "Trouve les chunks contenant exactement ce mot", value: "keyword" as const },
          { label: "$(lightbulb) Sémantique", description: "Trouve les documents conceptuellement proches", value: "semantic" as const },
        ],
        { placeHolder: "Mode de recherche" }
      );
      if (!mode) return;

      let results;
      try {
        results = await searchDocuments(query, mode.value, 30);
      } catch {
        await showBackendOffline();
        return;
      }

      if (results.length === 0) {
        vscode.window.showInformationMessage(`Quotely: aucun résultat pour "${query}"`);
        return;
      }

      const items = results.map((r) => ({
        label: `$(file-text) ${r.bibtex_key}`,
        description: `${r.authors || "?"} (${r.year})`,
        detail: mode.value === "keyword"
          ? `${r.title}  |  « ${r.excerpt} »`
          : r.title,
        key: r.bibtex_key,
        bibtex_key: r.bibtex_key,
      }));

      const selected = await vscode.window.showQuickPick(items, {
        placeHolder: `${results.length} résultat(s) pour "${query}" — Sélectionne pour insérer la clé`,
        matchOnDescription: true,
        matchOnDetail: true,
      });

      if (!selected) return;

      const editor = vscode.window.activeTextEditor;
      if (editor) {
        await editor.edit((eb) => eb.insert(editor.selection.active, selected.bibtex_key));
      } else {
        await vscode.env.clipboard.writeText(selected.bibtex_key);
        vscode.window.showInformationMessage(`Quotely: clé "${selected.bibtex_key}" copiée dans le presse-papiers`);
      }
    })
  );

  // Command: manually trigger citation picker (keybinding fallback)
  context.subscriptions.push(
    vscode.commands.registerCommand("quotely.suggest", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;

      const position = editor.selection.active;
      const contextText = getContext(editor.document, position, 3);
      if (!contextText.trim()) {
        vscode.window.showInformationMessage("Quotely: écris d'abord du texte pour contextualiser la citation.");
        return;
      }

      let citations;
      try {
        citations = await getSuggestions(contextText, 8, vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "");
      } catch {
        await showBackendOffline();
        return;
      }

      if (citations.length === 0) {
        vscode.window.showInformationMessage("Quotely: aucun article indexé. Dépose des PDFs dans data/papers/");
        return;
      }

      const items = citations.map((c) => ({
        label: `$(references) ${c.bibtex_key}`,
        description: `${c.authors || "?"} (${c.year}) — ${Math.round(c.score * 100)}%`,
        detail: c.title,
        key: c.bibtex_key,
        bibtex_entry: c.bibtex_entry,
      }));

      const selected = await vscode.window.showQuickPick(items, {
        placeHolder: "Sélectionne une citation à insérer",
        matchOnDescription: true,
        matchOnDetail: true,
      });

      if (!selected) return;

      // Insert key at cursor, replacing content between \cite{ and } if applicable
      const fullLine = editor.document.lineAt(position.line).text;
      const textBefore = fullLine.slice(0, position.character);
      const textAfter = fullLine.slice(position.character);

      const isLatex = editor.document.languageId === "latex" || editor.document.fileName.endsWith(".tex");
      if (/\\cite\{[^}]*$/.test(textBefore)) {
        // Already inside \cite{} → replace content between braces
        const closingIdx = textAfter.indexOf("}");
        const end = closingIdx >= 0 ? position.translate(0, closingIdx) : position;
        await editor.edit((eb) => eb.replace(new vscode.Range(position, end), selected.key));
      } else {
        // Outside \cite{} → insert a proper citation command
        const cite = isLatex ? `\\cite{${selected.key}}` : `[@${selected.key}]`;
        await editor.edit((eb) => eb.insert(position, cite));
      }

      // Auto-insert BibTeX entry in bibliography block
      await ensureBibliographyEntry(editor, selected.key, selected.bibtex_entry);
    })
  );

  // Command: insert bibliography at end of document
  context.subscriptions.push(
    vscode.commands.registerCommand("quotely.insertBibliography", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;

      let allPapers;
      try {
        allPapers = await listPapers();
      } catch {
        await showBackendOffline();
        return;
      }

      if (allPapers.length === 0) {
        vscode.window.showInformationMessage("Quotely: aucun article indexé. Dépose des PDFs dans data/papers/");
        return;
      }

      const isLatex = editor.document.languageId === "latex" ||
        editor.document.fileName.endsWith(".tex");

      insertBibliographyBlock(editor, allPapers, isLatex);
    })
  );

  // Command: open papers folder
  context.subscriptions.push(
    vscode.commands.registerCommand("quotely.openPapersFolder", async () => {
      const papersPath = path.join(projectPath || path.join(os.homedir(), ".quotely"), "data", "papers");
      fs.mkdirSync(papersPath, { recursive: true });
      vscode.env.openExternal(vscode.Uri.file(papersPath));
    })
  );

  // Command: list indexed papers in quick pick
  context.subscriptions.push(
    vscode.commands.registerCommand("quotely.listPapers", async () => {
      let papers;
      try {
        papers = await listPapers();
      } catch {
        await showBackendOffline();
        return;
      }

      if (papers.length === 0) {
        vscode.window.showInformationMessage(
          "Quotely: No papers indexed yet. Drop files in data/papers/"
        );
        return;
      }

      const items = papers.map((p) => ({
        label: `$(file-text) ${p.bibtex_key}`,
        description: `${p.authors || "Unknown"} (${p.year})`,
        detail: p.title,
        bibtex: p.bibtex_entry,
      }));

      const selected = await vscode.window.showQuickPick(items, {
        placeHolder: `${papers.length} papers indexed — Select to copy BibTeX`,
        matchOnDescription: true,
        matchOnDetail: true,
      });

      if (selected) {
        await vscode.env.clipboard.writeText(selected.bibtex);
        vscode.window.showInformationMessage(
          `Quotely: BibTeX for "${selected.label}" copied to clipboard.`
        );
      }
    })
  );

  // Command: open source file of a citation (used by hover links)
  context.subscriptions.push(
    vscode.commands.registerCommand("quotely.openFile", async (filePath: string) => {
      if (!filePath) return;
      try {
        await vscode.commands.executeCommand("vscode.open", vscode.Uri.file(filePath));
      } catch {
        vscode.env.openExternal(vscode.Uri.file(filePath));
      }
    })
  );

  // Command: add a custom folder to the index
  context.subscriptions.push(
    vscode.commands.registerCommand("quotely.addFolder", async () => {
      const uris = await vscode.window.showOpenDialog({
        canSelectFiles: false,
        canSelectFolders: true,
        canSelectMany: false,
        openLabel: "Indexer ce dossier",
        title: "Quotely — Choisir un dossier à indexer",
      });
      if (!uris || uris.length === 0) return;

      const folderPath = uris[0].fsPath;
      await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: `Quotely: indexation de "${path.basename(folderPath)}"…`,
          cancellable: false,
        },
        async () => {
          try {
            const result = await ingestFolder(folderPath);
            vscode.window.showInformationMessage(
              `Quotely: ${result.indexed} article(s) indexé(s), ${result.skipped} déjà présent(s).` +
              (result.errors.length ? ` ${result.errors.length} erreur(s).` : "")
            );
            await decorationProvider.refresh();
          } catch (e: any) {
            vscode.window.showErrorMessage(`Quotely: erreur d'indexation — ${e.message}`);
          }
        }
      );
    })
  );

  // Command: clear DB and re-index everything (picks up title-boost improvements)
  context.subscriptions.push(
    vscode.commands.registerCommand("quotely.reindex", async () => {
      const confirm = await vscode.window.showWarningMessage(
        "Quotely: réindexer tout le corpus ? La base sera effacée puis reconstruite avec les nouveaux embeddings.",
        { modal: true },
        "Réindexer"
      );
      if (confirm !== "Réindexer") return;

      await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: "Quotely: réindexation en cours… (peut prendre plusieurs minutes)",
          cancellable: false,
        },
        async () => {
          try {
            const result = await reindexAll();
            vscode.window.showInformationMessage(
              `Quotely: réindexation terminée — ${result.total} article(s).` +
              (result.errors.length ? ` ${result.errors.length} erreur(s).` : "")
            );
            await decorationProvider.refresh();
          } catch (e: any) {
            vscode.window.showErrorMessage(`Quotely: erreur de réindexation — ${e.message}`);
          }
        }
      );
    })
  );

  // Command: re-run setup / repair backend
  context.subscriptions.push(
    vscode.commands.registerCommand("quotely.setup", async () => {
      const projectPath = await ensureProjectPath(context);
      if (projectPath) {
        await startBackendIfOffline(projectPath);
        const healthy = await checkHealth();
        if (healthy) {
          vscode.window.showInformationMessage("Quotely: backend is running ✓");
          await updateStatusBar();
          await decorationProvider.refresh();
        } else {
          vscode.window.showWarningMessage(
            "Quotely: setup done but backend not yet reachable — it may still be loading (up to 20s)."
          );
        }
      }
    })
  );

  console.log("[Quotely] Extension activated.");
}

export function deactivate() {
  if (backendProcess) {
    backendProcess.kill();
    backendProcess = null;
  }
}

// ---------------------------------------------------------------------------
// Backend auto-start
// ---------------------------------------------------------------------------

async function startBackendIfOffline(projectPath: string): Promise<void> {
  if (!projectPath) return;

  const healthy = await checkHealth();
  if (healthy) return;

  const isWin = process.platform === "win32";
  const pythonBin = isWin
    ? path.join(projectPath, "backend", ".venv", "Scripts", "python.exe")
    : path.join(projectPath, "backend", ".venv", "bin", "python3");
  const mainPy = path.join(projectPath, "backend", "main.py");

  if (!fs.existsSync(pythonBin) || !fs.existsSync(mainPy)) {
    console.log("[Quotely] Backend not found at:", projectPath);
    return;
  }

  const env = { ...process.env } as NodeJS.ProcessEnv;
  delete env["PYTHONPATH"];

  backendProcess = cp.spawn(pythonBin, [mainPy], {
    cwd: path.join(projectPath, "backend"),
    env,
    detached: false,
    stdio: "ignore",
  });

  backendProcess.on("error", (err) => {
    console.error("[Quotely] Backend spawn error:", err.message);
  });

  backendProcess.unref();
  console.log("[Quotely] Backend started (PID", backendProcess.pid, ")");

  await new Promise((resolve) => setTimeout(resolve, 3500));
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function updateStatusBar() {
  const healthy = await checkHealth();
  if (healthy) {
    statusBarItem.text = "$(book) Quotely ✓";
    statusBarItem.backgroundColor = undefined;
    statusBarItem.tooltip = "Quotely: backend running — click to list papers";
  } else {
    statusBarItem.text = "$(book) Quotely ✗";
    statusBarItem.backgroundColor = new vscode.ThemeColor(
      "statusBarItem.warningBackground"
    );
    statusBarItem.tooltip = "Quotely: backend offline — click for setup";
    statusBarItem.command = "quotely.setup";
  }
}

async function showBackendOffline(): Promise<void> {
  const action = await vscode.window.showErrorMessage(
    "Quotely: backend offline. Run setup or wait for it to start.",
    "Setup / Repair",
    "Dismiss"
  );
  if (action === "Setup / Repair") {
    await vscode.commands.executeCommand("quotely.setup");
  }
}

async function ensureBibliographyEntry(
  editor: vscode.TextEditor,
  key: string,
  bibtexEntry: string
): Promise<void> {
  const doc = editor.document;
  const text = doc.getText();
  const isLatex = doc.languageId === "latex" || doc.fileName.endsWith(".tex");

  const marker = isLatex ? "% === Quotely Bibliography ===" : "<!-- Quotely Bibliography -->";

  // Already cited → nothing to do
  if (text.includes(`{${key},`) || text.includes(`@${key}`)) return;

  const markerIdx = text.indexOf(marker);

  if (markerIdx === -1) {
    // No section yet → create it at the end of the document
    const lastLine = doc.lineAt(doc.lineCount - 1);
    const block = isLatex
      ? `\n\n${marker}\n${bibtexEntry}\n`
      : `\n\n${marker}\n${bibtexEntry}\n`;
    await editor.edit((eb) => eb.insert(lastLine.range.end, block));
  } else {
    // Section exists → append the new entry just before end of file,
    // but inside the block (find the last @article or the marker itself)
    const afterMarker = text.indexOf("\n", markerIdx) + 1;
    const insertPos = doc.positionAt(afterMarker);
    await editor.edit((eb) => eb.insert(insertPos, `${bibtexEntry}\n\n`));
  }
}

function getContext(
  document: vscode.TextDocument,
  position: vscode.Position,
  numParagraphs: number
): string {
  const lines: string[] = [];
  let paragraphCount = 0;
  let i = position.line;
  while (i >= 0 && paragraphCount < numParagraphs) {
    const line = document.lineAt(i).text.trim();
    if (line === "") {
      if (lines.length > 0) paragraphCount++;
    } else {
      lines.unshift(line);
    }
    i--;
  }
  return lines.join(" ");
}

function insertBibliographyBlock(
  editor: vscode.TextEditor,
  papers: Awaited<ReturnType<typeof listPapers>>,
  isLatex: boolean
) {
  const doc = editor.document;
  const lastLine = doc.lineAt(doc.lineCount - 1);
  const insertPosition = lastLine.range.end;

  let block: string;
  if (isLatex) {
    const entries = papers.map((p) => p.bibtex_entry).join("\n\n");
    block = `\n\n% === Quotely Bibliography ===\n${entries}\n`;
  } else {
    const entries = papers
      .map((p) => `[@${p.bibtex_key}]: # "${p.title}" — ${p.authors} (${p.year})`)
      .join("\n");
    block = `\n\n<!-- Quotely Bibliography -->\n${entries}\n`;
  }

  editor.edit((editBuilder) => {
    editBuilder.insert(insertPosition, block);
  });

  vscode.window.showInformationMessage(
    `Quotely: ${papers.length} bibliography entries inserted.`
  );
}

// ---------------------------------------------------------------------------
// Workspace auto-indexing
// ---------------------------------------------------------------------------

const INDEXED_WORKSPACES_KEY = "quotely.indexedWorkspaces";

/**
 * Auto-index all supported documents in the current workspace folder.
 * Runs silently in the background. Skips workspaces already indexed.
 */
async function autoIndexWorkspace(context: vscode.ExtensionContext): Promise<void> {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length === 0) return;

  const healthy = await checkHealth();
  if (!healthy) return;

  const indexed: string[] = context.globalState.get<string[]>(INDEXED_WORKSPACES_KEY, []);

  for (const folder of folders) {
    const folderPath = folder.uri.fsPath;
    if (indexed.includes(folderPath)) continue;

    // Check if folder has any supported files before calling the backend
    const hasDocs = fs.readdirSync(folderPath).some((f) => {
      const ext = path.extname(f).toLowerCase();
      return SUPPORTED_DOC_EXTENSIONS.has(ext);
    });
    if (!hasDocs) {
      // Also check one level deep
      const subDirs = fs.readdirSync(folderPath).filter((f) => {
        try { return fs.statSync(path.join(folderPath, f)).isDirectory(); } catch { return false; }
      });
      const hasDocsDeep = subDirs.some((dir) =>
        fs.readdirSync(path.join(folderPath, dir)).some((f) =>
          SUPPORTED_DOC_EXTENSIONS.has(path.extname(f).toLowerCase())
        )
      );
      if (!hasDocsDeep) continue;
    }

    try {
      await ingestFolder(folderPath);
      indexed.push(folderPath);
      await context.globalState.update(INDEXED_WORKSPACES_KEY, indexed);
      await decorationProvider.refresh();
      console.log(`[Quotely] Auto-indexed workspace: ${folderPath}`);
    } catch {
      // Backend not ready yet — will retry on next window open
    }
  }
}

const SUPPORTED_DOC_EXTENSIONS = new Set([
  ".pdf", ".tex", ".docx", ".doc", ".md", ".txt",
  ".pptx", ".ppt", ".odt", ".rtf", ".xlsx", ".xls", ".csv", ".ipynb",
  ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp",
]);

// ---------------------------------------------------------------------------
// First-run auto-setup
// ---------------------------------------------------------------------------

/** Returns project path (configured or freshly installed). */
async function ensureProjectPath(context: vscode.ExtensionContext): Promise<string> {
  const config = vscode.workspace.getConfiguration("quotely");
  const configured = config.get<string>("projectPath") ?? "";

  // Already configured and venv present → nothing to do
  if (configured && hasVenv(configured)) {
    return configured;
  }

  // Check default install location (for users who ran setup.sh or a previous auto-setup)
  const defaultPath = path.join(os.homedir(), ".quotely");
  if (hasVenv(defaultPath)) {
    await config.update("projectPath", defaultPath, vscode.ConfigurationTarget.Global);
    return defaultPath;
  }

  // First-time: ask where to install
  const choice = await vscode.window.showInformationMessage(
    "Welcome to Quotely! The AI backend needs to be installed (~300 MB, one-time setup).",
    "Install in ~/.quotely",
    "Choose location"
  );

  if (!choice) return configured; // user dismissed → fall back gracefully

  let installPath = defaultPath;
  if (choice === "Choose location") {
    const uris = await vscode.window.showOpenDialog({
      canSelectFiles: false,
      canSelectFolders: true,
      canSelectMany: false,
      openLabel: "Install Quotely here",
      title: "Choose Quotely installation folder",
    });
    if (!uris || uris.length === 0) return configured;
    installPath = path.join(uris[0].fsPath, ".quotely");
  }

  try {
    await runFirstTimeSetup(context, installPath);
    await config.update("projectPath", installPath, vscode.ConfigurationTarget.Global);
    vscode.window.showInformationMessage(
      `Quotely installed in ${installPath}. Drop PDFs in: ${installPath}/data/papers/`
    );
    return installPath;
  } catch (e: any) {
    vscode.window.showErrorMessage(
      `Quotely setup failed: ${e.message}\n\nMake sure Python 3.10+ is installed.`
    );
    return configured;
  }
}

function hasVenv(installPath: string): boolean {
  const bin = process.platform === "win32"
    ? path.join(installPath, "backend", ".venv", "Scripts", "python.exe")
    : path.join(installPath, "backend", ".venv", "bin", "python3");
  return fs.existsSync(bin);
}

async function runFirstTimeSetup(
  context: vscode.ExtensionContext,
  installPath: string
): Promise<void> {
  const backendDest = path.join(installPath, "backend");

  // Create directory layout
  fs.mkdirSync(backendDest, { recursive: true });
  fs.mkdirSync(path.join(installPath, "data", "papers"), { recursive: true });
  fs.mkdirSync(path.join(installPath, "data", "db"), { recursive: true });

  // Copy bundled backend files (packed inside VSIX at install time)
  const bundleSrc = path.join(context.extensionPath, "backend-bundle");
  if (fs.existsSync(bundleSrc)) {
    for (const file of fs.readdirSync(bundleSrc)) {
      fs.copyFileSync(path.join(bundleSrc, file), path.join(backendDest, file));
    }
  }

  const python = await findPython();
  if (!python) {
    throw new Error("Python 3.10+ not found. Install from https://python.org then reload VS Code.");
  }

  await vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: "Quotely: setting up backend…",
      cancellable: false,
    },
    async (progress) => {
      progress.report({ message: "Creating Python environment…" });
      await spawnAsync(python, ["-m", "venv", ".venv"], backendDest);

      const pip = process.platform === "win32"
        ? path.join(backendDest, ".venv", "Scripts", "pip")
        : path.join(backendDest, ".venv", "bin", "pip");

      progress.report({ message: "Installing packages (~300 MB, please wait)…" });
      await spawnAsync(pip, ["install", "--upgrade", "pip", "--quiet"], backendDest);
      await spawnAsync(pip, ["install", "-r", "requirements.txt", "--quiet"], backendDest);
    }
  );
}

async function findPython(): Promise<string | null> {
  const candidates = process.platform === "win32"
    ? ["python", "python3", "py"]
    : ["python3", "python"];

  for (const cmd of candidates) {
    try {
      const out = await spawnAsync(cmd, ["--version"], ".", true);
      // Accept Python 3.8+
      if (/Python 3\.([89]|[1-9]\d)/.test(out)) return cmd;
    } catch { /* try next */ }
  }
  return null;
}

function spawnAsync(
  cmd: string,
  args: string[],
  cwd: string,
  silent = false
): Promise<string> {
  return new Promise((resolve, reject) => {
    const env = { ...process.env } as NodeJS.ProcessEnv;
    delete env["PYTHONPATH"];
    const proc = cp.spawn(cmd, args, { cwd, env });
    let stdout = "";
    let stderr = "";
    proc.stdout?.on("data", (d: Buffer) => { stdout += d.toString(); });
    proc.stderr?.on("data", (d: Buffer) => { stderr += d.toString(); if (!silent) console.error("[Quotely]", d.toString()); });
    proc.on("close", (code) =>
      code === 0
        ? resolve(stdout + stderr)
        : reject(new Error(`${cmd} exited with code ${code}\n${stderr}`))
    );
    proc.on("error", reject);
  });
}
