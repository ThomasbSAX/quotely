import * as vscode from "vscode";
import { getSuggestions, CitationResult } from "./api";

// Triggers inside \cite{} or [@], OR at end of a line with enough context
const CITE_TRIGGER_RE = /\\cite\{[^}]*$|\[@[^\]]*$/;
const MIN_CONTEXT_CHARS = 40;   // minimum chars before cursor to trigger end-of-line mode

export class CitationCompletionProvider
  implements vscode.InlineCompletionItemProvider
{
  async provideInlineCompletionItems(
    document: vscode.TextDocument,
    position: vscode.Position,
    _context: vscode.InlineCompletionContext,
    _token: vscode.CancellationToken
  ): Promise<vscode.InlineCompletionList | null> {
    const cfg = vscode.workspace.getConfiguration("quotely");
    if (!cfg.get<boolean>("triggerOnCite", true)) return null;

    const maxSuggestions = cfg.get<number>("maxSuggestions", 5);
    const contextParagraphs = cfg.get<number>("contextParagraphs", 3);

    const fullLine = document.lineAt(position.line).text;
    const textBefore = fullLine.slice(0, position.character);
    const textAfter = fullLine.slice(position.character);

    const insideCite = CITE_TRIGGER_RE.test(textBefore);
    // Also trigger at end of line (nothing after cursor) when enough text exists
    const atLineEnd = cfg.get<boolean>("suggestAtLineEnd", false) && textAfter.trim() === "" && textBefore.trim().length >= MIN_CONTEXT_CHARS;

    if (!insideCite && !atLineEnd) return null;

    const contextText = this._getContext(document, position, contextParagraphs);
    if (!contextText.trim()) return null;

    let citations: CitationResult[];
    try {
      const workspacePath = vscode.workspace.getWorkspaceFolder(document.uri)?.uri.fsPath ?? "";
      citations = await getSuggestions(contextText, maxSuggestions, workspacePath);
    } catch {
      vscode.window.setStatusBarMessage("$(book) Quotely: backend offline", 5000);
      return null;
    }

    if (citations.length === 0) {
      vscode.window.setStatusBarMessage(
        "$(book) Quotely: aucun article indexé — déposez des PDFs dans data/papers/",
        5000
      );
      return null;
    }

    const best = citations[0];
    const isLatex = document.languageId === "latex" || document.fileName.endsWith(".tex");

    let insertText: string;
    let replaceRange: vscode.Range;

    if (insideCite) {
      // Replace only the content inside \cite{...} or [@...]
      const closingChar = textBefore.includes("\\cite{") ? "}" : "]";
      const closingIdx = textAfter.indexOf(closingChar);
      const replaceEnd = closingIdx >= 0 ? position.translate(0, closingIdx) : position;
      insertText = textBefore.includes("\\cite{") ? best.bibtex_key : `${best.bibtex_key}]`;
      replaceRange = new vscode.Range(position, replaceEnd);
    } else {
      // Insert a full citation command at end of line
      insertText = isLatex ? ` \\cite{${best.bibtex_key}}` : ` [@${best.bibtex_key}]`;
      replaceRange = new vscode.Range(position, position);
    }

    const item = new vscode.InlineCompletionItem(insertText);
    item.range = replaceRange;
    return new vscode.InlineCompletionList([item]);
  }

  private _getContext(
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
}
