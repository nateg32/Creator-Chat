/**
 * Parse URLs to extract handle and detect platform
 */

export function parseCreatorUrl(url) {
  if (!url || typeof url !== "string") {
    return null;
  }

  const trimmed = url.trim();
  if (!trimmed) return null;

  try {
    // Try to parse as URL
    const urlObj = new URL(trimmed);
    const hostname = urlObj.hostname.toLowerCase();

    // Instagram
    if (hostname.includes("instagram.com")) {
      const pathParts = urlObj.pathname.split("/").filter(Boolean);
      // Support profile URLs, reel URLs, and post URLs
      if (pathParts.length > 0) {
        if (pathParts[0] === "reel" || pathParts[0] === "p") {
          // For reel/post URLs, we can still scrape the profile if we extract handle from URL
          // Or return the full URL and let backend parse it
          return {
            platform: "instagram",
            handle: pathParts[1] || pathParts[0], // Use reel/post ID or fallback
            source: "instagram",
          };
        } else {
          // Profile URL
          return {
            platform: "instagram",
            handle: pathParts[0],
            source: "instagram",
          };
        }
      }
    }

    // YouTube
    if (hostname.includes("youtube.com") || hostname.includes("youtu.be")) {
      if (hostname.includes("youtu.be")) {
        const videoId = urlObj.pathname.slice(1);
        // For YouTube, we'd need channel ID, but handle can be extracted from channel URL
        return {
          platform: "youtube",
          handle: videoId,
          source: "youtube",
        };
      }
      const pathParts = urlObj.pathname.split("/").filter(Boolean);
      if (pathParts[0] === "channel" || pathParts[0] === "c" || pathParts[0] === "user") {
        return {
          platform: "youtube",
          handle: pathParts[1] || pathParts[0],
          source: "youtube",
        };
      }
      // Try to extract from @handle format
      const match = trimmed.match(/@([\w-]+)/);
      if (match) {
        return {
          platform: "youtube",
          handle: match[1],
          source: "youtube",
        };
      }
    }

    // TikTok
    if (hostname.includes("tiktok.com")) {
      const pathParts = urlObj.pathname.split("/").filter(Boolean);
      if (pathParts.length > 0 && pathParts[0].startsWith("@")) {
        return {
          platform: "tiktok",
          handle: pathParts[0].replace("@", ""),
          source: "tiktok",
        };
      }
    }

    // Twitter/X
    if (hostname.includes("twitter.com") || hostname.includes("x.com")) {
      const pathParts = urlObj.pathname.split("/").filter(Boolean);
      if (pathParts.length > 0 && pathParts[0] !== "status" && pathParts[0] !== "i") {
        return {
          platform: "twitter",
          handle: pathParts[0].replace("@", ""),
          source: "twitter",
        };
      }
    }

    // Generic website/blog - extract domain as handle
    return {
      platform: "website",
      handle: hostname.replace("www.", ""),
      source: "website",
    };
  } catch {
    // Not a valid URL, try to extract handle from text
    const instagramMatch = trimmed.match(/instagram\.com\/([^/\s]+)/i);
    if (instagramMatch) {
      return {
        platform: "instagram",
        handle: instagramMatch[1],
        source: "instagram",
      };
    }

    const youtubeMatch = trimmed.match(/youtube\.com\/(?:channel\/|c\/|user\/|@)([^/\s]+)/i);
    if (youtubeMatch) {
      return {
        platform: "youtube",
        handle: youtubeMatch[1],
        source: "youtube",
      };
    }

    const tiktokMatch = trimmed.match(/tiktok\.com\/@([^/\s]+)/i);
    if (tiktokMatch) {
      return {
        platform: "tiktok",
        handle: tiktokMatch[1],
        source: "tiktok",
      };
    }

    const twitterMatch = trimmed.match(/(?:twitter|x)\.com\/([^/\s]+)/i);
    if (twitterMatch) {
      return {
        platform: "twitter",
        handle: twitterMatch[1],
        source: "twitter",
      };
    }

    // If it looks like just a handle (starts with @ or alphanumeric)
    const handleMatch = trimmed.match(/^@?([\w.-]+)$/);
    if (handleMatch) {
      // Default to instagram if just a handle
      return {
        platform: "instagram",
        handle: handleMatch[1],
        source: "instagram",
      };
    }

    return null;
  }
}

export function isValidUrl(str) {
  try {
    new URL(str);
    return true;
  } catch {
    return false;
  }
}
