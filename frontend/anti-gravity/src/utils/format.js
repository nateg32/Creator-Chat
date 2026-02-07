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

    // If it starts with @, preserve it but title case the rest
    if (name.startsWith('@')) {
        return '@' + toTitleCase(name.slice(1));
    }

    return toTitleCase(name);
}

/**
 * Formats message text by replacing any instance of the creator's name
 * with the properly formatted (Title Case) version
 */
export function formatMessageText(text, creatorName) {
    if (!text || !creatorName) return text;

    const formattedName = formatCreatorName(creatorName);

    // Create a regex to match the creator name case-insensitively
    // Escape special regex characters in the name
    const escapedName = creatorName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const regex = new RegExp(escapedName, 'gi');

    return text.replace(regex, formattedName);
}
