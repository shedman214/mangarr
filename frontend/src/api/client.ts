let apiKey = "";
let version = "";
let loginEnabled = false;

/** initialize.json refused: the optional login is on and this browser is not signed in. */
export class LoginRequiredError extends Error {}

export async function initClient(): Promise<void> {
  const resp = await fetch("/initialize.json");
  if (resp.status === 401) throw new LoginRequiredError("Sign in to continue");
  if (!resp.ok) throw new Error("Cannot reach mangarr backend");
  const data = await resp.json();
  apiKey = data.apiKey;
  version = data.version;
  loginEnabled = Boolean(data.login);
}

/** Turning login on replaces the browser key; keep using the new one. */
export function setApiKey(key: string): void {
  apiKey = key;
}

export function isLoginEnabled(): boolean {
  return loginEnabled;
}

export async function signIn(username: string, password: string): Promise<void> {
  const resp = await fetch("/auth/login", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }),
  });
  if (!resp.ok) {
    let detail = "Sign-in failed";
    try { detail = (await resp.json()).detail || detail; } catch { /* not json */ }
    throw new Error(detail);
  }
}

export async function signOut(): Promise<void> {
  await fetch("/auth/logout", { method: "POST" });
}

export function appVersion(): string {
  return version;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const resp = await fetch(`/api/v1${path}`, {
    method,
    headers: {
      "X-Api-Key": apiKey,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const data = await resp.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* not json */
    }
    throw new ApiError(resp.status, detail);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json();
}

export const api = {
  backupDownload: async (id: string) => {
    const resp = await fetch(`/api/v1/settings/backups/${id}`, { headers: { "X-Api-Key": apiKey } });
    if (!resp.ok) throw new Error("Backup download failed");
    return resp.blob();
  },
  restoreUpload: async (file: File) => {
    const resp = await fetch("/api/v1/settings/backups/restore", { method: "POST", headers: { "X-Api-Key": apiKey, "Content-Type": "application/zip" }, body: file });
    if (!resp.ok) {
      const data = await resp.json();
      throw new Error(data.detail || "Restore validation failed");
    }
    return resp.json();
  },
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};
