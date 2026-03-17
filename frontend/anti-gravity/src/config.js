const envApiBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim();

function trimTrailingSlash(value) {
  return String(value || "").replace(/\/+$/, "");
}

function isLocalHostname(hostname) {
  const normalized = String(hostname || "").toLowerCase();
  return normalized === "localhost" || normalized === "127.0.0.1" || normalized === "::1";
}

function resolveApiBaseUrl() {
  if (envApiBaseUrl) {
    return trimTrailingSlash(envApiBaseUrl);
  }

  if (typeof window !== "undefined" && window.location) {
    const { origin, hostname } = window.location;
    if (!isLocalHostname(hostname)) {
      return trimTrailingSlash(origin);
    }
  }

  return "http://127.0.0.1:8000";
}

export const API_BASE_URL = resolveApiBaseUrl();
export const API_IS_LOCAL = /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/i.test(API_BASE_URL);
export const API_CONNECTION_HELP = API_IS_LOCAL
  ? "Make sure the local backend server is running on port 8000."
  : "If your backend is on a different domain, set VITE_API_BASE_URL to that deployed API URL.";

export function formatBackendConnectionError() {
  return `Cannot connect to backend at ${API_BASE_URL}. ${API_CONNECTION_HELP}`;
}

export function formatBackendTimeoutError() {
  return `Request timeout: Backend at ${API_BASE_URL} is not responding. ${API_CONNECTION_HELP}`;
}
