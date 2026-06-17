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
const splitShortSuffixRe = /\b([A-Za-z]{2,4})\s+(ing|ings|ed|er|ers|est|ly)\b(?=\s+[A-Za-z]{2,})/gi;
const splitPrefixMergedSuffixRe = /(?<!')\b([A-Za-z]{2,4})\s+([a-z]{4,}(?:your|the))\b(?=(?:\s+[A-Za-z]{2,}\b|[,.!?;:]|$))/gi;
const mergedSingleHeadRe = /\b(I)([a-z]{3,})\b/g;
const mergedArticleHeadRe = /\b(A)(free|few|lot|little|long|short|big|small|new|good|bad|clear|single|simple)\b/gi;
const mergedCommonHeadRe = /\b(My|Your|Our|Their|This|That|These|Those|We|You)([a-z]{4,})\b/g;
const mergedFocusedSuffixRe = /\b([A-Za-z]{4,})(your|the)\b(?=(?:\s+[A-Za-z]{2,}\b|[,.!?;:]|$))/gi;
const contractionBoundaryRe = /((?:'s|'re|'ve|'ll|'d|'m))(?=(?:[a-z]{4,}|you|your|the|that|this|it|we|they|he|she|who|what|when|where|why)\b)/gi;
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
const iSplitWords = new Set([
    ...mergeableCommonWords,
    "attach", "attached", "attaching",
    "think", "want", "know", "love", "need", "like", "feel", "believe",
    "remember", "understand", "mean", "see", "hear", "hope", "wish",
    "guess", "got", "get", "really", "also", "always", "actually",
    "agree", "had", "may", "might", "said", "say", "told", "tell",
    "tried", "try", "used", "usually", "went", "made", "make",
    "build", "building", "built", "coach", "coaching", "coached",
    "talk", "talked", "thought", "found", "keep", "kept", "left",
    "live", "lived", "look", "looked", "met", "moved", "play",
    "read", "run", "saw", "started", "took", "work", "worked",
]);
const mergeableConnectorSuffixes = ["and"];
const mergedTokenBlocklist = new Set([
    "command", "commands", "demand", "demands", "expand", "expands", "grand", "brand",
    "island", "remand", "remands", "strand", "strands",
]);
const mergedTrailingBlocklist = new Set([
    "software", "hardware", "aware", "beware", "elsewhere", "somewhere", "anywhere", "nowhere",
    "everywhere", "somewhat", "lathe", "loathe", "clothe", "unclothe", "writhe",
    "scythe", "soothe", "seethe", "bathe", "breathe", "blithe",
]);
const splitBrandFixes = [
    [/\bI\s+nstagram\b/gi, "Instagram"],
    [/\bYou\s+Tube\b/gi, "YouTube"],
    [/\bTik\s+Tok\b/gi, "TikTok"],
    [/\bSnap\s+chat\b/gi, "Snapchat"],
    [/\bFace\s+book\b/gi, "Facebook"],
    [/\bLinked\s+In\b/gi, "LinkedIn"],
    [/\bOpen\s+AI\b/gi, "OpenAI"],
    [/\bChat\s+GPT\b/gi, "ChatGPT"],
    [/\bPay\s+Pal\b/gi, "PayPal"],
    [/\bMac\s+Book\b/gi, "MacBook"],
    [/\bi\s+Phone\b/g, "iPhone"],
];

function repairKnownBrandSpacing(text) {
    return splitBrandFixes.reduce((output, [pattern, replacement]) => {
        return output.replace(pattern, replacement);
    }, text);
}

function _repairSplitWordFragments(text) {
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
            })
            .replace(splitShortSuffixRe, (_, word, tail) => {
                return `${word}${tail}`;
            })
            .replace(splitPrefixMergedSuffixRe, (match, word, tail) => {
                return commonShortWords.has(word.toLowerCase()) ? match : `${word}${tail}`;
            });
        if (next === repaired) return repaired;
        repaired = next;
    }
}

function _repairMergedCommonWordPairs(text) {
    return text
        .replace(mergedSingleHeadRe, (match, head, tail) => {
            return iSplitWords.has(tail.toLowerCase()) ? `${head} ${tail}` : match;
        })
        .replace(mergedArticleHeadRe, (_, head, tail) => `${head} ${tail}`)
        .replace(mergedCommonHeadRe, (_, head, tail) => `${head} ${tail}`)
        .replace(mergedFocusedSuffixRe, (match, left, right) => {
            return mergedTrailingBlocklist.has(match.toLowerCase()) ? match : `${left} ${right}`;
        })
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

    // Keep this display formatter conservative. The backend now owns response
    // formatting, and aggressive word "repair" can corrupt valid text such as
    // "I appreciate" into "Iappreciate".
    return restoreSpans(repairKnownBrandSpacing(repaired), protectedSpans);
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
