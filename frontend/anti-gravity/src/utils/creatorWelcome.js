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

  // Only accept question_style if it's a real question (ends with ?) and isn't
  // a style description like "Bring me the real question, the messy version..."
  const rawQS = String(modeGreeting.question_style || "").trim();
  const questionStyleValid = rawQS.endsWith("?") && rawQS.split(" ").length <= 15;

  const questionCandidates = cleanOptions(
    greetingExamples
      .map((item) => {
        const match = String(item || "").match(/([^?]{4,120}\?)/);
        return match ? cleanText(match[1]) : "";
      })
      .concat(questionStyleValid ? [rawQS] : [])
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
  // Strip trailing punctuation and normalize casing for natural sentence embedding
  const cleaned = cleanText(topic).replace(/[.!?,;:]+$/, "").toLowerCase();
  if (!cleaned) return "";
  const bank = {
    energetic: [
      `What are you building with ${cleaned} right now?`,
      `Where are you pushing ${cleaned}?`,
    ],
    supportive: [
      `What's weighing on you around ${cleaned}?`,
      `Where does ${cleaned} feel hardest right now?`,
    ],
    direct: [
      `Where are you stuck with ${cleaned}?`,
      `What's the bottleneck with ${cleaned}?`,
    ],
    analytical: [
      `What part of ${cleaned} needs a decision right now?`,
      `Where is ${cleaned} getting complicated?`,
    ],
    reflective: [
      `What feels unresolved around ${cleaned}?`,
      `Where does ${cleaned} feel most important right now?`,
    ],
    general: [
      `What are you working on with ${cleaned}?`,
      `Where are you at with ${cleaned}?`,
    ],
  };
  const options = bank[vibe] || bank.general;
  return options[stableIndex(seed, options.length)] || "";
}

const STARTER_TEMPLATES = {
  energetic: [
    "I'm {name}. What are you building right now?",
    "I'm {name}. What's the move?",
    "I'm {name}. What are you working on?",
  ],
  supportive: [
    "I'm {name}. What's on your mind?",
    "I'm {name}. What are you working through?",
    "I'm {name}. What do you need help with?",
  ],
  direct: [
    "I'm {name}. What do you need?",
    "I'm {name}. What are you trying to figure out?",
    "I'm {name}. What's the question?",
  ],
  analytical: [
    "I'm {name}. What are you trying to solve?",
    "I'm {name}. What's the problem?",
    "I'm {name}. What decision are you sitting on?",
  ],
  reflective: [
    "I'm {name}. What's on your mind?",
    "I'm {name}. What are you thinking about?",
    "I'm {name}. What's weighing on you?",
  ],
  general: [
    "I'm {name}. What are you working on?",
    "I'm {name}. What do you need help with?",
    "I'm {name}. What's on your mind?",
  ],
};

const BODY_TEMPLATES = {
  energetic: [
    "What are you working on right now?",
    "What's the goal right now?",
  ],
  supportive: [
    "What's on your mind?",
    "What are you working through right now?",
  ],
  direct: [
    "What do you need help with?",
    "What are you trying to figure out?",
  ],
  analytical: [
    "What problem are you trying to solve?",
    "What decision are you sitting on right now?",
  ],
  reflective: [
    "What's on your mind right now?",
    "What are you trying to figure out?",
  ],
  general: [
    "What are you working on?",
    "What do you need help with?",
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
  return "Ask me anything about my content.";
}
