import os
from pathlib import Path
from typing import Dict, Optional

import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
_model = None

def _get_model():
    global _model
    if _model is None:
        if not API_KEY:
            raise RuntimeError("GOOGLE_API_KEY not set in .env")
        genai.configure(api_key=API_KEY)
        _model = genai.GenerativeModel("gemini-1.5-flash")
    return _model

def analyze_image_with_gemini(path: str) -> Dict[str, Optional[str]]:
    """
    Use Gemini 1.5 Flash to infer colour, style, material from a local image file.
    Returns: {'colour': str|None, 'style': str|None, 'material': str|None}
    """
    p = Path(path)
    if not p.is_file():
        return {"colour": None, "style": None, "material": None}

    model = _get_model()

    prompt = (
        "You are a product catalog assistant. Look at this furniture product image and "
        "return ONLY a compact JSON object with keys 'colour', 'style', and 'material'. "
        "Use short human-readable phrases, e.g.: "
        "{\"colour\": \"Light grey\", \"style\": \"Modern\", \"material\": \"Fabric\"}."
    )

    # Send image + prompt to Gemini
    result = model.generate_content(
        [
            prompt,
            {
                "mime_type": "image/jpeg",  # Gemini auto-detects, this is fine for most
                "data": p.read_bytes(),
            },
        ]
    )

    text = (result.text or "").strip()
    # Try to parse the text as JSON
    import json
    try:
        data = json.loads(text)
    except Exception:
        # If the model wrapped JSON in text, try to extract the first {...} block
        import re
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return {"colour": None, "style": None, "material": None}
        try:
            data = json.loads(m.group(0))
        except Exception:
            return {"colour": None, "style": None, "material": None}

    return {
        "colour": data.get("colour"),
        "style": data.get("style"),
        "material": data.get("material"),
    }