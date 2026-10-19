import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { humanizeError } from "../errors";
import { PreviewModal } from "./PreviewModal";
import type {
  BrowsePage,
  HomeSection,
  IwaraVideo,
  RatingFilter,
} from "./types";
import { VideoGrid } from "./VideoGrid";

type SectionPayload = BrowsePage & { section?: HomeSection; error?: string };

type Props = {
  downloadDir: string;
  cloudflareBypass: boolean;
  onEnqueueStarted?: () => void;
};

export function HomePage({ downloadDir, cloudflareBypass, onEnqueueStarted }: Props) {
  const [rating, setRating] = useState<RatingFilter>("all");
  const [sort, setSort] = useState("date");
  const [sections, setSections] = useState<HomeSection[]>([]);
  const [homeItems, setHomeItems] = useState<IwaraVideo[]>([]);
  const [sectionData, setSectionData] = useState<Record<number, IwaraVideo[]>>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState<IwaraVideo | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [newName, setNewName] = useState("");
  const [newKind, setNewKind] = useState("tag");
  const [newValue, setNewValue] = useState("");

  const loadSections = useCallback(async () => {
    const list = await invoke<HomeSection[]>("iwara_sections", {});
    setSections(Array.isArray(list) ? list : []);
    return Array.isArray(list) ? list : [];
  }, []);

  const refreshHome = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const home = await invoke<BrowsePage>("iwara_browse", {
        action: "home",
        query: "",
        page: 0,
        limit: 24,
        sort,
        rating: rating === "all" ? null : rating,
        cloudflareBypass,
      });
      if (home?.error) setError(humanizeError(home.error));
      setHomeItems(home?.items || []);

      const list = await loadSections();
      const next: Record<number, IwaraVideo[]> = {};
      for (const section of list) {
        try {
          const payload = await invoke<SectionPayload>("iwara_section_fetch", {
            id: section.id,
            page: 0,
            limit: 16,
            cloudflareBypass,
          });
          next[section.id] = payload?.items || [];
        } catch {
          next[section.id] = [];
        }
      }
      setSectionData(next);
      setSelected(new Set());
    } catch (e) {
      setError(humanizeError(String(e)));
    } finally {
      setBusy(false);
    }
  }, [sort, rating, cloudflareBypass, loadSections]);

  useEffect(() => {
    void refreshHome();
  }, [refreshHome]);

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function enqueueSelected() {
    if (selected.size === 0 || !downloadDir.trim()) return;
    setBusy(true);
    try {
      await invoke("iwara_enqueue", {
        videoIds: Array.from(selected),
        output: downloadDir,
        cloudflareBypass,
      });
      onEnqueueStarted?.();
      await refreshHome();
    } catch (e) {
      setError(humanizeError(String(e)));
      setBusy(false);
    }
  }

  async function addSection() {
    if (!newName.trim()) return;
    setBusy(true);
    try {
      await invoke("iwara_section_add", {
        name: newName.trim(),
        kind: newKind,
        value: newValue.trim(),
        sort,
        rating: rating === "all" ? null : rating,
      });
      setNewName("");
      setNewValue("");
      await refreshHome();
    } catch (e) {
      setError(humanizeError(String(e)));
      setBusy(false);
    }
  }

  async function removeSection(id: number) {
    setBusy(true);
    try {
      await invoke("iwara_section_remove", { id });
      await refreshHome();
    } catch (e) {
      setError(humanizeError(String(e)));
      setBusy(false);
    }
  }

  return (
    <section className="composer settings iwara-page" aria-label="Iwara 首頁推薦">
      <div className="queue-head">
        <h2>Iwara 首頁</h2>
        <p>推薦列表與自訂區塊；SFW／NSFW 切換（本版無帳號登入）</p>
      </div>

      <div className="settings-grid">
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
        <label className="field compact">
          <span>排序</span>
          <select value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="date">最新</option>
            <option value="trending">趨勢</option>
            <option value="popularity">熱門</option>
            <option value="views">觀看</option>
            <option value="likes">讚數</option>
          </select>
        </label>
      </div>

      <div className="row-actions">
        <button type="button" className="primary" disabled={busy} onClick={() => void refreshHome()}>
          {busy ? "載入中…" : "重新整理"}
        </button>
        <button
          type="button"
          className="ghost"
          disabled={busy || selected.size === 0}
          onClick={() => void enqueueSelected()}
        >
          選取入佇列（{selected.size}）
        </button>
      </div>

      {error ? <p className="error-text">{error}</p> : null}

      <h3>推薦</h3>
      <VideoGrid
        items={homeItems}
        selected={selected}
        onToggle={toggle}
        onPreview={setPreview}
      />

      <h3>自訂區塊</h3>
      <div className="settings-grid">
        <label className="field compact">
          <span>名稱</span>
          <input value={newName} onChange={(e) => setNewName(e.target.value)} />
        </label>
        <label className="field compact">
          <span>類型</span>
          <select value={newKind} onChange={(e) => setNewKind(e.target.value)}>
            <option value="home">推薦流</option>
            <option value="search">關鍵字</option>
            <option value="tag">標籤</option>
            <option value="author">作者</option>
            <option value="playlist">播放清單</option>
          </select>
        </label>
        <label className="field compact grow">
          <span>查詢值</span>
          <input
            value={newValue}
            onChange={(e) => setNewValue(e.target.value)}
            placeholder="標籤／關鍵字／作者／播放清單 ID"
          />
        </label>
      </div>
      <div className="row-actions">
        <button type="button" className="ghost" disabled={busy} onClick={() => void addSection()}>
          新增區塊
        </button>
      </div>

      {sections.map((section) => (
        <div key={section.id} className="iwara-section">
          <div className="iwara-section-head">
            <h3>
              {section.name}
              <span className="muted">
                {" "}
                · {section.query_kind}
                {section.query_value ? `:${section.query_value}` : ""}
              </span>
            </h3>
            <button
              type="button"
              className="ghost compact"
              disabled={busy}
              onClick={() => void removeSection(section.id)}
            >
              移除
            </button>
          </div>
          <VideoGrid
            items={sectionData[section.id] || []}
            selected={selected}
            onToggle={toggle}
            onPreview={setPreview}
          />
        </div>
      ))}

      <PreviewModal
        video={preview}
        cloudflareBypass={cloudflareBypass}
        onClose={() => setPreview(null)}
      />
    </section>
  );
}
