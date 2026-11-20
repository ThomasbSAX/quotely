import * as vscode from "vscode";

const DEFAULT_URL = "http://127.0.0.1:7331";
const CLIENT = { "X-Quotely-Client": "vscode" };
const JSON_HEADERS = { ...CLIENT, "Content-Type": "application/json" };

export interface Passage {
  text: string;
  page: number | null;
  section: string;
  title_row: boolean;
}

export interface CitationResult {
  bibtex_key: string;
  title: string;
  authors: string;
  year: string;
  score: number;
  confidence: number;
  bibtex_entry: string;
  file_path: string;
  doi: string;
  passage: Passage | null;
  kind: "cite" | "recall";
  contradiction?: string;
}

export interface AssistItem {
  start: number;
  end: number;
  sentence: string;
  claim: boolean;
  already_cited: boolean;
  suggestions: CitationResult[];
}

export interface PaperInfo {
  bibtex_key: string;
  title: string;
  authors: string;
  year: string;
  file_path: string;
  bibtex_entry: string;
  doi?: string;
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
    headers: JSON_HEADERS,
    body: JSON.stringify({ text, n, workspace_path: workspacePath }),
    signal: AbortSignal.timeout(4000),
  });
  if (!res.ok) {
    throw new Error(`Backend error ${res.status}`);
  }
  const data = (await res.json()) as { citations: CitationResult[] };
  return data.citations;
}

export async function assist(
  text: string,
  workspacePath: string,
  citedKeys: string[],
  threshold?: number,
  documentPath: string = ""
): Promise<AssistItem[]> {
  const res = await fetch(`${backendUrl()}/assist`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ text, workspace_path: workspacePath, cited_keys: citedKeys, threshold, document_path: documentPath }),
    signal: AbortSignal.timeout(8000),
  });
  if (!res.ok) throw new Error(`Backend error ${res.status}`);
  return ((await res.json()) as { items: AssistItem[] }).items;
}

export async function getPaper(key: string): Promise<PaperInfo | null> {
  const res = await fetch(`${backendUrl()}/paper?key=${encodeURIComponent(key)}`, {
    headers: CLIENT,
    signal: AbortSignal.timeout(3000),
  });
  return res.ok ? ((await res.json()) as PaperInfo) : null;
}

export async function listPapers(): Promise<PaperInfo[]> {
  const url = `${backendUrl()}/papers`;
  const res = await fetch(url, { headers: CLIENT, signal: AbortSignal.timeout(4000) });
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
  page: number | null;
}

export async function searchDocuments(
  q: string,
  mode: "keyword" | "semantic" = "keyword",
  n: number = 20
): Promise<SearchResult[]> {
  const url = `${backendUrl()}/search?q=${encodeURIComponent(q)}&mode=${mode}&n=${n}`;
  const res = await fetch(url, { headers: CLIENT, signal: AbortSignal.timeout(6000) });
  if (!res.ok) throw new Error(`Backend error ${res.status}`);
  const data = (await res.json()) as { results: SearchResult[] };
  return data.results;
}

export interface Health {
  status: string;
  version: string;
  pid: number;
  indexed_chunks: number;
  indexed_documents: number;
  indexing: { running: boolean; job: string; done: number; total: number };
}

export async function getHealth(): Promise<Health | null> {
  try {
    const res = await fetch(`${backendUrl()}/health`, {
      headers: CLIENT,
      signal: AbortSignal.timeout(1500),
    });
    return res.ok ? ((await res.json()) as Health) : null;
  } catch {
    return null;
  }
}

export async function checkHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${backendUrl()}/health`, {
      headers: CLIENT,
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
    headers: JSON_HEADERS,
    body: JSON.stringify({ path: folderPath }),
    signal: AbortSignal.timeout(600000),
  });
  if (!res.ok) throw new Error(`Backend error ${res.status}: ${await res.text()}`);
  return (await res.json()) as { indexed: number; skipped: number; errors: string[] };
}

export async function reindexAll(): Promise<{ total: number; errors: string[] }> {
  const res = await fetch(`${backendUrl()}/reindex`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: "{}",
    signal: AbortSignal.timeout(600000),
  });
  if (!res.ok) throw new Error(`Backend error ${res.status}: ${await res.text()}`);
  return (await res.json()) as { total: number; errors: string[] };
}
