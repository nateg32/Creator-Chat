function stableIndex(seed = "", length = 1) {
  if (!length) return 0;
  let hash = 0;
  const input = String(seed || "");
  for (let i = 0; i < input.length; i += 1) {
    hash = ((hash << 5) - hash + input.charCodeAt(i)) | 0;
  }
  return Math.abs(hash) % length;
}

function collectStyleText(styleFingerprint = {}) {
  const identity = styleFingerprint.identity_signature || {};
  const worldview = styleFingerprint.worldview || {};
  const lexical = styleFingerprint.lexical_rules || {};

  return [
    ...(styleFingerprint.traits || []),
    ...(styleFingerprint.signature_moves || []),
    ...(styleFingerprint.signature_phrases || []),
    ...(worldview.core_beliefs || []),
    ...(lexical.signature_phrases || []),
    identity.self_concept,
    identity.mission_frame,
    identity.power_position,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function inferCreatorVibe(styleFingerprint = {}) {
  const text = collectStyleText(styleFingerprint);
  if (!text) return "general";
  if (/(energy|hype|aggressive|intense|bold|competitive|relentless|ambitious|winning|momentum)/.test(text)) return "energetic";
  if (/(care|support|gentle|warm|compassion|empathetic|healing|kind|safe)/.test(text)) return "supportive";
  if (/(direct|blunt|operator|discipline|standards|hard truth|no excuses|clarity)/.test(text)) return "direct";
  if (/(systems|strategy|framework|analyze|first principles|logic|diagnose|precision)/.test(text)) return "analytical";
  if (/(story|narrative|journey|meaning|identity|belief|purpose|faith|spiritual)/.test(text)) return "reflective";
  return "general";
}

function pickTemplate(vibe, seed, templateMap) {
  const options = templateMap[vibe] || templateMap.general || [];
  return options[stableIndex(seed, options.length)] || "";
}

const STARTER_TEMPLATES = {
  energetic: [
    "I'm {name}. Let's get after it.",
    "I'm {name}. Bring me the big swing.",
    "I'm {name}. Let's build some momentum.",
  ],
  supportive: [
    "I'm {name}. Tell me what's been weighing on you.",
    "I'm {name}. Bring me the messy version.",
    "I'm {name}. Tell me what you need help carrying.",
  ],
  direct: [
    "I'm {name}. Bring me the real question.",
    "I'm {name}. Let's get straight to the problem.",
    "I'm {name}. Give me the part that's actually stuck.",
  ],
  analytical: [
    "I'm {name}. Give me the problem and we'll break it down.",
    "I'm {name}. Bring me the puzzle, not the polished version.",
    "I'm {name}. Let's sort the signal from the noise.",
  ],
  reflective: [
    "I'm {name}. Tell me where your head is at.",
    "I'm {name}. Bring me what feels heavy or unclear.",
    "I'm {name}. Tell me what you're trying to make sense of.",
  ],
  general: [
    "I'm {name}. Tell me what you're trying to figure out.",
    "I'm {name}. What's on your mind?",
    "I'm {name}. Bring me the question you've got.",
  ],
};

const BODY_TEMPLATES = {
  energetic: [
    "Bring me the goal, the bottleneck, or the wild idea. We'll get moving fast.",
    "Come in with the big ambition or the ugly blocker. We'll turn it into a next move.",
  ],
  supportive: [
    "Bring the unfinished thought. We can unpack ideas, hard decisions, and whatever feels tangled.",
    "You do not need the polished version. Start where you are and we'll work from there.",
  ],
  direct: [
    "Skip the fluff. I can help pressure-test ideas, decisions, and the next move that actually matters.",
    "Bring me the hard question, the blocker, or the thing you keep avoiding. We'll deal with it head on.",
  ],
  analytical: [
    "Give me the raw problem. I can help break it down, inspect it, and find the clearest next step.",
    "Bring the messy inputs. We'll sort the pattern, the leverage point, and the next decision.",
  ],
  reflective: [
    "Bring the question beneath the question. We can work through meaning, direction, and what feels true.",
    "If something feels foggy, heavy, or important, start there. We'll make sense of it together.",
  ],
  general: [
    "I can help unpack ideas, answer questions from the creator's content, or just talk through what's on your mind.",
    "Ask about their content, a decision you're making, or the thing you're trying to understand better.",
  ],
};

export function buildCreatorStarterMessage(creatorName = "Creator", styleFingerprint = {}) {
  const vibe = inferCreatorVibe(styleFingerprint);
  const seed = `${creatorName}|${JSON.stringify(styleFingerprint || {})}`;
  const template = pickTemplate(vibe, seed, STARTER_TEMPLATES);
  return template.replace("{name}", creatorName || "Creator");
}

export function buildCreatorWelcomeBody(styleFingerprint = {}, creatorName = "Creator") {
  const vibe = inferCreatorVibe(styleFingerprint);
  const seed = `${creatorName}|body|${JSON.stringify(styleFingerprint || {})}`;
  return pickTemplate(vibe, seed, BODY_TEMPLATES);
}
