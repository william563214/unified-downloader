import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { humanizeError } from "../errors";

type AccountInfo = {
  logged_in?: boolean;
  username?: string | null;
  name?: string | null;
  email?: string | null;
  user_id?: string | null;
  ok?: boolean;
  error?: string;
};

type Props = {
  cloudflareBypass: boolean;
  downloadDir?: string;
  showImport?: boolean;
  onImported?: () => void;
};

export function AccountSection({
  cloudflareBypass,
  downloadDir,
  showImport = false,
  onImported,
}: Props) {
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const info = await invoke<AccountInfo>("iwara_whoami", {
        cloudflareBypass,
      });
      setAccount(info);
    } catch (e) {
      setAccount({ logged_in: false });
      setError(humanizeError(String(e)));
    } finally {
      setBusy(false);
    }
  }, [cloudflareBypass]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function doLogin() {
    if (!email.trim() || !password) {
      setError("請輸入帳號與密碼");
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await invoke<{ ok?: boolean; account?: AccountInfo; error?: string }>(
        "iwara_login",
        {
          email: email.trim(),
          password,
          cloudflareBypass,
        },
      );
      setPassword("");
      if (result?.account) {
        setAccount({ ...result.account, logged_in: true });
      } else {
        await refresh();
      }
      setMessage("已登入；憑證僅存在作業系統鑰匙圈");
    } catch (e) {
      setError(humanizeError(String(e)));
    } finally {
      setBusy(false);
    }
  }

  async function doLogout() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await invoke("iwara_logout", {});
      setAccount({ logged_in: false });
      setMessage("已登出並清除鑰匙圈中的 Iwara 憑證");
    } catch (e) {
      setError(humanizeError(String(e)));
    } finally {
      setBusy(false);
    }
  }

  async function importFollowing(withSync: boolean) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await invoke<{
        added?: number;
        updated?: number;
        fetched?: number;
        sync?: { new_total?: number; enqueued_total?: number };
        error?: string;
      }>("iwara_import_following", {
        cloudflareBypass,
        sync: withSync,
        output: downloadDir || null,
      });
      const parts = [
        `已取得 ${result.fetched ?? 0} 位`,
        `新增 ${result.added ?? 0}`,
        `更新 ${result.updated ?? 0}`,
      ];
      if (result.sync) {
        parts.push(
          `同步新內容 ${result.sync.new_total ?? 0}／入佇列 ${result.sync.enqueued_total ?? 0}`,
        );
      }
      setMessage(parts.join(" · "));
      onImported?.();
    } catch (e) {
      setError(humanizeError(String(e)));
    } finally {
      setBusy(false);
    }
  }

  const loggedIn = Boolean(account?.logged_in);

  return (
    <div className="iwara-account">
      <h3>Iwara 帳號</h3>
      <p className="hint">
        憑證與 token 只寫入作業系統鑰匙圈（udl.secrets），不會寫入設定檔。
      </p>
      {loggedIn ? (
        <p className="muted">
          已登入：{account?.name || account?.username || account?.email || "帳號"}
          {account?.username ? ` (@${account.username})` : ""}
        </p>
      ) : (
        <p className="muted">尚未登入（公開內容仍可瀏覽／下載）</p>
      )}

      {!loggedIn ? (
        <div className="settings-grid">
          <label className="field compact">
            <span>Email／使用者名稱</span>
            <input
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={busy}
            />
          </label>
          <label className="field compact">
            <span>密碼</span>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={busy}
            />
          </label>
        </div>
      ) : null}

      <div className="row-actions">
        {loggedIn ? (
          <button type="button" className="ghost" disabled={busy} onClick={() => void doLogout()}>
            登出
          </button>
        ) : (
          <button type="button" className="primary" disabled={busy} onClick={() => void doLogin()}>
            登入
          </button>
        )}
        <button type="button" className="ghost" disabled={busy} onClick={() => void refresh()}>
          檢查工作階段
        </button>
        {showImport && loggedIn ? (
          <>
            <button
              type="button"
              className="ghost"
              disabled={busy}
              onClick={() => void importFollowing(false)}
            >
              匯入 Following
            </button>
            <button
              type="button"
              className="primary"
              disabled={busy}
              onClick={() => void importFollowing(true)}
            >
              匯入並同步
            </button>
          </>
        ) : null}
      </div>

      {message ? <p className="muted">{message}</p> : null}
      {error ? <p className="error-text">{error}</p> : null}
    </div>
  );
}
