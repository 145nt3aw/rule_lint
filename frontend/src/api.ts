import type {
  BatchLintResult,
  CodeEntry,
  FileLintResult,
  FixResult,
  ImportResult,
  PreviewResult,
} from "./types";

// Backend lives at /api both in dev (via Vite proxy) and prod (same origin).
const API = "/api";

export interface LintOptions {
  eqtype?: string;
  strict?: boolean;
  testlist?: File;
}

async function jsonOrThrow<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const body = await resp.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // not JSON, fall through with default detail
    }
    throw new Error(detail);
  }
  return resp.json() as Promise<T>;
}

export async function lintSingle(
  file: File,
  opts: LintOptions = {},
): Promise<FileLintResult> {
  const fd = new FormData();
  fd.append("file", file);
  if (opts.eqtype) fd.append("eqtype", opts.eqtype);
  if (opts.strict) fd.append("strict", "true");
  if (opts.testlist) fd.append("testlist", opts.testlist);
  const resp = await fetch(`${API}/lint`, { method: "POST", body: fd });
  return jsonOrThrow(resp);
}

export async function lintBatch(
  zip: File,
  opts: LintOptions = {},
): Promise<BatchLintResult> {
  const fd = new FormData();
  fd.append("archive", zip);
  if (opts.eqtype) fd.append("eqtype", opts.eqtype);
  if (opts.strict) fd.append("strict", "true");
  if (opts.testlist) fd.append("testlist", opts.testlist);
  const resp = await fetch(`${API}/lint-batch`, { method: "POST", body: fd });
  return jsonOrThrow(resp);
}

export async function runFix(file: File): Promise<FixResult> {
  const fd = new FormData();
  fd.append("file", file);
  const resp = await fetch(`${API}/fix`, { method: "POST", body: fd });
  return jsonOrThrow(resp);
}

export async function importXlsx(file: File): Promise<ImportResult> {
  const fd = new FormData();
  fd.append("file", file);
  const resp = await fetch(`${API}/import-xlsx`, { method: "POST", body: fd });
  return jsonOrThrow(resp);
}

export async function importXlsxZip(file: File): Promise<Blob> {
  const fd = new FormData();
  fd.append("file", file);
  const resp = await fetch(`${API}/import-xlsx/zip`, {
    method: "POST",
    body: fd,
  });
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const body = await resp.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // not JSON
    }
    throw new Error(detail);
  }
  return resp.blob();
}

export function templateUrl(): string {
  return `${API}/import-xlsx/template`;
}

export async function previewMask(
  text: string,
  gridWidth = 120,
  gridHeight = 25,
): Promise<PreviewResult> {
  const resp = await fetch(`${API}/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, grid_width: gridWidth, grid_height: gridHeight }),
  });
  return jsonOrThrow(resp);
}

export async function fetchCodes(): Promise<CodeEntry[]> {
  const resp = await fetch(`${API}/codes`);
  return jsonOrThrow(resp);
}

export async function fetchEqtypes(): Promise<string[]> {
  const resp = await fetch(`${API}/eqtypes`);
  const body = await jsonOrThrow<{ eqtypes: string[] }>(resp);
  return body.eqtypes;
}
