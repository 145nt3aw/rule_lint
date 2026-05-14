#!/usr/bin/env python3
"""
rule_lint_gui.py — graphical front-end for the Evolution rule-engine linter.

Wraps support/rule_lint.py with a tkinter window. Intended as the user-facing
distribution: bundled by PyInstaller into a standalone executable for macOS
and Windows so end-users don't need Python or the source tree.

For the command-line tool see rule_lint.py.
For build and packaging instructions see RULE_LINT_PACKAGING.md.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import List, Optional

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# Ensure sibling modules (rule_lint, rule_catalogue) can be imported when
# running both from source AND from the PyInstaller bundle.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# When PyInstaller bundles, sys._MEIPASS points to the temp extraction dir.
# Add it too so rule_catalogue.py is found.
_MEIPASS = getattr(sys, "_MEIPASS", None)
if _MEIPASS and _MEIPASS not in sys.path:
    sys.path.insert(0, _MEIPASS)

import rule_lint  # noqa: E402
from rule_lint import (  # noqa: E402
    lint, load_testlist, apply_fixes, build_suppress_map,
    apply_suppressions, find_include_masks, resolve_include,
    lint_file, format_sarif_multi, format_json_multi, ISSUE_CODES,
    EQ_TYPE_ALIASES, SUBROUTINES,
)
import rule_lint_xlsx  # noqa: E402


APP_TITLE = "Rule Lint"
APP_VERSION = "1.0"

SEVERITY_TAGS = {"error": "sev-error", "warning": "sev-warning", "info": "sev-info"}
SEVERITY_COLOURS = {"sev-error": "#cc3333", "sev-warning": "#cc8a00", "sev-info": "#2266cc"}


class RuleLintGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"{APP_TITLE} {APP_VERSION}")
        self.root.geometry("1000x720")
        self.root.minsize(800, 560)

        # State
        self.file_path = tk.StringVar()
        self.eqtype = tk.StringVar(value="(none)")
        self.testlist_path = tk.StringVar()
        self.include_paths: List[str] = []
        self.strict = tk.BooleanVar(value=False)
        self.testlist_cache: Optional[set] = None

        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        self._build_menubar()
        # Use a vertical paned window so the user can resize results area
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill=tk.BOTH, expand=True)

        self._build_input_frame(outer)
        self._build_action_frame(outer)
        self._build_results_frame(outer)
        self._build_statusbar(outer)

    def _build_menubar(self) -> None:
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open Rule File…", command=self._pick_file)
        file_menu.add_separator()
        file_menu.add_command(label="Import Workflow XLSX…",
                              command=self.import_workflow)
        file_menu.add_command(label="Save Workflow CSV Template…",
                              command=self.save_workflow_template)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self.root.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="Issue Code Reference…",
                              command=self.show_codes)
        help_menu.add_command(label="About…", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

    def _build_input_frame(self, parent: ttk.Frame) -> None:
        frm = ttk.LabelFrame(parent, text="Input", padding=8)
        frm.pack(fill=tk.X, pady=(0, 6))

        # Row 0: file
        ttk.Label(frm, text="Rule file:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(frm, textvariable=self.file_path, width=70).grid(
            row=0, column=1, sticky="ew", padx=(0, 6))
        ttk.Button(frm, text="Browse…", command=self._pick_file).grid(row=0, column=2)

        # Row 1: equation type
        ttk.Label(frm, text="Equation type:").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=(6, 0))
        eq_values = ["(none)"] + sorted(EQ_TYPE_ALIASES.keys())
        ttk.Combobox(frm, textvariable=self.eqtype, values=eq_values,
                     state="readonly", width=22).grid(
            row=1, column=1, sticky="w", padx=(0, 6), pady=(6, 0))

        # Row 2: testlist
        ttk.Label(frm, text="Test catalogue (CSV):").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=(6, 0))
        ttk.Entry(frm, textvariable=self.testlist_path, width=70).grid(
            row=2, column=1, sticky="ew", padx=(0, 6), pady=(6, 0))
        ttk.Button(frm, text="Browse…", command=self._pick_testlist).grid(row=2, column=2, pady=(6, 0))

        # Row 3: include paths
        ttk.Label(frm, text="Include paths:").grid(row=3, column=0, sticky="nw", padx=(0, 6), pady=(6, 0))
        inc_frame = ttk.Frame(frm)
        inc_frame.grid(row=3, column=1, sticky="ew", pady=(6, 0))
        self.inc_listbox = tk.Listbox(inc_frame, height=3)
        self.inc_listbox.pack(side=tk.LEFT, fill=tk.X, expand=True)
        inc_btns = ttk.Frame(inc_frame)
        inc_btns.pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(inc_btns, text="Add…", command=self._add_include_path).pack(fill=tk.X)
        ttk.Button(inc_btns, text="Remove", command=self._remove_include_path).pack(fill=tk.X, pady=(2, 0))

        # Row 4: flags
        flags = ttk.Frame(frm)
        flags.grid(row=4, column=1, sticky="w", pady=(6, 0))
        ttk.Checkbutton(flags, text="Strict (surface foot-gun notes)",
                        variable=self.strict).pack(side=tk.LEFT)

        frm.columnconfigure(1, weight=1)

    def _build_action_frame(self, parent: ttk.Frame) -> None:
        frm = ttk.Frame(parent)
        frm.pack(fill=tk.X, pady=(0, 6))

        ttk.Button(frm, text="Run Lint", command=self.run_lint).pack(
            side=tk.LEFT)
        ttk.Button(frm, text="Apply Auto-Fixes", command=self.apply_fixes).pack(
            side=tk.LEFT, padx=(6, 0))
        ttk.Button(frm, text="Export Report…", command=self.export_report).pack(
            side=tk.LEFT, padx=(6, 0))
        ttk.Button(frm, text="Code Reference…", command=self.show_codes).pack(
            side=tk.LEFT, padx=(6, 0))
        ttk.Button(frm, text="About…", command=self.show_about).pack(
            side=tk.RIGHT)

    def _build_results_frame(self, parent: ttk.Frame) -> None:
        paned = ttk.PanedWindow(parent, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # Results tree
        tree_frame = ttk.Frame(paned)
        cols = ("severity", "line", "col", "code", "message")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings",
                                 selectmode="browse")
        self.tree.heading("severity", text="Severity")
        self.tree.heading("line", text="Line")
        self.tree.heading("col", text="Col")
        self.tree.heading("code", text="Code")
        self.tree.heading("message", text="Message")
        self.tree.column("severity", width=80, anchor="w")
        self.tree.column("line", width=60, anchor="e")
        self.tree.column("col", width=50, anchor="e")
        self.tree.column("code", width=110, anchor="w")
        self.tree.column("message", width=600, anchor="w")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        for tag, colour in SEVERITY_COLOURS.items():
            self.tree.tag_configure(tag, foreground=colour)
        self.tree.bind("<<TreeviewSelect>>", self._on_select_issue)
        paned.add(tree_frame, weight=3)

        # Detail panel
        detail_frame = ttk.LabelFrame(paned, text="Issue detail", padding=6)
        self.detail_text = scrolledtext.ScrolledText(
            detail_frame, height=6, wrap=tk.WORD, font=("TkDefaultFont", 10))
        self.detail_text.pack(fill=tk.BOTH, expand=True)
        self.detail_text.configure(state=tk.DISABLED)
        paned.add(detail_frame, weight=1)

    def _build_statusbar(self, parent: ttk.Frame) -> None:
        self.status = tk.StringVar(value="Ready")
        bar = ttk.Frame(parent, relief=tk.SUNKEN, padding=(6, 2))
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Label(bar, textvariable=self.status, anchor="w").pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(bar, text=f"v{APP_VERSION}", anchor="e").pack(side=tk.RIGHT)

    # ----------------------------------------------------------- actions
    def _pick_file(self) -> None:
        filetypes = [
            ("Rule files", "*.eq *.rule *.mask"),
            ("All files", "*.*"),
        ]
        path = filedialog.askopenfilename(filetypes=filetypes)
        if path:
            self.file_path.set(path)

    def _pick_testlist(self) -> None:
        filetypes = [("CSV", "*.csv"), ("All files", "*.*")]
        path = filedialog.askopenfilename(filetypes=filetypes)
        if path:
            self.testlist_path.set(path)
            self.testlist_cache = None  # invalidate cache

    def _add_include_path(self) -> None:
        path = filedialog.askdirectory()
        if path and path not in self.include_paths:
            self.include_paths.append(path)
            self.inc_listbox.insert(tk.END, path)

    def _remove_include_path(self) -> None:
        sel = self.inc_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        del self.include_paths[idx]
        self.inc_listbox.delete(idx)

    def _on_select_issue(self, _evt) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        item = sel[0]
        values = self.tree.item(item, "values")
        if not values:
            return
        severity, line, col, code, message = values
        detail = (
            f"[{code}] {severity}\n"
            f"Location: line {line}, column {col}\n\n"
            f"{message}\n\n"
        )
        if code in ISSUE_CODES:
            _sev, desc = ISSUE_CODES[code]
            detail += f"About this check: {desc}\n"
        self.detail_text.configure(state=tk.NORMAL)
        self.detail_text.delete("1.0", tk.END)
        self.detail_text.insert("1.0", detail)
        self.detail_text.configure(state=tk.DISABLED)

    # ---------------------------------------------------------- linting
    def _validate(self) -> Optional[str]:
        if not self.file_path.get():
            messagebox.showwarning(APP_TITLE, "Pick a rule file first.")
            return None
        if not os.path.isfile(self.file_path.get()):
            messagebox.showerror(APP_TITLE,
                                 f"File not found: {self.file_path.get()}")
            return None
        return self.file_path.get()

    def _load_testlist_if_set(self) -> Optional[set]:
        path = self.testlist_path.get().strip()
        if not path:
            return None
        if not os.path.isfile(path):
            messagebox.showerror(APP_TITLE,
                                 f"Test catalogue not found: {path}")
            return None
        if self.testlist_cache is not None:
            return self.testlist_cache
        try:
            tests, _types = load_testlist(path)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not load testlist:\n{exc}")
            return None
        self.testlist_cache = tests
        return tests

    def run_lint(self) -> None:
        path = self._validate()
        if not path:
            return
        testlist = self._load_testlist_if_set()
        eqtype = None if self.eqtype.get() == "(none)" else self.eqtype.get()
        strict = self.strict.get()
        include_paths = list(self.include_paths)

        self.status.set("Linting…")
        self.root.update_idletasks()

        # Run on a worker thread so the UI stays responsive on big files.
        def worker():
            try:
                if include_paths:
                    issues = lint_file(path, eqtype=eqtype, strict=strict,
                                       testlist=testlist or set(),
                                       include_paths=include_paths)
                else:
                    with open(path) as f:
                        text = f.read()
                    suppress = build_suppress_map(text)
                    issues = lint(text, eqtype=eqtype, strict=strict,
                                  testlist=testlist)
                    issues = apply_suppressions(issues, suppress)
                self._last_path = path
                self._last_issues = issues
                self.root.after(0, lambda: self._render_issues(path, issues))
            except Exception as exc:
                self.root.after(0, lambda: self._report_error(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _render_issues(self, path: str, issues) -> None:
        # Clear tree
        for item in self.tree.get_children():
            self.tree.delete(item)

        issues_sorted = sorted(issues, key=lambda i: (i.line, i.column))
        for issue in issues_sorted:
            tag = SEVERITY_TAGS.get(issue.severity, "")
            chain = ""
            if issue.include_chain:
                chain = f"  (via {' ← '.join(reversed(issue.include_chain))})"
            self.tree.insert("", tk.END, values=(
                issue.severity, issue.line, issue.column,
                issue.code, issue.message + chain,
            ), tags=(tag,))

        n_err = sum(1 for i in issues if i.severity == "error")
        n_warn = sum(1 for i in issues if i.severity == "warning")
        n_info = sum(1 for i in issues if i.severity == "info")
        fname = Path(path).name
        if not issues:
            self.status.set(f"{fname}: ✅ clean")
        else:
            self.status.set(
                f"{fname}: {n_err} error(s), {n_warn} warning(s), {n_info} info")

    def _report_error(self, exc: Exception) -> None:
        self.status.set("Error")
        messagebox.showerror(APP_TITLE, f"Lint failed:\n\n{exc}")

    # ---------------------------------------------------------- auto-fix
    def apply_fixes(self) -> None:
        path = self._validate()
        if not path:
            return
        try:
            with open(path) as f:
                original = f.read()
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Could not read file:\n{exc}")
            return
        fixed, fixes = apply_fixes(original)
        if not fixes:
            messagebox.showinfo(APP_TITLE, "No auto-fixable issues found.")
            return
        preview = "\n".join(f"  line {ln}: {desc}" for ln, desc in fixes)
        if not messagebox.askyesno(
            APP_TITLE,
            f"{len(fixes)} fix(es) will be applied to:\n{path}\n\n"
            f"{preview}\n\nProceed?"
        ):
            return
        try:
            with open(path, "w") as f:
                f.write(fixed)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Could not write file:\n{exc}")
            return
        messagebox.showinfo(APP_TITLE,
                            f"Applied {len(fixes)} fix(es). Re-lint to see "
                            f"updated results.")

    # ---------------------------------------------------------- export
    def export_report(self) -> None:
        if not getattr(self, "_last_issues", None):
            messagebox.showinfo(APP_TITLE, "Run lint first, then export.")
            return
        path = self._last_path
        per_file = [(path, self._last_issues)]
        filetypes = [
            ("Text", "*.txt"),
            ("JSON", "*.json"),
            ("SARIF (CI annotation)", "*.sarif"),
        ]
        out = filedialog.asksaveasfilename(
            filetypes=filetypes,
            defaultextension=".txt",
            initialfile=f"{Path(path).stem}_lint")
        if not out:
            return
        ext = Path(out).suffix.lower()
        try:
            if ext == ".json":
                content = format_json_multi(per_file)
            elif ext == ".sarif":
                content = format_sarif_multi(per_file)
            else:
                # Plain text
                lines = []
                for issue in sorted(self._last_issues,
                                    key=lambda i: (i.line, i.column)):
                    lines.append(f"{path}:{issue.line}:{issue.column}: "
                                 f"{issue.severity} [{issue.code}] "
                                 f"{issue.message}")
                content = "\n".join(lines) + "\n"
            with open(out, "w") as f:
                f.write(content)
            messagebox.showinfo(APP_TITLE, f"Saved: {out}")
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Could not save:\n{exc}")

    # ------------------------------------------------------- dialogues
    def show_codes(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Issue Code Reference")
        win.geometry("780x520")
        cols = ("code", "severity", "description")
        tree = ttk.Treeview(win, columns=cols, show="headings", selectmode="browse")
        tree.heading("code", text="Code")
        tree.heading("severity", text="Severity")
        tree.heading("description", text="Description")
        tree.column("code", width=110, anchor="w")
        tree.column("severity", width=80, anchor="w")
        tree.column("description", width=580, anchor="w")
        vsb = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        for code in sorted(ISSUE_CODES):
            sev, desc = ISSUE_CODES[code]
            tag = SEVERITY_TAGS.get(sev, "")
            tree.insert("", tk.END, values=(code, sev, desc), tags=(tag,))
        for tag, colour in SEVERITY_COLOURS.items():
            tree.tag_configure(tag, foreground=colour)

    def show_about(self) -> None:
        messagebox.showinfo(
            APP_TITLE,
            f"{APP_TITLE} v{APP_VERSION}\n\n"
            f"Linter for Evolution rule-engine equations.\n"
            f"Catalogue: {len(SUBROUTINES)} subroutines, "
            f"{len(ISSUE_CODES)} issue codes.\n\n"
            f"This GUI wraps the rule_lint.py CLI for end users on "
            f"clean macOS / Windows installs."
        )

    # ----------------------------------------------------- workflow import
    def save_workflow_template(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save Workflow CSV Template",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
            initialfile="workflow_template.csv",
        )
        if not path:
            return
        try:
            rule_lint_xlsx.write_csv_template(path)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Could not write template:\n{exc}")
            return
        messagebox.showinfo(
            APP_TITLE,
            f"Template saved to:\n{path}\n\n"
            f"Open it in Excel, fill in your workflow rows, then save as "
            f".xlsx (or keep as CSV) and use File → Import Workflow XLSX.",
        )

    def import_workflow(self) -> None:
        in_path = filedialog.askopenfilename(
            title="Import Workflow Spreadsheet",
            filetypes=[
                ("Workflow spreadsheet", "*.xlsx *.csv"),
                ("Excel", "*.xlsx"),
                ("CSV", "*.csv"),
                ("All files", "*.*"),
            ],
        )
        if not in_path:
            return
        out_dir = filedialog.askdirectory(
            title="Choose Output Directory for Generated .eq Files")
        if not out_dir:
            return

        self.status.set(f"Importing {Path(in_path).name}…")
        self.root.update_idletasks()

        def worker():
            try:
                result = rule_lint_xlsx.import_spreadsheet(in_path)
                written: List[str] = []
                if result.files:
                    written = rule_lint_xlsx.write_files(result, out_dir)
                self.root.after(
                    0, lambda: self._render_import_result(
                        in_path, out_dir, result, written))
            except Exception as exc:
                self.root.after(0, lambda: self._report_error(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _render_import_result(self, in_path: str, out_dir: str,
                              result, written: List[str]) -> None:
        n_rows = len(result.rows)
        n_files = len(written)
        n_err = len(result.errors)
        n_warn = len(result.warnings)
        self.status.set(
            f"Imported {Path(in_path).name}: {n_rows} rule(s), "
            f"{n_files} file(s), {n_err} error(s), {n_warn} warning(s)")

        win = tk.Toplevel(self.root)
        win.title("Workflow Import Summary")
        win.geometry("780x520")

        # Header
        hdr = ttk.Frame(win, padding=8)
        hdr.pack(fill=tk.X)
        ttk.Label(hdr, text=f"Source: {in_path}").pack(anchor="w")
        ttk.Label(hdr, text=f"Output: {out_dir}").pack(anchor="w")
        ttk.Label(
            hdr,
            text=(f"{n_rows} row(s) parsed · {n_files} file(s) written · "
                  f"{n_err} error(s) · {n_warn} warning(s)"),
        ).pack(anchor="w", pady=(4, 0))

        paned = ttk.PanedWindow(win, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        # Files written
        files_frame = ttk.LabelFrame(paned, text="Files written", padding=4)
        files_list = tk.Listbox(files_frame)
        for p in written:
            files_list.insert(tk.END, p)
        files_list.pack(fill=tk.BOTH, expand=True)
        paned.add(files_frame, weight=1)

        # Issues
        issues_frame = ttk.LabelFrame(paned, text="Issues", padding=4)
        cols = ("severity", "row", "message")
        tree = ttk.Treeview(issues_frame, columns=cols, show="headings",
                            selectmode="browse")
        tree.heading("severity", text="Severity")
        tree.heading("row", text="Row")
        tree.heading("message", text="Message")
        tree.column("severity", width=80, anchor="w")
        tree.column("row", width=60, anchor="e")
        tree.column("message", width=600, anchor="w")
        for issue in result.issues:
            tag = SEVERITY_TAGS.get(issue.severity, "")
            tree.insert("", tk.END,
                        values=(issue.severity, issue.row_number, issue.message),
                        tags=(tag,))
        for tag, colour in SEVERITY_COLOURS.items():
            tree.tag_configure(tag, foreground=colour)
        tree.pack(fill=tk.BOTH, expand=True)
        paned.add(issues_frame, weight=2)

        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 8))


def main() -> int:
    root = tk.Tk()
    # ttk theme tweaks per platform
    try:
        style = ttk.Style()
        if sys.platform.startswith("darwin") and "aqua" in style.theme_names():
            style.theme_use("aqua")
        elif sys.platform.startswith("win") and "vista" in style.theme_names():
            style.theme_use("vista")
        else:
            style.theme_use("clam")
    except Exception:
        pass

    # Handle a file passed on the command line (e.g. "Open with..." on macOS)
    initial_file = None
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        initial_file = sys.argv[1]

    gui = RuleLintGUI(root)
    if initial_file:
        gui.file_path.set(initial_file)

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
