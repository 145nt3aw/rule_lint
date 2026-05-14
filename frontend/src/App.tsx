import { useState } from "react";
import {
  importXlsx,
  lintBatch,
  lintSingle,
  runFix,
  templateUrl,
} from "./api";
import { CodesPanel } from "./components/CodesPanel";
import { Dropzone } from "./components/Dropzone";
import { FixPanel } from "./components/FixPanel";
import { IssueTable } from "./components/IssueTable";
import {
  LintOptionsBar,
  type LintOptionsState,
} from "./components/LintOptionsBar";
import { PreviewPanel } from "./components/PreviewPanel";
import { Summary } from "./components/Summary";
import { WorkflowImportPanel } from "./components/WorkflowImportPanel";
import type {
  BatchLintResult,
  FileLintResult,
  FixResult,
  ImportResult,
} from "./types";

type Tab = "single" | "batch" | "workflow" | "preview" | "codes";

export function App() {
  const [tab, setTab] = useState<Tab>("single");
  const [options, setOptions] = useState<LintOptionsState>({
    eqtype: "",
    strict: false,
    testlist: null,
  });

  // single-file state
  const [pickedFile, setPickedFile] = useState<File | null>(null);
  const [singleResult, setSingleResult] = useState<FileLintResult | null>(null);
  const [fixResult, setFixResult] = useState<FixResult | null>(null);

  // batch state
  const [pickedZip, setPickedZip] = useState<File | null>(null);
  const [batchResult, setBatchResult] = useState<BatchLintResult | null>(null);

  // workflow-import state
  const [pickedWorkflow, setPickedWorkflow] = useState<File | null>(null);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);

  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const lintOpts = {
    eqtype: options.eqtype || undefined,
    strict: options.strict,
    testlist: options.testlist ?? undefined,
  };

  async function runSingle() {
    if (!pickedFile) return;
    setError(null);
    setRunning(true);
    try {
      const result = await lintSingle(pickedFile, lintOpts);
      setSingleResult(result);
      setFixResult(null);   // stale once a fresh lint runs
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setSingleResult(null);
    } finally {
      setRunning(false);
    }
  }

  async function runAutoFix() {
    if (!pickedFile) return;
    setError(null);
    setRunning(true);
    try {
      const result = await runFix(pickedFile);
      setFixResult(result);
      setSingleResult(null); // stale once fix changes the source
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setFixResult(null);
    } finally {
      setRunning(false);
    }
  }

  async function runImport() {
    if (!pickedWorkflow) return;
    setError(null);
    setRunning(true);
    try {
      const result = await importXlsx(pickedWorkflow);
      setImportResult(result);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setImportResult(null);
    } finally {
      setRunning(false);
    }
  }

  async function runBatch() {
    if (!pickedZip) return;
    setError(null);
    setRunning(true);
    try {
      const result = await lintBatch(pickedZip, lintOpts);
      setBatchResult(result);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setBatchResult(null);
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="app">
      <header>
        <h1>Rule Lint</h1>
        <span className="subtitle">
          Linter for Evolution rule-engine equations
        </span>
      </header>

      <div className="tabs">
        <button
          className={tab === "single" ? "active" : ""}
          onClick={() => setTab("single")}
        >
          Single file
        </button>
        <button
          className={tab === "batch" ? "active" : ""}
          onClick={() => setTab("batch")}
        >
          Multi-file (zip)
        </button>
        <button
          className={tab === "workflow" ? "active" : ""}
          onClick={() => setTab("workflow")}
        >
          Workflow import
        </button>
        <button
          className={tab === "preview" ? "active" : ""}
          onClick={() => setTab("preview")}
        >
          Mask preview
        </button>
        <button
          className={tab === "codes" ? "active" : ""}
          onClick={() => setTab("codes")}
        >
          Code reference
        </button>
      </div>

      {tab !== "codes" && tab !== "workflow" && tab !== "preview" && (
        <LintOptionsBar value={options} onChange={setOptions} />
      )}

      {tab === "single" && (
        <>
          <Dropzone
            accept=".eq,.rule,.mask"
            prompt="Drop a .eq / .rule / .mask file here"
            hint="Max 5 MiB"
            onFile={(f) => {
              setPickedFile(f);
              setSingleResult(null);
              setError(null);
            }}
          />
          <div className="run-row">
            <div className="picked">
              {pickedFile ? <>Picked: <strong>{pickedFile.name}</strong></> : "No file picked."}
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                className="primary"
                disabled={!pickedFile || running}
                onClick={runSingle}
              >
                {running ? "Working…" : "Run Lint"}
              </button>
              <button
                className="primary"
                disabled={!pickedFile || running}
                onClick={runAutoFix}
                title="Apply safe auto-fixes (scientific notation, trailing whitespace)"
              >
                Run Auto-Fix
              </button>
            </div>
          </div>
          {error && <div className="error-banner">{error}</div>}
          {singleResult && (
            <>
              <Summary
                errors={singleResult.errors}
                warnings={singleResult.warnings}
                info={singleResult.info}
                extra={`${singleResult.lines} line${singleResult.lines === 1 ? "" : "s"}`}
              />
              <IssueTable issues={singleResult.issues} />
            </>
          )}
          {fixResult && <FixPanel result={fixResult} />}
        </>
      )}

      {tab === "batch" && (
        <>
          <Dropzone
            accept=".zip"
            prompt="Drop a .zip containing your rule files"
            hint="Lints every .eq, .rule and .mask inside (max 500 files / 20 MiB)"
            onFile={(f) => {
              setPickedZip(f);
              setBatchResult(null);
              setError(null);
            }}
          />
          <div className="run-row">
            <div className="picked">
              {pickedZip ? <>Picked: <strong>{pickedZip.name}</strong></> : "No archive picked."}
            </div>
            <button
              className="primary"
              disabled={!pickedZip || running}
              onClick={runBatch}
            >
              {running ? "Linting…" : "Run Lint"}
            </button>
          </div>
          {error && <div className="error-banner">{error}</div>}
          {batchResult && (
            <>
              <Summary
                errors={batchResult.total_errors}
                warnings={batchResult.total_warnings}
                info={batchResult.total_info}
                extra={`${batchResult.files.length} file${batchResult.files.length === 1 ? "" : "s"}`}
              />
              {batchResult.files.map((f) => (
                <div className="file-block" key={f.filename}>
                  <h3>{f.filename}</h3>
                  <Summary
                    errors={f.errors}
                    warnings={f.warnings}
                    info={f.info}
                    extra={`${f.lines} line${f.lines === 1 ? "" : "s"}`}
                  />
                  <IssueTable issues={f.issues} />
                </div>
              ))}
            </>
          )}
        </>
      )}

      {tab === "workflow" && (
        <>
          <div className="run-row" style={{ marginTop: 0 }}>
            <div className="picked">
              Need the column layout? Save a starter template:
            </div>
            <a
              className="primary"
              href={templateUrl()}
              style={{ textDecoration: "none" }}
            >
              Save Template CSV
            </a>
          </div>
          <Dropzone
            accept=".xlsx,.csv"
            prompt="Drop a workflow .xlsx or .csv here"
            hint="Generates draft .eq files split by Action Category (Req Add / Req Delete / Modify by department)"
            onFile={(f) => {
              setPickedWorkflow(f);
              setImportResult(null);
              setError(null);
            }}
          />
          <div className="run-row">
            <div className="picked">
              {pickedWorkflow ? (
                <>Picked: <strong>{pickedWorkflow.name}</strong></>
              ) : (
                "No spreadsheet picked."
              )}
            </div>
            <button
              className="primary"
              disabled={!pickedWorkflow || running}
              onClick={runImport}
            >
              {running ? "Importing…" : "Import"}
            </button>
          </div>
          {error && <div className="error-banner">{error}</div>}
          {importResult && pickedWorkflow && (
            <WorkflowImportPanel
              result={importResult}
              sourceFile={pickedWorkflow}
            />
          )}
        </>
      )}

      {tab === "preview" && <PreviewPanel />}

      {tab === "codes" && <CodesPanel />}

      <footer>
        Rule Lint web UI · runs locally in Docker · same core as the CLI and Tk
        desktop GUI.
      </footer>
    </div>
  );
}
