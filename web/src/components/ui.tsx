import type { ReactNode } from "react";

import { formatSigned, pnlClass } from "../utils/format";

export function PageHeader({
  kicker,
  title,
  description,
  meta,
  actions,
}: {
  kicker: string;
  title: string;
  description: string;
  meta?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-head reveal">
      <div>
        <div className="kicker">
          <span className="kicker-line" />
          {kicker}
        </div>
        <h1 className="page-title">{title}</h1>
        <p className="page-desc">{description}</p>
      </div>
      <div className="page-head-side">
        {meta ? <span className="page-meta">{meta}</span> : null}
        {actions}
      </div>
    </header>
  );
}

export function StatCard({
  label,
  value,
  sub,
  tone = "neutral",
  delay = 0,
}: {
  label: string;
  value: string;
  sub: string;
  tone?: "neutral" | "amber" | "positive" | "negative";
  delay?: number;
}) {
  return (
    <article
      className={`stat-card tone-${tone} reveal`}
      style={{ animationDelay: `${delay}ms` }}
    >
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      <span className="stat-sub">{sub}</span>
    </article>
  );
}

export function PnL({ value, digits = 4 }: { value: number; digits?: number }) {
  const rounded = Number(value.toFixed(digits));
  return (
    <span className={`mono ${pnlClass(rounded)}`} title={String(value)}>
      {formatSigned(rounded)}
    </span>
  );
}

export function Pill({
  children,
  variant = "neutral",
}: {
  children: ReactNode;
  variant?: "neutral" | "green" | "amber" | "red" | "blue" | "dim";
}) {
  return <span className={`pill pill-${variant}`}>{children}</span>;
}

export function EmptyState({
  title,
  hint,
}: {
  title: string;
  hint: string;
}) {
  return (
    <div className="empty-state">
      <span className="empty-mark">∅</span>
      <div>
        <div className="empty-title">{title}</div>
        <div className="empty-hint">{hint}</div>
      </div>
    </div>
  );
}

export function LoadingBlock({ label = "读取账本中" }: { label?: string }) {
  return (
    <div className="loading-block">
      <span className="loading-bars" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      {label}
    </div>
  );
}

export function ErrorPanel({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="error-panel">
      <div>
        <div className="error-title">API 读取失败</div>
        <div className="error-hint mono">{message}</div>
      </div>
      <button type="button" className="button" onClick={onRetry}>
        重试
      </button>
    </div>
  );
}

export function RefreshButton({
  onClick,
  busy,
}: {
  onClick: () => void;
  busy: boolean;
}) {
  return (
    <button
      type="button"
      className={`button ${busy ? "is-busy" : ""}`}
      onClick={onClick}
      disabled={busy}
      aria-label="刷新数据"
    >
      <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        <path
          d="M13.2 6.2A5.4 5.4 0 1 0 13 9.8M13.2 2.6v3.6H9.6"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      刷新
    </button>
  );
}
