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

app = Flask(__name__)
CORS(app)

# ---------------------------
# Existing config (unchanged)
# ---------------------------
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

# ---------------------------
# New: in-memory session store
# ---------------------------
# Format per session:
# {
#   "message_count": int,
#   "collecting_lead": bool,
#   "lead_step": int,   # 1=name, 2=phone, 3=location
#   "lead_data": {"name": "", "phone": "", "location": ""}
# }
sessions = {}

def get_session_key():
    """Return session key from request JSON 'session_id' or fallback to remote IP."""
    body = {}
    try:
        body = request.get_json(force=False) or {}
    except Exception:
        body = {}
    session_id = body.get("session_id")
    if session_id:
        return f"sid:{session_id}"
    # fallback: use remote address (note: not perfect for many users behind same IP)
    return f"ip:{request.remote_addr}"

def get_or_create_session(key):
    if key not in sessions:
        sessions[key] = {
            "message_count": 0,
            "collecting_lead": False,
            "lead_step": 0,
            "lead_data": {"name": "", "phone": "", "location": ""}
        }
    return sessions[key]

# ---------------------------
# Routes (mostly unchanged)
# ---------------------------
@app.route("/")
def home():
    return "<h1>✅ IBT Connect Flask Server is Running</h1>"

@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json()
        message = data.get("message", "")
        is_voice = data.get("voice", False)  # Sent from frontend

        if not message:
            return jsonify({"error": "No message provided"}), 400

        # identify session
        session_key = get_session_key()
        session = get_or_create_session(session_key)

        # If we're currently collecting lead info, handle that flow server-side.
        if session["collecting_lead"]:
            lead_step = session["lead_step"]
            # Step 1: collect name
            if lead_step == 1:
                session["lead_data"]["name"] = (message or "").strip()
                session["lead_step"] = 2
                reply_text = "Thanks! What's the best phone number to reach you?"
                result = {"reply": reply_text}
                if is_voice:
                    lang = detect_language(reply_text)
                    tts = gTTS(text=reply_text, lang=lang)
                    filename = f"{uuid.uuid4().hex}.mp3"
                    filepath = os.path.join("static", filename)
                    os.makedirs(os.path.dirname(filepath), exist_ok=True)
                    tts.save(filepath)
                    result["audio_url"] = f"/audio/{filename}"
                return jsonify(result)

            # Step 2: collect phone
            if lead_step == 2:
                session["lead_data"]["phone"] = (message or "").strip()
                session["lead_step"] = 3
                reply_text = "Great — and your city or ZIP code?"
                result = {"reply": reply_text}
                if is_voice:
                    lang = detect_language(reply_text)
                    tts = gTTS(text=reply_text, lang=lang)
                    filename = f"{uuid.uuid4().hex}.mp3"
                    filepath = os.path.join("static", filename)
                    os.makedirs(os.path.dirname(filepath), exist_ok=True)
                    tts.save(filepath)
                    result["audio_url"] = f"/audio/{filename}"
                return jsonify(result)

            # Step 3: collect location, save lead, finish
            if lead_step == 3:
                session["lead_data"]["location"] = (message or "").strip()
                # append to Google Sheet
                name = session["lead_data"]["name"]
                phone = session["lead_data"]["phone"]
                location = session["lead_data"]["location"]
                try:
                    worksheet.append_row([name, phone, location])
                except Exception as sheet_err:
                    print("❌ Error appending to sheet:", sheet_err)
                    # continue anyway

                # Prepare final reply
                reply_text = "✅ Thanks! We've saved your info — we'll reach out soon. How else can I help?"
                # Reset session lead info and counters
                session["collecting_lead"] = False
                session["lead_step"] = 0
                session["lead_data"] = {"name": "", "phone": "", "location": ""}
                session["message_count"] = 0

                result = {"reply": reply_text}
                if is_voice:
                    lang = detect_language(reply_text)
                    tts = gTTS(text=reply_text, lang=lang)
                    filename = f"{uuid.uuid4().hex}.mp3"
                    filepath = os.path.join("static", filename)
                    os.makedirs(os.path.dirname(filepath), exist_ok=True)
                    tts.save(filepath)
                    result["audio_url"] = f"/audio/{filename}"
                return jsonify(result)

        # If not collecting lead, increment message count and maybe trigger lead flow
        session["message_count"] += 1

        # Start lead collection automatically after 3 user messages
        if session["message_count"] >= 3 and not session["collecting_lead"]:
            session["collecting_lead"] = True
            session["lead_step"] = 1
            reply_text = "Before we continue, can I grab your name?"
            result = {"reply": reply_text}
            if is_voice:
                lang = detect_language(reply_text)
                tts = gTTS(text=reply_text, lang=lang)
                filename = f"{uuid.uuid4().hex}.mp3"
                filepath = os.path.join("static", filename)
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                tts.save(filepath)
                result["audio_url"] = f"/audio/{filename}"
            return jsonify(result)

        # Normal behavior: call OpenAI and return assistant reply
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

        # 🔊 Only generate audio if input was voice
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
    try:
        data = request.get_json()
        name = (data.get("name") or "").strip()
        phone = (data.get("phone") or "").strip()
        location = (data.get("location") or "").strip()

        if not name and not phone and not location:
            return jsonify({"error": "Missing lead information"}), 400

        print(f"📩 New lead received: {name}, {phone}, {location}")

        # 📝 Append to Google Sheet
        worksheet.append_row([name, phone, location])

        return jsonify({"status": "success", "message": "Lead captured"})
    except Exception as e:
        print("❌ Error in /lead:", e)
        return jsonify({"error": str(e)}), 500

@app.route("/audio/<filename>")
def get_audio(filename):
    return app.send_static_file(filename)

# ✅ Better language detection using langdetect
def detect_language(text):
    try:
        lang = langdetect.detect(text)
        return "es" if lang == "es" else "en"
    except:
        return "en"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)