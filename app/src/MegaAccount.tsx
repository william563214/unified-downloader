import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

type Whoami = {
  logged_in?: boolean;
  email?: string | null;
  name?: string | null;
  error?: string;
};

export default function MegaAccount() {
  const [info, setInfo] = useState<Whoami | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const result = await invoke<Whoami>("mega_whoami");
      setInfo(result);
    } catch (e) {
      setInfo({ logged_in: false, error: String(e) });
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function login() {
    setBusy(true);
    setMessage(null);
    try {
      await invoke("mega_login", { email: email.trim(), password });
      setPassword("");
      setMessage("已登入，工作階段寫入 OS keychain（mega.session）");
      await refresh();
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    setBusy(true);
    setMessage(null);
    try {
      await invoke("mega_logout");
      setMessage("已登出");
      await refresh();
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" aria-label="MEGA 帳號">
      <h2>MEGA 帳號</h2>
      <p className="hint">
        登入後可瀏覽／下載雲端硬碟檔案。憑證僅存於 OS keychain（udl.secrets），不會寫入
        settings.json。
      </p>
      {info?.logged_in ? (
        <div className="meta-row">
          <strong>{info.email || info.name || "已登入"}</strong>
          <button type="button" className="ghost" disabled={busy} onClick={() => void logout()}>
            登出
          </button>
        </div>
      ) : (
        <div className="stack">
          <label className="field">
            <span>Email</span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
            />
          </label>
          <label className="field">
            <span>密碼</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </label>
          <button
            type="button"
            className="primary"
            disabled={busy || !email.trim() || !password}
            onClick={() => void login()}
          >
            {busy ? "登入中…" : "登入"}
          </button>
        </div>
      )}
      {message && <p className="hint">{message}</p>}
    </section>
  );
}
