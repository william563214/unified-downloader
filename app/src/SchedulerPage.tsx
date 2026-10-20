import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { humanizeError } from "./errors";
import {
  type AfterCompletion,
  type NamedQueue,
  type ScheduleListPayload,
  type SchedulerStatus,
  type SpeedProfile,
  after_completion_labels,
  empty_queue_draft,
  format_day_spec,
} from "./scheduler";

type Props = {
  onStatus?: (status: SchedulerStatus | null) => void;
};

const after_options: AfterCompletion[] = [
  "none",
  "notify",
  "quit",
  "sleep",
  "shutdown",
];

export function SchedulerPage({ onStatus }: Props) {
  const [payload, setPayload] = useState<ScheduleListPayload | null>(null);
  const [profiles, setProfiles] = useState<SpeedProfile[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<NamedQueue>(empty_queue_draft("night"));
  const [always, setAlways] = useState(true);
  const [profileName, setProfileName] = useState("daytime");
  const [profileDefault, setProfileDefault] = useState("2M");
  const [profileWindow, setProfileWindow] = useState("weekdays:09:00-18:00:512K");
  const [hourMb, setHourMb] = useState("500");
  const [dayMb, setDayMb] = useState("5000");
  const [quotaEnabled, setQuotaEnabled] = useState(false);

  const refresh = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const list = await invoke<ScheduleListPayload>("schedule_list");
      setPayload(list);
      onStatus?.(list?.status ?? null);
      const profs = await invoke<SpeedProfile[]>("schedule_profile_list");
      setProfiles(Array.isArray(profs) ? profs : []);
      const quota = list?.status?.quota;
      if (quota) {
        setQuotaEnabled(Boolean(quota.enabled));
        if (quota.hour_limit_mb != null) setHourMb(String(quota.hour_limit_mb));
        if (quota.day_limit_mb != null) setDayMb(String(quota.day_limit_mb));
      }
    } catch (e) {
      setError(humanizeError(String(e)));
      onStatus?.(null);
    } finally {
      setBusy(false);
    }
  }, [onStatus]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function saveQueue() {
    setBusy(true);
    setError(null);
    try {
      const daySpec =
        typeof draft.day_spec === "string"
          ? draft.day_spec
          : draft.day_spec.join(",");
      await invoke("schedule_set", {
        name: draft.name.trim(),
        enabled: draft.enabled,
        maxConcurrent: draft.max_concurrent,
        days: daySpec,
        start: always ? null : draft.start_time,
        end: always ? null : draft.end_time,
        always,
        repeatMode: draft.repeat_mode,
        after: draft.after_completion,
        speedProfile: draft.speed_profile ?? "",
      });
      await refresh();
    } catch (e) {
      setError(humanizeError(String(e)));
      setBusy(false);
    }
  }

  async function removeQueue(name: string) {
    setBusy(true);
    setError(null);
    try {
      await invoke("schedule_delete", { name });
      await refresh();
    } catch (e) {
      setError(humanizeError(String(e)));
      setBusy(false);
    }
  }

  async function resetOnce(name: string) {
    setBusy(true);
    try {
      await invoke("schedule_reset_once", { name });
      await refresh();
    } catch (e) {
      setError(humanizeError(String(e)));
      setBusy(false);
    }
  }

  async function saveProfile() {
    setBusy(true);
    setError(null);
    try {
      await invoke("schedule_profile_set", {
        name: profileName.trim(),
        defaultLimit: profileDefault,
        windows: profileWindow.trim() ? [profileWindow.trim()] : [],
        clearWindows: true,
      });
      await refresh();
    } catch (e) {
      setError(humanizeError(String(e)));
      setBusy(false);
    }
  }

  async function removeProfile(name: string) {
    setBusy(true);
    try {
      await invoke("schedule_profile_delete", { name });
      await refresh();
    } catch (e) {
      setError(humanizeError(String(e)));
      setBusy(false);
    }
  }

  async function saveQuota() {
    setBusy(true);
    setError(null);
    try {
      await invoke("schedule_quota", {
        enable: quotaEnabled,
        hourMb: Number(hourMb) || 0,
        dayMb: Number(dayMb) || 0,
        clearPause: false,
      });
      await refresh();
    } catch (e) {
      setError(humanizeError(String(e)));
      setBusy(false);
    }
  }

  async function clearQuotaPause() {
    setBusy(true);
    try {
      await invoke("schedule_quota", { clearPause: true });
      await refresh();
    } catch (e) {
      setError(humanizeError(String(e)));
      setBusy(false);
    }
  }

  function editQueue(q: NamedQueue) {
    setDraft({ ...q });
    setAlways(!q.start_time && !q.end_time);
  }

  const status = payload?.status;
  const queues = payload?.queues || [];

  return (
    <section className="panel scheduler-page" aria-label="排程">
      <div className="panel-head">
        <div>
          <h2>排程與配額</h2>
          <p className="muted">
            具名佇列、時段、完成後動作、流量配額與時段限速。設定寫入 SQLite，重啟後保留。
          </p>
        </div>
        <button type="button" className="ghost" onClick={() => void refresh()} disabled={busy}>
          重新整理
        </button>
      </div>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {status && (
        <div className="scheduler-status" role="status">
          <p>
            狀態：{status.active ? "可執行" : "暫停中"}
            {status.global_speed_limit
              ? ` · 有效限速 ${status.global_speed_limit}`
              : ""}
          </p>
          <p className="muted">
            配額{" "}
            {status.quota.enabled
              ? `小時 ${status.quota.hour_used_mb.toFixed(1)}/${status.quota.hour_limit_mb ?? "—"} MB · 日 ${status.quota.day_used_mb.toFixed(1)}/${status.quota.day_limit_mb ?? "—"} MB`
              : "關閉"}
            {status.quota.paused && status.quota.paused_until
              ? ` · 暫停至 ${status.quota.paused_until}`
              : ""}
          </p>
          {status.quota.paused && (
            <button type="button" className="ghost" onClick={() => void clearQuotaPause()}>
              清除配額暫停
            </button>
          )}
        </div>
      )}

      <div className="scheduler-grid">
        <div className="scheduler-block">
          <h3>具名佇列</h3>
          <ul className="scheduler-list">
            {queues.map((q) => {
              const runtime = status?.queues.find((r) => r.name === q.name);
              return (
                <li key={q.name}>
                  <div>
                    <strong>{q.name}</strong>
                    <span className="muted">
                      {" "}
                      {runtime?.active ? "進行中視窗" : runtime?.reason || "待機"} · 並發{" "}
                      {q.max_concurrent} · {format_day_spec(q.day_spec)}
                      {q.start_time
                        ? ` ${q.start_time}-${q.end_time}`
                        : " 全天"}
                      · 完成後 {after_completion_labels[q.after_completion]}
                      {q.speed_profile ? ` · 限速檔 ${q.speed_profile}` : ""}
                    </span>
                  </div>
                  <div className="row gap">
                    <button type="button" className="ghost" onClick={() => editQueue(q)}>
                      編輯
                    </button>
                    {q.once_completed_at && (
                      <button
                        type="button"
                        className="ghost"
                        onClick={() => void resetOnce(q.name)}
                      >
                        重設一次性
                      </button>
                    )}
                    {q.name !== "default" && (
                      <button
                        type="button"
                        className="danger"
                        onClick={() => void removeQueue(q.name)}
                      >
                        刪除
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>

          <h3>編輯佇列</h3>
          <div className="settings-grid">
            <label>
              名稱
              <input
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </label>
            <label>
              最大並發
              <input
                type="number"
                min={1}
                value={draft.max_concurrent}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    max_concurrent: Number(e.target.value) || 1,
                  })
                }
              />
            </label>
            <label>
              日期
              <select
                value={typeof draft.day_spec === "string" ? draft.day_spec : "daily"}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    day_spec: e.target.value as NamedQueue["day_spec"],
                  })
                }
              >
                <option value="daily">每天</option>
                <option value="weekdays">平日</option>
                <option value="weekend">週末</option>
              </select>
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={draft.enabled}
                onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
              />
              啟用
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={always}
                onChange={(e) => setAlways(e.target.checked)}
              />
              全天（不限時段）
            </label>
            {!always && (
              <>
                <label>
                  開始
                  <input
                    type="time"
                    value={draft.start_time || "00:00"}
                    onChange={(e) =>
                      setDraft({ ...draft, start_time: e.target.value })
                    }
                  />
                </label>
                <label>
                  結束
                  <input
                    type="time"
                    value={draft.end_time || "23:59"}
                    onChange={(e) =>
                      setDraft({ ...draft, end_time: e.target.value })
                    }
                  />
                </label>
              </>
            )}
            <label>
              重複
              <select
                value={draft.repeat_mode}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    repeat_mode: e.target.value as NamedQueue["repeat_mode"],
                  })
                }
              >
                <option value="repeat">重複</option>
                <option value="once">執行一次</option>
              </select>
            </label>
            <label>
              完成後動作
              <select
                value={draft.after_completion}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    after_completion: e.target.value as AfterCompletion,
                  })
                }
              >
                {after_options.map((a) => (
                  <option key={a} value={a}>
                    {after_completion_labels[a]}
                  </option>
                ))}
              </select>
            </label>
            <label>
              限速檔
              <select
                value={draft.speed_profile || ""}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    speed_profile: e.target.value || null,
                  })
                }
              >
                <option value="">無</option>
                {profiles.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <button type="button" onClick={() => void saveQueue()} disabled={busy || !draft.name.trim()}>
            儲存佇列
          </button>
        </div>

        <div className="scheduler-block">
          <h3>時段限速檔</h3>
          <ul className="scheduler-list">
            {profiles.map((p) => (
              <li key={p.name}>
                <div>
                  <strong>{p.name}</strong>
                  <span className="muted">
                    {" "}
                    預設 {p.default_speed_limit || "不限"} · {p.windows.length} 個視窗
                  </span>
                </div>
                <button
                  type="button"
                  className="danger"
                  onClick={() => void removeProfile(p.name)}
                >
                  刪除
                </button>
              </li>
            ))}
          </ul>
          <div className="settings-grid">
            <label>
              名稱
              <input value={profileName} onChange={(e) => setProfileName(e.target.value)} />
            </label>
            <label>
              預設限速
              <input
                value={profileDefault}
                onChange={(e) => setProfileDefault(e.target.value)}
                placeholder="2M"
              />
            </label>
            <label>
              視窗（days:HH:MM-HH:MM:limit）
              <input
                value={profileWindow}
                onChange={(e) => setProfileWindow(e.target.value)}
                placeholder="weekdays:09:00-18:00:512K"
              />
            </label>
          </div>
          <button type="button" onClick={() => void saveProfile()} disabled={busy}>
            儲存限速檔
          </button>

          <h3>流量配額</h3>
          <div className="settings-grid">
            <label className="check">
              <input
                type="checkbox"
                checked={quotaEnabled}
                onChange={(e) => setQuotaEnabled(e.target.checked)}
              />
              啟用配額
            </label>
            <label>
              每小時上限（MB）
              <input value={hourMb} onChange={(e) => setHourMb(e.target.value)} />
            </label>
            <label>
              每日上限（MB）
              <input value={dayMb} onChange={(e) => setDayMb(e.target.value)} />
            </label>
          </div>
          <button type="button" onClick={() => void saveQuota()} disabled={busy}>
            儲存配額
          </button>
          <p className="muted">
            超量時佇列暫停，下一個小時／日視窗自動恢復。下載位元組經既有 rate limiter 累計。
          </p>
        </div>
      </div>
    </section>
  );
}
