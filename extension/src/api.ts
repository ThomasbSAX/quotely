import * as vscode from "vscode";

const DEFAULT_URL = "http://127.0.0.1:7331";

export interface CitationResult {
  bibtex_key: string;
  title: string;
  authors: string;
  year: string;
  score: number;
  bibtex_entry: string;
  file_path: string;
}

export interface PaperInfo {
  bibtex_key: string;
  title: string;
  authors: string;
  year: string;
  file_path: string;
  bibtex_entry: string;
}

function backendUrl(): string {
  return (
    vscode.workspace
      .getConfiguration("quotely")
      .get<string>("backendUrl") ?? DEFAULT_URL
  );
}

export async function getSuggestions(
  text: string,
  n: number = 5,
  workspacePath: string = ""
): Promise<CitationResult[]> {
  const url = `${backendUrl()}/suggest`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, n, workspace_path: workspacePath }),
    signal: AbortSignal.timeout(4000),
  });
  if (!res.ok) {
    throw new Error(`Backend error ${res.status}`);
  }
  const data = (await res.json()) as { citations: CitationResult[] };
  return data.citations;
}

export async function listPapers(): Promise<PaperInfo[]> {
  const url = `${backendUrl()}/papers`;
  const res = await fetch(url, { signal: AbortSignal.timeout(4000) });
  if (!res.ok) throw new Error(`Backend error ${res.status}`);
  return (await res.json()) as PaperInfo[];
}

export interface SearchResult {
  bibtex_key: string;
  title: string;
  authors: string;
  year: string;
  excerpt: string;
  chunk_index: number;
  file_path: string;
}

export async function searchDocuments(
  q: string,
  mode: "keyword" | "semantic" = "keyword",
  n: number = 20
): Promise<SearchResult[]> {
  const url = `${backendUrl()}/search?q=${encodeURIComponent(q)}&mode=${mode}&n=${n}`;
  const res = await fetch(url, { signal: AbortSignal.timeout(6000) });
  if (!res.ok) throw new Error(`Backend error ${res.status}`);
  const data = (await res.json()) as { results: SearchResult[] };
  return data.results;
}

export async function checkHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${backendUrl()}/health`, {
      signal: AbortSignal.timeout(1500),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function ingestFolder(
  folderPath: string
): Promise<{ indexed: number; skipped: number; errors: string[] }> {
  const res = await fetch(`${backendUrl()}/ingest-folder`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: folderPath }),
    signal: AbortSignal.timeout(600000), // 10 min for large folders
  });
  if (!res.ok) throw new Error(`Backend error ${res.status}: ${await res.text()}`);
  return (await res.json()) as { indexed: number; skipped: number; errors: string[] };
}

export async function reindexAll(): Promise<{ total: number; errors: string[] }> {
  const res = await fetch(`${backendUrl()}/reindex`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
    signal: AbortSignal.timeout(600000), // 10 min
  });
  if (!res.ok) throw new Error(`Backend error ${res.status}: ${await res.text()}`);
  return (await res.json()) as { total: number; errors: string[] };
}
