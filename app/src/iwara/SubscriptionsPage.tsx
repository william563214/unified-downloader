import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { humanizeError } from "../errors";
import type { IwaraSubscription, IwaraVideo } from "./types";
import { VideoGrid } from "./VideoGrid";

type Props = {
  downloadDir: string;
  cloudflareBypass: boolean;
  onEnqueueStarted?: () => void;
};

export function SubscriptionsPage({
  downloadDir,
  cloudflareBypass,
  onEnqueueStarted,
}: Props) {
  const [subs, setSubs] = useState<IwaraSubscription[]>([]);
  const [newItems, setNewItems] = useState<IwaraVideo[]>([]);
  const [target, setTarget] = useState("");
  const [kind, setKind] = useState<"auto" | "author" | "playlist">("auto");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [syncSummary, setSyncSummary] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const [list, news] = await Promise.all([
        invoke<IwaraSubscription[]>("iwara_subscriptions", {}),
        invoke<{ items: IwaraVideo[] }>("iwara_new_items", { limit: 64 }),
      ]);
      setSubs(Array.isArray(list) ? list : []);
      setNewItems(Array.isArray(news?.items) ? news.items : []);
      setSelected(new Set());
    } catch (e) {
      setError(humanizeError(String(e)));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function addSub() {
    if (!target.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await invoke("iwara_subscribe", { target: target.trim(), kind });
      setTarget("");
      await refresh();
    } catch (e) {
      setError(humanizeError(String(e)));
      setBusy(false);
    }
  }

  async function removeSub(id: number) {
    setBusy(true);
    try {
      await invoke("iwara_unsubscribe", { id });
      await refresh();
    } catch (e) {
      setError(humanizeError(String(e)));
      setBusy(false);
    }
  }

  async function syncNow() {
    if (!downloadDir.trim()) {
      setError("請先在設定中指定下載目錄");
      return;
    }
    setBusy(true);
    setError(null);
    setSyncSummary(null);
    try {
      const result = await invoke<{
        new_total: number;
        enqueued_total: number;
        subscriptions: number;
      }>("iwara_sync", {
        output: downloadDir,
        cloudflareBypass,
        maxPages: 5,
      });
      setSyncSummary(
        `已檢查 ${result.subscriptions} 個訂閱：新內容 ${result.new_total}，已入佇列 ${result.enqueued_total}`,
      );
      onEnqueueStarted?.();
      await refresh();
    } catch (e) {
      setError(humanizeError(String(e)));
      setBusy(false);
    }
  }

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function enqueueSelected() {
    if (selected.size === 0) return;
    setBusy(true);
    try {
      await invoke("iwara_enqueue", {
        videoIds: Array.from(selected),
        output: downloadDir,
        cloudflareBypass,
      });
      onEnqueueStarted?.();
      await refresh();
    } catch (e) {
      setError(humanizeError(String(e)));
      setBusy(false);
    }
  }

  async function markSeen() {
    setBusy(true);
    try {
      await invoke("iwara_mark_seen", {});
      await refresh();
    } catch (e) {
      setError(humanizeError(String(e)));
      setBusy(false);
    }
  }

  return (
    <section className="composer settings iwara-page" aria-label="Iwara 訂閱">
      <div className="queue-head">
        <h2>Iwara 訂閱</h2>
        <p>本機訂閱作者／播放清單並同步新內容（不含帳號 following 匯入）</p>
      </div>

      <div className="settings-grid">
        <label className="field compact">
          <span>類型</span>
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as typeof kind)}
          >
            <option value="auto">自動偵測</option>
            <option value="author">作者</option>
            <option value="playlist">播放清單</option>
          </select>
        </label>
        <label className="field compact grow">
          <span>作者／URL／播放清單</span>
          <input
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            placeholder="username 或 https://www.iwara.tv/…"
          />
        </label>
      </div>

      <div className="row-actions">
        <button type="button" className="primary" disabled={busy} onClick={() => void addSub()}>
          新增訂閱
        </button>
        <button type="button" className="ghost" disabled={busy} onClick={() => void syncNow()}>
          {busy ? "同步中…" : "立即同步"}
        </button>
        <button type="button" className="ghost" disabled={busy} onClick={() => void refresh()}>
          重新整理
        </button>
        <button
          type="button"
          className="ghost"
          disabled={busy || selected.size === 0}
          onClick={() => void enqueueSelected()}
        >
          選取入佇列
        </button>
        <button type="button" className="ghost" disabled={busy} onClick={() => void markSeen()}>
          全部標為已讀
        </button>
      </div>

      {syncSummary ? <p className="muted">{syncSummary}</p> : null}
      {error ? <p className="error-text">{error}</p> : null}

      <div className="iwara-sub-list">
        {subs.length === 0 ? (
          <p className="muted">尚無訂閱</p>
        ) : (
          <ul>
            {subs.map((s) => (
              <li key={s.id}>
                <div>
                  <strong>{s.title}</strong>
                  <span className="muted">
                    {" "}
                    · {s.kind} · {s.key}
                    {s.new_count ? ` · 新 ${s.new_count}` : ""}
                    {s.last_checked_at ? ` · 檢查於 ${s.last_checked_at}` : ""}
                  </span>
                </div>
                <button
                  type="button"
                  className="ghost compact"
                  disabled={busy}
                  onClick={() => void removeSub(s.id)}
                >
                  移除
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <h3>新內容</h3>
      <VideoGrid items={newItems} selected={selected} onToggle={toggle} />
    </section>
  );
}
