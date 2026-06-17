import re
import math

class StyleScorer:
    def __init__(self, profile):
        self.profile = profile or {}

    def score_response(self, text):
        s_score = self._structural_score(text)
        l_score = self._lexical_score(text)
        b_score = self._behavioral_score(text)
        
        final_score = (0.35 * s_score) + (0.45 * l_score) + (0.20 * b_score)
        
        return {
            "final_score": round(final_score, 2),
            "structural_score": round(s_score, 2),
            "lexical_score": round(l_score, 2),
            "behavioral_score": round(b_score, 2),
            "passed": final_score >= 0.86,
            "needs_rewrite": 0.65 <= final_score < 0.86
        }

    def _structural_score(self, text):
        sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
        if not sentences: return 0.5
        
        avg_len = sum(len(s.split()) for s in sentences) / len(sentences)
        target = self.profile.get("avg_sentence_length", 12)
        dist = abs(avg_len - target)
        len_score = max(0, 1.0 - (dist / 10.0))
        
        paras = [p for p in text.split('\n\n') if p.strip()]
        avg_para = len(sentences) / max(1, len(paras))
        target_para = self.profile.get("paragraph_length", 2.5)
        para_score = max(0, 1.0 - abs(avg_para - target_para) / 3.0)
        
        return (len_score + para_score) / 2.0

    def _lexical_score(self, text):
        text_lower = text.lower()
        banned = self.profile.get("banned_phrases", ["delves into", "ultimately", "value proposition", "key takeaway", "in conclusion"])
        banned_hits = sum(1 for p in banned if p in text_lower)
        banned_penalty = min(1.0, banned_hits * 0.4)
        
        sigs = self.profile.get("signature_phrases", [])
        sig_hits = sum(1 for p in sigs if p in text_lower)
        sig_boost = min(1.0, sig_hits * 0.2)
        
        return max(0, (1.0 - banned_penalty) * 0.7 + sig_boost * 0.3)

    def _behavioral_score(self, text):
        has_framework = bool(re.search(r'\d+\.', text) or re.search(r'step \d', text, re.I))
        return 1.0 if has_framework else 0.8
