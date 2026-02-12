/**
 * Image utilities for Creator Bot.
 * - resizeImage: small avatar thumbnails (existing)
 * - compressChatImage: larger chat images with quality + size control
 */

// ── Avatar resize (existing, kept as-is) ──────────────────────────────
export function resizeImage(file, maxWidth = 128, maxHeight = 128) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.readAsDataURL(file);
        reader.onload = (event) => {
            const img = new Image();
            img.src = event.target.result;
            img.onload = () => {
                const canvas = document.createElement("canvas");
                let width = img.width;
                let height = img.height;

                if (width > height) {
                    if (width > maxWidth) {
                        height *= maxWidth / width;
                        width = maxWidth;
                    }
                } else {
                    if (height > maxHeight) {
                        width *= maxHeight / height;
                        height = maxHeight;
                    }
                }

                canvas.width = width;
                canvas.height = height;
                const ctx = canvas.getContext("2d");
                ctx.drawImage(img, 0, 0, width, height);

                // Convert to base64
                resolve(canvas.toDataURL("image/jpeg", 0.8));
            };
            img.onerror = () => reject(new Error("Failed to load image"));
        };
        reader.onerror = () => reject(new Error("Failed to read file"));
    });
}

// ── Chat image compression ────────────────────────────────────────────
// Max dimensions for vision API (keeps quality high while controlling cost)
const CHAT_IMAGE_MAX_SIDE = 1568;  // OpenAI vision recommended max
const CHAT_IMAGE_MAX_BYTES = 10 * 1024 * 1024; // 10 MB hard cap
const CHAT_IMAGE_QUALITY = 0.85;

/**
 * Compress an image file for chat attachment.
 * Returns { dataUrl, width, height, originalName, sizeKB }.
 * Throws if the file is not a valid image.
 */
export async function compressChatImage(file) {
    // Validate type
    const ALLOWED = ["image/jpeg", "image/png", "image/webp"];
    if (!ALLOWED.includes(file.type)) {
        throw new Error(`Unsupported image type: ${file.type}. Use JPG, PNG, or WebP.`);
    }

    // Validate size (pre-compression)
    if (file.size > CHAT_IMAGE_MAX_BYTES) {
        throw new Error(`Image too large (${(file.size / 1024 / 1024).toFixed(1)} MB). Max is 10 MB.`);
    }

    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.readAsDataURL(file);
        reader.onload = (event) => {
            const img = new Image();
            img.src = event.target.result;
            img.onload = () => {
                let { width, height } = img;

                // Scale down if either side exceeds max
                if (width > CHAT_IMAGE_MAX_SIDE || height > CHAT_IMAGE_MAX_SIDE) {
                    const ratio = Math.min(
                        CHAT_IMAGE_MAX_SIDE / width,
                        CHAT_IMAGE_MAX_SIDE / height
                    );
                    width = Math.round(width * ratio);
                    height = Math.round(height * ratio);
                }

                const canvas = document.createElement("canvas");
                canvas.width = width;
                canvas.height = height;
                const ctx = canvas.getContext("2d");
                ctx.drawImage(img, 0, 0, width, height);

                // Convert to JPEG for consistent compression
                const dataUrl = canvas.toDataURL("image/jpeg", CHAT_IMAGE_QUALITY);
                const sizeKB = Math.round((dataUrl.length * 3) / 4 / 1024); // estimate base64 → bytes

                resolve({
                    dataUrl,
                    width,
                    height,
                    originalName: file.name,
                    sizeKB,
                });
            };
            img.onerror = () => reject(new Error("Failed to load image. The file may be corrupted."));
        };
        reader.onerror = () => reject(new Error("Failed to read image file."));
    });
}
