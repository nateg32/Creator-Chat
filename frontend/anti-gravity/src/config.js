const envApiBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim();

export const API_BASE_URL = envApiBaseUrl
  ? envApiBaseUrl.replace(/\/+$/, "")
  : "http://127.0.0.1:8000";
