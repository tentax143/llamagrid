from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

import requests
from flask import Blueprint, Flask, Response, jsonify, request, send_from_directory, stream_with_context

from shared.protocol import HeartbeatRequest, RegisterRequest
from host import model_scanner

if TYPE_CHECKING:
    from host.config import HostConfig
    from host.coordinator import Coordinator
    from host.llama_manager import LlamaManager

log = logging.getLogger(__name__)

WEBROOT = os.path.join(os.path.dirname(__file__), "webroot")


def build_flask_app(coord: "Coordinator", llama: "LlamaManager", cfg: "HostConfig") -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["JSON_SORT_KEYS"] = False

    # ------------------------------------------------------------------ static

    @app.route("/")
    def index():
        return send_from_directory(WEBROOT, "index.html")

    @app.route("/<path:filename>")
    def static_files(filename):
        return send_from_directory(WEBROOT, filename)

    # ------------------------------------------------------------------ peer API

    @app.route("/api/register", methods=["POST"])
    def api_register():
        try:
            req = RegisterRequest.model_validate(request.get_json(force=True))
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        source_ip = request.remote_addr
        resp = coord.register(req, source_ip)
        return jsonify(resp.model_dump())

    @app.route("/api/heartbeat", methods=["POST"])
    def api_heartbeat():
        try:
            req = HeartbeatRequest.model_validate(request.get_json(force=True))
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        source_ip = request.remote_addr
        resp = coord.heartbeat(req, source_ip)
        if not resp.acknowledged:
            # Unknown peer — tell it to re-register
            return jsonify(resp.model_dump()), 404
        return jsonify(resp.model_dump())

    @app.route("/api/peer/error", methods=["POST"])
    def api_peer_error():
        data = request.get_json(force=True) or {}
        peer_id = data.get("peer_id", "")
        code = data.get("code", "UNKNOWN")
        message = data.get("message", "")
        # Lightweight auth
        token = data.get("auth_token", "")
        if token != cfg.auth_token:
            return jsonify({"error": "unauthorized"}), 401
        coord.report_error(peer_id, code, message)
        return jsonify({"ok": True})

    # ------------------------------------------------------------------ dashboard API

    @app.route("/api/peers", methods=["GET"])
    def api_peers():
        peers = coord.all_peers()
        return jsonify([p.to_dict() for p in peers])

    @app.route("/api/peers/<peer_id>", methods=["GET"])
    def api_peer_detail(peer_id):
        peer = coord.get_peer(peer_id)
        if peer is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(peer.to_dict())

    @app.route("/api/peers/<peer_id>/restart", methods=["POST"])
    def api_peer_restart(peer_id):
        ok = coord.queue_command(peer_id, "restart_rpc")
        if not ok:
            return jsonify({"error": "peer not found"}), 404
        return jsonify({"queued": True})

    @app.route("/api/peers/<peer_id>/update", methods=["POST"])
    def api_peer_update(peer_id):
        from shared.versioning import LLAMA_CPP_RELEASE_URL, LLAMA_CPP_SHA256
        payload = {
            "download_url": LLAMA_CPP_RELEASE_URL,
            "sha256": LLAMA_CPP_SHA256,
            "version": cfg.auth_token,
        }
        ok = coord.queue_command(peer_id, "update_rpc", payload)
        if not ok:
            return jsonify({"error": "peer not found"}), 404
        return jsonify({"queued": True})

    @app.route("/api/peers/<peer_id>/forget", methods=["POST"])
    def api_peer_forget(peer_id):
        ok = coord.forget_peer(peer_id)
        if not ok:
            return jsonify({"error": "peer not found"}), 404
        return jsonify({"forgotten": True})

    @app.route("/api/cluster", methods=["GET"])
    def api_cluster():
        summary = coord.cluster_summary(
            tokens_per_sec=llama.tokens_per_sec(),
            llama_running=llama.is_running(),
            model_path=cfg.model_path,
        )
        return jsonify(summary)

    @app.route("/api/models", methods=["GET"])
    def api_models():
        models = model_scanner.scan(cfg.model_dir)
        return jsonify([m.model_dump() for m in models])

    @app.route("/api/model/select", methods=["POST"])
    def api_model_select():
        data = request.get_json(force=True) or {}
        path = data.get("path", "")
        if not os.path.isfile(path):
            return jsonify({"error": "file not found"}), 400
        llama.update_model(path)
        return jsonify({"ok": True, "model": os.path.basename(path)})

    @app.route("/api/alerts", methods=["GET"])
    def api_alerts():
        alerts = coord.all_alerts()
        return jsonify([a.model_dump() for a in alerts])

    @app.route("/api/version", methods=["GET"])
    def api_version():
        from shared.versioning import HOST_VERSION, PROTOCOL_VERSION, LLAMA_CPP_PINNED_TAG
        return jsonify({
            "host_version": HOST_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "rpc_server_pinned": LLAMA_CPP_PINNED_TAG,
            "auth_token": cfg.auth_token,
        })

    # ------------------------------------------------------------------ inference proxy

    @app.route("/api/inference", methods=["POST"])
    def api_inference():
        if not llama.is_running():
            return jsonify({"error": "llama-server is not running"}), 503

        data = request.get_json(force=True) or {}
        prompt = data.get("prompt", "")
        max_tokens = int(data.get("max_tokens", 512))
        temperature = float(data.get("temperature", 0.7))

        llama_url = f"http://127.0.0.1:{cfg.llama_server_port}/completion"
        payload = {
            "prompt": prompt,
            "n_predict": max_tokens,
            "temperature": temperature,
            "stream": True,
        }

        def generate():
            try:
                with requests.post(llama_url, json=payload, stream=True, timeout=300) as r:
                    for chunk in r.iter_content(chunk_size=None):
                        if chunk:
                            yield chunk
            except requests.ConnectionError:
                yield b'{"error":"llama-server connection refused"}'
            except Exception as e:
                yield f'{{"error":"{str(e)}"}}'.encode()

        return Response(stream_with_context(generate()), content_type="application/json")

    # ------------------------------------------------------------------ llama-server control

    @app.route("/api/llama/start", methods=["POST"])
    def api_llama_start():
        if llama.is_running():
            return jsonify({"ok": True, "message": "already running"})
        llama.start()
        return jsonify({"ok": True})

    @app.route("/api/llama/stop", methods=["POST"])
    def api_llama_stop():
        llama.stop()
        llama._running = False
        return jsonify({"ok": True})

    @app.route("/api/llama/restart", methods=["POST"])
    def api_llama_restart():
        llama.restart()
        return jsonify({"ok": True})

    return app
