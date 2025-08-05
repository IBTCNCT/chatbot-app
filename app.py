from flask import Flask, request, jsonify
from flask_cors import CORS
import openai
import os
from gtts import gTTS
import uuid
import langdetect
import gspread
from google.oauth2.service_account import Credentials
import json
from datetime import datetime
import re
import time

app = Flask(__name__)
CORS(app)

# ✅ OpenAI client using environment variables (Render safe)
client = openai.OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    project=os.getenv("OPENAI_PROJECT_ID")
)

# ✅ Google Sheets setup
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Support both local file (for local dev) and environment variable (for Render)
SERVICE_ACCOUNT_FILE = "ibt-chatbot-integration.json"  # local fallback filename

if os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"):
    # On Render: read the full JSON from an environment variable (recommended)
    SERVICE_ACCOUNT_INFO = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"))
    credentials = Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=SCOPES)
else:
    # Local: read the file from disk (make sure it's present locally; it's in .gitignore)
    credentials = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)

gc = gspread.authorize(credentials)

# 📝 Use your actual spreadsheet ID (from the sheet URL)
SPREADSHEET_ID = "1CLiNQKabUCoxK0uhLAuhvOOuJymUSLY4yzmGmZ8hSTU"

# Try to open a worksheet named "Leads", otherwise fall back to the first sheet.
sh = gc.open_by_key(SPREADSHEET_ID)
try:
    worksheet = sh.worksheet("Leads")
except Exception:
    worksheet = sh.sheet1

# -----------------------
# Session & lead control
# -----------------------
# In-memory session store:
# session_key -> {
#   'count': int,
#   'lead_mode': bool,
#   'lead_step': int,   # 0 = not collecting, 1=name, 2=phone, 3=email, 4=location
#   'lead_data': {'name','phone','email','location'},
#   'last_seen': timestamp
# }
sessions = {}
SESSION_TTL = 60 * 60  # 1 hour TTL for session entries
LEAD_TRIGGER_COUNT = 3  # start lead flow after this many user messages

email_re = re.compile(r"[^@]+@[^@]+\.[^@]+")

def make_session_key():
    """Create a session key based on IP + user-agent to keep it simple."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    ua = request.headers.get("User-Agent", "")[:200]
    return f"{ip}||{ua}"

def get_session():
    key = make_session_key()
    s = sessions.get(key)
    now = time.time()
    if s:
        s['last_seen'] = now
        return s
    # create new
    s = {
        'count': 0,
        'lead_mode': False,
        'lead_step': 0,
        'lead_data': {'name': '', 'phone': '', 'email': '', 'location': ''},
        'last_seen': now
    }
    sessions[key] = s
    return s

def cleanup_sessions():
    now = time.time()
    to_delete = [k for k, v in sessions.items() if now - v.get('last_seen', 0) > SESSION_TTL]
    for k in to_delete:
        del sessions[k]

# -----------------------
# Utility helpers
# -----------------------
def detect_language(text):
    try:
        lang = langdetect.detect(text)
        return "es" if lang == "es" else "en"
    except:
        return "en"

def save_lead_to_sheet(name, phone, email, location):
    timestamp = datetime.utcnow().isoformat()
    row = [timestamp, name or "-", phone or "-", email or "-", location or "-"]
    worksheet.append_row(row)
    print(f"📩 New lead saved: {row}")

def is_valid_email(email):
    return bool(email_re.match(email))

# -----------------------
# Routes
# -----------------------
@app.route("/")
def home():
    return "<h1>✅ IBT Connect Flask Server is Running</h1>"

@app.route("/chat", methods=["POST"])
def chat():
    cleanup_sessions()
    session = get_session()

    try:
        data = request.get_json() or {}
        message = (data.get("message") or "").strip()
        is_voice = bool(data.get("voice", False))  # Sent from frontend

        if not message:
            return jsonify({"error": "No message provided"}), 400

        # If we are currently in lead capture mode for this session, handle it here.
        if session['lead_mode']:
            step = session['lead_step']
            ld = session['lead_data']

            # Step handlers:
            if step == 1:
                # expecting name
                ld['name'] = message
                session['lead_step'] = 2
                return jsonify({"reply": "Thanks! What’s the best phone number to reach you?"})

            elif step == 2:
                # expecting phone
                ld['phone'] = message
                session['lead_step'] = 3
                return jsonify({"reply": "Great — could I get your email address so we can follow up? (email is required)"})

            elif step == 3:
                # expecting email (required)
                if not is_valid_email(message):
                    return jsonify({"reply": "That doesn't look like a valid email. Could you please provide a valid email address?"})
                ld['email'] = message
                session['lead_step'] = 4
                return jsonify({"reply": "Thanks! Lastly, could you share your city or ZIP code (optional)? If you'd rather skip, type 'skip'."})

            elif step == 4:
                # location (optional). If user types "skip", treat as blank.
                if message.lower().strip() == "skip":
                    ld['location'] = ""
                else:
                    ld['location'] = message

                # Save lead (email required by flow)
                if not ld.get('email'):
                    # This shouldn't happen due to step enforcement, but check anyway.
                    session['lead_step'] = 3
                    return jsonify({"reply": "We still need your email address to save the lead. Please provide it."})

                try:
                    save_lead_to_sheet(ld.get('name', ''), ld.get('phone', ''), ld.get('email', ''), ld.get('location', ''))
                except Exception as e:
                    print("❌ Error saving lead to sheet:", e)
                    # still reset session to avoid stuck state
                    session.update({'lead_mode': False, 'lead_step': 0, 'lead_data': {'name': '', 'phone': '', 'email': '', 'location': ''}, 'count': 0})
                    return jsonify({"reply": "Thanks — we had trouble saving your info, but we've received it and will follow up."})

                # Reset lead state & message count
                session.update({'lead_mode': False, 'lead_step': 0, 'lead_data': {'name': '', 'phone': '', 'email': '', 'location': ''}, 'count': 0})
                return jsonify({"reply": "✅ Thanks — your info is saved. We’ll be in touch soon!"})

            else:
                # unknown step: reset lead flow to safe state
                session.update({'lead_mode': False, 'lead_step': 0, 'lead_data': {'name': '', 'phone': '', 'email': '', 'location': ''}})
                # continue on to normal processing below

        # Normal chat counting & OpenAI reply
        session['count'] += 1

        # If user reached message threshold and not already in lead mode -> start lead flow
        if not session['lead_mode'] and session['count'] >= LEAD_TRIGGER_COUNT:
            session['lead_mode'] = True
            session['lead_step'] = 1
            # Do not call OpenAI; instead ask for the first lead field
            return jsonify({"reply": "Before we continue, can I grab your name?"})

        # Otherwise, call OpenAI normally
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful bilingual assistant. Always respond in the same language the user used, either English or Spanish."
                },
                {"role": "user", "content": message}
            ],
            temperature=0.7
        )

        reply = response.choices[0].message.content.strip()
        result = {"reply": reply}

        # 🔊 Only generate audio if input was voice -> return audio_url for TTS
        if is_voice:
            lang = detect_language(reply)
            tts = gTTS(text=reply, lang=lang)
            filename = f"{uuid.uuid4().hex}.mp3"
            filepath = os.path.join("static", filename)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            tts.save(filepath)
            result["audio_url"] = f"/audio/{filename}"

        return jsonify(result)

    except Exception as e:
        print("❌ Error in /chat:", e)
        return jsonify({"error": str(e)}), 500

@app.route("/lead", methods=["POST"])
def capture_lead():
    """
    Backwards-compatible endpoint that accepts partial lead data and returns next prompt,
    or saves the lead. This coexists with the session-driven flow above.
    """
    try:
        data = request.get_json() or {}
        name = (data.get("name") or "").strip()
        phone = (data.get("phone") or "").strip()
        email = (data.get("email") or "").strip()
        location = (data.get("location") or "").strip()

        # If no fields at all, ask for name to start
        if not name and not phone and not email and not location:
            return jsonify({"status": "incomplete", "next": "Hi! Can I grab your name?"}), 200

        # If we have a name but not a phone yet, ask for phone
        if name and not phone and not email:
            return jsonify({"status": "incomplete", "next": "Thanks! What’s the best phone number to reach you?"}), 200

        # If we have name and phone but not email, ask for email (this is your requested flow)
        if name and phone and not email:
            return jsonify({"status": "incomplete", "next": "Great — could I get your email address so we can follow up? (email required)"}), 200

        # If we have phone but no name, ask for name
        if phone and not name and not email:
            return jsonify({"status": "incomplete", "next": "Thanks — may I have your name, please?"}), 200

        # If we have email but missing name/phone, still proceed but prefer to ask for name
        if email and not name:
            return jsonify({"status": "incomplete", "next": "Thanks! What name should we use for this lead?"}), 200

        # Enforce email validation before saving
        if email and not is_valid_email(email):
            return jsonify({"status": "incomplete", "next": "The email looks invalid. Please provide a valid email address."}), 200

        # If reached here, we have enough to save (email required by flow)
        if not email:
            return jsonify({"status": "incomplete", "next": "We require an email to save the lead. Please provide it."}), 200

        timestamp = datetime.utcnow().isoformat()
        row = [timestamp, name or "-", phone or "-", email or "-", location or "-"]
        worksheet.append_row(row)

        print(f"📩 New lead saved via /lead: {row}")

        return jsonify({"status": "success", "message": "Lead captured", "lead": {"name": name, "phone": phone, "email": email, "location": location}}), 200

    except Exception as e:
        print("❌ Error in /lead:", e)
        return jsonify({"error": str(e)}), 500

@app.route("/audio/<filename>")
def get_audio(filename):
    return app.send_static_file(filename)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)