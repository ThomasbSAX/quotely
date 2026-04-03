import * as vscode from "vscode";

// Matches \cite{key} and \cite[opt]{key} (LaTeX) — captures key in group 1
const CITE_RE = /\\cite(?:\[[^\]]*\])?\{([^}]+)\}/g;
// Matches [@key] (Pandoc/Markdown)
const PANDOC_RE = /\[@([^\]]+)\]/g;

const BIB_MARKER_TEX = "% === Quotely Bibliography ===";
const BIB_MARKER_MD  = "<!-- Quotely Bibliography -->";

// ---------------------------------------------------------------------------
// DocumentLinkProvider — makes \cite{KEY} / [@KEY] clickable
// Clicking jumps to the bibliography entry in the same file
// ---------------------------------------------------------------------------

export class CitationLinkProvider implements vscode.DocumentLinkProvider {
  provideDocumentLinks(document: vscode.TextDocument): vscode.DocumentLink[] {
    const isLatex = document.languageId === "latex" || document.fileName.endsWith(".tex");
    const text = document.getText();
    const marker = isLatex ? BIB_MARKER_TEX : BIB_MARKER_MD;
    const markerIdx = text.indexOf(marker);

    const links: vscode.DocumentLink[] = [];
    const re = isLatex ? CITE_RE : PANDOC_RE;
    re.lastIndex = 0;

    let match: RegExpExecArray | null;
    while ((match = re.exec(text)) !== null) {
      const key = match[1].trim();
      const start = document.positionAt(match.index);
      const end   = document.positionAt(match.index + match[0].length);
      const range = new vscode.Range(start, end);

      // Build a command URI that will navigate to this key's bib entry
      // The command quotely.goToBibEntry is registered in extension.ts
      let targetLine = -1;
      if (markerIdx !== -1) {
        const afterMarker = text.indexOf("\n", markerIdx) + 1;
        // BibTeX entries start with @type{KEY,
        const keyPattern = `{${key},`;
        const keyIdx = text.indexOf(keyPattern, afterMarker);
        if (keyIdx !== -1) {
          targetLine = document.positionAt(keyIdx).line;
        }
      }

      if (targetLine >= 0) {
        const args = encodeURIComponent(JSON.stringify([document.uri.fsPath, targetLine]));
        const target = vscode.Uri.parse(`command:quotely.goToBibEntry?${args}`);
        links.push(new vscode.DocumentLink(range, target));
      }
    }

    return links;
  }
}

// ---------------------------------------------------------------------------
// Citation decoration manager — overlays [n] badge on each \cite{KEY}
// ---------------------------------------------------------------------------

export function updateCitationDecorations(
  editor: vscode.TextEditor,
  decorationType: vscode.TextEditorDecorationType
): void {
  const document = editor.document;
  const isLatex = document.languageId === "latex" || document.fileName.endsWith(".tex");
  const isMd    = document.languageId === "markdown";
  if (!isLatex && !isMd) {
    editor.setDecorations(decorationType, []);
    return;
  }

  const text = document.getText();
  const re = isLatex ? CITE_RE : PANDOC_RE;
  re.lastIndex = 0;

  // Sequential numbering: same key → same number throughout the document
  const keyToNum = new Map<string, number>();
  let counter = 0;

  const decorations: vscode.DecorationOptions[] = [];
  let match: RegExpExecArray | null;

  while ((match = re.exec(text)) !== null) {
    const key = match[1].trim();
    if (!keyToNum.has(key)) {
      keyToNum.set(key, ++counter);
    }
    const n = keyToNum.get(key)!;

    const start = document.positionAt(match.index);
    const end   = document.positionAt(match.index + match[0].length);

    decorations.push({
      range: new vscode.Range(start, end),
      hoverMessage: `Citation Quotely — clé: \`${key}\``,
      renderOptions: {
        before: {
          contentText: `[${n}]`,
          color: new vscode.ThemeColor("textLink.foreground"),
          fontWeight: "bold",
          margin: "0 4px 0 0",
          textDecoration: "none; font-size: 0.75em; vertical-align: super;",
        },
      },
    });
  }

  editor.setDecorations(decorationType, decorations);
}
