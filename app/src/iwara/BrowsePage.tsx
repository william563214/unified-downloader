import { useCallback, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { humanizeError } from "../errors";
import { PreviewModal } from "./PreviewModal";
import type { BrowsePage as BrowsePayload, IwaraVideo, RatingFilter } from "./types";
import { VideoGrid } from "./VideoGrid";

type Mode = "search" | "tag" | "author" | "playlist";

type Props = {
  downloadDir: string;
  cloudflareBypass: boolean;
  onEnqueueStarted?: () => void;
};

export function BrowsePage({ downloadDir, cloudflareBypass, onEnqueueStarted }: Props) {
  const [mode, setMode] = useState<Mode>("search");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("date");
  const [rating, setRating] = useState<RatingFilter>("all");
  const [page, setPage] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BrowsePayload | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState<IwaraVideo | null>(null);
  const [minLikes, setMinLikes] = useState("");
  const [minViews, setMinViews] = useState("");
  const [excludeTags, setExcludeTags] = useState("");
  const [titleKw, setTitleKw] = useState("");

  const runBrowse = useCallback(
    async (nextPage = 0) => {
      const q = query.trim();
      if (!q && mode !== "search") {
        setError("請輸入查詢內容");
        return;
      }
      if (mode === "search" && !q) {
        setError("請輸入關鍵字");
        return;
      }
      setBusy(true);
      setError(null);
      try {
        const payload = await invoke<BrowsePayload>("iwara_browse", {
          action: mode,
          query: q,
          page: nextPage,
          limit: 32,
          sort: mode === "search" ? (sort === "date" ? "relevance" : sort) : sort,
          rating: rating === "all" ? null : rating,
          cloudflareBypass,
          minLikes: minLikes.trim() ? Number(minLikes) : null,
          minViews: minViews.trim() ? Number(minViews) : null,
          excludeTags: excludeTags.trim() || null,
          title: titleKw.trim() || null,
        });
        if (payload?.error) {
          setError(humanizeError(payload.error));
        }
        setResult(payload);
        setPage(nextPage);
        setSelected(new Set());
      } catch (e) {
        setError(humanizeError(String(e)));
        setResult(null);
      } finally {
        setBusy(false);
      }
    },
    [
      query,
      mode,
      sort,
      rating,
      cloudflareBypass,
      minLikes,
      minViews,
      excludeTags,
      titleKw,
    ],
  );

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
    if (!downloadDir.trim()) {
      setError("請先在設定中指定下載目錄");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await invoke("iwara_enqueue", {
        videoIds: Array.from(selected),
        output: downloadDir,
        cloudflareBypass,
      });
      onEnqueueStarted?.();
      await runBrowse(page);
    } catch (e) {
      setError(humanizeError(String(e)));
    } finally {
      setBusy(false);
    }
  }

  async function subscribeAuthor(username: string) {
    try {
      await invoke("iwara_subscribe", {
        target: username,
        kind: "author",
      });
    } catch (e) {
      setError(humanizeError(String(e)));
    }
  }

  return (
    <section className="composer settings iwara-page" aria-label="Iwara 瀏覽">
      <div className="queue-head">
        <h2>Iwara 瀏覽</h2>
        <p>搜尋、標籤、作者、播放清單；結果顯示本地下載狀態（本版不含帳號登入）</p>
      </div>

      <div className="settings-grid">
        <label className="field compact">
          <span>模式</span>
          <select value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
            <option value="search">關鍵字搜尋</option>
            <option value="tag">標籤</option>
            <option value="author">作者</option>
            <option value="playlist">播放清單 ID</option>
          </select>
        </label>
        <label className="field compact grow">
          <span>查詢</span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={
              mode === "search"
                ? "關鍵字"
                : mode === "tag"
                  ? "標籤"
                  : mode === "author"
                    ? "使用者名稱"
                    : "播放清單 ID"
            }
            onKeyDown={(e) => {
              if (e.key === "Enter") void runBrowse(0);
            }}
          />
        </label>
        <label className="field compact">
          <span>排序</span>
          <select value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="date">最新</option>
            <option value="trending">趨勢</option>
            <option value="popularity">熱門</option>
            <option value="views">觀看</option>
            <option value="likes">讚數</option>
            <option value="relevance">相關（搜尋）</option>
          </select>
        </label>
        <label className="field compact">
          <span>分級</span>
          <select
            value={rating}
            onChange={(e) => setRating(e.target.value as RatingFilter)}
          >
            <option value="all">全部</option>
            <option value="general">SFW</option>
            <option value="ecchi">NSFW</option>
          </select>
        </label>
      </div>

      <div className="settings-grid">
        <label className="field compact">
          <span>最低讚數</span>
          <input value={minLikes} onChange={(e) => setMinLikes(e.target.value)} inputMode="numeric" />
        </label>
        <label className="field compact">
          <span>最低觀看</span>
          <input value={minViews} onChange={(e) => setMinViews(e.target.value)} inputMode="numeric" />
        </label>
        <label className="field compact">
          <span>排除標籤</span>
          <input
            value={excludeTags}
            onChange={(e) => setExcludeTags(e.target.value)}
            placeholder="逗號分隔"
          />
        </label>
        <label className="field compact">
          <span>標題關鍵字</span>
          <input value={titleKw} onChange={(e) => setTitleKw(e.target.value)} />
        </label>
      </div>

      <div className="row-actions">
        <button
          type="button"
          className="primary"
          disabled={busy}
          onClick={() => void runBrowse(0)}
        >
          {busy ? "載入中…" : "搜尋"}
        </button>
        <button
          type="button"
          className="ghost"
          disabled={busy || selected.size === 0}
          onClick={() => void enqueueSelected()}
        >
          加入佇列（{selected.size}）
        </button>
        <button
          type="button"
          className="ghost"
          disabled={busy || page <= 0}
          onClick={() => void runBrowse(page - 1)}
        >
          上一頁
        </button>
        <button
          type="button"
          className="ghost"
          disabled={busy || !result?.has_more}
          onClick={() => void runBrowse(page + 1)}
        >
          下一頁
        </button>
      </div>

      {error ? <p className="error-text">{error}</p> : null}

      <VideoGrid
        items={result?.items || []}
        selected={selected}
        onToggle={toggle}
        onPreview={setPreview}
        onSubscribeAuthor={(u) => void subscribeAuthor(u)}
      />

      <PreviewModal
        video={preview}
        cloudflareBypass={cloudflareBypass}
        onClose={() => setPreview(null)}
      />
    </section>
  );
}
