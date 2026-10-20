import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
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

type SmsCaptchaPayload = {
  token?: string;
  gt?: string;
  challenge?: string;
  note?: string;
  best_effort?: boolean;
};

type GeetestResult = {
  token?: string;
  challenge?: string;
  validate?: string;
  seccode?: string;
};

const sms_geetest_hint =
  "簡訊登入需完成 GeeTest 驗證。將開啟內嵌視窗供你本人完成挑戰（不會自動破解驗證碼）。無圖形環境請改用 QR 或貼上 Cookie。";

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
  const [smsTel, setSmsTel] = useState("");
  const [smsCid, setSmsCid] = useState("86");
  const [smsCode, setSmsCode] = useState("");
  const [smsToken, setSmsToken] = useState("");
  const [smsChallenge, setSmsChallenge] = useState("");
  const [smsValidate, setSmsValidate] = useState("");
  const [smsSeccode, setSmsSeccode] = useState("");
  const [smsCaptchaKey, setSmsCaptchaKey] = useState("");
  const [smsStep, setSmsStep] = useState<string>("");
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

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    void listen<GeetestResult>("geetest-result", (event) => {
      const p = event.payload;
      if (p.challenge) setSmsChallenge(p.challenge);
      if (p.validate) setSmsValidate(p.validate);
      if (p.seccode) setSmsSeccode(p.seccode);
      if (p.token) setSmsToken(p.token);
      setSmsStep("GeeTest 完成，可發送簡訊");
      void invoke("close_geetest_window").catch(() => {
        /* window may already be closed */
      });
    }).then((fn) => {
      unlisten = fn;
    });
    return () => {
      unlisten?.();
    };
  }, []);

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
      setQrStatus(result.message || result.status || "");
      if (result.code === 0 || result.logged_in) {
        stopPoll();
        if (result.profile) {
          setProfile(result.profile);
          onStatus?.(result.profile);
        } else {
          await refreshStatus();
        }
      }
    } catch (e) {
      setError(String(e));
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
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function doLogout() {
    setBusy(true);
    setError(null);
    try {
      const result = await invoke<BiliAccountProfile>("bili_login_logout");
      setProfile(result);
      onStatus?.(result);
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

  async function startSmsGeetest() {
    setBusy(true);
    setError(null);
    setSmsStep("取得 GeeTest 挑戰…");
    try {
      const captcha = await invoke<SmsCaptchaPayload>("bili_login_sms_captcha");
      const gt = captcha.gt || "";
      const challenge = captcha.challenge || "";
      const token = captcha.token || "";
      if (!gt || !challenge || !token) {
        throw new Error(
          "captcha 回應缺少 gt／challenge／token；請改用 QR 或 Cookie",
        );
      }
      setSmsToken(token);
      setSmsChallenge(challenge);
      setSmsValidate("");
      setSmsSeccode("");
      setSmsStep("請在彈出視窗完成 GeeTest");
      await invoke("open_geetest_window", { gt, challenge, token });
    } catch (e) {
      setError(String(e));
      setSmsStep(
        "無法開啟 GeeTest 視窗時請改用 CLI：`udl bili login sms-captcha` 後以瀏覽器完成驗證",
      );
    } finally {
      setBusy(false);
    }
  }

  async function sendSms() {
    if (!smsTel.trim() || !smsToken || !smsChallenge || !smsValidate || !smsSeccode) {
      setError("請先完成 GeeTest，並填寫手機號碼");
      return;
    }
    setBusy(true);
    setError(null);
    setSmsStep("發送簡訊中…");
    try {
      const result = await invoke<{ captcha_key?: string }>("bili_login_sms_send", {
        tel: smsTel.trim(),
        cid: smsCid.trim() || "86",
        token: smsToken,
        challenge: smsChallenge,
        validate: smsValidate,
        seccode: smsSeccode,
      });
      setSmsCaptchaKey(String(result.captcha_key || ""));
      setSmsStep("簡訊已發送，請輸入驗證碼");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function completeSms() {
    if (!smsTel.trim() || !smsCode.trim() || !smsCaptchaKey) {
      setError("請填寫驗證碼（需先發送簡訊）");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await invoke<BiliAccountProfile>("bili_login_sms", {
        tel: smsTel.trim(),
        cid: smsCid.trim() || "86",
        code: smsCode.trim(),
        captchaKey: smsCaptchaKey,
      });
      setProfile(result);
      onStatus?.(result);
      setSmsStep("登入成功");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel bilibili-account" aria-label="Bilibili 帳號">
      <header className="panel-head">
        <h2>Bilibili 帳號</h2>
        <p className="hint">
          Session 存於 OS keychain（bilibili.session）。不繞過 VIP／付費／區域。
        </p>
      </header>

      <div className="account-status">
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
                <div className="account-avatar placeholder" />
              )}
              <div>
                <div className="account-name">{profile.uname || "已登入"}</div>
                <div className="muted">
                  mid {profile.mid ?? "—"}
                  {profile.vip_label ? ` · ${profile.vip_label}` : ""}
                  {profile.level ? ` · Lv${profile.level}` : ""}
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
              簡訊
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
              <p className="warn">{sms_geetest_hint}</p>
              <div className="row wrap gap">
                <label className="field inline">
                  <span>國碼</span>
                  <input
                    value={smsCid}
                    onChange={(e) => setSmsCid(e.target.value)}
                    style={{ width: "4rem" }}
                  />
                </label>
                <label className="field inline">
                  <span>手機</span>
                  <input
                    value={smsTel}
                    onChange={(e) => setSmsTel(e.target.value)}
                    placeholder="手機號碼"
                  />
                </label>
              </div>
              <div className="row gap">
                <button
                  type="button"
                  onClick={() => void startSmsGeetest()}
                  disabled={busy}
                >
                  開啟 GeeTest 視窗
                </button>
                <button
                  type="button"
                  onClick={() => void sendSms()}
                  disabled={busy || !smsValidate || !smsTel.trim()}
                >
                  發送簡訊
                </button>
              </div>
              {smsValidate && (
                <p className="muted">已取得 validate／seccode</p>
              )}
              <label className="field">
                <span>簡訊驗證碼</span>
                <input
                  value={smsCode}
                  onChange={(e) => setSmsCode(e.target.value)}
                  placeholder="收到的驗證碼"
                />
              </label>
              <button
                type="button"
                className="primary"
                onClick={() => void completeSms()}
                disabled={busy || !smsCaptchaKey || !smsCode.trim()}
              >
                完成簡訊登入
              </button>
              {smsStep && <p className="muted">{smsStep}</p>}
            </div>
          )}
        </>
      )}

      {error && <p className="error">{error}</p>}
    </section>
  );
}
