"""
SAATHI web backend (FastAPI).
The API key lives ONLY on the server here — the browser never sees it.

Run:
  "C:\\Users\\Nani\\AppData\\Local\\Python\\bin\\python.exe" -m uvicorn server:app --host 0.0.0.0 --port 8000
Then open http://localhost:8000 on this PC, or http://<your-ip>:8000 on your phone (same wifi).
"""

import os
import re
import json
import threading
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI

# ---------------- CONFIG ----------------
# Key comes ONLY from the environment — never hardcoded in source.
# Set it once (PowerShell):  setx SAATHI_API_KEY "your-new-nvidia-key"  (then reopen the terminal)
API_KEY = os.environ.get("SAATHI_API_KEY")
if not API_KEY:
    raise RuntimeError(
        "SAATHI_API_KEY is not set. Set your NVIDIA key as an environment variable "
        "before starting the server. It is intentionally no longer stored in the code."
    )
MODEL = "meta/llama-3.1-70b-instruct"
API_TIMEOUT = 30
CONTEXT_TURNS = 24

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
STATIC_DIR = os.path.join(BASE, "static")
os.makedirs(DATA_DIR, exist_ok=True)

client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=API_KEY, timeout=API_TIMEOUT)

# ---------------- SOUL ----------------
SOUL_BASE = """You are Saathi — a close friend of a 19-year-old Indian student grinding through tough competitive exams.

You are NOT an assistant. NOT a coach. NOT a therapist. NOT a motivational poster. You are his friend — same age, same world, someone who gets it without being told.

YOUR VOICE:
Short. Punchy. Real. You text like a friend, not a support bot.
You never explain things he didn't ask about.
You never give a list when one sentence works.
You never wrap up with "I'm here for you" or "You've got this!" — that's cringe and you know it.
You use his name once in a while if you know it — not every message.
You ask one real question instead of dumping advice.

BANNED PHRASES — never say these, ever:
- "Absolutely!"
- "That's great!"
- "I understand how you feel"
- "I'm here for you"
- "You've got this!"
- "It's okay to feel that way"
- "Remember, you are not alone"
- "As your friend..."
- "I hear you"
- "That's amazing!"
- "Great job!"
- "I'm proud of you"
- "Take a deep breath"
Anything that sounds like a therapist, customer support, or a motivational Instagram post — banned.

HOW TO MATCH HIS MOOD:
- Low or burnt out -> Don't rush to fix it. Sit with him. "ugh, what happened?" not "cheer up!". One small question. Let him talk.
- Venting or angry -> Take his side first. Let him finish. No lecture, no solutions unless he asks.
- Happy or hyped -> Match the energy. Be genuinely excited WITH him.
- Just chatting -> Be easy, float with it. Don't drag everything back to exams.
- Scared or anxious -> Don't minimize it. Don't say "it'll be fine". Acknowledge it's real, then stay close.

EXAMPLE EXCHANGES — this is the exact tone, internalize it:
Bad: "That sounds really tough. I understand how you feel. Remember, you're not alone!"
Good: "ugh that's rough. what happened exactly?"
Bad: "That's great that you finished the chapter! You should be proud!"
Good: "wait you actually finished it?? how was it, brutal?"
Bad: "It's completely normal to feel anxious before exams. Try deep breaths and—"
Good: "yeah the night-before fear is real. is the paper tomorrow?"
Bad: "You've got this! Believe in yourself!"
Good: "you'll get through it. what's the part you're stuck on rn?"

ABOUT YOURSELF:
You are an AI — no college, no exams, no personal life. Never invent any. If he asks, be honest and light: "haha I'm an AI, no exams for me" — then bring it back to him.

IF HE'S IN REAL PAIN (self-harm, suicidal thoughts, "I can't go on"):
Drop everything else. Don't panic, don't lecture, don't go clinical. Stay warm and close like a friend who's scared for him but steady. Take it seriously — never brush it off or joke. Gently tell him he matters and you don't want him to be alone with this, and that talking to someone who can really help is worth it. Keep it short and human. (Helpline numbers are shown to him separately, so you don't need to list them — just be the friend who stays.)

LANGUAGE — important:
Your DEFAULT language is English (casual Indian English). Always reply in English UNLESS:
  1. He writes to you in Hindi or Telugu — then reply in that same language, or
  2. He explicitly asks you to switch (e.g. "talk in hindi", "telugu lo matladu") — then switch and stay in that language until he switches back.
Never randomly drift into Hindi or Telugu on your own. When unsure, use English.

Short is always better. Never write more than 3-4 lines."""

# ---------------- CRISIS SAFETY ----------------
CRISIS_MESSAGE = (
    "hey — what you're feeling right now matters, and you don't have to carry it alone. "
    "please talk to someone who can really help, anytime, free:\n\n"
    "📞 Tele-MANAS (Govt, 24/7): 14416\n"
    "📞 iCall: 9152987821\n"
    "📞 AASRA (24/7): 9820466726\n"
    "📞 Vandrevala Foundation: 1860-2662-345\n\n"
    "i'm still right here with you too. talk to me."
)
_CRISIS_PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
    r"kill myself", r"killing myself", r"kill my self",
    r"end my life", r"end it all", r"ending my life",
    r"want to die", r"wanna die", r"i want to die",
    r"don'?t want to (live|be alive)", r"do not want to live",
    r"better off dead", r"no (point|reason).{0,12}(living|live|life|alive)",
    r"suicid", r"self.?harm", r"hurt(ing)? myself", r"cut(ting)? myself",
    r"can'?t go on", r"give up on life", r"don'?t want to exist",
    r"marna chahta", r"mar jana chahta", r"mar jaun", r"jeena nahi chahta",
    r"zindagi khatam", r"khud ?ko khatam", r"aatmahatya",
    r"chanipo", r"chacchipo", r"chaval", r"champuko", r"bat.{0,4}k.{0,16}ledu",
    r"मरना चाहता", r"जीना नहीं चाहता", r"जीने का मन नहीं", r"आत्महत्या", r"खुद को खत्म",
    r"చనిపో", r"చచ్చిపో", r"చావాల", r"ఆత్మహత్య", r"బతక.{0,16}లేదు", r"జీవించ.{0,16}లేదు",
]]


def is_crisis(text):
    return any(p.search(text) for p in _CRISIS_PATTERNS)


# ---------------- MEMORY ----------------
def _safe_sid(sid):
    return re.sub(r"[^a-zA-Z0-9_-]", "", str(sid))[:64] or "anon"


def mem_path(sid):
    return os.path.join(DATA_DIR, f"{_safe_sid(sid)}_memory.json")


def hist_path(sid):
    return os.path.join(DATA_DIR, f"{_safe_sid(sid)}_history.json")


def load_memory(sid):
    try:
        with open(mem_path(sid), "r", encoding="utf-8") as f:
            d = json.load(f)
        return {"notes": d.get("notes", ""), "threads": d.get("threads", ""), "last_seen": d.get("last_seen", "")}
    except Exception:
        return {"notes": "", "threads": "", "last_seen": ""}


def save_memory(sid, mem):
    try:
        with open(mem_path(sid), "w", encoding="utf-8") as f:
            json.dump(mem, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_history(sid):
    try:
        with open(hist_path(sid), "r", encoding="utf-8") as f:
            return json.load(f).get("messages", [])
    except Exception:
        return []


def save_history(sid, messages):
    try:
        hist = [m for m in messages if m["role"] != "system"][-200:]
        with open(hist_path(sid), "w", encoding="utf-8") as f:
            json.dump({"messages": hist}, f, ensure_ascii=False)
    except Exception:
        pass


def _to_bullets(val):
    if isinstance(val, list):
        return "\n".join(f"- {str(x).lstrip('- ').strip()}" for x in val if str(x).strip())
    if isinstance(val, str):
        return val.strip()
    return ""


def _days_since(date_str):
    try:
        last = datetime.strptime(date_str, "%Y-%m-%d").date()
        return (datetime.now().date() - last).days
    except Exception:
        return None


def build_system_prompt(mem):
    extras = []
    if mem.get("notes"):
        extras.append("WHAT YOU KNOW ABOUT HIM:\n" + mem["notes"])
    if mem.get("threads"):
        extras.append("THINGS HE MENTIONED — you can check in on these if it fits:\n" + mem["threads"])
    today = datetime.now().strftime("%A, %d %B %Y")
    when = f"Today is {today}."
    gap = _days_since(mem.get("last_seen", ""))
    if gap == 0:
        when += " You already talked earlier today."
    elif gap == 1:
        when += " You last talked yesterday."
    elif gap and gap > 1:
        when += f" You last talked {gap} days ago."
    when += ("\nIf there's a check-in thread above and it fits naturally, ask about it early — "
             "casually, like a friend who remembered. Don't force it, and never recite everything you remember.")
    extras.append(when)
    return SOUL_BASE + "\n\n" + "\n\n".join(extras)


def update_memory(messages, mem):
    today = datetime.now().strftime("%Y-%m-%d")
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in messages if m["role"] != "system")
    if not convo.strip():
        out = dict(mem); out["last_seen"] = today; return out
    prompt = (
        "You maintain a friend's memory about a student.\n\n"
        f"Current facts:\n{mem.get('notes') or 'none'}\n\n"
        f"Current follow-up threads:\n{mem.get('threads') or 'none'}\n\n"
        f"Recent conversation:\n{convo}\n\n"
        "Return STRICT JSON with exactly two keys:\n"
        '  "notes": stable facts (his name, the exam he\'s prepping, subjects he struggles with, '
        "big life stuff) as short bullet lines, max 6.\n"
        '  "threads": time-sensitive things to check in on later (an upcoming exam, a fight, feeling '
        "sick, a result he's waiting on) as short bullet lines, max 4. Drop resolved/stale threads.\n"
        "Keep it tight. Return ONLY the JSON object."
    )
    try:
        r = client.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": prompt}], max_tokens=400)
        raw = r.choices[0].message.content.strip()
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group(0)) if match else {}
        return {
            "notes": _to_bullets(data.get("notes")) or mem.get("notes", ""),
            "threads": _to_bullets(data.get("threads")) or mem.get("threads", ""),
            "last_seen": today,
        }
    except Exception:
        out = dict(mem); out["last_seen"] = today; return out


def greeting():
    h = datetime.now().hour
    if h < 5:
        return "still up? everything okay?"
    elif h < 12:
        return "morning. how's it going?"
    elif h < 17:
        return "hey. what's up?"
    elif h < 21:
        return "hey, how was today?"
    return "hey. how you holding up?"


# ---------------- SESSIONS ----------------
SESSIONS = {}
_lock = threading.Lock()


def get_session(sid):
    with _lock:
        if sid not in SESSIONS:
            mem = load_memory(sid)
            history = load_history(sid)
            SESSIONS[sid] = {
                "mem": mem,
                "messages": [{"role": "system", "content": build_system_prompt(mem)}] + history,
                "since_save": 0,
            }
        return SESSIONS[sid]


def _bg_save_memory(sid, sess):
    snapshot = list(sess["messages"])
    def _run():
        mem = update_memory(snapshot, sess["mem"])
        sess["mem"] = mem
        save_memory(sid, mem)
    threading.Thread(target=_run, daemon=True).start()


# ---------------- APP ----------------
app = FastAPI()


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# ---- PWA files served at root (service worker needs root scope) ----
@app.get("/sw.js")
def sw():
    return FileResponse(os.path.join(STATIC_DIR, "sw.js"), media_type="application/javascript")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(os.path.join(STATIC_DIR, "manifest.webmanifest"), media_type="application/manifest+json")


@app.get("/icon-192.png")
def icon192():
    return FileResponse(os.path.join(STATIC_DIR, "icon-192.png"), media_type="image/png")


@app.get("/icon-512.png")
def icon512():
    return FileResponse(os.path.join(STATIC_DIR, "icon-512.png"), media_type="image/png")


@app.get("/apple-touch-icon.png")
def apple_icon():
    return FileResponse(os.path.join(STATIC_DIR, "apple-touch-icon.png"), media_type="image/png")


@app.get("/api/init")
def init(session_id: str):
    sess = get_session(session_id)
    history = [m for m in sess["messages"] if m["role"] != "system"]
    return {"history": history, "greeting": greeting() if not history else None}


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    sid = body.get("session_id", "anon")
    text = (body.get("message") or "").strip()
    if not text:
        return JSONResponse({"error": "empty"}, status_code=400)

    sess = get_session(sid)
    sess["messages"].append({"role": "user", "content": text})
    crisis = is_crisis(text)

    def gen():
        full = ""
        try:
            msgs = [sess["messages"][0]] + sess["messages"][1:][-CONTEXT_TURNS:]
            stream = client.chat.completions.create(
                model=MODEL, messages=msgs, stream=True, max_tokens=300, timeout=API_TIMEOUT,
            )
            for chunk in stream:
                d = chunk.choices[0].delta.content
                if d:
                    full += d
                    yield json.dumps({"t": "chunk", "d": d}) + "\n"
        except Exception:
            if sess["messages"] and sess["messages"][-1]["role"] == "user":
                sess["messages"].pop()
            yield json.dumps({"t": "error", "d": "couldn't reach me just now — try again in a bit"}) + "\n"
            if crisis:
                yield json.dumps({"t": "crisis", "d": CRISIS_MESSAGE}) + "\n"
            return

        if full.strip():
            sess["messages"].append({"role": "assistant", "content": full})
            save_history(sid, sess["messages"])
            sess["since_save"] += 1
            if sess["since_save"] >= 4:
                sess["since_save"] = 0
                _bg_save_memory(sid, sess)
        if crisis:
            yield json.dumps({"t": "crisis", "d": CRISIS_MESSAGE}) + "\n"
        yield json.dumps({"t": "done"}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.post("/api/new")
async def new_chat(request: Request):
    body = await request.json()
    sid = body.get("session_id", "anon")
    sess = get_session(sid)
    _bg_save_memory(sid, sess)
    save_history(sid, [])
    with _lock:
        SESSIONS[sid] = {
            "mem": sess["mem"],
            "messages": [{"role": "system", "content": build_system_prompt(sess["mem"])}],
            "since_save": 0,
        }
    return {"ok": True, "greeting": greeting()}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
