from flask import Flask, request, jsonify
from flask_cors import CORS
import openai
import os
from gtts import gTTS
import uuid
import langdetect

app = Flask(__name__)
CORS(app)

# ✅ OpenAI client using environment variables (Render safe)
client = openai.OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    project=os.getenv("OPENAI_PROJECT_ID")
)

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
        name = data.get("name")
        phone = data.get("phone")
        location = data.get("location")

        print(f"📩 New lead received: {name}, {phone}, {location}")
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