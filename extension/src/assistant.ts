import * as vscode from "vscode";
import { assist, AssistItem, CitationResult } from "./api";
import { citedKeys, ensureEntry, insertCitation, insertNote } from "./bibliography";

const LANGUAGES = new Set(["latex", "tex", "markdown", "plaintext"]);
const DISMISSED_KEY = "quotely.dismissed";
const THRESHOLDS: Record<string, number> = { faible: 0.84, normale: 0.82, "élevée": 0.80 };
const CACHE_SIZE = 300;

interface Flag {
  uri: string;
  offset: number;
  item: AssistItem;
}

interface ActionArgs {
  uri: string;
  offset: number;
  sentence: string;
  index: number;
}

function cfg() {
  return vscode.workspace.getConfiguration("quotely.assistant");
}

function hash(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  return `${s.length}:${h}`;
}

export class WritingAssistant implements vscode.HoverProvider, vscode.CodeActionProvider {
  private flags = new Map<string, Flag[]>();
  private timers = new Map<string, NodeJS.Timeout>();
  private cache = new Map<string, AssistItem[]>();
  private status: vscode.StatusBarItem;
  private citeDeco = vscode.window.createTextEditorDecorationType({
    textDecoration: "underline dotted",
    after: { contentText: " 📚", color: new vscode.ThemeColor("editorCodeLens.foreground") },
  });
  private recallDeco = vscode.window.createTextEditorDecorationType({
    textDecoration: "underline dotted",
    after: { contentText: " 📝", color: new vscode.ThemeColor("editorCodeLens.foreground") },
  });

  constructor(private context: vscode.ExtensionContext) {
    this.status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 99);
    this.status.command = "quotely.assist.list";
    const sub = context.subscriptions;
    sub.push(this.status, this.citeDeco, this.recallDeco);
    sub.push(
      vscode.workspace.onDidChangeTextDocument((e) => this.onChange(e)),
      vscode.window.onDidChangeActiveTextEditor((ed) => ed && (this.render(ed), this.schedule(ed.document, 200))),
      vscode.workspace.onDidCloseTextDocument((d) => this.flags.delete(d.uri.toString())),
      vscode.commands.registerCommand("quotely.assist.cite", (a: ActionArgs) => this.cite(a)),
      vscode.commands.registerCommand("quotely.assist.note", (a: ActionArgs) => this.note(a)),
      vscode.commands.registerCommand("quotely.assist.dismiss", (a: ActionArgs) => this.dismiss(a)),
      vscode.commands.registerCommand("quotely.assist.open", (a: ActionArgs) => this.open(a)),
      vscode.commands.registerCommand("quotely.assist.list", () => this.list()),
      vscode.commands.registerCommand("quotely.assist.analyze", () => this.analyzeAll()),
      vscode.commands.registerCommand("quotely.assist.toggle", async () => {
        const on = !cfg().get<boolean>("enabled", true);
        await cfg().update("enabled", on, vscode.ConfigurationTarget.Global);
        if (!on) this.clearAll();
        vscode.window.setStatusBarMessage(`Quotely : assistant ${on ? "activé" : "désactivé"}`, 3000);
      })
    );
    for (const lang of LANGUAGES) {
      sub.push(vscode.languages.registerHoverProvider({ language: lang }, this));
      sub.push(vscode.languages.registerCodeActionsProvider({ language: lang }, this, {
        providedCodeActionKinds: [vscode.CodeActionKind.QuickFix],
      }));
    }
    if (vscode.window.activeTextEditor) this.schedule(vscode.window.activeTextEditor.document, 500);
  }

  private enabled(doc: vscode.TextDocument): boolean {
    return cfg().get<boolean>("enabled", true) && LANGUAGES.has(doc.languageId) && doc.uri.scheme === "file";
  }

  private onChange(e: vscode.TextDocumentChangeEvent): void {
    if (!this.enabled(e.document) || e.contentChanges.length === 0) return;
    const editor = vscode.window.visibleTextEditors.find((ed) => ed.document === e.document);
    if (editor) this.render(editor);
    this.schedule(e.document, cfg().get<number>("delay", 1200));
  }

  private schedule(doc: vscode.TextDocument, delay: number): void {
    if (!this.enabled(doc)) return;
    const key = doc.uri.toString();
    clearTimeout(this.timers.get(key));
    this.timers.set(key, setTimeout(() => this.analyzeAround(doc), delay));
  }

  private paragraphAround(doc: vscode.TextDocument, line: number): vscode.Range {
    let a = line;
    let b = line;
    while (a > 0 && doc.lineAt(a - 1).text.trim() !== "" && line - a < 40) a--;
    while (b < doc.lineCount - 1 && doc.lineAt(b + 1).text.trim() !== "" && b - line < 40) b++;
    return new vscode.Range(a, 0, b, doc.lineAt(b).text.length);
  }

  private async analyzeAround(doc: vscode.TextDocument): Promise<void> {
    const editor = vscode.window.visibleTextEditors.find((ed) => ed.document === doc);
    const line = editor?.selection.active.line ?? 0;
    await this.analyzeRange(doc, this.paragraphAround(doc, line));
    if (editor) this.render(editor);
  }

  private async analyzeAll(): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) return;
    const doc = editor.document;
    await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "Quotely : analyse du document…" },
      async () => {
        let line = 0;
        while (line < doc.lineCount) {
          if (doc.lineAt(line).text.trim() === "") { line++; continue; }
          const range = this.paragraphAround(doc, line);
          await this.analyzeRange(doc, range);
          line = range.end.line + 1;
        }
      }
    );
    this.render(editor);
    this.list();
  }

  private async analyzeRange(doc: vscode.TextDocument, range: vscode.Range): Promise<void> {
    const text = doc.getText(range);
    if (text.trim().split(/\s+/).length < 8) return;
    const cited = citedKeys(doc);
    const threshold = THRESHOLDS[cfg().get<string>("sensitivity", "normale")] ?? 0.82;
    const cacheKey = hash(text + "|" + cited.join(",") + "|" + threshold);
    let items = this.cache.get(cacheKey);
    if (!items) {
      const ws = vscode.workspace.getWorkspaceFolder(doc.uri)?.uri.fsPath ?? "";
      try {
        items = await assist(text, ws, cited, threshold, doc.uri.fsPath);
      } catch {
        return;
      }
      this.cache.set(cacheKey, items);
      if (this.cache.size > CACHE_SIZE) this.cache.delete(this.cache.keys().next().value!);
    }
    const dismissed = new Set(this.context.workspaceState.get<string[]>(DISMISSED_KEY, []));
    const base = doc.offsetAt(range.start);
    const end = doc.offsetAt(range.end);
    const uri = doc.uri.toString();
    const kept = (this.flags.get(uri) ?? []).filter((f) => f.offset < base || f.offset >= end);
    for (const item of items) {
      item.suggestions = item.suggestions.filter((s) => !dismissed.has(hash(item.sentence) + s.bibtex_key));
      if (item.suggestions.length) kept.push({ uri, offset: base + item.start, item });
    }
    this.flags.set(uri, kept);
  }

  private locate(doc: vscode.TextDocument, flag: Flag): vscode.Range | null {
    const text = doc.getText();
    const from = Math.max(0, flag.offset - 2000);
    const idx = text.indexOf(flag.item.sentence, from);
    if (idx < 0 || Math.abs(idx - flag.offset) > 4000) return null;
    flag.offset = idx;
    return new vscode.Range(doc.positionAt(idx), doc.positionAt(idx + flag.item.sentence.length));
  }

  private live(doc: vscode.TextDocument): { flag: Flag; range: vscode.Range }[] {
    const uri = doc.uri.toString();
    const out: { flag: Flag; range: vscode.Range }[] = [];
    const kept: Flag[] = [];
    for (const flag of this.flags.get(uri) ?? []) {
      const range = this.locate(doc, flag);
      if (range) {
        out.push({ flag, range });
        kept.push(flag);
      }
    }
    this.flags.set(uri, kept);
    return out;
  }

  private render(editor: vscode.TextEditor): void {
    const live = this.live(editor.document);
    const isRecall = (f: Flag) => f.item.suggestions.every((s) => s.kind === "recall");
    editor.setDecorations(this.citeDeco, live.filter((l) => !isRecall(l.flag)).map((l) => l.range));
    editor.setDecorations(this.recallDeco, live.filter((l) => isRecall(l.flag)).map((l) => l.range));
    if (live.length) {
      this.status.text = `$(book) ${live.length} source${live.length > 1 ? "s" : ""}`;
      this.status.tooltip = "Quotely : passages de ta bibliothèque qui appuient ce que tu écris";
      this.status.show();
    } else {
      this.status.hide();
    }
  }

  private clearAll(): void {
    this.flags.clear();
    for (const ed of vscode.window.visibleTextEditors) this.render(ed);
  }

  private flagAt(doc: vscode.TextDocument, pos: vscode.Position) {
    return this.live(doc).find((l) => l.range.contains(pos));
  }

  provideHover(doc: vscode.TextDocument, pos: vscode.Position): vscode.Hover | null {
    const hit = this.flagAt(doc, pos);
    if (!hit) return null;
    const md = new vscode.MarkdownString("", true);
    md.isTrusted = true;
    md.supportThemeIcons = true;
    hit.flag.item.suggestions.forEach((s, index) => {
      const args = encodeURIComponent(JSON.stringify([{ uri: hit.flag.uri, offset: hit.flag.offset, sentence: hit.flag.item.sentence, index }]));
      const page = s.passage?.page ? `, p. ${s.passage.page}` : "";
      const label = s.kind === "recall" ? "$(note) Déjà citée, passage utile" : "$(book) Source possible";
      md.appendMarkdown(`${label} · **${s.bibtex_key}** — ${s.title} *(${s.authors || "?"}, ${s.year})* · ${Math.round(s.confidence * 100)} %\n\n`);
      if (s.contradiction) md.appendMarkdown("$(warning) **À vérifier : la source semble dire l'inverse.**\n\n");
      if (s.passage && !s.passage.title_row) {
        const excerpt = s.passage.text.length > 360 ? `${s.passage.text.slice(0, 357)}…` : s.passage.text;
        md.appendMarkdown(`> ${excerpt.replace(/\n/g, " ")}${page ? ` *(${page.slice(2)})*` : ""}\n\n`);
      }
      const actions = [
        s.kind === "cite" ? `[$(add) Citer](command:quotely.assist.cite?${args})` : "",
        `[$(note) Note pour moi](command:quotely.assist.note?${args})`,
        `[$(go-to-file) Ouvrir${page}](command:quotely.assist.open?${args})`,
        `[$(close) Ignorer](command:quotely.assist.dismiss?${args})`,
      ].filter(Boolean);
      md.appendMarkdown(actions.join(" · ") + "\n\n---\n\n");
    });
    return new vscode.Hover(md, hit.range);
  }

  provideCodeActions(doc: vscode.TextDocument, range: vscode.Range | vscode.Selection): vscode.CodeAction[] {
    const hit = this.flagAt(doc, range.start);
    if (!hit) return [];
    const actions: vscode.CodeAction[] = [];
    hit.flag.item.suggestions.forEach((s, index) => {
      const args: ActionArgs = { uri: hit.flag.uri, offset: hit.flag.offset, sentence: hit.flag.item.sentence, index };
      const make = (title: string, command: string, preferred = false) => {
        const a = new vscode.CodeAction(title, vscode.CodeActionKind.QuickFix);
        a.command = { title, command, arguments: [args] };
        a.isPreferred = preferred;
        actions.push(a);
      };
      if (s.kind === "cite") make(`Quotely : citer ${s.bibtex_key} — ${s.title.slice(0, 60)}${s.contradiction ? " (à vérifier)" : ""}`, "quotely.assist.cite", index === 0 && !s.contradiction);
      make(`Quotely : note avec le passage de ${s.bibtex_key}${s.passage?.page ? ` (p. ${s.passage.page})` : ""}`, "quotely.assist.note");
      make(`Quotely : ignorer ${s.bibtex_key} ici`, "quotely.assist.dismiss");
    });
    return actions;
  }

  private async resolve(a: ActionArgs) {
    const doc = await vscode.workspace.openTextDocument(vscode.Uri.parse(a.uri));
    const editor = await vscode.window.showTextDocument(doc, { preserveFocus: false });
    const flag = (this.flags.get(a.uri) ?? []).find((f) => f.item.sentence === a.sentence);
    if (!flag) return null;
    const range = this.locate(doc, flag);
    if (!range) return null;
    return { editor, flag, range, suggestion: flag.item.suggestions[a.index] as CitationResult };
  }

  private drop(flag: Flag, key: string): void {
    flag.item.suggestions = flag.item.suggestions.filter((s) => s.bibtex_key !== key);
    if (flag.item.suggestions.length === 0) {
      this.flags.set(flag.uri, (this.flags.get(flag.uri) ?? []).filter((f) => f !== flag));
    }
    this.cache.clear();
    for (const ed of vscode.window.visibleTextEditors) if (ed.document.uri.toString() === flag.uri) this.render(ed);
  }

  private async cite(a: ActionArgs): Promise<void> {
    const r = await this.resolve(a);
    if (!r) return;
    const s = r.suggestion;
    await insertCitation(r.editor, r.range.start, r.range.end, s.bibtex_key);
    const added = await ensureEntry(r.editor, s.bibtex_key, s.bibtex_entry);
    if (!added) vscode.window.setStatusBarMessage(`Quotely : ${s.bibtex_key} cité, bibliographie inchangée`, 4000);
    this.drop(r.flag, s.bibtex_key);
  }

  private async note(a: ActionArgs): Promise<void> {
    const r = await this.resolve(a);
    if (!r) return;
    const s = r.suggestion;
    await insertNote(r.editor, r.range.end, s.bibtex_key, s.passage?.page ?? null, s.passage?.text ?? s.title);
    this.drop(r.flag, s.bibtex_key);
  }

  private async dismiss(a: ActionArgs): Promise<void> {
    const flag = (this.flags.get(a.uri) ?? []).find((f) => f.item.sentence === a.sentence);
    if (!flag) return;
    const key = flag.item.suggestions[a.index].bibtex_key;
    const list = this.context.workspaceState.get<string[]>(DISMISSED_KEY, []);
    await this.context.workspaceState.update(DISMISSED_KEY, [...list, hash(a.sentence) + key].slice(-2000));
    this.drop(flag, key);
  }

  private async open(a: ActionArgs): Promise<void> {
    const flag = (this.flags.get(a.uri) ?? []).find((f) => f.item.sentence === a.sentence);
    const s = flag?.item.suggestions[a.index];
    if (!s?.file_path) return;
    const uri = vscode.Uri.file(s.file_path);
    if (s.file_path.toLowerCase().endsWith(".pdf")) {
      await vscode.env.openExternal(s.passage?.page ? uri.with({ fragment: `page=${s.passage.page}` }) : uri);
    } else {
      await vscode.commands.executeCommand("vscode.open", uri);
    }
  }

  private async list(): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) return;
    const live = this.live(editor.document);
    if (live.length === 0) {
      vscode.window.showInformationMessage("Quotely : aucune suggestion pour l'instant dans ce document.");
      return;
    }
    const pick = await vscode.window.showQuickPick(
      live.map((l) => ({
        label: `$(book) ${l.flag.item.suggestions.map((s) => s.bibtex_key).join(", ")}`,
        description: `ligne ${l.range.start.line + 1}`,
        detail: l.flag.item.sentence,
        range: l.range,
      })),
      { placeHolder: "Phrases pour lesquelles ta bibliothèque a une source" }
    );
    if (pick) {
      editor.selection = new vscode.Selection(pick.range.start, pick.range.start);
      editor.revealRange(pick.range, vscode.TextEditorRevealType.InCenter);
      await vscode.commands.executeCommand("editor.action.showHover");
    }
  }
}
