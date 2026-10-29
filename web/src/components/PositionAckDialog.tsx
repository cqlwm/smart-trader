import { useEffect, useState } from "react";

import { formatNumber } from "../utils/format";

export function PositionAckDialog({
  open,
  strategyKey,
  surplus,
  busy,
  error,
  onConfirm,
  onClose,
}: {
  open: boolean;
  strategyKey: string;
  surplus: number;
  busy: boolean;
  error: string | null;
  onConfirm: (externalQty: number, note: string) => void;
  onClose: () => void;
}) {
  const [qty, setQty] = useState("");
  const [note, setNote] = useState("");

  useEffect(() => {
    if (open) {
      setQty(String(surplus));
      setNote("");
    }
  }, [open, surplus]);

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

  const parsed = Number(qty);
  const valid =
    Number.isFinite(parsed) && parsed > 0 && parsed <= surplus + 0.000001;

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) {
          onClose();
        }
      }}
    >
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ack-title"
      >
        <div className="modal-kicker mono">POSITION / ACK</div>
        <h2 className="modal-title" id="ack-title">
          标记为人工仓位
        </h2>
        <p className="modal-desc">
          这是对账声明，不会把外部仓位纳入策略账本，也不会触发任何交易。
          交易所事实变化导致差额不再匹配时，标记会自动失效并重新告警。
        </p>

        <div className="ack-meta mono">
          <span>策略 {strategyKey}</span>
          <span>当前差额 {formatNumber(surplus)}</span>
        </div>

        <label className="modal-field">
          <span>人工仓位数量</span>
          <input
            type="text"
            inputMode="decimal"
            value={qty}
            onChange={(event) => setQty(event.target.value)}
            autoFocus
          />
        </label>

        <label className="modal-field">
          <span>备注（可选）</span>
          <textarea
            rows={2}
            value={note}
            placeholder="例如：手工中线仓 / 其他策略仓位"
            onChange={(event) => setNote(event.target.value)}
          />
        </label>

        {!valid && qty.trim() !== "" ? (
          <div className="modal-error mono">
            数量必须大于 0 且不超过当前差额 {formatNumber(surplus)}
          </div>
        ) : null}
        {error ? <div className="modal-error mono">{error}</div> : null}

        <div className="modal-actions">
          <button
            type="button"
            className="button"
            onClick={onClose}
            disabled={busy}
          >
            取消
          </button>
          <button
            type="button"
            className="button button-amber"
            disabled={!valid || busy}
            onClick={() => onConfirm(parsed, note.trim())}
          >
            {busy ? "提交中…" : "标记人工"}
          </button>
        </div>
      </div>
    </div>
  );
}
