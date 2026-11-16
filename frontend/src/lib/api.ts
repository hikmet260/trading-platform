export const API = process.env.NEXT_PUBLIC_API_URL ?? "";

export function apiFetch(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  if (typeof window !== "undefined") {
    const token = window.localStorage.getItem("trading-platform-token");
    if (token) headers.set("Authorization", `Bearer ${token}`);
  }
  return fetch(`${API}${path}`, { ...init, headers });
}

export async function saveAccessToken(token: string): Promise<boolean> {
  const response = await fetch(`${API}/auth/session`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access_token: token }),
  });
  if (!response.ok) return false;
  const data = await response.json() as { valid: boolean };
  if (!data.valid) return false;
  if (token) window.localStorage.setItem("trading-platform-token", token);
  else window.localStorage.removeItem("trading-platform-token");
  return true;
}

export async function login(email: string, password: string, tenantId: string): Promise<boolean> {
  const response = await fetch(`${API}/auth/login`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, tenant_id: tenantId }),
  });
  if (!response.ok) return false;
  const data = await response.json() as { access_token: string };
  window.localStorage.setItem("trading-platform-token", data.access_token);
  return true;
}

export function clearSession() {
  window.localStorage.removeItem("trading-platform-token");
}
