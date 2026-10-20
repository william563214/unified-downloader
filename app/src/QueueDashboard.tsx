import { statusLabel } from "./errors";
import { formatBytes, formatEta, formatSpeed, jobProgress, siteTone } from "./format";
import type { JobRow, QueueFilter } from "./jobTypes";

type Props = {
  jobs: JobRow[];
  filter: QueueFilter;
  selected: Set<string>;
  maxConcurrent: number;
  onFilterChange: (f: QueueFilter) => void;
  onMaxConcurrentChange: (n: number) => void;
  onToggleSelect: (clientId: string) => void;
  onSelectAllVisible: () => void;
  onClearSelection: () => void;
  onPause: (job: JobRow) => void;
  onResume: (job: JobRow) => void;
  onCancel: (job: JobRow) => void;
  onRetry: (job: JobRow) => void;
  onReorder: (clientId: string, direction: -1 | 1) => void;
  onOpenFolder: (path: string) => void;
  onOpenRepair: () => void;
  onBulkPause: () => void;
  onBulkCancel: () => void;
  onBulkRetry: () => void;
  onBulkClearFinished: () => void;
};

function matchesFilter(job: JobRow, filter: QueueFilter): boolean {
  switch (filter) {
    case "active":
      return job.status === "running";
    case "queued":
      return job.status === "queued";
    case "paused":
      return job.status === "paused";
    case "failed":
      return job.status === "error" || job.status === "cancelled";
    case "done":
      return job.status === "done";
    default:
      return true;
  }
}

export function QueueDashboard({
  jobs,
  filter,
  selected,
  maxConcurrent,
  onFilterChange,
  onMaxConcurrentChange,
  onToggleSelect,
  onSelectAllVisible,
  onClearSelection,
  onPause,
  onResume,
  onCancel,
  onRetry,
  onReorder,
  onOpenFolder,
  onOpenRepair,
  onBulkPause,
  onBulkCancel,
  onBulkRetry,
  onBulkClearFinished,
}: Props) {
  const visible = jobs.filter((j) => matchesFilter(j, filter));
  const counts = {
    all: jobs.length,
    active: jobs.filter((j) => j.status === "running").length,
    queued: jobs.filter((j) => j.status === "queued").length,
    paused: jobs.filter((j) => j.status === "paused").length,
    failed: jobs.filter((j) => j.status === "error" || j.status === "cancelled").length,
    done: jobs.filter((j) => j.status === "done").length,
  };

  return (
    <section className="queue queue-dashboard" aria-label="統一佇列">
      <div className="queue-head">
        <div>
          <h2>統一佇列</h2>
          <p>
            {jobs.length === 0
              ? "尚未有任務"
              : `進行中 ${counts.active} · 排隊 ${counts.queued} · 暫停 ${counts.paused} · 失敗 ${counts.failed}`}
          </p>
        </div>
        <label className="field compact concurrency-inline">
          <span>並發</span>
          <input
            type="number"
            min={1}
            max={8}
            value={maxConcurrent}
            onChange={(e) => onMaxConcurrentChange(Number(e.target.value) || 1)}
          />
        </label>
      </div>

      <div className="queue-filters" role="tablist" aria-label="佇列篩選">
        {(
          [
            ["all", "全部"],
            ["active", "進行中"],
            ["queued", "排隊"],
            ["paused", "暫停"],
            ["failed", "失敗"],
            ["done", "完成"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={filter === id}
            className={filter === id ? "chip active" : "chip"}
            onClick={() => onFilterChange(id)}
          >
            {label} {counts[id]}
          </button>
        ))}
      </div>

      <div className="queue-bulk actions">
        <button type="button" className="ghost" onClick={onSelectAllVisible}>
          全選可見
        </button>
        <button type="button" className="ghost" onClick={onClearSelection}>
          清除選取
        </button>
        <button type="button" className="ghost" onClick={onBulkPause} disabled={selected.size === 0}>
          批次暫停
        </button>
        <button type="button" className="ghost danger" onClick={onBulkCancel} disabled={selected.size === 0}>
          批次取消
        </button>
        <button type="button" className="ghost" onClick={onBulkRetry} disabled={selected.size === 0}>
          批次重試
        </button>
        <button type="button" className="ghost" onClick={onBulkClearFinished}>
          清除已完成
        </button>
      </div>

      {visible.length === 0 ? (
        <div className="empty">
          <p>此篩選下沒有任務。可貼上多個網址、拖放連結，或從各站點頁加入佇列。</p>
        </div>
      ) : (
        <ul className="jobs">
          {visible.map((job, index) => {
            const progress = jobProgress(job.files);
            const isSelected = selected.has(job.clientId);
            return (
              <li key={job.clientId} className={isSelected ? "job selected" : "job"}>
                <div className="job-top">
                  <label className="check job-select">
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => onToggleSelect(job.clientId)}
                    />
                  </label>
                  <span className={`badge ${siteTone(job.site)}`}>{job.site}</span>
                  <div className="job-title">
                    <strong>{job.title}</strong>
                    <span>{job.url}</span>
                  </div>
                  <div className="job-actions">
                    <button
                      type="button"
                      className="ghost"
                      disabled={index === 0 && filter === "all"}
                      onClick={() => onReorder(job.clientId, -1)}
                      title="上移"
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      className="ghost"
                      onClick={() => onReorder(job.clientId, 1)}
                      title="下移"
                    >
                      ↓
                    </button>
                    {job.status === "running" && (
                      <>
                        <button type="button" className="ghost" onClick={() => onPause(job)}>
                          暫停
                        </button>
                        <button type="button" className="ghost danger" onClick={() => onCancel(job)}>
                          取消
                        </button>
                      </>
                    )}
                    {job.status === "queued" && (
                      <button type="button" className="ghost danger" onClick={() => onCancel(job)}>
                        取消
                      </button>
                    )}
                    {job.status === "paused" && (
                      <button type="button" className="ghost" onClick={() => onResume(job)}>
                        繼續
                      </button>
                    )}
                    {(job.status === "error" ||
                      job.status === "cancelled" ||
                      job.status === "paused") && (
                      <button type="button" className="ghost" onClick={() => onRetry(job)}>
                        重試
                      </button>
                    )}
                    {job.status === "error" && (
                      <button type="button" className="ghost" onClick={onOpenRepair}>
                        開啟修復
                      </button>
                    )}
                    {job.files.some((f) => f.path) && (
                      <button
                        type="button"
                        className="ghost"
                        onClick={() => {
                          const file = job.files.find((f) => f.path);
                          if (file?.path) onOpenFolder(file.path);
                        }}
                      >
                        開啟檔案
                      </button>
                    )}
                    <button type="button" className="ghost" onClick={() => onOpenFolder(job.output)}>
                      開啟資料夾
                    </button>
                  </div>
                </div>

                {(job.status === "running" || job.files.length > 0) && (
                  <div className="job-progress">
                    <div className="file-meta">
                      <span className="name">整體進度</span>
                      <span className="stats">
                        {formatBytes(progress.bytes)}
                        {progress.total > 0 ? ` / ${formatBytes(progress.total)}` : ""}
                        {" · "}
                        {formatSpeed(progress.bps)}
                        {" · ETA "}
                        {formatEta(progress.etaSec)}
                        {" · "}
                        {progress.pct.toFixed(0)}%
                      </span>
                    </div>
                    <div className="bar" aria-hidden>
                      <i style={{ width: `${progress.pct}%` }} />
                    </div>
                  </div>
                )}

                <ul className="files">
                  {job.files.length === 0 && job.status === "running" && (
                    <li className="file muted">解析中…</li>
                  )}
                  {job.files.length === 0 && job.status === "queued" && (
                    <li className="file muted">排隊等待中…</li>
                  )}
                  {job.files.map((file) => {
                    const pct =
                      file.total && file.total > 0
                        ? Math.min(100, (file.bytes / file.total) * 100)
                        : file.status === "done"
                          ? 100
                          : file.status === "merging"
                            ? 95
                            : 0;
                    const remain =
                      file.total && file.total > file.bytes && file.bps > 0
                        ? (file.total - file.bytes) / file.bps
                        : null;
                    return (
                      <li key={file.id} className="file">
                        <div className="file-meta">
                          <span className="name">{file.name}</span>
                          <span className="stats">
                            {formatBytes(file.bytes)}
                            {file.total != null ? ` / ${formatBytes(file.total)}` : ""}
                            {" · "}
                            {formatSpeed(file.bps)}
                            {" · ETA "}
                            {formatEta(remain)}
                            {" · "}
                            {file.alreadyDownloaded ? "已下載" : statusLabel(file.status)}
                          </span>
                        </div>
                        <div className="bar" aria-hidden>
                          <i
                            style={{
                              width: `${
                                file.status === "skipped" || file.alreadyDownloaded ? 100 : pct
                              }%`,
                            }}
                          />
                        </div>
                        {file.message && file.status === "error" && (
                          <p className="error">{file.message}</p>
                        )}
                        {file.message &&
                          (file.status === "skipped" || file.alreadyDownloaded) && (
                            <p className="hint">{file.message}</p>
                          )}
                      </li>
                    );
                  })}
                </ul>

                <p className="job-foot">
                  狀態：{statusLabel(job.status)}
                  {job.ok != null ? ` · 成功 ${job.ok}` : ""}
                  {job.failed != null ? ` · 失敗 ${job.failed}` : ""}
                  {job.message ? ` · ${job.message}` : ""}
                </p>
                {job.status === "error" && job.message && (
                  <div className="job-error-box">
                    <p className="error">{job.message}</p>
                    <button type="button" className="ghost" onClick={onOpenRepair}>
                      開啟修復
                    </button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
