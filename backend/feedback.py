import re



# ─────────────────────────────────────────────
# FILLER WORD DETECTION (done in code, not LLM)
# ─────────────────────────────────────────────

FILLER_WORDS = [
    "um", "uh", "uhh", "umm", "hmm",
    "like", "basically", "literally",
    "you know", "i mean", "kind of", "sort of",
    "right", "okay so", "so yeah", "actually",
    "anyway", "whatever"
]

def count_filler_words(transcript: str) -> dict:
    """
    Counts filler word occurrences in transcript.
    Returns count per filler and a total, and a 1–10 score
    (10 = very fluent, few fillers).
    """
    text = transcript.lower()
    counts = {}
    total = 0

    for filler in FILLER_WORDS:
        # word boundary match
        pattern = r'\b' + re.escape(filler) + r'\b'
        matches = re.findall(pattern, text)
        if matches:
            counts[filler] = len(matches)
            total += len(matches)

    word_count = len(text.split())
    if word_count == 0:
        filler_score = 5
    else:
        filler_ratio = total / word_count
        # 0% fillers → 10, 20%+ fillers → 1
        filler_score = max(1, round(10 - (filler_ratio * 50)))
        filler_score = min(10, filler_score)

    return {
        "breakdown": counts,
        "total": total,
        "score": filler_score
    }


# ─────────────────────────────────────────────
# RUBRIC-BASED PROMPT (structured, consistent)
# ─────────────────────────────────────────────

ANALYSIS_PROMPT = """
You are an expert speech coach evaluating a speaker's performance.

Analyze the transcript below using these EXACT scoring rubrics:

FLUENCY (1–10):
- 9–10: Smooth, natural flow, no awkward pauses
- 7–8: Minor stumbles, mostly smooth
- 5–6: Noticeable pauses, some repetition
- 3–4: Frequent breaks, hard to follow
- 1–2: Very broken, barely coherent

CLARITY (1–10):
- 9–10: Every point is precise and well-structured
- 7–8: Most ideas are clear, minor vagueness
- 5–6: Some ideas unclear or underdeveloped
- 3–4: Difficult to understand the main message
- 1–2: No clear message at all

CONFIDENCE (1–10):
- 9–10: Strong assertive tone, no hedging
- 7–8: Mostly confident, minor uncertainty
- 5–6: Noticeable hedging ("I think maybe...", "I'm not sure but...")
- 3–4: Frequent self-doubt, weak delivery
- 1–2: Very timid, apologetic tone throughout

ON_TOPIC (1–10):
- 9–10: Every sentence directly addresses the topic
- 7–8: Mostly on topic, minor tangents
- 5–6: Drifts off topic occasionally
- 3–4: Frequently off topic
- 1–2: Barely addresses the topic

VOCABULARY (1–10):
- 9–10: Rich, varied, precise word choices
- 7–8: Good vocabulary, some repetition
- 5–6: Basic vocabulary, frequent repetition
- 3–4: Very limited vocabulary
- 1–2: Extremely basic or incoherent

Transcript:
\"\"\"{transcript}\"\"\"

Respond in THIS EXACT FORMAT only. No extra text:

PROS:
- [specific observation about what the speaker did well]
- [another specific strength]
- [another specific strength]

CONS:
- [specific, actionable weakness]
- [another actionable weakness]
- [another actionable weakness]

SCORES:
fluency: X/10
clarity: X/10
confidence: X/10
on_topic: X/10
vocabulary: X/10

IMPROVEMENT_TIP:
[One concrete, specific tip the speaker can apply in their next session]
"""


def get_llm_analysis(transcript: str, client) -> str:
    completion = client.chat.completions.create(
        model="llama-3.3-70b-versatile",  # upgrade from 8b → much better quality
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a professional speech coach. "
                    "You give honest, structured, actionable feedback. "
                    "You always follow the exact format given. "
                    "You never add extra commentary outside the format."
                )
            },
            {
                "role": "user",
                "content": ANALYSIS_PROMPT.format(transcript=transcript)
            }
        ],
        temperature=0.4,   # lower = more consistent, less hallucination
        max_tokens=600,
    )
    return completion.choices[0].message.content.strip()


# ─────────────────────────────────────────────
# PARSER (robust, handles LLM quirks)
# ─────────────────────────────────────────────

def normalize_key(key: str) -> str:
    return key.lower().replace("-", "_").replace(" ", "_").strip()


def parse_analysis(text: str) -> dict:
    pros = []
    cons = []
    scores = {}
    improvement_tip = ""
    section = None

    for raw_line in text.splitlines():
        line = raw_line.strip().replace("**", "")

        if not line:
            continue

        upper = line.upper()

        if upper.startswith("PROS"):
            section = "pros"
            continue
        if upper.startswith("CONS"):
            section = "cons"
            continue
        if upper.startswith("SCORES"):
            section = "scores"
            continue
        if upper.startswith("IMPROVEMENT_TIP") or upper.startswith("IMPROVEMENT TIP"):
            section = "tip"
            continue

        if section in ["pros", "cons"] and line.startswith("-"):
            point = line.lstrip("-").strip()
            if section == "pros":
                pros.append(point)
            else:
                cons.append(point)

        elif section == "scores" and ":" in line:
            line = line.lstrip("-").strip()
            key, val = line.split(":", 1)
            key = normalize_key(key)
            nums = re.findall(r"\d+", val)
            scores[key] = int(nums[0]) if nums else 5

        elif section == "tip" and line:
            improvement_tip += line + " "

    return {
        "pros": pros or ["Speech was recorded successfully."],
        "cons": cons or ["More detail needed for deeper analysis."],
        "scores": {
            "fluency":    scores.get("fluency", 5),
            "clarity":    scores.get("clarity", 5),
            "confidence": scores.get("confidence", 5),
            "on_topic":   scores.get("on_topic", 5),
            "vocabulary": scores.get("vocabulary", 5),
        },
        "improvement_tip": improvement_tip.strip() or "Practice speaking for 2 minutes daily on any topic."
    }


# ─────────────────────────────────────────────
# MAIN FUNCTION — call this from your FastAPI route
# ─────────────────────────────────────────────

def analyze_speech_full(transcript: str, client) -> dict:
    """
    Full analysis pipeline:
    1. Filler word detection (in code)
    2. LLM rubric-based analysis
    3. Merge results
    """
    filler_data = count_filler_words(transcript)
    raw_llm = get_llm_analysis(transcript, client)
    
    # Step 1: filler words (accurate, no LLM needed)
    parsed = parse_analysis(raw_llm)

    # Step 3: merge filler score into scores
    parsed["scores"]["filler_words"] = filler_data["score"]
    parsed["filler_details"] = {
        "total_count": filler_data["total"],
        "breakdown": filler_data["breakdown"]
    }

    # Step 4: overall score (average of all 6)
    all_scores = list(parsed["scores"].values())
    parsed["overall_score"] = round(sum(all_scores) / len(all_scores), 1)

    return parsed