function stableIndex(seed = "", length = 1) {
  if (!length) return 0;
  let hash = 0;
  const input = String(seed || "");
  for (let i = 0; i < input.length; i += 1) {
    hash = ((hash << 5) - hash + input.charCodeAt(i)) | 0;
  }
  return Math.abs(hash) % length;
}

function cleanText(value = "") {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function cleanOptions(values = []) {
  const seen = new Set();
  return (values || [])
    .map((value) => cleanText(value))
    .filter((value) => {
      if (!value) return false;
      const key = value.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
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

function extractGreetingExamples(styleFingerprint = {}) {
  const golden = styleFingerprint.golden_examples || {};
  const speech = styleFingerprint.speech_mechanics || {};
  const modeGreeting = (styleFingerprint.mode_matrix || {}).greeting || {};
  const greetingExamples = golden.greeting || [];

  const openerCandidates = cleanOptions([
    ...greetingExamples.map((item) => cleanText(String(item || "").split(/[.!?\n]/)[0] || "")),
    ...(speech.signature_openings || []),
    modeGreeting.opening_move,
  ]);
  const questionCandidates = cleanOptions(
    greetingExamples
      .map((item) => {
        const match = String(item || "").match(/([^?]{4,120}\?)/);
        return match ? cleanText(match[1]) : "";
      })
      .concat(modeGreeting.question_style ? [modeGreeting.question_style] : [])
  );

  return {
    openers: openerCandidates,
    questions: questionCandidates,
  };
}

function extractTopicSeeds(styleFingerprint = {}) {
  const domainMap = styleFingerprint.domain_map || {};
  const valueModel = styleFingerprint.value_model || {};
  const contentTruth = styleFingerprint.content_truth || {};
  const values = cleanOptions([
    ...(domainMap.strong_topics || []),
    ...(styleFingerprint.recurring_themes || []),
    ...(contentTruth.products || []),
    ...(contentTruth.businesses || []),
    ...(valueModel.decision_heuristics || []),
  ]);
  return values.filter((value) => value.split(" ").length <= 7).slice(0, 6);
}

function pickTemplate(vibe, seed, templateMap) {
  const options = templateMap[vibe] || templateMap.general || [];
  return options[stableIndex(seed, options.length)] || "";
}

function buildTopicQuestion(vibe, topic, seed) {
  const cleaned = cleanText(topic);
  if (!cleaned) return "";
  const bank = {
    energetic: [
      `What's the play with ${cleaned} right now?`,
      `Where are you pushing ${cleaned} next?`,
    ],
    supportive: [
      `What feels heaviest around ${cleaned} right now?`,
      `Where does ${cleaned} feel hardest at the moment?`,
    ],
    direct: [
      `Where is ${cleaned} breaking right now?`,
      `What part of ${cleaned} needs tightening?`,
    ],
    analytical: [
      `What part of ${cleaned} needs a cleaner decision?`,
      `Where is ${cleaned} getting muddy right now?`,
    ],
    reflective: [
      `What feels true but unresolved around ${cleaned} right now?`,
      `Where does ${cleaned} feel most important right now?`,
    ],
    general: [
      `What part of ${cleaned} feels most important right now?`,
      `Where are you getting stuck with ${cleaned}?`,
    ],
  };
  const options = bank[vibe] || bank.general;
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
    "I'm {name}. Bring me the question you've got.",
    "I'm {name}. Tell me what needs a clearer next move.",
    "I'm {name}. Bring me what's actually stuck.",
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
    "Ask about their content, a decision you're making, or the thing you're trying to understand better.",
    "Bring me the real question, the messy version, or the thing that keeps snagging your attention.",
  ],
};

export function buildCreatorStarterMessage(creatorName = "Creator", styleFingerprint = {}) {
  const vibe = inferCreatorVibe(styleFingerprint);
  const seed = `${creatorName}|${JSON.stringify(styleFingerprint || {})}`;
  const greetingExamples = extractGreetingExamples(styleFingerprint);
  const topics = extractTopicSeeds(styleFingerprint);

  const opener =
    greetingExamples.openers[stableIndex(`${seed}|opener`, greetingExamples.openers.length)] || "";
  const question =
    greetingExamples.questions[stableIndex(`${seed}|question`, greetingExamples.questions.length)] ||
    buildTopicQuestion(vibe, topics[0], `${seed}|topic-question`);

  if (opener || question) {
    const openerText = opener || `I'm ${creatorName}.`;
    const finalOpener = openerText.endsWith(".") || openerText.endsWith("!") ? openerText : `${openerText}.`;
    return question ? `${finalOpener} ${question}` : finalOpener;
  }

  const template = pickTemplate(vibe, seed, STARTER_TEMPLATES);
  return template.replace("{name}", creatorName || "Creator");
}

export function buildCreatorWelcomeBody(styleFingerprint = {}, creatorName = "Creator") {
  const vibe = inferCreatorVibe(styleFingerprint);
  const seed = `${creatorName}|body|${JSON.stringify(styleFingerprint || {})}`;
  const topics = extractTopicSeeds(styleFingerprint);
  const topicLine = buildTopicQuestion(vibe, topics[0], `${seed}|topic`);
  const template = pickTemplate(vibe, seed, BODY_TEMPLATES);
  return topicLine ? `${template} ${topicLine}` : template;
}
