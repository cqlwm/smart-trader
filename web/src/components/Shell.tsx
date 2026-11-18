import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { useHealth } from "../api/hooks";
import { ApiError } from "../api/client";
import { formatClock } from "../utils/format";
import { BackendSwitcher } from "./BackendSwitcher";

const PAGES = [
  {
    path: "/",
    index: "01",
    label: "总览",
    en: "OVERVIEW",
    desc: "账本聚合与告警",
  },
  {
    path: "/positions",
    index: "02",
    label: "持仓",
    en: "POSITIONS",
    desc: "逻辑仓位与订单派生",
  },
  {
    path: "/orders",
    index: "03",
    label: "订单",
    en: "ORDERS",
    desc: "委托流水与 WAL 状态",
  },
  {
    path: "/market",
    index: "04",
    label: "行情",
    en: "MARKET",
    desc: "K 线 / AlphaTrend / 仓位标记",
  },
  {
    path: "/configs",
    index: "05",
    label: "配置",
    en: "CONFIG",
    desc: "策略参数与实例",
  },
] as const;

export function Shell({ children }: { children: ReactNode }) {
  const location = useLocation();
  const health = useHealth();
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const current =
    PAGES.find((page) =>
      page.path === "/" ? location.pathname === "/" : location.pathname.startsWith(page.path),
    ) ?? PAGES[0];

  const dbName = health.data?.database.split("/").pop() ?? "ledger.db";
  const online = health.isSuccess;
  const unauthorized =
    health.error instanceof ApiError && health.error.status === 401;
  const statusLabel = online
    ? "API ONLINE"
    : unauthorized
      ? "API UNAUTHORIZED"
      : health.isError
        ? "API OFFLINE"
        : "CONNECTING";
  const miniLabel = online ? "LIVE" : unauthorized ? "AUTH" : "DOWN";

  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand">
          <div className="brand-mark">ST</div>
          <div>
            <div className="brand-name">SMART TRADER</div>
            <div className="brand-sub">LEDGER CONSOLE</div>
          </div>
        </div>

        <nav className="nav" aria-label="主导航">
          {PAGES.map((page) => (
            <NavLink
              key={page.path}
              to={page.path}
              end={page.path === "/"}
              className={({ isActive }) => `nav-item ${isActive ? "is-active" : ""}`}
            >
              <span className="nav-index">{page.index}</span>
              <span className="nav-text">
                <span className="nav-label">{page.label}</span>
                <span className="nav-en">{page.en}</span>
              </span>
              <span className="nav-arrow">→</span>
            </NavLink>
          ))}
        </nav>

        <div className="rail-foot">
          <div className={`status-line ${online ? "is-online" : "is-offline"}`}>
            <span className="status-dot" />
            {statusLabel}
          </div>
          <div className="rail-file mono" title={health.data?.database}>
            {dbName}
          </div>
          <div className="rail-mode">P3 / CONFIG PLANE</div>
        </div>
      </aside>

      <div className="main">
        <div className="topbar">
          <div className="topbar-crumb">
            <span className="mono">SMART TRADER</span>
            <span className="crumb-sep">/</span>
            <span>{current.en}</span>
            <span className="crumb-desc">{current.desc}</span>
          </div>
          <div className="topbar-right">
            <BackendSwitcher />
            <span className="clock mono">UTC {formatClock(now)}</span>
            <span className={`mini-status ${online ? "is-online" : "is-offline"}`}>
              <span className="status-dot" />
              {miniLabel}
            </span>
          </div>
        </div>

        <main className="content">{children}</main>
      </div>
    </div>
  );
}
