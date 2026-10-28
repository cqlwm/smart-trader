import { useEffect } from "react";

export function ConfirmDialog({
  open,
  kicker = "CONTROL / CONFIRM",
  title,
  description,
  confirmLabel,
  tone,
  busy,
  error,
  onConfirm,
  onClose,
}: {
  open: boolean;
  kicker?: string;
  title: string;
  description: string;
  confirmLabel: string;
  tone: "danger" | "amber";
  busy: boolean;
  error: string | null;
  onConfirm: () => void;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!open) {
      return;
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) {
        onClose();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, busy, onClose]);

  if (!open) {
    return null;
  }

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) {
          onClose();
        }
      }}
    >
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
        <div className="modal-kicker mono">{kicker}</div>
        <h2 className="modal-title" id="confirm-title">
          {title}
        </h2>
        <p className="modal-desc">{description}</p>
        {error ? <div className="modal-error mono">{error}</div> : null}
        <div className="modal-actions">
          <button type="button" className="button" onClick={onClose} disabled={busy} autoFocus>
            取消
          </button>
          <button
            type="button"
            className={`button ${tone === "danger" ? "button-danger" : "button-amber"}`}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "提交中…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
