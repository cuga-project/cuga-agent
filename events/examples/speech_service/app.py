"""Reference speech service — the other side of ``provider: http`` in the events turn pipelines.

It implements the contract (see ``src/cuga/backend/events/turns/services.py``) in front of OPEN
models, so a deployment can replace Sarvam with no vendor key. Any team can ship their own service
with the same endpoints; the eventing layer cannot tell the difference, which is the point.

THE SERVICE OWNS THE HARD PARTS. The events layer sends audio and gets text, sends text and gets
audio — that is all it knows. Which model hears the audio, which voice answers, which language it is,
and what to strip before speaking (markdown, links, page citations) are decided HERE, where they can
change without touching the channel or the agent.

    POST /v1/speech-to-text   multipart: file, [language], [model]            → {text, language, confidence}
    POST /v1/text-to-speech   json: {text, language, [voice], [accept], [params]} → audio (Content-Type set)
    POST /v1/translate        → 501 (this service does speech only; use provider: llm for translation)
    GET  /v1/capabilities     what this instance can do

ENGINES are chosen per instance by env, so one image serves several roles:

    SPEECH_STT=whisper:large-v3-turbo | none      faster-whisper
    SPEECH_STT_LANGUAGE=te-IN                     pin it for a single-language deployment (Whisper
                                                  mishears unhinted Telugu as Tamil)
    SPEECH_TTS=piper | parler | mms | none        the default engine
    SPEECH_TTS_BY_LANGUAGE='{"ta-IN": "parler"}'  a different engine where the default has no voice
    SPEECH_VOICES='{"te-IN": "te_IN-venkatesh-medium"}'   engine-specific voice per language
    SPEECH_MAX_CHARS=700                          how much of a long answer to speak

What we measured on the Farm Assistant review (round-trip character error rate, Whisper listening):
  piper   hi 0.09 · te 0.04-0.08 · no Tamil     0.2-0.3 s per 5 s of speech (CPU)   GPL engine, per-voice licenses
  parler  hi 0.09 · ta 0.09 · te 0.08           ~3.4x real time on Apple GPU, ~9x on CPU   Apache-2.0, gated on HF
  mms     hi 0.03 · ta 0.09 · te 0.19           ~1 s                                 CC-BY-NC (non-commercial)
Piper's macOS wheel is broken (espeak-ng data path); run the piper role in the Linux container.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import threading
import wave

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

app = FastAPI(title="speech service", docs_url=None, redoc_url=None)
_LOCK = threading.Lock()
_CACHE: dict = {}


def _env_json(key: str) -> dict:
    try:
        return json.loads(os.environ.get(key, "") or "{}")
    except json.JSONDecodeError:
        return {}


# Indic Unicode blocks, 0x80 wide from U+0900 — enough to tell which language an answer is written in
# when the caller does not say. The events layer no longer tracks language at all.
_BLOCKS = ("hi-IN", "bn-IN", "pa-IN", "gu-IN", "or-IN", "ta-IN", "te-IN", "kn-IN", "ml-IN")


def detect_language(text: str, default: str = "en-IN") -> str:
    counts: dict[str, int] = {}
    for ch in text or "":
        idx = (ord(ch) - 0x0900) // 0x80
        if 0 <= idx < len(_BLOCKS):
            counts[_BLOCKS[idx]] = counts.get(_BLOCKS[idx], 0) + 1
    return max(counts, key=counts.get) if counts else default


_MARKDOWN = [
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),  # [label](url) → label
    (re.compile(r"https?://\S+"), ""),
    (re.compile(r"\*\*|__|`|~~"), ""),
    (re.compile(r"^\s{0,3}#{1,6}\s*", re.M), ""),
    (re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.M), ""),
    (re.compile(r"\(\s*(?:p|pp|page|pages|पृ)\.?\s*[\d,\s–-]+\)", re.I), ""),  # (p. 29)
    (re.compile(r"[ \t]+"), " "),
    (re.compile(r"\n{2,}"), "\n"),
]
_SENTENCE_END = re.compile(r"[.!?।॥](?=\s|$)")


def speakable(text: str, max_chars: int) -> str:
    """An answer written to be READ contains markdown, links and page citations. Spoken aloud they
    are noise, so they come off here — the caller's text reply keeps them."""
    s = text or ""
    for rx, rep in _MARKDOWN:
        s = rx.sub(rep, s)
    s = s.strip()
    if len(s) > max_chars:
        cut = s[:max_chars]
        ends = [m.end() for m in _SENTENCE_END.finditer(cut)]
        s = (cut[: ends[-1]] if ends else cut).strip()
    return s


def _lang(code: str) -> str:
    c = (code or "").replace("_", "-").strip()
    lang, _, region = c.partition("-")
    lang = {"od": "or"}.get(lang.lower(), lang.lower())
    return f"{lang}-{region.upper()}" if region else lang


def _once(key, build):
    with _LOCK:
        if key not in _CACHE:
            _CACHE[key] = build()
        return _CACHE[key]


# ── speech-to-text ──────────────────────────────────────────────────────────────────────────────
class WhisperSTT:
    def __init__(self, model: str):
        self.model = model or "large-v3-turbo"

    def transcribe(self, data: bytes, language: str) -> dict:
        from faster_whisper import WhisperModel

        m = _once(
            ("whisper", self.model), lambda: WhisperModel(self.model, device="cpu", compute_type="int8")
        )
        hint = _lang(language).split("-")[0] or None
        segments, info = m.transcribe(io.BytesIO(data), language=hint, beam_size=5)
        text = "".join(s.text for s in segments).strip()
        return {
            "text": text,
            "language": info.language,
            "confidence": round(float(info.language_probability), 3),
        }


# ── text-to-speech ──────────────────────────────────────────────────────────────────────────────
_SENT = re.compile(r"(?<=[.!?।॥])\s+")


def _chunks(text: str, limit: int) -> list[str]:
    """Sentence-sized pieces: long prompts degrade Parler/MMS and blow up their latency."""
    out, cur = [], ""
    for s in _SENT.split(text.strip()):
        if cur and len(cur) + len(s) + 1 > limit:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    return [c for c in out + [cur] if c]


def _wav(samples, rate: int) -> bytes:
    import numpy as np

    pcm = (np.clip(np.asarray(samples, dtype="float32"), -1, 1) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


class PiperTTS:
    DEFAULT = {
        "hi-IN": "hi_IN-rohan-medium",
        "te-IN": "te_IN-venkatesh-medium",
        "ml-IN": "ml_IN-meera-medium",
        "mr-IN": "mr_IN-google-medium",
    }

    def __init__(self, voices: dict):
        self.voices = {**self.DEFAULT, **voices}

    def _voice(self, vid: str):
        from huggingface_hub import hf_hub_download
        from piper import PiperVoice

        locale, name, quality = vid.split("-", 2)
        path = f"{locale.split('_')[0]}/{locale}/{name}/{quality}/{vid}"
        onnx = hf_hub_download("rhasspy/piper-voices", path + ".onnx")
        hf_hub_download("rhasspy/piper-voices", path + ".onnx.json")
        return PiperVoice.load(onnx)

    def synthesize(self, text: str, language: str, voice: str) -> bytes:
        vid = voice or self.voices.get(_lang(language))
        if not vid:
            raise HTTPException(422, f"piper has no voice for {language}")
        v = _once(("piper", vid), lambda: self._voice(vid))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            v.synthesize_wav(text, w)
        return buf.getvalue()


class ParlerTTS:
    DEFAULT = {  # the model card's recommended speakers
        "hi-IN": "Divya",
        "ta-IN": "Jaya",
        "te-IN": "Lalitha",
        "kn-IN": "Anu",
        "ml-IN": "Anjali",
        "bn-IN": "Aditi",
        "mr-IN": "Sunita",
        "gu-IN": "Neha",
        "or-IN": "Debjani",
        "en-IN": "Mary",
    }

    def __init__(self, voices: dict):
        self.voices = {**self.DEFAULT, **voices}

    def _model(self):
        import torch
        from parler_tts import ParlerTTSForConditionalGeneration
        from transformers import AutoTokenizer

        dev = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
        model = ParlerTTSForConditionalGeneration.from_pretrained("ai4bharat/indic-parler-tts").to(dev)
        tok = AutoTokenizer.from_pretrained("ai4bharat/indic-parler-tts")
        dtok = AutoTokenizer.from_pretrained(model.config.text_encoder._name_or_path)
        return model, tok, dtok, dev

    def synthesize(self, text: str, language: str, voice: str) -> bytes:
        import numpy as np
        import torch

        model, tok, dtok, dev = _once("parler", self._model)
        speaker = voice or self.voices.get(_lang(language), "Divya")
        desc = dtok(
            f"{speaker} speaks at a moderate pace with a warm, clear tone. Very clear audio, with no background noise.",
            return_tensors="pt",
        ).to(dev)
        pieces = []
        for chunk in _chunks(text, 220):
            p = tok(chunk, return_tensors="pt").to(dev)
            with torch.no_grad():
                g = model.generate(
                    input_ids=desc.input_ids,
                    attention_mask=desc.attention_mask,
                    prompt_input_ids=p.input_ids,
                    prompt_attention_mask=p.attention_mask,
                )
            pieces += [
                g.cpu().numpy().squeeze(),
                np.zeros(int(model.config.sampling_rate * 0.25), dtype="float32"),
            ]
        return _wav(np.concatenate(pieces), model.config.sampling_rate)


class MmsTTS:
    ISO3 = {
        "hi-IN": "hin",
        "ta-IN": "tam",
        "te-IN": "tel",
        "kn-IN": "kan",
        "ml-IN": "mal",
        "bn-IN": "ben",
        "mr-IN": "mar",
        "gu-IN": "guj",
        "pa-IN": "pan",
        "or-IN": "ory",
    }

    def __init__(self, voices: dict):
        self.voices = voices

    def synthesize(self, text: str, language: str, voice: str) -> bytes:
        import numpy as np
        import torch
        from transformers import AutoTokenizer, VitsModel

        code = voice or self.voices.get(_lang(language)) or self.ISO3.get(_lang(language))
        if not code:
            raise HTTPException(422, f"mms has no model for {language}")
        m, tok = _once(
            ("mms", code),
            lambda: (
                VitsModel.from_pretrained(f"facebook/mms-tts-{code}"),
                AutoTokenizer.from_pretrained(f"facebook/mms-tts-{code}"),
            ),
        )
        pieces = []
        for chunk in _chunks(text, 300):
            with torch.no_grad():
                pieces.append(m(**tok(chunk, return_tensors="pt")).waveform.squeeze().numpy())
        return _wav(np.concatenate(pieces), m.config.sampling_rate)


def _engine(kind: str, spec: str, voices: dict):
    name, _, model = (spec or "none").partition(":")
    if name == "none":
        return None
    table = {
        "stt": {"whisper": lambda: WhisperSTT(model)},
        "tts": {
            "piper": lambda: PiperTTS(voices),
            "parler": lambda: ParlerTTS(voices),
            "mms": lambda: MmsTTS(voices),
        },
    }
    try:
        return table[kind][name]()
    except KeyError as e:
        raise SystemExit(f"unknown {kind} engine {name!r}; known: {sorted(table[kind])}") from e


STT = _engine("stt", os.environ.get("SPEECH_STT", "whisper:large-v3-turbo"), {})
STT_LANGUAGE = os.environ.get("SPEECH_STT_LANGUAGE", "").strip()
TTS_NAME = os.environ.get("SPEECH_TTS", "piper").partition(":")[0]
TTS = _engine("tts", os.environ.get("SPEECH_TTS", "piper"), _env_json("SPEECH_VOICES"))
# A second engine for languages the default one has no voice for (Piper has no Tamil; Parler does).
TTS_BY_LANGUAGE = {
    _lang(k): _engine("tts", v, _env_json("SPEECH_VOICES"))
    for k, v in _env_json("SPEECH_TTS_BY_LANGUAGE").items()
}
MAX_CHARS = int(os.environ.get("SPEECH_MAX_CHARS", "700"))


# ── format: what the caller's channel can play ──────────────────────────────────────────────────
_ENCODE = {
    "audio/ogg": (["-c:a", "libopus", "-b:a", "32k", "-f", "ogg"], "audio/ogg; codecs=opus"),
    "audio/mpeg": (["-c:a", "libmp3lame", "-b:a", "64k", "-f", "mp3"], "audio/mpeg"),
}


def _deliverable(wav: bytes, accept: list[str]) -> tuple[bytes, str]:
    for a in accept or []:
        base = a.split(";", 1)[0].strip().lower()
        if base in ("audio/wav", "audio/x-wav"):
            return wav, "audio/wav"
        if base in _ENCODE and shutil.which("ffmpeg"):
            args, mime = _ENCODE[base]
            p = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", *args, "pipe:1"],
                input=wav,
                capture_output=True,
                timeout=120,
            )
            if p.returncode == 0 and p.stdout:
                return p.stdout, mime
    return wav, "audio/wav"  # the caller transcodes, or falls back to text


# ── routes ──────────────────────────────────────────────────────────────────────────────────────
@app.get("/v1/capabilities")
def capabilities():
    return {
        "speech_to_text": {"engine": os.environ.get("SPEECH_STT", "whisper:large-v3-turbo")} if STT else None,
        "text_to_speech": {
            "engine": TTS_NAME,
            "by_language": {k: type(v).__name__ for k, v in TTS_BY_LANGUAGE.items()},
            "voices": getattr(TTS, "voices", {}),
            "max_chars": MAX_CHARS,
        }
        if TTS
        else None,
        "ffmpeg": bool(shutil.which("ffmpeg")),
    }


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/v1/speech-to-text")
def speech_to_text(
    file: UploadFile = File(...), language: str = Form(""), model: str = Form(""), params: str = Form("{}")
):
    if STT is None:
        raise HTTPException(501, "this instance has no speech-to-text engine (SPEECH_STT=none)")
    data = file.file.read()
    if not data:
        raise HTTPException(400, "empty audio")
    return STT.transcribe(data, language)


class TTSRequest(BaseModel):
    text: str
    language: str = ""  # the caller need not know; detected from the text when absent
    voice: str = ""
    model: str = ""
    accept: list[str] = []
    params: dict = {}


@app.post("/v1/text-to-speech")
def text_to_speech(req: TTSRequest):
    """Text in, audio out. The caller sends the answer as written and what its channel can play;
    everything else — language, voice, engine, and what not to read aloud — is decided here."""
    if TTS is None:
        raise HTTPException(501, "this instance has no text-to-speech engine (SPEECH_TTS=none)")
    text = speakable(req.text, MAX_CHARS)
    if not text:
        raise HTTPException(400, "empty text")
    language = _lang(req.language) if req.language else detect_language(text)
    engine = TTS_BY_LANGUAGE.get(language, TTS)
    voice = req.voice or (req.params.get("voices") or {}).get(language, "")
    wav = engine.synthesize(text, language, voice)
    data, mime = _deliverable(wav, req.accept)
    return Response(
        content=data, media_type=mime, headers={"X-Language": language, "X-Spoken-Chars": str(len(text))}
    )


@app.post("/v1/translate")
def translate():
    return JSONResponse({"error": "this service does speech only; bind translation to provider: llm"}, 501)
