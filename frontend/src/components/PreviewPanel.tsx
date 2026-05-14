import { useEffect, useRef, useState } from "react";
import { previewMask } from "../api";
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
const GRID_HEIGHTS = [25, 43];

export function PreviewPanel() {
  const [text, setText] = useState(DEFAULT_MASK);
  const [width, setWidth] = useState(120);
  const [height, setHeight] = useState(25);
  const [result, setResult] = useState<PreviewResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const debounceRef = useRef<number | null>(null);

  // Debounced live re-parse on text / dimensions change.
  useEffect(() => {
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
    }
    debounceRef.current = window.setTimeout(async () => {
      setRunning(true);
      try {
        const r = await previewMask(text, width, height);
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
  }, [text, width, height]);

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
        <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--muted)" }}>
          {running
            ? "rendering…"
            : result
            ? `${result.commands.length} cell${result.commands.length === 1 ? "" : "s"} placed · ${result.branches_expanded} branch${result.branches_expanded === 1 ? "" : "es"} expanded (superset preview)`
            : "no input"}
        </span>
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
