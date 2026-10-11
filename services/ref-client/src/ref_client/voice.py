"""Optional voice: mic → speech-to-text → agent → Amazon Polly → speaker. Behind `REF_VOICE=1`; never required by
a test, never on by default.

- Speech in: `arecord` (ALSA) records `REF_VOICE_SECONDS` (default 5) of audio, then a local `whisper` CLI
  transcribes it (`pip install openai-whisper`; runs offline, no API call). Without either, the utterance is typed.
- Speech out: Polly `synthesize_speech` (voice `REF_POLLY_VOICE`, default Joanna) → `afplay`/`ffplay`/`mpg123`.
  Polly is speech synthesis, not a language model.

`ref-client say -` reads the utterance from the mic when `REF_VOICE=1`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def enabled() -> bool:
    return os.environ.get("REF_VOICE", "0") == "1"


def listen() -> str:
    """One utterance from the microphone (or typed, if no recorder/transcriber is installed)."""
    rec, stt = shutil.which("arecord"), shutil.which("whisper")
    if not (rec and stt):
        return input("you (typed; install arecord + whisper for speech)> ").strip()
    seconds = os.environ.get("REF_VOICE_SECONDS", "5")
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "utterance.wav"
        print(f"listening for {seconds} s…", flush=True)
        subprocess.run([rec, "-q", "-f", "cd", "-d", seconds, str(wav)], check=True)  # noqa: S603
        subprocess.run(  # noqa: S603
            [stt, str(wav), "--model", "base.en", "--output_format", "txt", "--output_dir", d],
            check=True,
            capture_output=True,
        )
        return (Path(d) / "utterance.txt").read_text(encoding="utf-8").strip()


def speak(text: str, *, polly: Any = None) -> None:
    import boto3

    client = polly or boto3.client("polly", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    resp = client.synthesize_speech(
        Text=text, OutputFormat="mp3", VoiceId=os.environ.get("REF_POLLY_VOICE", "Joanna")
    )
    audio = resp["AudioStream"].read()
    player = next((p for p in ("afplay", "ffplay", "mpg123") if shutil.which(p)), None)
    if player is None:
        print("(no audio player found: install mpg123 or ffmpeg)")
        return
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio)
    args = (
        [player, f.name]
        if player != "ffplay"
        else [player, "-nodisp", "-autoexit", "-loglevel", "quiet", f.name]
    )
    subprocess.run(args, check=False)  # noqa: S603
    os.unlink(f.name)


def maybe_speak(text: str) -> None:
    """Speak when `REF_VOICE=1`; a TTS failure is printed, never fatal (the text is already on screen)."""
    if not enabled() or not text:
        return
    try:
        speak(text)
    except Exception as e:  # noqa: BLE001
        print(f"(voice: TTS unavailable: {type(e).__name__})")
