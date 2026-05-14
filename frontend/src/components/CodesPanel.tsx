import { useEffect, useState } from "react";
import { fetchCodes } from "../api";
import type { CodeEntry } from "../types";

export function CodesPanel() {
  const [codes, setCodes] = useState<CodeEntry[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetchCodes()
      .then(setCodes)
      .catch((e: unknown) => setErr(String(e)));
  }, []);

  if (err) return <div className="error-banner">Could not load codes: {err}</div>;
  if (!codes) return <div className="empty">Loading code reference…</div>;

  return (
    <table className="issues codes-table">
      <thead>
        <tr>
          <th>Code</th>
          <th>Severity</th>
          <th>Description</th>
        </tr>
      </thead>
      <tbody>
        {codes.map((c) => (
          <tr key={c.code}>
            <td className="code">{c.code}</td>
            <td className={`severity ${c.severity} sev`}>{c.severity}</td>
            <td>{c.description}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
