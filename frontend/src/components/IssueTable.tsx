import type { Issue } from "../types";

interface Props {
  issues: Issue[];
}

export function IssueTable({ issues }: Props) {
  if (issues.length === 0) {
    return <div className="empty">No issues found — file is clean.</div>;
  }

  const sorted = [...issues].sort((a, b) => {
    if (a.line !== b.line) return a.line - b.line;
    return a.column - b.column;
  });

  return (
    <table className="issues">
      <thead>
        <tr>
          <th>Severity</th>
          <th>Line</th>
          <th>Col</th>
          <th>Code</th>
          <th>Message</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((i, idx) => (
          <tr key={`${i.line}-${i.column}-${i.code}-${idx}`}>
            <td className={`severity ${i.severity}`}>{i.severity}</td>
            <td className="loc">{i.line}</td>
            <td className="loc">{i.column}</td>
            <td className="code">{i.code}</td>
            <td>
              {i.message}
              {i.include_chain.length > 0 && (
                <span style={{ color: "var(--muted)", marginLeft: 8 }}>
                  (via {[...i.include_chain].reverse().join(" ← ")})
                </span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
