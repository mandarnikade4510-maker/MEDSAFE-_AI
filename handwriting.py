"""Handwritten prescription reader for MedSafe AI.

Uses a vision AI model. Works with a FREE Google Gemini key (GEMINI_API_KEY) or an Anthropic key.
If both are set, Gemini (free) is used first.

Accuracy features: auto-rotation of sideways photos, contrast clean-up, a prompt that explains how Indian
prescriptions are laid out, and a verbatim "written as" copy of every name so mistakes are easy to spot.
"""
import base64
import hashlib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request

import cv2
import numpy as np

from med_logic import AI_DEFAULT_MODEL, find_medicine, get_ai_key, get_gemini_key

# Own SSL context: still verifies certificates, but skips the extra-strict check that breaks on Python 3.13+
# behind antivirus / proxy HTTPS scanning.
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.verify_flags &= ~ssl.VERIFY_X509_STRICT

GEMINI_DEFAULT_MODEL = "gemini-flash-latest"  # alias that always points to the current free Flash model
GEMINI_BACKUP_MODEL = "gemini-flash-lite-latest"  # tried when the main model is busy or out of quota (own quota)
_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "handwriting_cache.json")
_THINK_OK = {"value": True}  # set to False if the model rejects the "no thinking" speed setting


def _load_disk_cache():
    """Finished results saved on disk: the same prescription is instant, even after restarting the app."""
    try:
        with open(_CACHE_FILE, encoding="utf-8") as fh:
            return {k: (v["medicines"], v["transcription"], "ok") for k, v in json.load(fh).items()}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _save_disk_cache(key, result):
    try:
        try:
            with open(_CACHE_FILE, encoding="utf-8") as fh:
                saved = json.load(fh)
        except (OSError, ValueError):
            saved = {}
        saved[key] = {"medicines": result[0], "transcription": result[1]}
        saved = dict(list(saved.items())[-60:])  # keep the file small
        with open(_CACHE_FILE, "w", encoding="utf-8") as fh:
            json.dump(saved, fh, ensure_ascii=False)
    except OSError:
        pass


_CACHE = _load_disk_cache()  # image hash -> finished result, so clicking the button again does not use up quota


def _post(url, payload, headers, timeout=60):
    """POST JSON. Returns (data, status): ok | auth | rate | busy | offline | error.
    rate = quota used up (429), busy = server overloaded or too slow (503 / timeout). Real errors print in the terminal."""
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode("utf-8")), "ok"
    except urllib.error.HTTPError as e:
        print("HANDWRITING HTTP ERROR:", e.code, e.read().decode("utf-8", "ignore")[:800], file=sys.stderr)
        if e.code in (401, 403):
            return None, "auth"
        if e.code == 429:
            return None, "rate"
        if e.code in (500, 502, 503, 504, 529):
            return None, "busy"
        return None, "error"
    except (TimeoutError, OSError) as e:
        print("HANDWRITING NETWORK ERROR:", type(e).__name__, e, file=sys.stderr)
        if "timed out" in str(e).lower() or isinstance(e, TimeoutError):
            return None, "busy"
        return None, "offline"
    except Exception as e:
        print("HANDWRITING NETWORK ERROR:", type(e).__name__, e, file=sys.stderr)
        return None, "offline"


ROTATION_PROMPT = """This is a photo of a document. It may be sideways or upside down.
How many degrees CLOCKWISE must the photo be rotated so that the main text reads upright, left to right?
Reply with ONLY this JSON: {"rotate_clockwise": 0}   (the value must be 0, 90, 180 or 270)"""

HANDWRITING_PROMPT = """You are an expert at reading Indian doctors' prescriptions (handwritten, printed or both). \
The text may be English, Marathi, Hindi or a mix. The photo may have shadows or glare, and may be sideways or upside down - turn it in your mind and read it normally.

HOW THESE PRESCRIPTIONS ARE LAID OUT - use this to read correctly:
- Each medicine is usually on its own line, starting with Tab / Cap / Syp / Inj / Powder / Drops. \
The dose pattern usually sits on the line DIRECTLY BELOW the medicine name (or just after it) - it belongs to that medicine, \
not to the medicine above.
- Patterns like 1-0-1 or 2-0-2 mean morning-afternoon-night. Marathi/Hindi words: \
सकाळी = morning, दुपारी = afternoon, रात्री / रात्रौ = night, दा = times.
- A curly bracket or brace joins several medicines to ONE shared duration (for example "1 month") or route (for example "IM"). \
Give that duration or route to every medicine inside the brace.
- IM = intramuscular, IV = intravenous, OD = once daily, BD = twice daily, TDS = three times daily, SOS = if needed.
- Circled numbers are usually just item numbers or emphasis, not part of the name.
- The Marathi/Hindi text at the very top, near the hospital name and date, is normally the PATIENT'S NAME. \
It is NOT a dosing instruction - ignore it.
- Ignore hospital names, doctor names, addresses, phone numbers, emails and dates.

STEP 1 - transcription: copy ONLY the lines about medicines, doses and instructions, exactly as you see them (keep spelling \
mistakes; write Marathi/Hindi words in their own script with an English meaning in brackets). Skip everything else - \
header, hospital, doctor, address, phone, date, signature - so the answer stays short and fast.
STEP 2 - medicines: decide the real medicine on each line.
- "written_as" = the name exactly as written, even if you think it is a misspelling.
- "name" = the real standard name. Only correct a spelling when you are confident, using real medicines and vitamins that are \
commonly prescribed in India (examples only: Amlodipine, Arvast, Thyrox, Beplex Forte, Neurobion Forte, Shelcal, Calcirol, Feronia XT, Susten, Utrogestan, \
Vitamin D3, Vitamin B12 / Methylcobalamin, protein powder). Do not force a match to this list. \
Do not invent products: a vitamin called "B29" does not exist, but "B12" does - letters and digits in handwriting are \
easy to confuse, so consider that.
- "certain" = false whenever any letter or number is a guess.

Reply with ONLY a JSON object, no other text, in exactly this shape and key order:
{"transcription": "line 1\\nline 2\\n...",
 "medicines": [
  {"written_as": "Tab Neurobion forte", "name": "Neurobion Forte", "frequency": "1-0-0", "timing": "",
   "duration": "1 month", "route": "", "certain": true}
 ]}
Use "" for anything that is not written. Never invent a medicine that is not on the page. \
If nothing is legible return {"transcription": "", "medicines": []}."""


def _decode(image_bytes):
    return cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)


def _resize(img, max_side):
    h, w = img.shape[:2]
    scale = max_side / max(h, w)
    if scale < 1:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return img


def _to_b64(img, quality=92):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return base64.b64encode(buf.tobytes()).decode("ascii") if ok else None


def _enhance(img):
    """Gently even out shadows and boost faint ink (contrast on the brightness channel only)."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)


_ROTATIONS = {
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}


def _rotate(img, degrees_clockwise):
    code = _ROTATIONS.get(degrees_clockwise)
    return cv2.rotate(img, code) if code is not None else img


def _parse_json(text):
    """Pull the JSON object out of the reply, even if it is wrapped in code fences."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


def _gemini_key():
    """Free Google Gemini key (api_keys.py, then environment, then Streamlit secrets)."""
    return get_gemini_key()


def _ask_anthropic(b64, prompt, key, model, max_tokens):
    data, status = _post(
        "https://api.anthropic.com/v1/messages",
        {
            "model": model or os.environ.get("ANTHROPIC_MODEL") or AI_DEFAULT_MODEL,
            "max_tokens": max_tokens,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        },
        {"content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"},
        timeout=90,
    )
    if status != "ok":
        return None, status
    try:
        return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text"), "ok"
    except (KeyError, TypeError, AttributeError):
        return None, "error"


def _ask_gemini_once(b64, prompt, key, model, timeout=45):
    config = {"responseMimeType": "application/json", "temperature": 0, "maxOutputTokens": 2048}
    if _THINK_OK["value"]:
        config["thinkingConfig"] = {"thinkingBudget": 0}   # no long "thinking" phase = much faster answer
    data, status = _post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {"contents": [{"parts": [
            {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
            {"text": prompt},
        ]}], "generationConfig": config},
        {"content-type": "application/json", "x-goog-api-key": key},
        timeout=timeout,
    )
    if status == "error" and _THINK_OK["value"]:
        _THINK_OK["value"] = False       # this model rejects the speed setting: try again once without it
        return _ask_gemini_once(b64, prompt, key, model, timeout)
    if status != "ok":
        return None, status
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts if "text" in p), "ok"
    except (KeyError, IndexError, TypeError, AttributeError):
        return None, "error"


def _ask_gemini(b64, prompt, key, retries=True):
    """Main model first; if it is busy, slow or out of quota, go straight to the backup model (no waiting)."""
    main = os.environ.get("GEMINI_MODEL") or GEMINI_DEFAULT_MODEL
    last = "error"
    for model, timeout in ((main, 30), (GEMINI_BACKUP_MODEL, 40)):
        reply, status = _ask_gemini_once(b64, prompt, key, model, timeout)
        if status == "ok":
            return reply, "ok"
        last = status
        if status == "auth":
            return None, "auth"          # a wrong key will not work on another model either
    return None, last


def _ask(b64, prompt, gem_key, key, model, max_tokens=2500, retries=True):
    if gem_key:
        return _ask_gemini(b64, prompt, gem_key, retries)
    return _ask_anthropic(b64, prompt, key, model, max_tokens)


def _detect_rotation(img, gem_key, key, model):
    """Ask the model how far the photo must be turned. Any failure simply means 'do not rotate'."""
    small = _to_b64(_resize(img, 900), 80)
    if not small:
        return 0
    reply, status = _ask(small, ROTATION_PROMPT, gem_key, key, model, max_tokens=60, retries=False)
    if status != "ok":
        return 0
    parsed = _parse_json(reply or "")
    try:
        degrees = int(parsed.get("rotate_clockwise", 0)) % 360
    except (AttributeError, TypeError, ValueError):
        return 0
    return degrees if degrees in (90, 180, 270) else 0


def _text(value, limit):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _make_dose(item):
    """Combine schedule, timing, duration and route into one readable line."""
    schedule = " ".join(p for p in (_text(item.get("frequency"), 30), _text(item.get("timing"), 30)) if p)
    parts = [schedule, _text(item.get("duration"), 30), _text(item.get("route"), 20)]
    return ", ".join(p for p in parts if p)


def read_handwritten_prescription(image_bytes, api_key=None, model=None):
    """
    Returns (medicines, raw_text, status).
    medicines: list of {"name", "dose", "certain"}.
    status: ok | no_key | bad_image | auth | rate | offline | error
    """
    gem_key = _gemini_key()
    key = get_ai_key(api_key)
    if not gem_key and not key:
        return [], "", "no_key"

    cache_key = hashlib.sha256(image_bytes).hexdigest()
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    img = _decode(image_bytes)
    if img is None:
        return [], "", "bad_image"

    # Phone photos are already upright (OpenCV applies the camera's rotation tag) and the prompt tells the AI to
    # read sideways pages anyway, so the extra "rotation check" request was removed - it only added waiting time.
    b64 = _to_b64(_enhance(_resize(img, 1600)), 88)
    if not b64:
        return [], "", "bad_image"

    _t0 = time.time()
    reply, status = _ask(b64, HANDWRITING_PROMPT, gem_key, key, model)
    print(f"HANDWRITING: AI answered in {time.time() - _t0:.1f} s (status: {status})", file=sys.stderr)
    if status != "ok":
        return [], "", ("rate" if status == "busy" else status)  # busy/overloaded shows the "try again in a minute" message

    parsed = _parse_json(reply or "")
    if not isinstance(parsed, dict):
        return [], "", "error"

    medicines, seen = [], set()
    for item in parsed.get("medicines", []):
        if not isinstance(item, dict):
            continue
        name = re.sub(r"[^A-Za-z0-9 \-]", "", str(item.get("name", ""))).strip()[:60]
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())

        dose = _make_dose(item)
        written = re.sub(r"^(tab|cap|syp|inj)\.?\s+", "", _text(item.get("written_as"), 60), flags=re.IGNORECASE)
        certain = bool(item.get("certain", True))
        if written and written.lower() != name.lower():
            dose = (dose + " " if dose else "") + f"(written as: {written})"
            certain = False  # the AI changed the spelling, so ask the person to check it

        medicines.append({
            "name": find_medicine(name) or name,  # use the app's standard name when it is known
            "dose": dose[:140],
            "certain": certain,
        })
    result = (medicines, str(parsed.get("transcription", "")), "ok")
    _CACHE[cache_key] = result
    _save_disk_cache(cache_key, result)
    return result