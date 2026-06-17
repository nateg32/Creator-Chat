const PARTICLES = new Set([
    "van", "von", "de", "del", "da", "di", "la", "le", "du", "der", "den", "ten", "ter", "bin", "ibn", "al"
]);

const SUFFIXES = new Map([
    ["jr", "Jr."],
    ["sr", "Sr."],
    ["ii", "II"],
    ["iii", "III"],
    ["iv", "IV"],
    ["v", "V"],
]);

const WS_RE = /\s+/g;
const HTMLISH_RE = /[<>]/;
const RIGHT_APOSTROPHE = "\u2019";
const ALLOWED_RE = /^[\w\s.'\u2019&-]+$/u;

function hasLetter(s) {
    for (const ch of s) {
        if (/\p{L}/u.test(ch)) return true;
    }
    return false;
}

function stripControlCharacters(value) {
    return Array.from(value).filter((ch) => {
        const code = ch.charCodeAt(0);
        return code > 31 && code !== 127;
    }).join("");
}

function punctRatio(s) {
    if (!s) return 1;
    let punct = 0;
    for (const ch of s) {
        if (/\p{P}|\p{S}/u.test(ch)) punct++;
    }
    return punct / s.length;
}

function isAllCapsAcronym(token) {
    if (token.length < 2 || token.length > 10) return false;
    if (!/^[A-Z0-9]+$/.test(token)) return false;
    return /[A-Z]/.test(token);
}

function isMixedCase(token) {
    return /[a-z]/.test(token) && /[A-Z]/.test(token);
}

function titlecaseWord(word) {
    if (!word) return word;
    const w = word.replaceAll(RIGHT_APOSTROPHE, "'");
    const parts = w.split(/([-'])/);
    return parts.map(p => {
        if (p === "-" || p === "'") return p;
        if (isAllCapsAcronym(p)) return p;
        return p ? p[0].toUpperCase() + p.slice(1).toLowerCase() : p;
    }).join("");
}

export function normalizeCreatorName(raw) {
    if (raw == null) {
        return { normalized: null, isValid: false, error: "Enter a creator name.", suggested: null, flags: { changed: false } };
    }

    let s = String(raw);
    s = stripControlCharacters(s.normalize("NFKC")).trim().replace(WS_RE, " ");

    const original = s;

    if (!s) return { normalized: null, isValid: false, error: "Enter a creator name.", suggested: null, flags: { changed: false } };
    if (s.length < 2) return { normalized: null, isValid: false, error: "Name is too short.", suggested: null, flags: { changed: false } };
    if (s.length > 80) return { normalized: null, isValid: false, error: "Name is too long.", suggested: null, flags: { changed: false } };
    if (HTMLISH_RE.test(s)) return { normalized: null, isValid: false, error: "Name contains invalid characters.", suggested: null, flags: { changed: false } };
    if (!hasLetter(s)) return { normalized: null, isValid: false, error: "Name must include letters.", suggested: null, flags: { changed: false } };
    if (!ALLOWED_RE.test(s)) return { normalized: null, isValid: false, error: "Name contains invalid characters.", suggested: null, flags: { changed: false } };
    if (punctRatio(s) > 0.25) return { normalized: null, isValid: false, error: "Name contains too much punctuation.", suggested: null, flags: { changed: false } };

    const tokens = s.split(" ");
    const flags = { changed: false, likelyAcronym: false };

    if (tokens.length === 1) {
        const t = tokens[0].replaceAll(RIGHT_APOSTROPHE, "'");

        if (isMixedCase(t) || isAllCapsAcronym(t)) {
            flags.changed = (t !== original);
            return { normalized: t, isValid: true, error: null, suggested: null, flags };
        }

        if (/^[a-z0-9]{2,10}$/.test(t) && t === t.toLowerCase()) {
            const suggested = t.toUpperCase();
            const normalized = titlecaseWord(t);
            flags.likelyAcronym = true;
            flags.changed = (normalized !== original);
            return { normalized, isValid: true, error: null, suggested, flags };
        }

        const normalized = titlecaseWord(t);
        flags.changed = (normalized !== original);
        return { normalized, isValid: true, error: null, suggested: null, flags };
    }

    const out = tokens.map((token, i) => {
        const clean = token.replaceAll(RIGHT_APOSTROPHE, "'");

        if (i === tokens.length - 1) {
            const k = clean.replaceAll(".", "").toLowerCase();
            if (SUFFIXES.has(k)) return SUFFIXES.get(k);
        }

        if (isMixedCase(clean) || isAllCapsAcronym(clean)) return clean;

        const low = clean.toLowerCase();
        if (i !== 0 && PARTICLES.has(low)) return low;

        return titlecaseWord(clean);
    });

    const normalized = out.join(" ");
    flags.changed = (normalized !== original);
    return { normalized, isValid: true, error: null, suggested: null, flags };
}
