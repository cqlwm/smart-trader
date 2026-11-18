import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";

import {
  LOCAL_BACKEND_ID,
  getActiveBackendId,
  loadBackends,
  newBackendId,
  normalizeBaseUrl,
  saveBackends,
  setActiveBackendId,
} from "../api/backends";
import type { BackendProfile } from "../api/backends";
import { ApiError, testBackendConnection } from "../api/client";
import { useHealth } from "../api/hooks";
import { ConfirmDialog } from "./ConfirmDialog";

function maskKey(key: string): string {
  if (!key) {
    return "未配置";
  }
  if (key.length <= 8) {
    return "••••";
  }
  return `${key.slice(0, 4)}••••${key.slice(-4)}`;
}

function displayUrl(baseUrl: string): string {
  return baseUrl || "同源 (当前页面)";
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return "认证失败 (401): API Key 不正确";
    }
    return `请求失败 (${error.status}): ${error.message}`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "连接失败: 未知错误";
}

interface DraftState {
  id: string | null;
  name: string;
  baseUrl: string;
  apiKey: string;
}

type TestState =
  | { status: "idle"; message: "" }
  | { status: "testing" | "ok" | "error"; message: string };

const IDLE_TEST: TestState = { status: "idle", message: "" };

function emptyDraft(): DraftState {
  return { id: null, name: "", baseUrl: "", apiKey: "" };
}

export function BackendSwitcher() {
  const queryClient = useQueryClient();
  const health = useHealth();
  const [profiles, setProfiles] = useState<BackendProfile[]>(loadBackends);
  const [activeId, setActiveId] = useState<string>(getActiveBackendId);
  const [menuOpen, setMenuOpen] = useState(false);
  const [managerOpen, setManagerOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  const active = profiles.find((profile) => profile.id === activeId) ?? profiles[0];
  const unauthorized =
    health.error instanceof ApiError && health.error.status === 401;
  const online = health.isSuccess;

  useEffect(() => {
    if (!menuOpen) {
      return;
    }
    function onMouseDown(event: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, [menuOpen]);

  function refresh() {
    void queryClient.cancelQueries();
    queryClient.clear();
  }

  function activate(id: string) {
    setActiveBackendId(id);
    setActiveId(id);
    setMenuOpen(false);
    refresh();
  }

  function persist(next: BackendProfile[]) {
    saveBackends(next);
    setProfiles(next);
    if (!next.some((profile) => profile.id === activeId)) {
      setActiveBackendId(LOCAL_BACKEND_ID);
      setActiveId(LOCAL_BACKEND_ID);
      refresh();
    }
  }

  if (!active) {
    return null;
  }

  const statusLabel = online
    ? "API ONLINE"
    : unauthorized
      ? "API UNAUTHORIZED"
      : health.isError
        ? "API OFFLINE"
        : "API CONNECTING";

  return (
    <>
      <div className="backend-switcher" ref={rootRef}>
        <button
          type="button"
          className="backend-trigger"
          onClick={() => setMenuOpen((open) => !open)}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          title={statusLabel}
        >
          <span className={online ? "is-online" : health.isError ? "is-offline" : undefined}>
            <span className="status-dot" />
          </span>
          <span className="backend-trigger-name">{active.name}</span>
          <span className="backend-caret" aria-hidden="true">
            ▾
          </span>
        </button>

        {menuOpen ? (
          <div className="backend-menu" role="menu">
            {profiles.map((profile) => (
              <button
                key={profile.id}
                type="button"
                role="menuitem"
                className={`backend-menu-item ${profile.id === activeId ? "is-active" : ""}`}
                onClick={() => activate(profile.id)}
              >
                <span className="backend-menu-name">{profile.name}</span>
                <span className="backend-menu-url mono">
                  {displayUrl(profile.baseUrl)}
                </span>
              </button>
            ))}
            <div className="backend-menu-sep" />
            <button
              type="button"
              role="menuitem"
              className="backend-menu-item"
              onClick={() => {
                setMenuOpen(false);
                setManagerOpen(true);
              }}
            >
              <span className="backend-menu-name">管理后端…</span>
            </button>
          </div>
        ) : null}
      </div>

      {managerOpen ? (
        <BackendManagerDialog
          profiles={profiles}
          activeId={activeId}
          onActivate={activate}
          onPersist={persist}
          onRefresh={refresh}
          onClose={() => setManagerOpen(false)}
        />
      ) : null}
    </>
  );
}

function BackendManagerDialog({
  profiles,
  activeId,
  onActivate,
  onPersist,
  onRefresh,
  onClose,
}: {
  profiles: BackendProfile[];
  activeId: string;
  onActivate: (id: string) => void;
  onPersist: (profiles: BackendProfile[]) => void;
  onRefresh: () => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState<DraftState | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [testState, setTestState] = useState<TestState>(IDLE_TEST);
  const [deleteTarget, setDeleteTarget] = useState<BackendProfile | null>(null);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && deleteTarget === null && draft === null) {
        onClose();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [deleteTarget, draft, onClose]);

  function startCreate() {
    setDraft(emptyDraft());
    setFormError(null);
    setTestState(IDLE_TEST);
  }

  function startEdit(profile: BackendProfile) {
    setDraft({
      id: profile.id,
      name: profile.name,
      baseUrl: profile.baseUrl,
      apiKey: profile.apiKey,
    });
    setFormError(null);
    setTestState(IDLE_TEST);
  }

  function draftProfile(): BackendProfile | null {
    if (!draft) {
      return null;
    }
    const name = draft.name.trim();
    const baseUrl = normalizeBaseUrl(draft.baseUrl);
    const apiKey = draft.apiKey.trim();
    if (!name) {
      setFormError("名称不能为空");
      return null;
    }
    if (baseUrl) {
      try {
        const parsed = new URL(baseUrl);
        if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
          setFormError("API 地址只支持 http:// 或 https://");
          return null;
        }
      } catch {
        setFormError("API 地址格式不正确");
        return null;
      }
    }
    if (!apiKey) {
      setFormError("API Key 不能为空");
      return null;
    }
    return { id: draft.id ?? newBackendId(), name, baseUrl, apiKey };
  }

  async function handleTest() {
    const candidate = draftProfile();
    if (!candidate) {
      return;
    }
    setFormError(null);
    setTestState({ status: "testing", message: "正在连接…" });
    try {
      const result = await testBackendConnection(candidate);
      setTestState({
        status: "ok",
        message: `连接成功 · ${result.database} · ${result.server_time}`,
      });
    } catch (error) {
      setTestState({ status: "error", message: describeError(error) });
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const candidate = draftProfile();
    if (!candidate) {
      return;
    }
    const exists = profiles.some((profile) => profile.id === candidate.id);
    const next = exists
      ? profiles.map((profile) =>
          profile.id === candidate.id ? candidate : profile,
        )
      : [...profiles, candidate];
    onPersist(next);
    if (candidate.id === activeId) {
      onRefresh();
    }
    setDraft(null);
    setFormError(null);
    setTestState(IDLE_TEST);
  }

  function confirmDelete() {
    if (!deleteTarget) {
      return;
    }
    onPersist(profiles.filter((profile) => profile.id !== deleteTarget.id));
    setDeleteTarget(null);
  }

  return (
    <>
      <div
        className="modal-backdrop"
        onMouseDown={(event) => {
          if (event.target === event.currentTarget && draft === null) {
            onClose();
          }
        }}
      >
        <div
          className="modal backend-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="backend-manager-title"
        >
          <div className="modal-kicker mono">CONNECTION / BACKENDS</div>
          <h2 className="modal-title" id="backend-manager-title">
            后端连接
          </h2>
          <p className="modal-desc">
            连接配置与 API Key 仅保存在当前浏览器的 localStorage。切换后端会清空
            页面缓存并重新拉取数据。
          </p>

          {draft === null ? (
            <>
              <div className="backend-list">
                {profiles.map((profile) => (
                  <div
                    key={profile.id}
                    className={`backend-row ${profile.id === activeId ? "is-active" : ""}`}
                  >
                    <div className="backend-row-main">
                      <div className="backend-row-name">
                        {profile.name}
                        {profile.id === LOCAL_BACKEND_ID ? (
                          <span className="backend-tag">默认</span>
                        ) : null}
                        {profile.id === activeId ? (
                          <span className="backend-tag is-current">当前</span>
                        ) : null}
                      </div>
                      <div className="backend-row-url mono">
                        {displayUrl(profile.baseUrl)}
                      </div>
                      <div className="backend-row-key mono">
                        API KEY {maskKey(profile.apiKey)}
                      </div>
                    </div>
                    <div className="backend-row-actions">
                      {profile.id !== activeId ? (
                        <button
                          type="button"
                          className="btn-xs"
                          onClick={() => onActivate(profile.id)}
                        >
                          切换
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className="btn-xs"
                        onClick={() => startEdit(profile)}
                      >
                        编辑
                      </button>
                      {profile.id !== LOCAL_BACKEND_ID ? (
                        <button
                          type="button"
                          className="btn-xs btn-xs-danger"
                          onClick={() => setDeleteTarget(profile)}
                        >
                          删除
                        </button>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>

              <div className="backend-form-actions">
                <button type="button" className="button" onClick={startCreate}>
                  + 新增后端
                </button>
                <button type="button" className="button" onClick={onClose}>
                  关闭
                </button>
              </div>
            </>
          ) : (
            <form onSubmit={handleSubmit}>
              <label className="modal-field">
                名称
                <input
                  value={draft.name}
                  onChange={(event) =>
                    setDraft({ ...draft, name: event.target.value })
                  }
                  placeholder="例如 服务器 / 备用节点"
                  autoFocus
                />
              </label>
              <label className="modal-field">
                API 地址
                <input
                  value={draft.baseUrl}
                  onChange={(event) =>
                    setDraft({ ...draft, baseUrl: event.target.value })
                  }
                  placeholder="留空 = 同源；例如 http://1.2.3.4:8000"
                />
              </label>
              <label className="modal-field">
                API Key
                <input
                  type="password"
                  value={draft.apiKey}
                  onChange={(event) =>
                    setDraft({ ...draft, apiKey: event.target.value })
                  }
                  placeholder="与后端 SMART_TRADER_API_TOKEN 一致"
                  autoComplete="off"
                />
              </label>

              {formError ? (
                <div className="modal-error mono">{formError}</div>
              ) : null}
              {testState.message ? (
                <div className={`backend-test is-${testState.status}`}>
                  {testState.message}
                </div>
              ) : null}

              <div className="backend-form-actions">
                <button
                  type="button"
                  className="button"
                  onClick={() => {
                    void handleTest();
                  }}
                  disabled={testState.status === "testing"}
                >
                  {testState.status === "testing" ? "测试中…" : "测试连接"}
                </button>
                <div className="backend-form-buttons">
                  <button
                    type="button"
                    className="button"
                    onClick={() => {
                      setDraft(null);
                      setFormError(null);
                      setTestState(IDLE_TEST);
                    }}
                  >
                    返回
                  </button>
                  <button type="submit" className="button button-amber">
                    保存
                  </button>
                </div>
              </div>
            </form>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除后端"
        description={`确认删除「${deleteTarget?.name ?? ""}」？该地址与保存在本机的 API Key 会被一并移除。`}
        confirmLabel="删除"
        tone="danger"
        busy={false}
        error={null}
        onConfirm={confirmDelete}
        onClose={() => setDeleteTarget(null)}
      />
    </>
  );
}
