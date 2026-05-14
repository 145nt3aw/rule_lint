import type { RenderCmd } from "../types";

interface Props {
  width: number;
  height: number;
  commands: RenderCmd[];
}

/**
 * Paint render commands onto a fixed character grid.
 * Stores the grid in a width*height char buffer for cell-accurate
 * placement, then renders one <div> per row of the buffer.
 *
 * Rules:
 *   - text/field commands write left-to-right starting at (x, y).
 *     Each character of the command's text occupies one cell.
 *   - line commands fill a row segment with the supplied character(s).
 *   - box commands draw a simple outline (─│┌┐└┘).
 *   - Out-of-bounds writes are silently clipped.
 *   - A second-arrived command overwrites a first.
 */
export function MaskGrid({ width, height, commands }: Props) {
  // 2D character buffer initialised with non-breaking spaces so the
  // browser doesn't collapse empty cells.
  const cells: string[][] = [];
  const kinds: (string | null)[][] = [];
  for (let r = 0; r < height; r++) {
    cells.push(new Array(width).fill(" "));
    kinds.push(new Array(width).fill(null));
  }

  function put(x: number, y: number, ch: string, kind: string) {
    if (y < 0 || y >= height) return;
    if (x < 0 || x >= width) return;
    cells[y][x] = ch;
    kinds[y][x] = kind;
  }

  function paintText(x: number, y: number, text: string, kind: string) {
    for (let i = 0; i < text.length; i++) {
      put(x + i, y, text.charAt(i), kind);
    }
  }

  for (const cmd of commands) {
    if (cmd.kind === "text" || cmd.kind === "field" || cmd.kind === "line") {
      paintText(cmd.x, cmd.y, cmd.text, cmd.kind);
    } else if (cmd.kind === "box") {
      const x = cmd.x;
      const y = cmd.y;
      const w = Math.max(1, cmd.width);
      const h = Math.max(1, cmd.height);
      // Top
      put(x, y, "┌", "box");
      put(x + w - 1, y, "┐", "box");
      for (let i = 1; i < w - 1; i++) put(x + i, y, "─", "box");
      // Bottom
      put(x, y + h - 1, "└", "box");
      put(x + w - 1, y + h - 1, "┘", "box");
      for (let i = 1; i < w - 1; i++) put(x + i, y + h - 1, "─", "box");
      // Sides
      for (let j = 1; j < h - 1; j++) {
        put(x, y + j, "│", "box");
        put(x + w - 1, y + j, "│", "box");
      }
    }
  }

  return (
    <div className="mask-grid" style={{ width: `${width + 4}ch` }}>
      <div className="mask-grid-header">
        {/* column ruler — tens markers every 10 cols */}
        <span className="mask-rule-corner">{"   "}</span>
        <span className="mask-rule">
          {Array.from({ length: width }, (_, i) =>
            i % 10 === 0 && i > 0 ? `${i / 10}` : " ",
          ).join("")}
        </span>
      </div>
      {cells.map((row, y) => {
        const kindRow = kinds[y];
        return (
          <div key={y} className="mask-row">
            <span className="mask-rule-y">{String(y).padStart(2, " ")} </span>
            <span className="mask-row-body">
              {row.map((ch, x) => {
                const k = kindRow[x];
                return (
                  <span
                    key={x}
                    className={k ? `cell cell-${k}` : "cell"}
                  >
                    {ch}
                  </span>
                );
              })}
            </span>
          </div>
        );
      })}
    </div>
  );
}
