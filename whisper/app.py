"""Transcrição de áudio local (faster-whisper). POST /transcribe com o arquivo no campo 'file'."""
import os, tempfile, threading
from faster_whisper import WhisperModel
from flask import Flask, jsonify, request

app = Flask(__name__)
MODEL = os.environ.get("WHISPER_MODEL", "small")
_m, _lock = None, threading.Lock()

def modelo():
    global _m
    with _lock:
        if _m is None:
            _m = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=2)
        return _m

@app.get("/health")
def health():
    return "ok"

@app.post("/transcribe")
def transcribe():
    f = request.files.get("file")
    if not f:
        return jsonify(erro="envie o arquivo no campo 'file'"), 400
    with tempfile.NamedTemporaryFile(suffix=".audio") as t:
        f.save(t.name)
        segs, info = modelo().transcribe(t.name, language=request.form.get("language") or "pt",
                                         vad_filter=True, beam_size=1,
                                         initial_prompt="Conversa com a Empresa Exemplo, loja de materiais de construção em Gurupi, Tocantins. Empresa Exemplo Casa e Construção, Empresa Exemplo.")
        texto = " ".join(s.text.strip() for s in segs).strip()
    return jsonify(text=texto, language=info.language, duration=round(info.duration, 1))
