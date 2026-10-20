import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { AppSettings } from "./settings";

export type BiliPart = {
  page: number;
  title: string;
  cid?: number;
  bvid?: string;
  badge?: string;
  duration?: number | null;
  upload_date?: string | null;
  needs_expand?: boolean;
  kind?: string;
  edge_id?: number | null;
};

export type BiliQuality = { qn: number; label: string; codecs?: number[] };
export type BiliCodec = { codecid: number; label: string };
export type BiliAudio = { id: number; label: string; kind?: string };
export type BiliSubTrack = {
  language: string;
  language_doc: string;
  url: string;
};

export type BiliProbe = {
  site: string | null;
  kind: string | null;
  title: string | null;
  url: string;
  parts: BiliPart[];
  qualities: BiliQuality[];
  codecs?: BiliCodec[];
  audio_qualities?: BiliAudio[];
  subtitle_tracks?: BiliSubTrack[];
  default_part: number | null;
  default_quality: number | null;
  message: string | null;
  pagination?: {
    total_pages?: number;
    total_items?: number;
    current_page?: number;
    expanded?: boolean;
  } | null;
  naming_template?: string | null;
  error?: string;
  code?: string;
};

const expandable_kinds = new Set([
  "space",
  "favlist",
  "collection",
  "series",
  "followed_bangumi",
  "subscribed_collections",
  "favorite_folders",
  "watchlater",
  "history",
]);

type Props = {
  settings: AppSettings;
  output: string;
  onEnqueue: (job: {
    url: string;
    quality: string | null;
    part: string | null;
    codec: string | null;
    audioQuality: string | null;
    container: string;
    danmaku: string | null;
    subtitle: string | null;
    subtitleLang: string | null;
    embedDanmaku: boolean;
    embedSubtitle: boolean;
    filterTitle: string | null;
    partRange: string | null;
    checkedPages: number[];
    expandAll?: boolean;
    nodeNames?: string | null;
  }) => void;
};

function formatDuration(sec?: number | null): string {
  if (sec == null || Number.isNaN(sec)) return "—";
  const s = Math.floor(sec);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, "0")}`;
}

export default function BilibiliParse({ settings, output, onEnqueue }: Props) {
  const [paste, setPaste] = useState("");
  const [probes, setProbes] = useState<BiliProbe[]>([]);
  const [activeIdx, setActiveIdx] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checked, setChecked] = useState<Record<string, Set<number>>>({});
  const [titleEdits, setTitleEdits] = useState<Record<string, string>>({});
  const [quality, setQuality] = useState("");
  const [codec, setCodec] = useState("");
  const [audioQuality, setAudioQuality] = useState("");
  const [container, setContainer] = useState("mp4");
  const [danmaku, setDanmaku] = useState("");
  const [subtitle, setSubtitle] = useState("");
  const [subtitleLang, setSubtitleLang] = useState("all");
  const [embedDanmaku, setEmbedDanmaku] = useState(false);
  const [embedSubtitle, setEmbedSubtitle] = useState(false);
  const [filterTitle, setFilterTitle] = useState("");
  const [filterDurMin, setFilterDurMin] = useState("");
  const [filterDurMax, setFilterDurMax] = useState("");
  const [partRange, setPartRange] = useState("");
  const [pageNum, setPageNum] = useState(1);
  const [expandProgress, setExpandProgress] = useState<string | null>(null);

  const active = probes[activeIdx] ?? null;

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    void listen<Record<string, unknown>>("bili-expand", (event) => {
      const p = event.payload;
      const message = typeof p.message === "string" ? p.message : "";
      const current = typeof p.current === "number" ? p.current : 0;
      const total = typeof p.total === "number" ? p.total : 0;
      const phase = typeof p.phase === "string" ? p.phase : "expand";
      setExpandProgress(
        message || `${phase} ${current}/${total || "?"}`,
      );
    }).then((fn) => {
      unlisten = fn;
    });
    return () => {
      unlisten?.();
    };
  }, []);

  const visibleParts = useMemo(() => {
    if (!active?.parts) return [];
    let parts = active.parts;
    const kw = filterTitle.trim().toLowerCase();
    if (kw && !active.pagination?.expanded) {
      parts = parts.filter((p) => (p.title || "").toLowerCase().includes(kw));
    }
    const dmin = filterDurMin ? Number(filterDurMin) : null;
    const dmax = filterDurMax ? Number(filterDurMax) : null;
    if (dmin != null && !Number.isNaN(dmin)) {
      parts = parts.filter((p) => (p.duration ?? 0) >= dmin);
    }
    if (dmax != null && !Number.isNaN(dmax)) {
      parts = parts.filter((p) => (p.duration ?? 0) <= dmax);
    }
    if (partRange.trim() && !active.pagination?.expanded) {
      const m = partRange.trim().match(/^(\d+)\s*-\s*(\d+)$/);
      if (m) {
        const a = Number(m[1]);
        const b = Number(m[2]);
        const lo = Math.min(a, b);
        const hi = Math.max(a, b);
        parts = parts.filter((p) => p.page >= lo && p.page <= hi);
      }
    }
    return parts;
  }, [active, filterTitle, filterDurMin, filterDurMax, partRange]);

  const applyProbeResult = useCallback(
    (items: BiliProbe[]) => {
      setProbes(items);
      setActiveIdx(0);
      const nextChecked: Record<string, Set<number>> = {};
      const nextTitles: Record<string, string> = {};
      for (const item of items) {
        const key = item.url || item.title || "item";
        nextChecked[key] = new Set(item.parts?.map((p) => p.page) ?? []);
        for (const p of item.parts ?? []) {
          nextTitles[`${key}:${p.page}`] = p.title || `P${p.page}`;
        }
      }
      setChecked(nextChecked);
      setTitleEdits(nextTitles);
      const first = items[0];
      if (first?.default_quality) {
        setQuality(String(first.default_quality));
      } else if (settings.bilibiliQuality) {
        setQuality(settings.bilibiliQuality);
      }
      if (first?.codecs?.length) {
        setCodec(String(first.codecs[0].codecid));
      }
      if (first?.audio_qualities?.length) {
        setAudioQuality(String(first.audio_qualities[0].id));
      }
    },
    [settings.bilibiliQuality],
  );

  const runProbe = useCallback(
    async (expandAll: boolean) => {
      const text = paste.trim();
      if (!text) return;
      setBusy(true);
      setError(null);
      setExpandProgress(expandAll ? "開始展開…" : null);
      try {
        const lines = text
          .split(/[\n,]+/)
          .map((s) => s.trim())
          .filter(Boolean);
        const result = await invoke<
          BiliProbe | { batch?: boolean; items?: BiliProbe[] }
        >("probe_bilibili", {
          urls: lines,
          page: pageNum,
          keyword: filterTitle.trim() || null,
          expandAll,
          filterTitle: filterTitle.trim() || null,
          filterDurationMin: filterDurMin ? Number(filterDurMin) : null,
          filterDurationMax: filterDurMax ? Number(filterDurMax) : null,
          partRange: partRange.trim() || null,
        });
        let items: BiliProbe[] = [];
        if (
          result &&
          typeof result === "object" &&
          "items" in result &&
          Array.isArray(result.items)
        ) {
          items = result.items;
        } else {
          items = [result as BiliProbe];
        }
        applyProbeResult(items);
      } catch (e) {
        setError(String(e));
        setProbes([]);
      } finally {
        setBusy(false);
        setExpandProgress(null);
      }
    },
    [
      paste,
      pageNum,
      filterTitle,
      filterDurMin,
      filterDurMax,
      partRange,
      applyProbeResult,
    ],
  );

  function togglePart(urlKey: string, page: number) {
    setChecked((prev) => {
      const next = { ...prev };
      const set = new Set(next[urlKey] ?? []);
      if (set.has(page)) set.delete(page);
      else set.add(page);
      next[urlKey] = set;
      return next;
    });
  }

  function selectAllVisible(urlKey: string, on: boolean) {
    setChecked((prev) => {
      const next = { ...prev };
      next[urlKey] = on ? new Set(visibleParts.map((p) => p.page)) : new Set();
      return next;
    });
  }

  function buildNodeNames(item: BiliProbe, urlKey: string): string | null {
    if (item.kind !== "interactive") return null;
    const map: Record<string, string> = {};
    for (const p of item.parts ?? []) {
      const edited = titleEdits[`${urlKey}:${p.page}`];
      if (edited && edited !== p.title) {
        map[String(p.page)] = edited;
        if (p.edge_id != null) map[`edge:${p.edge_id}`] = edited;
        if (p.cid != null) map[`cid:${p.cid}`] = edited;
      }
    }
    return Object.keys(map).length ? JSON.stringify(map) : null;
  }

  function enqueueOpts(item: BiliProbe, pages: number[], expandAll = false) {
    const key = item.url || item.title || "item";
    const partArg =
      expandAll || pages.length === (item.parts?.length ?? 0)
        ? "all"
        : String(pages[0]);
    onEnqueue({
      url: item.url,
      quality: quality || null,
      part: partArg,
      codec: codec || null,
      audioQuality: audioQuality || null,
      container,
      danmaku: danmaku || null,
      subtitle: subtitle || null,
      subtitleLang: subtitleLang === "all" ? null : subtitleLang,
      embedDanmaku,
      embedSubtitle,
      filterTitle: filterTitle.trim() || null,
      partRange:
        partRange.trim() ||
        (pages.length > 1 ? `${pages[0]}-${pages[pages.length - 1]}` : null),
      checkedPages: pages,
      expandAll,
      nodeNames: buildNodeNames(item, key),
    });
  }

  function enqueueActive() {
    if (!active || active.error) return;
    if (!output) {
      setError("請先在下載頁或設定中選擇輸出目錄");
      return;
    }
    const key = active.url || active.title || "item";
    const pages = [...(checked[key] ?? new Set<number>())].sort((a, b) => a - b);
    if (!pages.length) {
      setError("請至少勾選一個分 P／項目");
      return;
    }
    enqueueOpts(active, pages, Boolean(active.pagination?.expanded));
  }

  function enqueueAll() {
    if (!output) {
      setError("請先選擇輸出目錄");
      return;
    }
    for (const item of probes) {
      if (item.error || !item.url) continue;
      const key = item.url || item.title || "item";
      const pages = [...(checked[key] ?? new Set<number>())].sort(
        (a, b) => a - b,
      );
      if (!pages.length) continue;
      enqueueOpts(item, pages, Boolean(item.pagination?.expanded));
    }
  }

  async function expandAndEnqueue() {
    if (!paste.trim()) return;
    if (!output) {
      setError("請先選擇輸出目錄");
      return;
    }
    setBusy(true);
    setError(null);
    setExpandProgress("一鍵展開中…");
    try {
      const lines = paste
        .split(/[\n,]+/)
        .map((s) => s.trim())
        .filter(Boolean);
      const result = await invoke<
        BiliProbe | { batch?: boolean; items?: BiliProbe[] }
      >("probe_bilibili", {
        urls: lines,
        page: 1,
        keyword: filterTitle.trim() || null,
        expandAll: true,
        filterTitle: filterTitle.trim() || null,
        filterDurationMin: filterDurMin ? Number(filterDurMin) : null,
        filterDurationMax: filterDurMax ? Number(filterDurMax) : null,
        partRange: partRange.trim() || null,
      });
      let items: BiliProbe[] = [];
      if (
        result &&
        typeof result === "object" &&
        "items" in result &&
        Array.isArray(result.items)
      ) {
        items = result.items;
      } else {
        items = [result as BiliProbe];
      }
      applyProbeResult(items);
      for (const item of items) {
        if (item.error || !item.url) continue;
        const pages = (item.parts ?? []).map((p) => p.page);
        if (!pages.length) continue;
        enqueueOpts(item, pages, true);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
      setExpandProgress(null);
    }
  }

  const urlKey = active?.url || active?.title || "item";
  const canExpand =
    Boolean(active?.kind && expandable_kinds.has(active.kind)) ||
    paste.trim().length > 0;

  return (
    <section className="panel bili-parse" aria-label="Bilibili 解析">
      <header className="panel-head">
        <h2>Bilibili 解析</h2>
        <p className="hint">
          貼上 BV／番劇／課程／音訊／UP 空間／收藏夾／每周必看／合集／追番／訂閱合集連結（可多行）。
          一鍵展開會翻頁並展開巢狀清單後加入佇列（沿用下方篩選）。
        </p>
      </header>

      <label className="field">
        <span>連結</span>
        <textarea
          rows={5}
          value={paste}
          onChange={(e) => setPaste(e.target.value)}
          placeholder={
            "https://www.bilibili.com/video/BVxxxx\nhttps://space.bilibili.com/123/video"
          }
        />
      </label>

      <div className="row wrap gap">
        <label className="field inline">
          <span>關鍵字</span>
          <input
            value={filterTitle}
            onChange={(e) => setFilterTitle(e.target.value)}
            placeholder="UP／收藏夾搜尋或本地篩選"
          />
        </label>
        <label className="field inline">
          <span>頁碼</span>
          <input
            type="number"
            min={1}
            value={pageNum}
            onChange={(e) => setPageNum(Number(e.target.value) || 1)}
          />
        </label>
        <button
          type="button"
          className="primary"
          disabled={busy || !paste.trim()}
          onClick={() => void runProbe(false)}
        >
          {busy && !expandProgress ? "解析中…" : "解析"}
        </button>
        <button
          type="button"
          disabled={busy || !canExpand}
          onClick={() => void expandAndEnqueue()}
          title="翻頁展開追番／收藏夾／訂閱合集／UP 空間並加入佇列"
        >
          一鍵展開並加入佇列
        </button>
      </div>

      {expandProgress && <p className="hint expand-progress">{expandProgress}</p>}
      {error && <p className="error">{error}</p>}

      {probes.length > 1 && (
        <div className="bili-batch-tabs" role="tablist">
          {probes.map((p, i) => (
            <button
              key={`${p.url}-${i}`}
              type="button"
              role="tab"
              className={i === activeIdx ? "chip active" : "chip"}
              onClick={() => setActiveIdx(i)}
            >
              {p.error ? "錯誤" : p.title || p.kind || `項目 ${i + 1}`}
            </button>
          ))}
        </div>
      )}

      {active && (
        <div className="bili-tree">
          {active.error ? (
            <p className="error">
              {active.code === "requires_login"
                ? "此類型需要登入（請至帳號頁 QR／Cookie／簡訊）"
                : active.error}
            </p>
          ) : (
            <>
              <div className="bili-tree-head">
                <div>
                  <strong>{active.title}</strong>
                  <span className="muted"> · {active.kind}</span>
                  {active.pagination && (
                    <span className="muted">
                      {" "}
                      · 第 {active.pagination.current_page}/
                      {active.pagination.total_pages} 頁（共{" "}
                      {active.pagination.total_items}）
                      {active.pagination.expanded ? " · 已展開" : ""}
                    </span>
                  )}
                </div>
                <div className="row gap">
                  <button
                    type="button"
                    onClick={() => selectAllVisible(urlKey, true)}
                  >
                    全選可見
                  </button>
                  <button
                    type="button"
                    onClick={() => selectAllVisible(urlKey, false)}
                  >
                    清除
                  </button>
                  {active.kind && expandable_kinds.has(active.kind) && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void runProbe(true)}
                    >
                      展開全部頁
                    </button>
                  )}
                </div>
              </div>

              <div className="row wrap gap filter-row">
                <label className="field inline">
                  <span>時長≥</span>
                  <input
                    value={filterDurMin}
                    onChange={(e) => setFilterDurMin(e.target.value)}
                    placeholder="秒"
                  />
                </label>
                <label className="field inline">
                  <span>時長≤</span>
                  <input
                    value={filterDurMax}
                    onChange={(e) => setFilterDurMax(e.target.value)}
                    placeholder="秒"
                  />
                </label>
                <label className="field inline">
                  <span>P 範圍</span>
                  <input
                    value={partRange}
                    onChange={(e) => setPartRange(e.target.value)}
                    placeholder="1-5"
                  />
                </label>
              </div>

              {active.kind === "interactive" && (
                <p className="hint">
                  互動影片已遍歷全圖節點（去重）。可於下方修改節點名稱後再加入佇列。
                </p>
              )}

              <ul className="bili-part-list">
                {visibleParts.map((p) => (
                  <li key={p.page}>
                    <label>
                      <input
                        type="checkbox"
                        checked={checked[urlKey]?.has(p.page) ?? false}
                        onChange={() => togglePart(urlKey, p.page)}
                      />
                      <span className="part-no">P{p.page}</span>
                      {active.kind === "interactive" ? (
                        <input
                          className="part-title-edit"
                          value={
                            titleEdits[`${urlKey}:${p.page}`] ?? p.title ?? ""
                          }
                          onChange={(e) =>
                            setTitleEdits((prev) => ({
                              ...prev,
                              [`${urlKey}:${p.page}`]: e.target.value,
                            }))
                          }
                        />
                      ) : (
                        <span className="part-title">{p.title}</span>
                      )}
                      <span className="muted">
                        {formatDuration(p.duration)}
                      </span>
                      {p.badge ? <span className="badge">{p.badge}</span> : null}
                    </label>
                  </li>
                ))}
              </ul>

              <div className="bili-options-grid">
                <label className="field">
                  <span>畫質</span>
                  <select
                    value={quality}
                    onChange={(e) => setQuality(e.target.value)}
                  >
                    <option value="">最佳可得（不繞過 VIP）</option>
                    {(active.qualities ?? []).map((q) => (
                      <option key={q.qn} value={String(q.qn)}>
                        {q.label} ({q.qn})
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>編碼</span>
                  <select
                    value={codec}
                    onChange={(e) => setCodec(e.target.value)}
                  >
                    <option value="">偏好 AVC→HEVC→AV1</option>
                    {(active.codecs ?? []).map((c) => (
                      <option key={c.codecid} value={String(c.codecid)}>
                        {c.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>音訊</span>
                  <select
                    value={audioQuality}
                    onChange={(e) => setAudioQuality(e.target.value)}
                  >
                    <option value="">最佳可得</option>
                    {(active.audio_qualities ?? []).map((a) => (
                      <option key={a.id} value={String(a.id)}>
                        {a.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>封裝</span>
                  <select
                    value={container}
                    onChange={(e) => setContainer(e.target.value)}
                  >
                    <option value="mp4">mp4</option>
                    <option value="mkv">mkv</option>
                  </select>
                </label>
                <label className="field">
                  <span>彈幕</span>
                  <select
                    value={danmaku}
                    onChange={(e) => setDanmaku(e.target.value)}
                  >
                    <option value="">不上傳</option>
                    <option value="xml">xml</option>
                    <option value="ass">ass</option>
                    <option value="json">json</option>
                  </select>
                </label>
                <label className="field">
                  <span>字幕</span>
                  <select
                    value={subtitle}
                    onChange={(e) => setSubtitle(e.target.value)}
                  >
                    <option value="">不上傳</option>
                    <option value="srt">srt</option>
                    <option value="lrc">lrc</option>
                    <option value="txt">txt</option>
                    <option value="ass">ass</option>
                    <option value="json">json</option>
                  </select>
                </label>
                <label className="field">
                  <span>字幕語言</span>
                  <select
                    value={subtitleLang}
                    onChange={(e) => setSubtitleLang(e.target.value)}
                  >
                    <option value="all">全部可得</option>
                    {(active.subtitle_tracks ?? []).map((t) => (
                      <option key={t.language} value={t.language}>
                        {t.language_doc} ({t.language})
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="row wrap gap">
                <label className="check">
                  <input
                    type="checkbox"
                    checked={embedDanmaku}
                    onChange={(e) => {
                      setEmbedDanmaku(e.target.checked);
                      if (e.target.checked && !danmaku) setDanmaku("ass");
                    }}
                  />
                  嵌入彈幕（ASS）
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={embedSubtitle}
                    onChange={(e) => {
                      setEmbedSubtitle(e.target.checked);
                      if (e.target.checked && !subtitle) setSubtitle("ass");
                    }}
                  />
                  嵌入字幕（ASS）
                </label>
              </div>

              {active.message && <p className="hint">{active.message}</p>}
              {active.naming_template && (
                <p className="hint">預設命名：{active.naming_template}</p>
              )}

              <div className="row gap">
                <button
                  type="button"
                  className="primary"
                  onClick={enqueueActive}
                >
                  加入佇列（目前項目）
                </button>
                {probes.length > 1 && (
                  <button type="button" onClick={enqueueAll}>
                    全部加入佇列
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}
