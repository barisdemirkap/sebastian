"""Local voice in/out (ADR 0005). Everything runs on this Mac through binaries: ffmpeg, whisper-cli, say.

Voice never leaves the machine: no cloud speech API. Opt-in via SEBASTIAN_VOICE=1. Temp files live in a private
(0700) directory that is deleted afterwards. Failures return None, never raise."""
from __future__ import annotations
import os
import shutil
import subprocess
import sys
import tempfile

MAX_SECONDS = 120
MAX_BYTES = 5 * 1024 * 1024
STT_TIMEOUT, CONVERT_TIMEOUT, SAY_TIMEOUT = 120, 30, 30
VOICE = "Yelda"
SPEAK_MAX_CHARS, SPEAK_MAX_LINES, SPEAK_MAX_MESSAGES = 300, 6, 2


def enabled(env=None) -> bool:
    return (env if env is not None else os.environ).get("SEBASTIAN_VOICE") == "1"


def short_enough(text: str) -> bool:
    return len(text) <= SPEAK_MAX_CHARS and len(text.splitlines()) <= SPEAK_MAX_LINES


class Voice:
    def __init__(self, env=None, runner=subprocess.run, which=shutil.which, exists=os.path.exists):
        self.env = env if env is not None else os.environ
        self.run, self.which, self.exists = runner, which, exists

    @property
    def model(self) -> str:
        return self.env.get("SEBASTIAN_WHISPER_MODEL", "")

    def missing(self) -> list[str]:
        out = [b for b in ("ffmpeg", "whisper-cli", "say") if not self.which(b)]
        if not self.model or not self.exists(self.model):
            out.append("SEBASTIAN_WHISPER_MODEL")
        return out

    def _run(self, cmd, timeout, **kw):
        return self.run(cmd, capture_output=True, timeout=timeout, check=False, **kw)

    def transcribe(self, audio: bytes, suffix: str = ".oga") -> str | None:
        """Telegram audio bytes -> Turkish transcript, or None on any failure or empty result."""
        if not audio or len(audio) > MAX_BYTES:
            return None
        d = tempfile.mkdtemp(prefix="sebastian-voice-")
        try:
            src, wav = os.path.join(d, "in" + suffix), os.path.join(d, "in.wav")
            with open(src, "wb") as f:
                f.write(audio)
            if self._run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-ar", "16000", "-ac", "1", wav], CONVERT_TIMEOUT).returncode != 0:
                return None
            r = self._run([self.env.get("SEBASTIAN_WHISPER_BIN", "whisper-cli"), "-m", self.model, "-l", "tr", "-nt", "-np", "-f", wav], STT_TIMEOUT)
            if r.returncode != 0:
                return None
            out = r.stdout.decode("utf-8", "replace") if isinstance(r.stdout, bytes) else (r.stdout or "")
            return " ".join(out.split()) or None
        except Exception as exc:  # noqa: BLE001
            print(f"voice transcribe error: {type(exc).__name__}", file=sys.stderr)
            return None
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def synthesize(self, text: str) -> bytes | None:
        """Text -> ogg/opus bytes for sendVoice, or None on failure."""
        if not text.strip():
            return None
        d = tempfile.mkdtemp(prefix="sebastian-voice-")
        try:
            aiff, ogg = os.path.join(d, "o.aiff"), os.path.join(d, "o.ogg")
            if self._run(["say", "-v", VOICE, "-o", aiff, "-f", "-"], SAY_TIMEOUT, input=text.encode("utf-8")).returncode != 0:
                return None
            if self._run(["ffmpeg", "-y", "-loglevel", "error", "-i", aiff, "-c:a", "libopus", "-b:a", "32k", ogg], CONVERT_TIMEOUT).returncode != 0:
                return None
            with open(ogg, "rb") as f:
                return f.read() or None
        except Exception as exc:  # noqa: BLE001
            print(f"voice synth error: {type(exc).__name__}", file=sys.stderr)
            return None
        finally:
            shutil.rmtree(d, ignore_errors=True)
