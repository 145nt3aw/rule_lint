import { useEffect, useRef, useState } from "react";
import { cfpanelTemplateUrl, cftestTemplateUrl, previewMask } from "../api";
import type { PreviewResult } from "../types";
import { MaskGrid } from "./MaskGrid";

const DEFAULT_MASK = `/* Try editing this mask — the preview redraws as you type. */
output_text(0, 0, 0, 0, 0, "Patient header");

heading[1] = "Renal Profile";
heading[2] = "Liver Profile";

output_text(5, 2, 0, 0, 0, heading[1]);
output_text(5, 12, 0, 0, 0, heading[2]);

if (test_ordered("CREAT")) {
    output_testname(7, 4, 0, 0, 0, 0, 0, 0, 0, "CREAT");
    output_results(30, 4, 0, 0, 0, 0, "8w", "e", 0, "CREAT");
    output_units(40, 4, 0, 0, 0, 0, 0, "CREAT");
}
`;

const GRID_WIDTHS = [80, 120, 132];
const GRID_HEIGHTS = [25, 43, 60];

/** Strip a common .mask / .eq / .rule suffix to derive an include name. */
function deriveIncludeName(filename: string): string {
  return filename.replace(/\.(mask|eq|rule|txt)$/i, "");
}

/** Parse the ordered-tests text into a list, or undefined if blank. */
function parseOrderedTests(s: string): string[] | undefined {
  const trimmed = s.trim();
  if (!trimmed) return undefined;     // blank → superset mode
  return trimmed
    .split(/[,\s]+/)
    .map((t) => t.trim())
    .filter(Boolean);
}

export function PreviewPanel() {
  const [text, setText] = useState(DEFAULT_MASK);
  const [width, setWidth] = useState(120);
  const [height, setHeight] = useState(25);
  const [includes, setIncludes] = useState<Record<string, string>>({});
  const [orderedTestsText, setOrderedTestsText] = useState("");
  const [cftestTsv, setCftestTsv] = useState<string>("");
  const [cfpanelTsv, setCfpanelTsv] = useState<string>("");
  const [result, setResult] = useState<PreviewResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const debounceRef = useRef<number | null>(null);
  const includeInputRef = useRef<HTMLInputElement | null>(null);
  const cftestInputRef = useRef<HTMLInputElement | null>(null);
  const cfpanelInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
    }
    debounceRef.current = window.setTimeout(async () => {
      setRunning(true);
      try {
        const r = await previewMask(
          text, width, height, includes,
          parseOrderedTests(orderedTestsText),
          cftestTsv || undefined,
          cfpanelTsv || undefined,
        );
        setResult(r);
        setError(null);
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setRunning(false);
      }
    }, 300);
    return () => {
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
      }
    };
  }, [text, width, height, includes, orderedTestsText, cftestTsv, cfpanelTsv]);

  async function addIncludeFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    const updates: Record<string, string> = {};
    for (const file of Array.from(files)) {
      const name = deriveIncludeName(file.name);
      updates[name] = await file.text();
    }
    setIncludes((cur) => ({ ...cur, ...updates }));
    if (includeInputRef.current) includeInputRef.current.value = "";
  }

  function removeInclude(name: string) {
    setIncludes((cur) => {
      const next = { ...cur };
      delete next[name];
      return next;
    });
  }

  async function loadCftest(files: FileList | null) {
    if (!files || files.length === 0) return;
    setCftestTsv(await files[0].text());
    if (cftestInputRef.current) cftestInputRef.current.value = "";
  }

  async function loadCfpanel(files: FileList | null) {
    if (!files || files.length === 0) return;
    setCfpanelTsv(await files[0].text());
    if (cfpanelInputRef.current) cfpanelInputRef.current.value = "";
  }

  const includeNames = Object.keys(includes).sort();
  const parsedOrdered = parseOrderedTests(orderedTestsText);
  const fixtureMode = parsedOrdered !== undefined;
  const cftestRows = cftestTsv ? Math.max(0, cftestTsv.split("\n").length - 1) : 0;
  const cfpanelRows = cfpanelTsv ? Math.max(0, cfpanelTsv.split("\n").length - 1) : 0;
  const testsLoaded = result?.tests_loaded ?? 0;
  const panelsLoaded = result?.panels_loaded ?? 0;

  return (
    <div className="preview-panel">
      <div className="options">
        <label>
          Grid width:
          <select
            value={width}
            onChange={(e) => setWidth(parseInt(e.target.value, 10))}
          >
            {GRID_WIDTHS.map((w) => (
              <option key={w} value={w}>
                {w}
              </option>
            ))}
          </select>
        </label>
        <label>
          Grid height:
          <select
            value={height}
            onChange={(e) => setHeight(parseInt(e.target.value, 10))}
          >
            {GRID_HEIGHTS.map((h) => (
              <option key={h} value={h}>
                {h}
              </option>
            ))}
          </select>
        </label>
        <label style={{ flex: 1, minWidth: 280 }}>
          Ordered tests:
          <input
            type="text"
            value={orderedTestsText}
            onChange={(e) => setOrderedTestsText(e.target.value)}
            placeholder="comma-separated, e.g. SEMCON, SEMCASA, LP"
            style={{
              flex: 1,
              fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
              fontSize: 12,
              padding: "3px 6px",
              border: "1px solid var(--border)",
              borderRadius: 4,
              minWidth: 200,
            }}
          />
        </label>
        <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--muted)" }}>
          {running
            ? "rendering…"
            : result
            ? `${result.commands.length} cell${result.commands.length === 1 ? "" : "s"} · ${result.branches_expanded} branch${result.branches_expanded === 1 ? "" : "es"} expanded · ${includeNames.length} include${includeNames.length === 1 ? "" : "s"} · ${fixtureMode ? `fixture (${parsedOrdered?.length ?? 0} ordered)` : "superset"}${testsLoaded || panelsLoaded ? ` · ${testsLoaded} test${testsLoaded === 1 ? "" : "s"} · ${panelsLoaded} panel${panelsLoaded === 1 ? "" : "s"}` : " · no catalogue"}`
            : "no input"}
        </span>
      </div>

      <div className="include-bar">
        <button
          className="primary"
          style={{ padding: "4px 10px", fontSize: 12 }}
          onClick={() => includeInputRef.current?.click()}
        >
          Add include file(s)…
        </button>
        <input
          ref={includeInputRef}
          type="file"
          accept=".mask,.eq,.rule,.txt"
          multiple
          style={{ display: "none" }}
          onChange={(e) => addIncludeFiles(e.target.files)}
        />
        {includeNames.length === 0 && (
          <span style={{ fontSize: 12, color: "var(--muted)" }}>
            No includes attached. include_mask("X") references will warn
            until X is supplied here.
          </span>
        )}
        {includeNames.map((name) => (
          <span key={name} className="include-chip">
            <strong>{name}</strong>
            <span style={{ color: "var(--muted)", marginLeft: 4 }}>
              ({includes[name].split("\n").length} lines)
            </span>
            <button
              onClick={() => removeInclude(name)}
              title="Remove this include"
              aria-label={`Remove ${name}`}
            >
              ×
            </button>
          </span>
        ))}
      </div>

      <div className="include-bar">
        <button
          className="primary"
          style={{ padding: "4px 10px", fontSize: 12 }}
          onClick={() => cftestInputRef.current?.click()}
        >
          Load test catalogue (CFtest.tsv)…
        </button>
        <a
          href={cftestTemplateUrl()}
          download="CFtest.tsv"
          style={{ fontSize: 12, color: "var(--accent)" }}
        >
          template ↓
        </a>
        <input
          ref={cftestInputRef}
          type="file"
          accept=".tsv,.csv,.txt"
          style={{ display: "none" }}
          onChange={(e) => loadCftest(e.target.files)}
        />
        <button
          className="primary"
          style={{ padding: "4px 10px", fontSize: 12 }}
          onClick={() => cfpanelInputRef.current?.click()}
        >
          Load panel catalogue (CFpanel.tsv)…
        </button>
        <a
          href={cfpanelTemplateUrl()}
          download="CFpanel.tsv"
          style={{ fontSize: 12, color: "var(--accent)" }}
        >
          template ↓
        </a>
        <input
          ref={cfpanelInputRef}
          type="file"
          accept=".tsv,.csv,.txt"
          style={{ display: "none" }}
          onChange={(e) => loadCfpanel(e.target.files)}
        />
        {!cftestTsv && !cfpanelTsv && (
          <span style={{ fontSize: 12, color: "var(--muted)" }}>
            No catalogue loaded. Test mnemonics will render as bracketed
            placeholders; panel expansion is off until CFpanel is supplied.
          </span>
        )}
        {cftestTsv && (
          <span className="include-chip">
            <strong>CFtest</strong>
            <span style={{ color: "var(--muted)", marginLeft: 4 }}>
              ({cftestRows} rows · {testsLoaded} parsed)
            </span>
            <button
              onClick={() => setCftestTsv("")}
              title="Clear test catalogue"
              aria-label="Clear test catalogue"
            >
              ×
            </button>
          </span>
        )}
        {cfpanelTsv && (
          <span className="include-chip">
            <strong>CFpanel</strong>
            <span style={{ color: "var(--muted)", marginLeft: 4 }}>
              ({cfpanelRows} rows · {panelsLoaded} parsed)
            </span>
            <button
              onClick={() => setCfpanelTsv("")}
              title="Clear panel catalogue"
              aria-label="Clear panel catalogue"
            >
              ×
            </button>
          </span>
        )}
      </div>

      <div className="preview-split">
        <div className="preview-editor">
          <textarea
            spellCheck={false}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Paste or type a .mask source here…"
          />
        </div>
        <div className="preview-render">
          {error && <div className="error-banner">{error}</div>}
          {result && (
            <MaskGrid
              width={result.grid_width}
              height={result.grid_height}
              commands={result.commands}
            />
          )}
        </div>
      </div>

      {result && result.warnings.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <h3 style={{ margin: "0 0 8px", fontSize: 14 }}>
            Warnings ({result.warnings.length})
          </h3>
          <table className="issues">
            <thead>
              <tr>
                <th>Line</th>
                <th>Message</th>
              </tr>
            </thead>
            <tbody>
              {result.warnings.map((w, i) => (
                <tr key={`${w.line}-${i}`}>
                  <td className="loc">{w.line}</td>
                  <td>{w.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
