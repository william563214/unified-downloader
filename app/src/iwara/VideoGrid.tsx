import type { IwaraVideo, LocalStatus } from "./types";

function statusLabel(status?: LocalStatus): string {
  switch (status) {
    case "downloaded":
      return "已下載";
    case "queued":
      return "佇列中";
    default:
      return "";
  }
}

type Props = {
  items: IwaraVideo[];
  selected: Set<string>;
  onToggle: (id: string) => void;
  onPreview?: (item: IwaraVideo) => void;
  onSubscribeAuthor?: (username: string) => void;
};

export function VideoGrid({
  items,
  selected,
  onToggle,
  onPreview,
  onSubscribeAuthor,
}: Props) {
  if (items.length === 0) {
    return <p className="muted">沒有結果</p>;
  }
  return (
    <div className="iwara-grid" role="list">
      {items.map((item) => {
        const id = item.video_id;
        const checked = selected.has(id);
        const badge = statusLabel(item.local_status);
        return (
          <article
            key={id}
            className={`iwara-card ${checked ? "selected" : ""}`}
            role="listitem"
          >
            <label className="iwara-thumb">
              <input
                type="checkbox"
                checked={checked}
                onChange={() => onToggle(id)}
                aria-label={`選取 ${item.title}`}
              />
              {item.thumbnail_url ? (
                <img src={item.thumbnail_url} alt="" loading="lazy" />
              ) : (
                <div className="iwara-thumb-empty" />
              )}
              {badge ? <span className={`iwara-badge ${item.local_status}`}>{badge}</span> : null}
            </label>
            <div className="iwara-meta">
              <h3 title={item.title}>{item.title}</h3>
              <p>
                {item.author || item.author_username || "—"}
                {item.like_count != null ? ` · 讚 ${item.like_count}` : ""}
                {item.view_count != null ? ` · 觀看 ${item.view_count}` : ""}
              </p>
              <div className="iwara-card-actions">
                {onPreview ? (
                  <button type="button" className="ghost compact" onClick={() => onPreview(item)}>
                    預覽
                  </button>
                ) : null}
                {onSubscribeAuthor && (item.author_username || item.author) ? (
                  <button
                    type="button"
                    className="ghost compact"
                    onClick={() =>
                      onSubscribeAuthor(String(item.author_username || item.author))
                    }
                  >
                    訂閱作者
                  </button>
                ) : null}
              </div>
            </div>
          </article>
        );
      })}
    </div>
  );
}
