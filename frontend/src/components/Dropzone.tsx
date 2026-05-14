import { useCallback, useRef, useState, type DragEvent } from "react";

interface Props {
  accept: string;             // e.g. ".eq,.rule,.mask" or ".zip"
  prompt: string;             // visible primary line
  hint?: string;              // smaller helper text
  multiple?: boolean;
  onFile: (file: File) => void;
}

export function Dropzone({ accept, prompt, hint, multiple, onFile }: Props) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const onDrop = useCallback(
    (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setDragging(false);
      const files = e.dataTransfer.files;
      if (files.length > 0) onFile(files[0]);
    },
    [onFile],
  );

  return (
    <div
      className={`dropzone ${dragging ? "dragging" : ""}`}
      onClick={() => inputRef.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      <strong>{prompt}</strong>
      <div>or click to browse</div>
      {hint && <div className="hint">{hint}</div>}
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple={multiple}
        style={{ display: "none" }}
        onChange={(e) => {
          if (e.target.files && e.target.files.length > 0) {
            onFile(e.target.files[0]);
          }
          // reset so the same filename can be re-picked
          if (inputRef.current) inputRef.current.value = "";
        }}
      />
    </div>
  );
}
