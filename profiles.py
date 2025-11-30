import json

# --- 1. THE PHYSICS (Language Rules) ---
# These apply to EVERYONE speaking this language.

LANGUAGES = {
    "is": {
        "name": "Icelandic",
        "bible": "Biblían 2007",
        "god_address": "Þér (Reverent Formal)",
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
           - God is addressed as "Þér" (Reverent Formal).
           - Humans/Friends are addressed as "Þú" (Casual).
           - Never use "ég er" for God's title; use "ÉG ER".
           
        2. SCRIPTURE PROTOCOL:
           - Use **Biblían 2007**.
           - If English speaker paraphrases, RECALL the official Icelandic verse.
           
        3. AVOID ANGLICISMS:
           - "Died for you" -> "Dó vegna þín".
           - "On fire" -> "Brennandi".
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
    }
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

def get_system_instruction(lang_code="is", profile_key="standard"):
    """
    Composes the final System Prompt by merging Language Physics + Persona Soul.
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
    1. MUSIC: Ignore singing. Output `{lang['music_prompt']}`.
    2. FORMAT: Return JSON array matching input IDs.
    """
    
    return prompt
