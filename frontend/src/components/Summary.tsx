interface Props {
  errors: number;
  warnings: number;
  info: number;
  extra?: string;
}

export function Summary({ errors, warnings, info, extra }: Props) {
  const clean = errors === 0 && warnings === 0 && info === 0;
  return (
    <div className="summary">
      {clean && <span className="pill clean">clean ✓</span>}
      {errors > 0 && <span className="pill error">{errors} error{errors === 1 ? "" : "s"}</span>}
      {warnings > 0 && (
        <span className="pill warning">
          {warnings} warning{warnings === 1 ? "" : "s"}
        </span>
      )}
      {info > 0 && <span className="pill info">{info} info</span>}
      {extra && <span className="pill">{extra}</span>}
    </div>
  );
}
