/**
 * Converts a string to Title Case format
 * Examples: "john smith" -> "John Smith"
 *           "ALEX HORMOZI" -> "Alex Hormozi"
 *           "jane-doe" -> "Jane-Doe"
 */
export function toTitleCase(str) {
    if (!str) return str;

    return str
        .toLowerCase()
        .split(' ')
        .map(word => {
            if (word.length === 0) return word;
            return word.charAt(0).toUpperCase() + word.slice(1);
        })
        .join(' ');
}

/**
 * Formats a creator's display name to Title Case
 * Handles both names and handles (e.g., @username)
 */
export function formatCreatorName(name) {
    if (!name) return '';

    if (name.startsWith('@')) {
        return '@' + toTitleCase(name.slice(1));
    }

    return toTitleCase(name);
}

function protectSpans(text) {
    const protectedSpans = [];
    const tokenized = text.replace(/\[[^\]]+\]\(https?:\/\/[^\s)]+\)|https?:\/\/[^\s)]+/gi, (match) => {
        const token = `__CB_PROTECTED_${protectedSpans.length}__`;
        protectedSpans.push({ token, value: match });
        return token;
    });
    return { tokenized, protectedSpans };
}

function restoreSpans(text, protectedSpans) {
    return protectedSpans.reduce((output, span) => output.replace(span.token, span.value), text);
}

export function repairDisplaySpacing(text) {
    if (!text) return text;

    const { tokenized, protectedSpans } = protectSpans(text);
    const repaired = tokenized
        .replace(/^(\s*\d+[.)])(?=\S)/gm, '$1 ')
        .replace(/([A-Za-z])(?=([1-3]?\d{1,3}:\d{1,3}(?:-\d{1,3})?))/g, '$1 ')
        .replace(/([A-Za-z])(?=((?:www\.)?(?:\d|[A-Z])[A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)+(?:\/[^\s]*)?))/g, '$1 ')
        .replace(/[ \t]{2,}/g, ' ');

    return restoreSpans(repaired, protectedSpans);
}

/**
 * Formats message text by replacing any instance of the creator's name
 * with the properly formatted (Title Case) version
 */
export function formatMessageText(text, creatorName) {
    if (!text) return text;

    let formattedText = repairDisplaySpacing(text);
    if (!creatorName) return formattedText;

    const formattedName = formatCreatorName(creatorName);
    const escapedName = creatorName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const regex = new RegExp(escapedName, 'gi');

    return formattedText.replace(regex, formattedName);
}
