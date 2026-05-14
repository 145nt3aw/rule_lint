import { useEffect, useState } from "react";
import { fetchEqtypes } from "../api";

export interface LintOptionsState {
  eqtype: string;
  strict: boolean;
  testlist: File | null;
}

interface Props {
  value: LintOptionsState;
  onChange: (v: LintOptionsState) => void;
}

export function LintOptionsBar({ value, onChange }: Props) {
  const [eqtypes, setEqtypes] = useState<string[]>([]);

  useEffect(() => {
    fetchEqtypes()
      .then(setEqtypes)
      .catch(() => setEqtypes([]));
  }, []);

  return (
    <div className="options">
      <label>
        Equation type:
        <select
          value={value.eqtype}
          onChange={(e) => onChange({ ...value, eqtype: e.target.value })}
        >
          <option value="">(none)</option>
          {eqtypes.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </label>

      <label>
        <input
          type="checkbox"
          checked={value.strict}
          onChange={(e) => onChange({ ...value, strict: e.target.checked })}
        />
        Strict
      </label>

      <label>
        Test catalogue (CSV):
        <input
          type="file"
          accept=".csv"
          onChange={(e) =>
            onChange({
              ...value,
              testlist: e.target.files?.[0] ?? null,
            })
          }
        />
        {value.testlist && (
          <span style={{ fontSize: 12, color: "var(--muted)" }}>
            {value.testlist.name}
          </span>
        )}
      </label>
    </div>
  );
}
