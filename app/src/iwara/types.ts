export type LocalStatus = "downloaded" | "queued" | "not_downloaded";

export type IwaraVideo = {
  video_id: string;
  item_id?: string;
  title: string;
  author?: string | null;
  author_username?: string | null;
  author_id?: string | null;
  upload_date?: string | null;
  like_count?: number;
  view_count?: number;
  duration?: number | null;
  rating?: string | null;
  tags?: string[];
  thumbnail_url?: string | null;
  canonical_url?: string;
  site?: string;
  local_status?: LocalStatus;
};

export type BrowsePage = {
  kind: string;
  items: IwaraVideo[];
  total?: number | null;
  has_more?: boolean;
  page?: number;
  limit?: number;
  sort?: string | null;
  rating?: string | null;
  query?: string | null;
  error?: string | null;
  count?: number;
};

export type IwaraSubscription = {
  id: number;
  kind: string;
  key: string;
  title: string;
  remote_id?: string | null;
  enabled: boolean;
  last_checked_at?: string | null;
  last_seen_video_id?: string | null;
  filters?: Record<string, unknown>;
  new_count: number;
  created_at?: string | null;
  updated_at?: string | null;
  source_url?: string;
};

export type HomeSection = {
  id: number;
  name: string;
  query_kind: string;
  query_value: string;
  sort: string;
  rating?: string | null;
  enabled: boolean;
  position: number;
  filters?: Record<string, unknown>;
};

export type PreviewResult = {
  ok: boolean;
  video_id?: string;
  title?: string;
  stream_url?: string;
  quality?: string;
  canonical_url?: string;
  error?: string;
  headers?: Record<string, string>;
};

export type RatingFilter = "all" | "ecchi" | "general";
