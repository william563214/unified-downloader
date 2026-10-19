import { invoke } from "@tauri-apps/api/core";
import { useCallback, useEffect, useRef, useState } from "react";

export type BiliAccountProfile = {
  logged_in: boolean;
  uname: string;
  mid: number | null;
  face: string;
  vip_type: number;
  vip_status: number;
  vip_label: string;
  level: number;
  message: string;
};

type QrPayload = {
  url: string;
  qrcode_key: string;
  image_png_base64?: string;
};

type QrPollPayload = {
  code: number;
  message: string;
  status?: string;
  profile?: BiliAccountProfile;
  logged_in?: boolean;
};

const sms_geetest_hint =
  "簡訊登入需完成 GeeTest 驗證（通常要瀏覽器）。無瀏覽器環境請改用 QR 或貼上 Cookie。";

type Props = {
  onStatus?: (profile: BiliAccountProfile | null) => void;
};

export default function BilibiliAccount({ onStatus }: Props) {
  const [profile, setProfile] = useState<BiliAccountProfile | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"qr" | "cookie" | "sms">("qr");
  const [qr, setQr] = useState<QrPayload | null>(null);
  const [qrStatus, setQrStatus] = useState<string>("");
  const [cookieText, setCookieText] = useState("");
  const [smsNote] = useState(sms_geetest_hint);
  const pollRef = useRef<number | null>(null);

  const stopPoll = useCallback(() => {
    if (pollRef.current != null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const refreshStatus = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await invoke<BiliAccountProfile>("bili_login_status");
      setProfile(result);
      onStatus?.(result);
    } catch (e) {
      setProfile(null);
      onStatus?.(null);
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }, [onStatus]);

  useEffect(() => {
    void refreshStatus();
    return () => stopPoll();
  }, [refreshStatus, stopPoll]);

  async function startQr() {
    stopPoll();
    setBusy(true);
    setError(null);
    setQrStatus("產生 QR 中…");
    try {
      const payload = await invoke<QrPayload>("bili_login_qr");
      setQr(payload);
      setQrStatus("請用手機 B 站 App 掃描");
      pollRef.current = window.setInterval(() => {
        void pollQr(payload.qrcode_key);
      }, 1500);
    } catch (e) {
      setError(String(e));
      setQrStatus("");
    } finally {
      setBusy(false);
    }
  }

  async function pollQr(key: string) {
    try {
      const result = await invoke<QrPollPayload>("bili_login_qr_poll", {
        key,
      });
      setQrStatus(result.message || result.status || String(result.code));
      if (result.code === 0) {
        stopPoll();
        if (result.profile) {
          setProfile(result.profile);
          onStatus?.(result.profile);
        } else {
          await refreshStatus();
        }
        setQrStatus("登入成功");
      } else if (result.code === 86038) {
        stopPoll();
        setQrStatus("QR 已過期，請重新產生");
      }
    } catch (e) {
      // keep polling on transient errors
      setQrStatus(String(e));
    }
  }

  async function submitCookie() {
    setBusy(true);
    setError(null);
    try {
      const result = await invoke<BiliAccountProfile>("bili_login_cookie", {
        text: cookieText,
      });
      setProfile(result);
      onStatus?.(result);
      setCookieText("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function doLogout() {
    setBusy(true);
    setError(null);
    stopPoll();
    try {
      const result = await invoke<BiliAccountProfile>("bili_login_logout");
      setProfile(result);
      onStatus?.(result);
      setQr(null);
      setQrStatus("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function doRefresh() {
    setBusy(true);
    setError(null);
    try {
      const result = await invoke<BiliAccountProfile>("bili_login_refresh");
      setProfile(result);
      onStatus?.(result);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const vipText =
    profile?.vip_label ||
    (profile && profile.vip_type > 0 ? `vipType=${profile.vip_type}` : "非大會員");

  return (
    <section className="panel bilibili-account">
      <header className="panel-header">
        <h2>Bilibili 帳號</h2>
        <p className="muted">
          憑證僅存於 OS keychain（udl.secrets），不會寫入 settings.json 或日誌。
        </p>
      </header>

      <div className="account-status cardless">
        {profile?.logged_in ? (
          <>
            <div className="account-row">
              {profile.face ? (
                <img
                  className="account-avatar"
                  src={profile.face}
                  alt=""
                  referrerPolicy="no-referrer"
                />
              ) : (
                <div className="account-avatar placeholder" aria-hidden />
              )}
              <div>
                <div className="account-name">{profile.uname}</div>
                <div className="muted">
                  mid {profile.mid ?? "—"} · Lv{profile.level} · {vipText}
                </div>
              </div>
            </div>
            <div className="row gap">
              <button type="button" onClick={() => void doRefresh()} disabled={busy}>
                重新驗證
              </button>
              <button
                type="button"
                className="danger"
                onClick={() => void doLogout()}
                disabled={busy}
              >
                登出
              </button>
            </div>
          </>
        ) : (
          <p className="muted">{profile?.message || "尚未登入"}</p>
        )}
      </div>

      {!profile?.logged_in && (
        <>
          <div className="row gap tabs">
            <button
              type="button"
              className={mode === "qr" ? "active" : ""}
              onClick={() => setMode("qr")}
            >
              QR 掃碼
            </button>
            <button
              type="button"
              className={mode === "cookie" ? "active" : ""}
              onClick={() => setMode("cookie")}
            >
              Cookie
            </button>
            <button
              type="button"
              className={mode === "sms" ? "active" : ""}
              onClick={() => setMode("sms")}
            >
              簡訊（實驗）
            </button>
          </div>

          {mode === "qr" && (
            <div className="login-block">
              <button type="button" onClick={() => void startQr()} disabled={busy}>
                產生登入 QR
              </button>
              {qr?.image_png_base64 && (
                <img
                  className="qr-image"
                  alt="Bilibili 登入 QR"
                  src={`data:image/png;base64,${qr.image_png_base64}`}
                />
              )}
              {qrStatus && <p className="muted">{qrStatus}</p>}
            </div>
          )}

          {mode === "cookie" && (
            <div className="login-block">
              <label>
                貼上 Cookie（需含 SESSDATA；亦可 JSON）
                <textarea
                  rows={5}
                  value={cookieText}
                  onChange={(e) => setCookieText(e.target.value)}
                  placeholder="SESSDATA=...; bili_jct=...; DedeUserID=..."
                  spellCheck={false}
                />
              </label>
              <button
                type="button"
                onClick={() => void submitCookie()}
                disabled={busy || !cookieText.trim()}
              >
                驗證並儲存
              </button>
            </div>
          )}

          {mode === "sms" && (
            <div className="login-block">
              <p className="warn">{smsNote}</p>
              <p className="muted">
                CLI：`udl bili login sms-captcha` → 瀏覽器完成 GeeTest →
                `sms-send` / `sms`。GUI 僅標示此路徑為 best-effort。
              </p>
            </div>
          )}
        </>
      )}

      {error && <p className="error">{error}</p>}
    </section>
  );
}
