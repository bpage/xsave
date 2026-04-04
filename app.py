import os
import tempfile
import threading
import urllib.request
import yt_dlp
from flask import Flask, request, jsonify, send_from_directory, send_file, Response, stream_with_context
from flask_cors import CORS

app = Flask(__name__, static_folder="static")
CORS(app)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# Info-only opts — no download, prefer direct mp4 over HLS
INFO_OPTS = {
    "format": "best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=mp4]/best",
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "http_headers": {"User-Agent": UA},
}


def _is_hls(url):
    return url and (".m3u8" in url or "m3u8" in url)


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/privacy")
def privacy():
    return send_from_directory("static", "privacy.html")


@app.route("/about")
def about():
    return send_from_directory("static", "about.html")


@app.route("/dmca")
def dmca():
    return send_from_directory("static", "dmca.html")


@app.route("/contact")
def contact():
    return send_from_directory("static", "contact.html")


@app.route("/sitemap.xml")
def sitemap():
    return send_from_directory("static", "sitemap.xml", mimetype="application/xml")


@app.route("/robots.txt")
def robots():
    return send_from_directory("static", "robots.txt", mimetype="text/plain")


@app.route("/ads.txt")
def ads_txt():
    return send_from_directory("static", "ads.txt")


@app.route("/download", methods=["POST"])
def download():
    data = request.get_json(silent=True)
    if not data or not data.get("url"):
        return jsonify({"error": "Missing URL"}), 400

    url = data["url"].strip()
    if not url:
        return jsonify({"error": "URL cannot be empty"}), 400

    try:
        with yt_dlp.YoutubeDL(INFO_OPTS) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            return jsonify({"error": "Could not extract video info"}), 422

        # Pick the best direct mp4 URL — skip HLS manifests
        video_url = None
        hls_fallback = None

        if info.get("formats"):
            for fmt in reversed(info["formats"]):
                fmt_url = fmt.get("url", "")
                if fmt.get("ext") == "mp4" and not _is_hls(fmt_url) and fmt_url:
                    video_url = fmt_url
                    break
            # Keep an HLS fallback in case nothing else exists
            if not video_url:
                for fmt in reversed(info["formats"]):
                    if fmt.get("url"):
                        if _is_hls(fmt["url"]):
                            hls_fallback = fmt["url"]
                        else:
                            video_url = fmt["url"]
                            break

        if not video_url:
            top = info.get("url", "")
            if _is_hls(top):
                hls_fallback = top
            else:
                video_url = top

        title = info.get("title") or info.get("description") or "Twitter Video"
        thumbnail = info.get("thumbnail") or ""

        # If we only have HLS, tell the frontend to use /convert instead
        if not video_url and hls_fallback:
            return jsonify({
                "url": hls_fallback,
                "title": title,
                "thumbnail": thumbnail,
                "hls": True,
            })

        if not video_url:
            return jsonify({"error": "No downloadable stream found for this tweet"}), 422

        return jsonify({"url": video_url, "title": title, "thumbnail": thumbnail, "hls": False})

    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if "ERROR:" in msg:
            msg = msg.split("ERROR:")[-1].strip()
        return jsonify({"error": msg}), 422
    except Exception as e:
        return jsonify({"error": "Unexpected error: " + str(e)}), 500


@app.route("/convert", methods=["POST"])
def convert():
    """Download an HLS stream server-side, remux to mp4, stream to client."""
    data = request.get_json(silent=True)
    if not data or not data.get("url"):
        return jsonify({"error": "Missing URL"}), 400

    source_url = data["url"].strip()

    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp_path = tmp.name
    tmp.close()

    convert_opts = {
        "format": "best[ext=mp4]/best",
        "outtmpl": tmp_path,
        "quiet": True,
        "no_warnings": True,
        "http_headers": {"User-Agent": UA},
        "socket_timeout": 30,
        "retries": 2,
        "merge_output_format": "mp4",
    }

    try:
        with yt_dlp.YoutubeDL(convert_opts) as ydl:
            ydl.download([source_url])
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return jsonify({"error": "Conversion failed: " + str(e)}), 500

    def remove_after_send(path):
        try:
            os.unlink(path)
        except OSError:
            pass

    response = send_file(
        tmp_path,
        mimetype="application/octet-stream",
        as_attachment=True,
        download_name="xsave-video.mp4",
    )

    # Clean up temp file after response is sent
    threading.Timer(60, remove_after_send, args=[tmp_path]).start()
    return response


@app.route("/proxy-download")
def proxy_download():
    """Stream a video URL through the server so all browsers get a native download."""
    video_url = request.args.get("url", "").strip()
    if not video_url:
        return "Missing url parameter", 400

    # Basic sanity check — only allow http/https URLs
    if not video_url.startswith(("http://", "https://")):
        return "Invalid URL", 400

    req = urllib.request.Request(video_url, headers={"User-Agent": UA})

    try:
        upstream = urllib.request.urlopen(req, timeout=30)
    except Exception as e:
        return f"Could not fetch video: {e}", 502

    content_length = upstream.headers.get("Content-Length")

    def generate():
        try:
            while True:
                chunk = upstream.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            upstream.close()

    headers = {
        "Content-Type": "video/mp4",
        "Content-Disposition": 'attachment; filename="xsave-video.mp4"',
    }
    if content_length:
        headers["Content-Length"] = content_length

    return Response(
        stream_with_context(generate()),
        status=200,
        headers=headers,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
