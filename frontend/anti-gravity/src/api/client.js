import { API_BASE_URL } from "../config";

async function readErrorPayload(res) {
  // Try JSON first, fall back to text.
  try {
    const data = await res.json();
    if (typeof data === "string") return data;
    if (data && typeof data.detail === "string") return data.detail;
    return JSON.stringify(data);
  } catch {
    try {
      const text = await res.text();
      return text || null;
    } catch {
      return null;
    }
  }
}

async function postJson(path, body) {
  let res;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      credentials: "include", // Include cookies
    });
  } catch (err) {
    // Network error, server down, CORS, etc.
    const errorMsg = err?.message || "Network error";
    if (errorMsg.includes("Failed to fetch") || errorMsg.includes("NetworkError")) {
      throw new Error(`Cannot connect to backend at ${API_BASE_URL}. Make sure the backend server is running on port 8000.`);
    }
    throw new Error(`Network error: ${errorMsg}`);
  }

  if (!res.ok) {
    const details = await readErrorPayload(res);
    const msg = details ? `Request failed (${res.status}): ${details}` : `Request failed (${res.status})`;
    throw new Error(msg);
  }

  try {
    return await res.json();
  } catch {
    throw new Error("Invalid JSON response from server");
  }
}

export function ask({ creator_id, question, top_k, max_distance, messages, debug, thread_id, images }) {
  const body = { creator_id, question, top_k, max_distance };
  if (messages != null) body.messages = messages;
  if (debug) body.debug = true;
  if (thread_id) body.thread_id = thread_id;
  if (images != null) body.images = images;
  return postJson("/ask", body);
}

export async function askStream({ creator_id, question, top_k, max_distance, messages, thread_id, images, onToken, onComplete, onError }) {
  const body = { creator_id, question, top_k, max_distance, messages, thread_id, images };

  const response = await fetch(`${API_BASE_URL}/ask-stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    credentials: "include",
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed (${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let fullAnswer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop();

      for (const part of parts) {
        if (part.startsWith("data: ")) {
          const dataStr = part.slice(6);
          if (dataStr === "[DONE]") {
            if (onComplete) onComplete(fullAnswer);
            return { answer: fullAnswer };
          }
          try {
            const data = JSON.parse(dataStr);
            if (data.content) {
              fullAnswer += data.content;
              if (onToken) onToken(data.content);
            }
          } catch (e) {
            console.error("Error parsing stream chunk:", e);
          }
        }
      }
    }
  } catch (err) {
    if (onError) onError(err);
    else throw err;
  }
}

export function ingest({ creator_id, title, content, source, source_id, doc_type }) {
  return postJson("/ingest", { creator_id, title, content, source, source_id, doc_type });
}

// Search: legacy { url, limit } or config-based { creator_id, platform_configs? }
export function search({ url, limit = 10, creator_id, platform_configs }) {
  const body = {};
  if (url != null) body.url = url;
  body.limit = limit;
  if (creator_id != null) body.creator_id = creator_id;
  if (platform_configs != null) body.platform_configs = platform_configs;
  return postJson("/search", body);
}

// Scrape = search by creator_id. Returns { scrape_id, ... } for backward compatibility.
export async function scrape({ creator_id, platform_configs }) {
  const data = await search({ creator_id, platform_configs });
  return { scrape_id: data.search_id, ...data };
}

export function getPlatforms() {
  return getJson("/platforms");
}

export function validatePlatformUrl(key, url) {
  return getJson(`/platforms/${encodeURIComponent(key)}/validate?url=${encodeURIComponent(url || "")}`);
}

export function createCreatorWithConfig({ name, handle, profile_picture_url, platform_configs }) {
  return postJson("/creators/config", { name, handle, profile_picture_url, platform_configs: platform_configs || {} });
}

export async function updateCreator(creatorId, { name, handle, profile_picture_url, platform_configs, visual_config }) {
  const body = {};
  if (name != null) body.name = name;
  if (handle != null) body.handle = handle;
  if (profile_picture_url != null) body.profile_picture_url = profile_picture_url;
  if (platform_configs != null) body.platform_configs = platform_configs;
  if (visual_config != null) body.visual_config = visual_config;
  const res = await fetch(`${API_BASE_URL}/creators/${creatorId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    credentials: "include",
  });
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    console.error("Update failed detail:", d);
    const msg = typeof d.detail === 'object' ? JSON.stringify(d.detail) : (d.detail || "Update failed");
    throw new Error(msg);
  }
  return res.json();
}

export async function getUserSettings() {
  return getJson("/user/settings");
}

export async function updateUserSettings({ display_name, profile_picture_url, response_preferences }) {
  const res = await fetch(`${API_BASE_URL}/user/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ display_name, profile_picture_url, response_preferences }),
    credentials: "include"
  });

  if (!res.ok) {
    // Reuse readErrorPayload if accessible, or just text
    let msg = "Update failed";
    try {
      const data = await res.json();
      if (data && typeof data.detail === 'string') msg = data.detail;
      else if (data) msg = JSON.stringify(data);
    } catch {
      try { msg = await res.text(); } catch { }
    }
    throw new Error(msg);
  }
  return res.json();
}

export function getCreatorConfig(creatorId) {
  return getJson(`/creators/${creatorId}/config`);
}

export function getScrapeRuns(creatorId, limit = 5) {
  return getJson(`/scrape/runs?creator_id=${creatorId}&limit=${limit}`);
}

export function startScrapeRun(creatorId, platforms = null, forceFull = false) {
  return postJson("/scrape/run", { creator_id: creatorId, platforms, force_full: forceFull });
}

// Legacy scrape function (for backward compatibility)
export function scrapeLegacy({ creator_id, handle, source, limit }) {
  return postJson("/scrape", { creator_id, handle, source, limit });
}

// New approval endpoint for scrape_items (Instagram Reels)
export async function approveIngestV2({ scrape_id, decisions, creator_id }) {
  return postJson("/approve_ingest_v2", { scrape_id, decisions, creator_id });
}

// Streaming version with progress updates via Server-Sent Events
export async function approveIngestV2Stream({ scrape_id, decisions, creator_id, onProgress }) {
  const response = await fetch(`${API_BASE_URL}/approve_ingest_v2/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scrape_id, decisions, creator_id }),
    credentials: "include",
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed (${response.status})`);
  }

  // Process Server-Sent Events stream
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResult = null;

  while (true) {
    const { done, value } = await reader.read();

    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // Process complete SSE messages (split by double newline)
    const messages = buffer.split("\n\n");
    buffer = messages.pop() || ""; // Keep incomplete message in buffer

    for (const message of messages) {
      if (message.startsWith("data: ")) {
        const data = JSON.parse(message.slice(6));

        // Call progress callback
        if (onProgress) {
          onProgress(data);
        }

        // Store final result
        if (data.stage === "complete" && data.result) {
          finalResult = data.result;
        }

        // Handle errors
        if (data.stage === "error") {
          throw new Error(data.message || "Unknown error occurred");
        }
      }
    }
  }

  return finalResult || { approved: 0, ingested: [] };
}

// Legacy approval endpoint (for backward compatibility)
export async function approveIngest({ creator_id, queue_ids }) {
  const res = await fetch(`${API_BASE_URL}/approve_ingest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ creator_id, queue_ids }),
    credentials: "include", // Include cookies
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text);
  }

  return res.json();
}

async function getJson(path) {
  let res;
  try {
    // Add timeout
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 30000); // 30 second timeout

    const separator = path.includes('?') ? '&' : '?';
    res = await fetch(`${API_BASE_URL}${path}${separator}_t=${Date.now()}`, {
      method: "GET",
      headers: { "Content-Type": "application/json" },
      credentials: "include", // Include cookies
      signal: controller.signal,
    });

    clearTimeout(timeoutId);
  } catch (err) {
    const errorMsg = err?.message || "Network error";
    if (err.name === "AbortError") {
      throw new Error(`Request timeout: Backend at ${API_BASE_URL} is not responding. Make sure the backend server is running.`);
    }
    if (errorMsg.includes("Failed to fetch") || errorMsg.includes("NetworkError")) {
      throw new Error(`Cannot connect to backend at ${API_BASE_URL}. Make sure the backend server is running on port 8000.`);
    }
    throw new Error(`Network error: ${errorMsg}`);
  }

  if (!res.ok) {
    const details = await readErrorPayload(res);
    const msg = details ? `Request failed (${res.status}): ${details}` : `Request failed (${res.status})`;
    throw new Error(msg);
  }

  try {
    return await res.json();
  } catch {
    throw new Error("Invalid JSON response from server");
  }
}

export function health() {
  return getJson("/health");
}

export function getPersona(creator_id) {
  return getJson(`/creator/${creator_id}/persona`);
}

export function savePersona(creator_id, persona) {
  return postJson(`/creator/${creator_id}/persona`, { persona });
}

export function getQueueItems(creator_id) {
  return getJson(`/creator/${creator_id}/queue`);
}




// Get items for a search run (new endpoint)
export function getSearchItems(search_id) {
  return getJson(`/search/${search_id}/items`);
}

// Alias for backward compatibility (scrape_id === search_id)
export function getScrapeItems(scrape_id) {
  return getSearchItems(scrape_id);
}

// Get search progress
export function getSearchProgress(search_id) {
  return getJson(`/search/${search_id}/progress`);
}

export async function getFingerprintStatus(creatorId) {
  return getJson(`/creators/${creatorId}/fingerprint/status`);
}

export async function generateFingerprint(creatorId) {
  return postJson(`/creators/${creatorId}/fingerprint/generate`, { creator_id: creatorId });
}

// Alias for backward compatibility
export function getScrapeProgress(scrape_id) {
  return getSearchProgress(scrape_id);
}

// Auth functions
export async function login(email, password) {
  return postJson("/auth/login", { email, password });
}

export async function register(email, password) {
  return postJson("/auth/register", { email, password });
}

export async function getSession() {
  return getJson("/auth/session");
}

export async function logout() {
  return postJson("/auth/logout", {});
}

// Creator functions
export async function listCreators() {
  return getJson("/creators");
}

export async function createCreator(name, handle, platforms) {
  return postJson("/creators", { name, handle, platforms: platforms || [] });
}

export async function getCreatorStats(creator_id) {
  return getJson(`/creators/${creator_id}/stats`);
}
export async function deleteCreator(creator_id) {
  const res = await fetch(`${API_BASE_URL}/creators/${creator_id}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "Failed to delete creator");
  }
  return res.json();
}

// Thread functions
export function createThread(creator_id) {
  return postJson("/threads", { creator_id });
}

export function listThreads(creator_id, archived = false) {
  return getJson(`/creators/${creator_id}/threads${archived ? '?archived=true' : ''}`);
}

export function getThreadMessages(thread_id) {
  return getJson(`/threads/${thread_id}/messages`);
}

export function updateThread(threadId, { title, is_archived }) {
  const body = {};
  if (title !== undefined) body.title = title;
  if (is_archived !== undefined) body.is_archived = is_archived;

  return fetch(`${API_BASE_URL}/threads/${threadId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    credentials: "include",
  }).then(async res => {
    if (!res.ok) {
      const txt = await res.text();
      throw new Error(txt || "Failed to update thread");
    }
    return res.json();
  });
}

export function deleteThread(thread_id) {
  return fetch(`${API_BASE_URL}/threads/${thread_id}`, {
    method: "DELETE",
    credentials: "include",
  }).then(res => {
    if (!res.ok) throw new Error("Failed to delete thread");
    return res.json();
  });
}

export function getLastActiveThread(creator_id) {
  return getJson(`/creators/${creator_id}/last_active_thread`);
}
