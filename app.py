import os
import yt_dlp
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder="static")
CORS(app)

YDL_OPTS = {
    "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    },
}


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/download", methods=["POST"])
def download():
    data = request.get_json(silent=True)
    if not data or not data.get("url"):
        return jsonify({"error": "Missing URL"}), 400

    url = data["url"].strip()
    if not url:
        return jsonify({"error": "URL cannot be empty"}), 400

    try:
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            return jsonify({"error": "Could not extract video info"}), 422

        # Resolve direct URL — prefer formats list for best mp4
        video_url = None
        if info.get("formats"):
            # Walk formats in reverse (highest quality last)
            for fmt in reversed(info["formats"]):
                if fmt.get("ext") == "mp4" and fmt.get("url"):
                    video_url = fmt["url"]
                    break
            # Fallback: any format with a URL
            if not video_url:
                for fmt in reversed(info["formats"]):
                    if fmt.get("url"):
                        video_url = fmt["url"]
                        break

        if not video_url:
            video_url = info.get("url")

        if not video_url:
            return jsonify({"error": "No downloadable stream found for this tweet"}), 422

        return jsonify({
            "url": video_url,
            "title": info.get("title") or info.get("description") or "Twitter Video",
            "thumbnail": info.get("thumbnail") or "",
        })

    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        # Strip verbose yt-dlp prefix
        if "ERROR:" in msg:
            msg = msg.split("ERROR:")[-1].strip()
        return jsonify({"error": msg}), 422
    except Exception as e:
        return jsonify({"error": "Unexpected error: " + str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
