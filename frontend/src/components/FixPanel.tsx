import type { FixResult } from "../types";

interface Props {
  result: FixResult;
}

function downloadText(filename: string, text: string) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function suggestFilename(original: string): string {
  // Insert .fixed before the extension so reviewers can diff easily.
  const dot = original.lastIndexOf(".");
  if (dot <= 0) return `${original}.fixed`;
  return `${original.slice(0, dot)}.fixed${original.slice(dot)}`;
}

export function FixPanel({ result }: Props) {
  if (!result.fixed) {
    return (
      <div className="empty">
        No auto-fixable issues found — file is already in shape.
      </div>
    );
  }

  const remaining =
    result.remaining_errors + result.remaining_warnings + result.remaining_info;

  return (
    <div className="fix-panel">
      <div className="summary">
        <span className="pill clean">
          {result.fixes.length} fix{result.fixes.length === 1 ? "" : "es"} applied
        </span>
        {result.remaining_errors > 0 && (
          <span className="pill error">
            {result.remaining_errors} error{result.remaining_errors === 1 ? "" : "s"} remain
          </span>
        )}
        {result.remaining_warnings > 0 && (
          <span className="pill warning">
            {result.remaining_warnings} warning{result.remaining_warnings === 1 ? "" : "s"} remain
          </span>
        )}
        {result.remaining_info > 0 && (
          <span className="pill info">{result.remaining_info} info remain</span>
        )}
        {remaining === 0 && <span className="pill clean">clean after fix ✓</span>}
      </div>

      <table className="issues">
        <thead>
          <tr>
            <th>Line</th>
            <th>Fix</th>
          </tr>
        </thead>
        <tbody>
          {result.fixes.map((f, i) => (
            <tr key={`${f.line}-${i}`}>
              <td className="loc">{f.line}</td>
              <td>{f.description}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="run-row">
        <div className="picked">
          {result.original_size} B → {result.fixed_size} B
        </div>
        <button
          className="primary"
          onClick={() =>
            downloadText(suggestFilename(result.filename), result.fixed_text)
          }
        >
          Download fixed file
        </button>
      </div>
    </div>
  );
}
