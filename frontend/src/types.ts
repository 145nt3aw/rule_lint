// Mirrors backend/routes.py response shapes.

export interface Issue {
  severity: "error" | "warning" | "info";
  line: number;
  column: number;
  code: string;
  message: string;
  include_chain: string[];
}

export interface FileLintResult {
  filename: string;
  lines: number;
  errors: number;
  warnings: number;
  info: number;
  issues: Issue[];
}

export interface BatchLintResult {
  files: FileLintResult[];
  total_errors: number;
  total_warnings: number;
  total_info: number;
}

export interface CodeEntry {
  code: string;
  severity: string;
  description: string;
}

export interface FixEntry {
  line: number;
  description: string;
}

export interface FixResult {
  filename: string;
  fixed: boolean;
  fixes: FixEntry[];
  original_size: number;
  fixed_size: number;
  fixed_text: string;
  remaining_errors: number;
  remaining_warnings: number;
  remaining_info: number;
}
