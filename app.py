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

        # 🧠 Get assistant reply
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
            # Ensure static directory exists (Render will serve from static)
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
    This endpoint accepts partial lead data and returns either:
    - a prompt to request the next missing field (status: "incomplete"),
    - or appends the lead to Google Sheets and returns success (status: "success").
    It expects JSON: { name?, phone?, email?, location? }
    """
    try:
        data = request.get_json()
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
            return jsonify({"status": "incomplete", "next": "Great — could I get your email address so we can follow up?"}), 200

        # If we have phone but no name, ask for name
        if phone and not name and not email:
            return jsonify({"status": "incomplete", "next": "Thanks — may I have your name, please?"}), 200

        # If we have email but missing name/phone, still proceed but prefer to ask for name
        if email and not name:
            return jsonify({"status": "incomplete", "next": "Thanks! What name should we use for this lead?"}), 200

        # If we reached here, we have at least some contact info (preferably email or phone).
        # We'll append whatever we have to the sheet (include timestamp).
        timestamp = datetime.utcnow().isoformat()
        row = [timestamp, name or "-", phone or "-", email or "-", location or "-"]
        worksheet.append_row(row)

        print(f"📩 New lead saved: {row}")

        return jsonify({"status": "success", "message": "Lead captured", "lead": {"name": name, "phone": phone, "email": email, "location": location}}), 200

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