import json

# --- 1. THE PHYSICS (Language Rules) ---
# These apply to EVERYONE speaking this language.

LANGUAGES = {
    "en": {
        "name": "English",
        "bible": "English Standard Version (ESV)",
        "god_address": "Reverent second-person (You/Your) capitalized when customary",
        "human_address": "Natural broadcast English",
        "music_prompt": "(MUSIC)",
        "conjunctions": ["and", "but", "that", "or", "because", "so"],
        "abbreviations": {},
        "base_prompt": """
        1. THEOLOGICAL ACCURACY (Strict):
           - Use **English Standard Version (ESV)** for scripture.
           - Honorifics: God -> capitalized pronouns where customary.
           
        2. BROADCAST CLARITY:
           - Clear, neutral English; remove filler and keep phrasing concise.
        """
    },
    "is": {
        "name": "Icelandic",
        "bible": "Biblían 2007",
        "god_address": "Þú (Broadcast Standard)",
        "human_address": "Þú (Casual)",
        "music_prompt": "(MUSIC)",
        "conjunctions": ["og", "en", "sem", "að", "eða", "því"],
        "abbreviations": {
            r"(?i)Fyrra Korintubréfi": "1. Kor.",
            r"(?i)Síðara Korintubréfi": "2. Kor.",
            # ... (Full list from finalizer.py) ...
        },
        "base_prompt": """
        1. THEOLOGICAL ACCURACY (Strict):
           - God is addressed as "Þú".
           - Do NOT use "Þér" for God.
           - Humans/Friends are addressed as "Þú" (Casual).
           - Never use "ég er" for God's title; use "ÉG ER".
           
        2. SCRIPTURE PROTOCOL:
           - Use **Biblían 2007**.
           - If English speaker paraphrases, RECALL the official Icelandic verse.
           
        3. AVOID ANGLICISMS & ROBOTIC FLOW:
           - "Died for you" -> "Dó vegna þín".
           - "On fire" -> "Brennandi" (NOT "á eldi").
           - **NATURAL FLOW**: Avoid literal "We have received/gotten" (Við höfum fengið) for weather or states. Use existential forms: "It has been/there is" (Það hefur verið / Það er).
        
        4. TERMINOLOGY:
           - "Pastor" -> "Prestur".
           - "Hallowed" -> "Heilagt" (NOT "halls", "halloween").
           - "Hold" -> "Tak" or "Halda" (NOT "hole").
        """
    },
    "es": {
        "name": "Spanish",
        "bible": "Reina-Valera 1960 (RVR1960)",
        "god_address": "Tú (Reverent Capitalized)",
        "human_address": "Tú (Casual)",
        "music_prompt": "(MUSIC)",
        "conjunctions": ["y", "o", "que", "pero", "de", "en"],
        "abbreviations": {
            r"(?i)Primera de Corintios": "1 Cor.",
            # ... (Full list from finalizer.py) ...
        },
        "base_prompt": """
        1. THEOLOGICAL ACCURACY (Strict):
           - Use **Reina-Valera 1960**.
           - Address God as "Tú" (Capitalized: Tú, Ti, Él).
           - Titles: "YO SOY", "Señor", "Espíritu Santo".
           
        2. BROADCAST CLARITY:
           - Use neutral Latin American Evangelical Standard.
        """
    },
    "pt": {
        "name": "Portuguese",
        "bible": "Almeida Revista e Atualizada (ARA)",
        "god_address": "Reverent second-person (capitalize pronouns as customary)",
        "human_address": "Natural broadcast second-person",
        "music_prompt": "(MUSIC)",
        "conjunctions": ["e", "mas", "que", "ou", "porque", "para"],
        "abbreviations": {},
        "base_prompt": """
        1. THEOLOGICAL ACCURACY (Strict):
           - Use **Almeida Revista e Atualizada (ARA)** for scripture.
           - Honorifics: God -> reverent second-person (capitalize where customary).
           
        2. BROADCAST CLARITY:
           - Use neutral, contemporary Portuguese; avoid slang.
        """
    },
    "fr": {
        "name": "French",
        "bible": "Louis Segond 1910 (LSG)",
        "god_address": "Reverent second-person (capitalize pronouns as customary)",
        "human_address": "Natural broadcast second-person",
        "music_prompt": "(MUSIC)",
        "conjunctions": ["et", "mais", "que", "ou", "parce", "pour"],
        "abbreviations": {},
        "base_prompt": """
        1. THEOLOGICAL ACCURACY (Strict):
           - Use **Louis Segond 1910 (LSG)** for scripture.
           - Honorifics: God -> reverent second-person (capitalize where customary).
           
        2. BROADCAST CLARITY:
           - Use clear, neutral French; avoid slang.
        """
    },
    "de": {
        "name": "German",
        "bible": "Luther 2017",
        "god_address": "Reverent second-person (capitalize pronouns as customary)",
        "human_address": "Natural broadcast second-person",
        "music_prompt": "(MUSIC)",
        "conjunctions": ["und", "aber", "dass", "oder", "weil", "für"],
        "abbreviations": {},
        "base_prompt": """
        1. THEOLOGICAL ACCURACY (Strict):
           - Use **Luther 2017** for scripture.
           - Honorifics: God -> reverent second-person (capitalize where customary).
           
        2. BROADCAST CLARITY:
           - Use clear, neutral German; avoid slang.
        """
    },
    "it": {
        "name": "Italian",
        "bible": "Nuova Riveduta 2006",
        "god_address": "Reverent second-person (capitalize pronouns as customary)",
        "human_address": "Natural broadcast second-person",
        "music_prompt": "(MUSIC)",
        "conjunctions": ["e", "ma", "che", "o", "perché", "per"],
        "abbreviations": {},
        "base_prompt": """
        1. THEOLOGICAL ACCURACY (Strict):
           - Use **Nuova Riveduta 2006** for scripture.
           - Honorifics: God -> reverent second-person (capitalize where customary).
           
        2. BROADCAST CLARITY:
           - Use clear, neutral Italian; avoid slang.
        """
    },
}

# --- 2. THE SOUL (Persona/Program Profiles) ---
# These apply ACROSS languages.

PROFILES = {
    "standard": {
        "name": "Standard (Omega TV)",
        "tone": "Professional, Accurate, Broadcast-Quality.",
        "glossary": {} 
    },
    "in_touch": {
        "name": "In Touch (Charles Stanley)",
        "tone": "Fatherly, Teaching, Calm, Educational, Gentle.",
        "glossary": {
            "In Touch": {"is": "In Touch", "es": "En Contacto"},
            "Life Principles": {"is": "Lífsreglur", "es": "Principios de Vida"},
            "Walk with God": {"is": "Ganga með Guði", "es": "Caminar con Dios"},
            "Dr. Stanley": {"is": "Dr. Stanley", "es": "Dr. Stanley"}
        }
    },
    "benny_hinn": {
        "name": "Benny Hinn",
        "tone": "Dynamic, Prophetic, High-Energy, Reverent, Authoritative.",
        "glossary": {
            "Anointing": {"is": "Smurning", "es": "Unción"},
            "Crusade": {"is": "Trúboðsfundur", "es": "Cruzada"},
            "Presence": {"is": "Nærvera", "es": "Presencia"},
            "Glory": {"is": "Dýrð", "es": "Gloria"},
            "Touch": {"is": "Snerting", "es": "Toque"}
        }
    }
}

def get_system_instruction(lang_code="is", profile_key="standard", extra_terms=None):
    """
    Composes the final System Prompt by merging Language Physics + Persona Soul.
    
    Args:
        lang_code: Target language code (e.g., "is", "es")
        profile_key: Program profile key (e.g., "standard", "in_touch")
        extra_terms: Optional dict of additional terms from job-specific termbook.
                     Format: {"source_term": "translated_term", ...}
                     These override profile terms for per-job customization.
    """
    lang = LANGUAGES.get(lang_code, LANGUAGES["is"])
    profile = PROFILES.get(profile_key, PROFILES["standard"])
    
    # Build Glossary for this specific language
    # We extract only the terms relevant to the target language
    active_glossary = {}
    for term, translations in profile["glossary"].items():
        if lang_code in translations:
            active_glossary[term] = translations[lang_code]
        else:
            # Fallback to English/Key if translation missing
            active_glossary[term] = term
    
    # Merge extra terms from job-specific termbook (overrides profile terms)
    if extra_terms and isinstance(extra_terms, dict):
        active_glossary.update(extra_terms)

    prompt = f"""
    ROLE: You are the Lead Translator for Omega TV.
    CURRENT PROGRAM: **{profile['name']}**
    TARGET LANGUAGE: **{lang['name']}**
    
    --- TONE & STYLE (The Soul) ---
    Tone: {profile['tone']}
    
    --- LANGUAGE RULES (The Physics) ---
    {lang['base_prompt']}
    
    --- GLOSSARY (Strict Terminology) ---
    {json.dumps(active_glossary, indent=2, ensure_ascii=False)}
    
    --- UNIVERSAL RULES ---
    1. MUSIC: If a segment is purely singing/lyrics or instrumental with no speech, output `{lang['music_prompt']}`. If speech is present over music (e.g., organ under speech), translate the speech and do NOT output `{lang['music_prompt']}`.
    2. FORMAT: Return JSON array matching input IDs.
    3. BREVITY (Broadcast): Prefer concise, natural phrasing; remove filler; keep sentences tight to reduce CPS.
    4. CAPITALIZATION (Broadcast): Use normal sentence case (not ALL CAPS). If the source segment is ALL CAPS, convert it to natural casing. Preserve acronyms/initialisms (e.g., USA, TV, I-690), Bible abbreviations, and mandatory theological titles (e.g., ÉG ER / YO SOY).
    5. ASR CLEANUP: If the source contains an obvious speech-to-text error and the intended word is clear, fix it before translating. If unsure, keep the original wording.
    """
    
    return prompt

# --- 3. THE POLITICS (Delivery Policies) ---
# Defaults for Dubbing vs. Subtitling based on region.

LANGUAGE_POLICIES = {
    # Subtitling Markets (Scandinavia, Benelux, etc.)
    "is": {"mode": "sub", "voice": "alloy"},
    "en": {"mode": "sub", "voice": "alloy"}, # SDH
    "no": {"mode": "sub", "voice": "alloy"},
    "sv": {"mode": "sub", "voice": "alloy"},
    "da": {"mode": "sub", "voice": "alloy"},
    "nl": {"mode": "sub", "voice": "alloy"},
    "pt": {"mode": "sub", "voice": "onyx"}, # Portugal (European)
    
    # Dubbing Markets (DACH, Romance, LatAm)
    "es": {"mode": "dub", "voice": "echo"}, 
    "fr": {"mode": "dub", "voice": "shimmer"}, 
    "de": {"mode": "dub", "voice": "onyx"}, 
    "it": {"mode": "dub", "voice": "fable"},
    "ru": {"mode": "dub", "voice": "echo"},
}

def get_language_policy(lang_code):
    """Returns the default delivery policy (mode, voice) for a language."""
    # Default to Subtitling if unknown (safest)
    return LANGUAGE_POLICIES.get(lang_code, {"mode": "sub", "voice": "alloy"})

