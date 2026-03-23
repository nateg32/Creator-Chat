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

const letterEndPunctBoundaryRe = /([A-Za-z][.!?])(?=[A-Z0-9])/g;
const splitHeadRe = /(^|[\n([{"])([A-Za-z])\s+([a-z]{3,})(?=\b)/gm;
const splitMiddleRe = /\b([A-Za-z]{2,})\s+([aeiou])\b(?=\s+[A-Za-z]{2,}\s+[bcdfghjklmnpqrstvwxyz]\b)/gi;
const splitTailRe = /\b([A-Za-z]{2,})\s+([bcdfghjklmnpqrstvwxyz])\b/gi;
const splitSuffixRe = /\b([A-Za-z]{3,})\s+(ify|ifies|ified|ifying|ise|ises|ised|ising|ize|izes|ized|izing|ation|ations|ment|ments|ness|less|able|ably|ible|ibly|ally|fully|ously|ship|ships|ward|wards)\b/gi;
const mergedSingleHeadRe = /\b([AI])([a-z]{3,})\b/g;
const mergedCommonHeadRe = /\b(My|Your|Our|Their|This|That|These|Those|We|You)([a-z]{4,})\b/g;
const contractionBoundaryRe = /((?:'s|'re|'ve|'ll|'d|'m))(?=(?:you|your|the|that|this|it|we|they|he|she|who|what|when|where|why)\b)/gi;
const mergedCommonTokenRe = /\b[A-Za-z]{4,24}\b/g;
const commonShortWords = new Set([
    "a", "i", "an", "as", "at", "be", "by", "do", "go", "he", "if", "in", "is",
    "it", "me", "my", "no", "of", "on", "or", "so", "to", "up", "us", "we",
    "for", "and", "but", "not", "the", "you", "your",
]);
const mergeableCommonWords = new Set([
    ...commonShortWords,
    "are", "been", "before", "being", "because", "between", "can", "could", "did",
    "does", "every", "from", "have", "here", "how", "into", "just", "more", "much",
    "must", "never", "now", "onto", "only", "over", "right", "should", "since",
    "still", "than", "that", "their", "them", "then", "there", "these", "they",
    "this", "those", "through", "under", "until", "very", "was", "were", "what",
    "when", "where", "which", "while", "who", "why", "will", "with", "without",
    "would",
]);
const mergeableConnectorSuffixes = ["and"];
const mergedTokenBlocklist = new Set([
    "command", "commands", "demand", "demands", "expand", "expands", "grand", "brand",
    "island", "remand", "remands", "strand", "strands",
]);

function repairSplitWordFragments(text) {
    let repaired = text.replace(splitHeadRe, (_, prefix, head, tail) => `${prefix}${head}${tail}`);

    while (true) {
        const next = repaired
            .replace(splitMiddleRe, (match, word, tail) => {
                return commonShortWords.has(word.toLowerCase()) ? match : `${word}${tail}`;
            })
            .replace(splitTailRe, (match, word, tail) => {
                return commonShortWords.has(word.toLowerCase()) ? match : `${word}${tail}`;
            })
            .replace(splitSuffixRe, (match, word, tail) => {
                return commonShortWords.has(word.toLowerCase()) ? match : `${word}${tail}`;
            });
        if (next === repaired) return repaired;
        repaired = next;
    }
}

function repairMergedCommonWordPairs(text) {
    return text
        .replace(mergedSingleHeadRe, (_, head, tail) => `${head} ${tail}`)
        .replace(mergedCommonHeadRe, (_, head, tail) => `${head} ${tail}`)
        .replace(mergedCommonTokenRe, (token) => {
        const lower = token.toLowerCase();
        if (mergeableCommonWords.has(lower)) return token;

        for (let index = 2; index < token.length - 1; index += 1) {
            const left = lower.slice(0, index);
            const right = lower.slice(index);
            if (mergeableCommonWords.has(left) && mergeableCommonWords.has(right)) {
                return `${token.slice(0, index)} ${token.slice(index)}`;
            }
        }

        if (!mergedTokenBlocklist.has(lower)) {
            for (const suffix of mergeableConnectorSuffixes) {
                if (lower.endsWith(suffix)) {
                    const left = lower.slice(0, -suffix.length);
                    if (left.length >= 4 && /[aeiou]/i.test(left)) {
                        return `${token.slice(0, left.length)} ${token.slice(left.length)}`;
                    }
                }
            }
        }

        return token;
    });
}

export function repairDisplaySpacing(text) {
    if (!text) return text;

    const { tokenized, protectedSpans } = protectSpans(text);
    const repaired = tokenized
        .replace(/^(\s*\d+[.)])(?=\S)/gm, '$1 ')
        .replace(letterEndPunctBoundaryRe, '$1 ')
        .replace(/([A-Za-z])(?=([1-3]?\d{1,3}:\d{1,3}(?:-\d{1,3})?))/g, '$1 ')
        .replace(/(?<=[A-Za-z])(?=\d{1,4}(?=(?:\s|[,.;:!?)]|$)))/g, ' ')
        .replace(/(?<=[A-Za-z])(?=\d{1,4}(?:s|x|st|nd|rd|th)(?=(?:\s|[,.;:!?)]|$)))/gi, ' ')
        .replace(/(?<=\d)(?=[A-Za-z]{2,}(?=(?:\s|[,;:!?)]|$)))/g, ' ')
        .replace(/([A-Za-z])(?=((?:www\.)?(?:\d|[A-Z])[A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)+(?:\/[^\s]*)?))/g, '$1 ')
        .replace(contractionBoundaryRe, '$1 ')
        .replace(/[ \t]+([,.;:!?])/g, '$1')
        .replace(/[ \t]{2,}/g, ' ');

    return restoreSpans(repairMergedCommonWordPairs(repairSplitWordFragments(repaired)), protectedSpans);
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
