from flask import Flask, request, jsonify
from flask_cors import CORS
import openai
import os

app = Flask(__name__)
CORS(app)

# ✅ Use the official OpenAI client with project support
client = openai.OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    project=os.getenv("OPENAI_PROJECT_ID")  # required for sk-proj keys
)

@app.route("/")
def home():
    return "<h1>✅ IBT Connect Flask Server is Running</h1>"

@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json()
        message = data.get("message", "")

        if not message:
            return jsonify({"error": "No message provided"}), 400

        # 🧠 Chat completion request
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that answers user questions politely."},
                {"role": "user", "content": message}
            ],
            temperature=0.7
        )

        reply = response.choices[0].message.content.strip()
        return jsonify({"reply": reply})

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)