import { importXlsxZip } from "../api";
import type { ImportResult } from "../types";
import { Summary } from "./Summary";

interface Props {
  result: ImportResult;
  sourceFile: File;     // the original upload, re-used for the zip download
}

function downloadText(filename: string, text: string, mime = "text/plain") {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  triggerDownload(filename, blob);
}

function triggerDownload(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export function WorkflowImportPanel({ result, sourceFile }: Props) {
  async function downloadZip() {
    try {
      const blob = await importXlsxZip(sourceFile);
      triggerDownload("generated_rules.zip", blob);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <>
      <Summary
        errors={result.total_errors}
        warnings={result.total_warnings}
        info={0}
        extra={`${result.rows_parsed} row${result.rows_parsed === 1 ? "" : "s"} → ${result.files.length} file${result.files.length === 1 ? "" : "s"}`}
      />

      {result.files.length > 0 && (
        <>
          <div className="run-row">
            <div className="picked">Generated files:</div>
            <button
              className="primary"
              onClick={downloadZip}
              disabled={result.files.length === 0}
            >
              Download all as zip
            </button>
          </div>
          <table className="issues">
            <thead>
              <tr>
                <th>Filename</th>
                <th>Lines</th>
                <th style={{ width: 120 }}></th>
              </tr>
            </thead>
            <tbody>
              {result.files.map((f) => (
                <tr key={f.filename}>
                  <td className="code">{f.filename}</td>
                  <td className="loc">{f.lines}</td>
                  <td>
                    <button
                      className="primary"
                      style={{ padding: "4px 10px", fontSize: 12 }}
                      onClick={() => downloadText(f.filename, f.content)}
                    >
                      Download
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {result.issues.length > 0 && (
        <div style={{ marginTop: 20 }}>
          <h3 style={{ margin: "0 0 8px", fontSize: 15 }}>
            Issues ({result.issues.length})
          </h3>
          <table className="issues">
            <thead>
              <tr>
                <th>Severity</th>
                <th>Row</th>
                <th>Message</th>
              </tr>
            </thead>
            <tbody>
              {result.issues.map((i, idx) => (
                <tr key={`${i.row_number}-${idx}`}>
                  <td className={`severity ${i.severity}`}>{i.severity}</td>
                  <td className="loc">{i.row_number}</td>
                  <td>{i.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {result.files.length === 0 && result.issues.length === 0 && (
        <div className="empty">No rows produced any output.</div>
      )}
    </>
  );
}
