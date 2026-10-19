import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { humanizeError } from "../errors";
import type { IwaraVideo, PreviewResult } from "./types";

type Props = {
  video: IwaraVideo | null;
  cloudflareBypass: boolean;
  onClose: () => void;
};

export function PreviewModal({ video, cloudflareBypass, onClose }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewResult | null>(null);

  useEffect(() => {
    if (!video) {
      setPreview(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setBusy(true);
    setError(null);
    setPreview(null);
    void (async () => {
      try {
        const result = await invoke<PreviewResult>("iwara_preview", {
          videoId: video.video_id,
          cloudflareBypass,
        });
        if (cancelled) return;
        if (!result?.ok) {
          setError(humanizeError(result?.error || "無法取得預覽串流"));
          setPreview(result);
        } else {
          setPreview(result);
        }
      } catch (e) {
        if (!cancelled) setError(humanizeError(String(e)));
      } finally {
        if (!cancelled) setBusy(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [video, cloudflareBypass]);

  if (!video) return null;

  return (
    <div className="iwara-modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="iwara-modal"
        role="dialog"
        aria-modal="true"
        aria-label="影片預覽"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="iwara-modal-head">
          <h2>{preview?.title || video.title}</h2>
          <button type="button" className="ghost" onClick={onClose}>
            關閉
          </button>
        </div>
        {busy ? <p className="muted">解析串流中…</p> : null}
        {error ? <p className="error-text">{error}</p> : null}
        {preview?.stream_url ? (
          <video
            className="iwara-preview-player"
            controls
            autoPlay
            src={preview.stream_url}
            // cross-origin stream hosts may reject without referer; best-effort
          />
        ) : null}
        {!busy && !error && !preview?.stream_url ? (
          <p className="muted">此環境可能無法取得串流（例如 Cloudflare 阻擋）</p>
        ) : null}
      </div>
    </div>
  );
}
