export type AfterCompletion = "none" | "notify" | "quit" | "sleep" | "shutdown";
export type RepeatMode = "repeat" | "once";
export type DaySpec = "daily" | "weekdays" | "weekend" | number[];

export type NamedQueue = {
  name: string;
  enabled: boolean;
  max_concurrent: number;
  day_spec: DaySpec;
  start_time: string | null;
  end_time: string | null;
  repeat_mode: RepeatMode;
  once_completed_at: string | null;
  after_completion: AfterCompletion;
  speed_profile: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type SpeedWindow = {
  day_spec: DaySpec;
  start_time: string;
  end_time: string;
  speed_limit: string;
};

export type SpeedProfile = {
  name: string;
  default_speed_limit: string;
  windows: SpeedWindow[];
};

export type QuotaStatus = {
  enabled: boolean;
  exceeded: boolean;
  paused: boolean;
  paused_until: string | null;
  hour_used_mb: number;
  day_used_mb: number;
  hour_limit_mb: number | null;
  day_limit_mb: number | null;
  reason: string | null;
};

export type QueueRuntime = {
  name: string;
  active: boolean;
  max_concurrent: number;
  after_completion: AfterCompletion;
  speed_limit_bps: number | null;
  speed_limit: string | null;
  speed_profile: string | null;
  reason: string | null;
  queue: NamedQueue;
};

export type SchedulerStatus = {
  now: string;
  active: boolean;
  quota: QuotaStatus;
  queues: QueueRuntime[];
  global_speed_limit_bps: number | null;
  global_speed_limit: string | null;
  default_queue: string;
};

export type ScheduleListPayload = {
  queues: NamedQueue[];
  status: SchedulerStatus;
};

export type PendingPowerAction = {
  action: AfterCompletion;
  queueName: string;
  seconds: number;
};

export const after_completion_labels: Record<AfterCompletion, string> = {
  none: "無",
  notify: "通知",
  quit: "結束應用程式",
  sleep: "進入睡眠",
  shutdown: "關機",
};

export const day_spec_labels: Record<"daily" | "weekdays" | "weekend", string> = {
  daily: "每天",
  weekdays: "平日",
  weekend: "週末",
};

export function format_day_spec(spec: DaySpec): string {
  if (typeof spec === "string") {
    return day_spec_labels[spec] || spec;
  }
  const names = ["一", "二", "三", "四", "五", "六", "日"];
  return spec.map((d) => `週${names[d] || "?"}`).join("、");
}

export function empty_queue_draft(name = ""): NamedQueue {
  return {
    name,
    enabled: true,
    max_concurrent: 2,
    day_spec: "daily",
    start_time: null,
    end_time: null,
    repeat_mode: "repeat",
    once_completed_at: null,
    after_completion: "none",
    speed_profile: null,
  };
}
