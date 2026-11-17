export const API = process.env.NEXT_PUBLIC_API_URL ?? "";

export type AuthUser = {
  id: string;
  email: string | null;
  tenant_id: string;
  is_platform_admin: boolean;
};

export type LoginResult = {
  success: boolean;
  user?: AuthUser;
  error?: string;
};

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (typeof window !== "undefined") {
    const token = window.localStorage.getItem("trading-platform-token");
    if (token) headers.set("Authorization", `Bearer ${token}`);
  }
  const response = await fetch(`${API}${path}`, { ...init, headers });
  if (response.status === 401 && path !== "/auth/login" && path !== "/auth/session") {
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("trading:unauthorized"));
    }
  }
  return response;
}

export async function getCurrentUser(): Promise<AuthUser | null> {
  try {
    const res = await apiFetch("/auth/me");
    if (!res.ok) return null;
    return (await res.json()) as AuthUser;
  } catch {
    return null;
  }
}

export async function saveAccessToken(token: string): Promise<boolean> {
  const response = await fetch(`${API}/auth/session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access_token: token }),
  });
  if (!response.ok) return false;
  const data = (await response.json()) as { valid: boolean };
  if (!data.valid) return false;
  if (token) {
    window.localStorage.setItem("trading-platform-token", token);
  } else {
    window.localStorage.removeItem("trading-platform-token");
  }
  return true;
}

export async function login(
  email: string,
  password: string,
  tenantId: string = "local"
): Promise<LoginResult> {
  try {
    const response = await fetch(`${API}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: email.trim().toLowerCase(),
        password,
        tenant_id: tenantId.trim() || "local",
      }),
    });
    if (!response.ok) {
      let errorMsg = "Invalid credentials";
      try {
        const errorData = await response.json();
        errorMsg = formatErrorDetail(errorData.detail, "Invalid email or password");
      } catch {
        // fallback
      }
      return { success: false, error: errorMsg };
    }
    const data = (await response.json()) as { access_token: string; user?: AuthUser };
    if (typeof window !== "undefined") {
      window.localStorage.setItem("trading-platform-token", data.access_token);
    }
    return { success: true, user: data.user };
  } catch (err) {
    return {
      success: false,
      error: err instanceof Error ? err.message : "Connection failed to auth server",
    };
  }
}

export async function logout(): Promise<void> {
  try {
    await apiFetch("/auth/logout", { method: "POST" });
  } catch {
    // ignore network errors on logout
  } finally {
    clearSession();
  }
}

export function clearSession() {
  if (typeof window !== "undefined") {
    window.localStorage.removeItem("trading-platform-token");
  }
}

export function formatErrorDetail(detail: unknown, fallback = "Request failed"): string {
  if (!detail) return fallback;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object") {
          const itemObj = item as Record<string, unknown>;
          const msg = typeof itemObj.msg === "string" ? itemObj.msg : "";
          const loc = Array.isArray(itemObj.loc)
            ? itemObj.loc.filter((part) => part !== "body").join(".")
            : "";
          if (msg && loc) return `${loc}: ${msg}`;
          if (msg) return msg;
          return JSON.stringify(item);
        }
        return String(item);
      })
      .join("; ");
  }
  if (typeof detail === "object") {
    return JSON.stringify(detail);
  }
  return String(detail);
}
