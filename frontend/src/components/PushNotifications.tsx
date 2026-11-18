"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch, formatErrorDetail } from "@/lib/api";
import { useToast } from "@/context/ToastContext";

type PushState = {
  configured: boolean;
  subscriptions: { id: string; endpoint_fingerprint: string; created_at: string; last_success_at: string | null; last_error: string | null }[];
  deliveries_last_7_days: number;
  failed_deliveries_last_7_days: number;
  delivery_summary: Record<string, Record<string, number>>;
};

function decodeApplicationKey(value: string): Uint8Array {
  const base64 = value.replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(base64 + "=".repeat((4 - (base64.length % 4)) % 4));
  return Uint8Array.from(raw, (character) => character.charCodeAt(0));
}

async function responseError(response: Response, fallback: string): Promise<Error> {
  let message = fallback;
  try {
    const data = await response.json();
    message = formatErrorDetail(data.detail, fallback);
  } catch {
    // Keep the useful fallback when the server did not return JSON.
  }
  return new Error(message);
}

export default function PushNotifications() {
  const { toast } = useToast();
  const [state, setState] = useState<PushState | null>(null);
  const [publicKey, setPublicKey] = useState("");
  const [permission, setPermission] = useState<NotificationPermission | "unsupported">("default");
  const [supported, setSupported] = useState(false);
  const [deviceSubscribed, setDeviceSubscribed] = useState(false);
  const [workerReady, setWorkerReady] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (typeof window === "undefined") return;
    setLoading(true);
    const canPush = window.isSecureContext && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
    setSupported(canPush);
    setPermission("Notification" in window ? Notification.permission : "unsupported");
    if (!canPush) {
      setPermission("unsupported");
      setLoading(false);
      return;
    }
    try {
      const registration = await navigator.serviceWorker.getRegistration("/");
      const browserSubscription = await registration?.pushManager.getSubscription();
      const [configResponse, statusResponse] = await Promise.all([
        apiFetch("/notifications/push/config"),
        apiFetch("/notifications/push/subscriptions"),
      ]);
      if (!configResponse.ok) throw await responseError(configResponse, "Could not load browser notification setup.");
      if (!statusResponse.ok) throw await responseError(statusResponse, "Could not load your notification devices.");
      const config = await configResponse.json();
      setPublicKey(config.public_key || "");
      setWorkerReady(Boolean(config.worker_ready));
      const nextState: PushState = await statusResponse.json();
      setState(nextState);
      if (browserSubscription) {
        const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(browserSubscription.endpoint));
        const fingerprint = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
        setDeviceSubscribed(nextState.subscriptions.some((item) => item.endpoint_fingerprint === fingerprint));
      } else {
        setDeviceSubscribed(false);
      }
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load notification settings.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const enable = async () => {
    if (!supported || !publicKey) return;
    setBusy(true);
    setError("");
    try {
      const result = await Notification.requestPermission();
      setPermission(result);
      if (result !== "granted") {
        throw new Error(result === "denied"
          ? "Notifications are blocked for this site. Allow them in your browser's site settings, then try again."
          : "Allow notifications to receive trade alerts on this device.");
      }
      const registration = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      await navigator.serviceWorker.ready;
      let subscription = await registration.pushManager.getSubscription();
      const applicationKey = decodeApplicationKey(publicKey);
      if (subscription) {
        const currentKey = subscription.options.applicationServerKey;
        const currentBytes = currentKey ? new Uint8Array(currentKey) : new Uint8Array();
        const keyMatches = currentBytes.length === applicationKey.length
          && currentBytes.every((byte, index) => byte === applicationKey[index]);
        if (!keyMatches) {
          await subscription.unsubscribe();
          subscription = null;
        }
      }
      if (!subscription) {
        subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: applicationKey as BufferSource,
        });
      }
      const response = await apiFetch("/notifications/push/subscriptions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(subscription.toJSON()),
      });
      if (!response.ok) throw await responseError(response, "This device could not be registered.");
      await refresh();
      toast.success("This device will receive trade alerts.", "Browser notifications enabled");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Browser notifications could not be enabled.";
      setError(message);
      toast.error(message, "Notifications unavailable");
    } finally {
      setBusy(false);
    }
  };

  const disable = async () => {
    setBusy(true);
    setError("");
    try {
      const registration = await navigator.serviceWorker.getRegistration("/");
      const subscription = await registration?.pushManager.getSubscription();
      if (subscription) {
        const response = await apiFetch("/notifications/push/unsubscribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ endpoint: subscription.endpoint }),
        });
        if (!response.ok) throw await responseError(response, "This device could not be removed from your account.");
        await subscription.unsubscribe();
      }
      await refresh();
      toast.success("This browser will no longer receive trade alerts.", "Device removed");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "This device could not be removed.";
      setError(message);
      toast.error(message, "Could not remove device");
    } finally {
      setBusy(false);
    }
  };

  const sendTest = async () => {
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch("/notifications/push/test", { method: "POST" });
      if (!response.ok) throw await responseError(response, "The test notification could not be queued.");
      toast.success("A test alert is queued for this device.", "Test alert queued");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "The test notification could not be queued.";
      setError(message);
      toast.error(message, "Test notification failed");
    } finally {
      setBusy(false);
    }
  };

  const deviceCount = state?.subscriptions.length ?? 0;
  const thisDeviceEnabled = permission === "granted" && deviceSubscribed;
  const queuedFor = (channel: string) => {
    const statuses = state?.delivery_summary?.[channel];
    return (statuses?.pending ?? 0) + (statuses?.retry ?? 0) + (statuses?.processing ?? 0);
  };

  return (
    <section className="mt-6 rounded-xl border border-[#27272a] bg-[#18181b] p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-[#26262b] pb-4">
        <div>
          <h2 className="text-sm font-semibold text-zinc-100">Browser trade alerts</h2>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-zinc-400">
            Receive trade proposals, position entries, and closures when this app is closed. Permission is requested only when you enable alerts.
          </p>
        </div>
        {thisDeviceEnabled && <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-1 text-[11px] text-emerald-300">This browser is enabled</span>}
      </div>

      <div className="mt-4 grid gap-4 md:grid-cols-[1fr_auto] md:items-center">
        <div className="space-y-1 text-xs text-zinc-400">
          {loading ? <p>Loading notification settings…</p> : !supported ? (
            <p>Browser push is unavailable here. Use a browser with service worker and push support over HTTPS.</p>
          ) : !state?.configured ? (
            <p>Browser push is not configured on the server yet. Ask your administrator to set the Web Push VAPID keys.</p>
          ) : permission === "denied" ? (
            <p>Browser permission is blocked. Allow notifications for this site in browser settings, then return here.</p>
          ) : (
            <>
              {!workerReady && <p className="text-amber-300">The browser-push delivery worker is offline or not configured. Alerts will remain queued until it is ready.</p>}
              <p>{deviceCount} registered device{deviceCount === 1 ? "" : "s"} on this account.</p>
              <p>{state?.deliveries_last_7_days ?? 0} delivery attempts in the last 7 days; {state?.failed_deliveries_last_7_days ?? 0} failed.</p>
              <p>{state?.delivery_summary?.telegram?.failed ?? 0} Telegram deliveries failed in the last 7 days.</p>
              <p>Queued or retrying: {queuedFor("webpush")} browser · {queuedFor("telegram")} Telegram.</p>
            </>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {!thisDeviceEnabled ? (
            <button type="button" onClick={() => void enable()} disabled={loading || busy || !supported || !state?.configured}
              className="min-h-11 rounded-lg bg-cyan-500 px-4 text-xs font-semibold text-zinc-950 transition hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-50">
              {busy ? "Enabling…" : "Enable on this device"}
            </button>
          ) : (
            <>
              <button type="button" onClick={() => void sendTest()} disabled={busy}
                className="min-h-11 rounded-lg border border-[#3a3a42] px-4 text-xs text-zinc-200 transition hover:bg-[#242429] disabled:opacity-50">
                {busy ? "Working…" : "Send test alert"}
              </button>
              <button type="button" onClick={() => void disable()} disabled={busy}
                className="min-h-11 rounded-lg border border-rose-500/30 px-4 text-xs text-rose-300 transition hover:bg-rose-500/10 disabled:opacity-50">
                Remove this device
              </button>
            </>
          )}
        </div>
      </div>

      {error && <div role="alert" className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-200">
        <span>{error}</span>
        <button type="button" onClick={() => void refresh()} disabled={loading} className="min-h-10 rounded-md border border-rose-300/30 px-3 font-medium hover:bg-rose-500/10 disabled:opacity-50">Retry status</button>
      </div>}
      {state?.subscriptions.some((item) => item.last_error) && (
        <p className="mt-3 text-xs text-amber-300">At least one device recently failed to receive an alert. Remove and re-enable that device if the problem continues.</p>
      )}
    </section>
  );
}
