import * as vscode from "vscode";
import { listPapers } from "./api";

const INDEXED_COLOR = new vscode.ThemeColor("gitDecoration.addedResourceForeground");

export class QuotelyDecorationProvider implements vscode.FileDecorationProvider {
  private readonly _onDidChange = new vscode.EventEmitter<undefined>();
  readonly onDidChangeFileDecorations = this._onDidChange.event;

  private indexedPaths = new Set<string>();

  /** Fetch current indexed files from backend and refresh all decorations. */
  async refresh(): Promise<void> {
    try {
      const papers = await listPapers();
      this.indexedPaths = new Set(papers.map((p) => p.file_path));
      this._onDidChange.fire(undefined);
    } catch {
      // Backend offline — keep previous state, decorations remain visible
    }
  }

  provideFileDecoration(uri: vscode.Uri): vscode.FileDecoration | undefined {
    if (this.indexedPaths.has(uri.fsPath)) {
      return {
        badge: "✓",
        tooltip: `Vectorisé par Quotely`,
        color: INDEXED_COLOR,
        propagate: true, // parent folders get the green tint too
      };
    }
    return undefined;
  }
}
