"""Manual local voice comparison with synthetic copy, never private newsletters.

No accounts, credentials, LLM, publishing, or scheduled jobs are used. Offline by
default. --download-models allows missing public model/voice downloads from the
official Hugging Face repository; speech synthesis still runs on this machine.
"""

from __future__ import annotations

import argparse
import html
import os
import sys
import wave
from pathlib import Path

# Run directly from a checkout without loading the deployment configuration.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from audiodigest.audio import KokoroAudioRenderer  # noqa: E402
from audiodigest.config import AudioSettings, HostSettings  # noqa: E402
from audiodigest.speech import prepare_speech  # noqa: E402

VOICES = {
    "af_heart": ("Dalia — warm", "a"),
    "af_bella": ("Dalia — expressive", "a"),
    "bf_emma": ("Dalia — British", "b"),
    "am_michael": ("Nox — warm", "a"),
    "am_eric": ("Nox — analytical (current option)", "a"),
    "am_puck": ("Nox — energetic", "a"),
    "am_fenrir": ("Nox — candidate, not enabled in production", "a"),
}
# Identical invented facts in both versions. These are not claims about real news.
BASELINE = (
    "This is a fictional voice comparison for The Daily Nexus, not a news report. "
    "The demonstration company has announced the release of version 1.2.0 of its "
    "database connector, which supports an API and SQL queries, with the stated "
    "objective of improving how engineering teams move data between systems. "
    "The pilot involves twelve teams, and the reported reduction in processing time "
    "is 18.5%, although the results have not been independently verified. "
    "The system also uses an LLM to categorize requests, but it does not automatically "
    "change production records. The important question concerns whether the benefits "
    "will persist as the pilot expands. No expansion date has been announced. "
    "That completes the demonstration, which was designed to compare voices rather "
    "than to establish the truth of the fictional figures."
)
NATURAL = (
    "This is a fictional voice comparison for The Daily Nexus, not a news report. "
    "A demonstration company has released version 1.2.0 of its database connector. "
    "It supports an API and SQL queries. The aim? Help engineering teams move data "
    "between systems. Twelve teams are testing it. Processing time fell by 18.5%, "
    "according to the pilot report. That's promising, but the results haven't been "
    "independently verified. An LLM categorizes requests. It doesn't automatically "
    "change production records, though. So there's a clear limit to what it can do. "
    "Will the benefits hold up when the pilot grows? We don't know yet, and there's "
    "no expansion date. That's the end of this demonstration. The figures are "
    "fictional: we're comparing voices, not reporting a real product launch."
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("runtime/voice-preview"))
    parser.add_argument(
        "--voices",
        nargs="+",
        choices=tuple(VOICES),
        default=["af_heart", "am_michael", "am_fenrir"],
    )
    parser.add_argument("--speeds", nargs="+", type=float, default=[1.0])
    parser.add_argument("--download-models", action="store_true")
    args = parser.parse_args()
    if any(not 0.90 <= speed <= 1.10 for speed in args.speeds) or len(args.speeds) > 3:
        parser.error("Use up to three synthesis speeds from 0.90 to 1.10")

    if not args.download_models:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"  # noqa: S105 - Disable token use.
    os.environ["DO_NOT_TRACK"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"

    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    renderer = KokoroAudioRenderer(AudioSettings(), HostSettings())
    pipelines = {}
    rows = []
    failures = 0
    for voice in dict.fromkeys(args.voices):
        label, language = VOICES[voice]
        for speed in dict.fromkeys(args.speeds):
            renderer.settings.synthesis_speed = speed
            for edition, text in (("baseline", BASELINE), ("natural", NATURAL)):
                filename = f"{voice}-{edition}-{speed:.2f}.wav"
                path = root / filename
                if path.exists():
                    print(f"Keeping existing preview: {filename}", flush=True)
                else:
                    print(f"Rendering {filename} locally...", flush=True)
                    try:
                        if language not in pipelines:
                            pipelines[language] = renderer._pipeline(language)
                        renderer._write_speech_chunk(
                            pipelines[language],
                            prepare_speech(text) if edition == "natural" else text,
                            path,
                            voice=voice,
                        )
                    except Exception as exc:
                        # Do not echo provider errors that might contain local account paths.
                        print(
                            f"Preview unavailable: {filename} ({type(exc).__name__}). "
                            "Check local dependencies and "
                            "cached weights; use --download-models for official public assets.",
                            flush=True,
                        )
                        failures += 1
                        continue
                with wave.open(str(path), "rb") as audio:
                    seconds = audio.getnframes() / audio.getframerate()
                rows.append(
                    f"<article><h2>{html.escape(label)} · {edition} · {speed:.2f}x</h2>"
                    f"<p>{seconds:.1f} seconds · <code>{voice}</code></p>"
                    f'<audio controls preload="none" src="{filename}"></audio></article>'
                )

    page = (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>The Daily Nexus · local voice comparison</title>"
        "<style>body{background:#160d06;color:#ffe6ac;font:16px system-ui;"
        "max-width:850px;margin:30px auto;padding:20px}article{border:1px solid #8a4a18;"
        "border-radius:14px;padding:16px;margin:15px 0}h1,h2{color:#ff9c19}"
        "h2{font-size:18px}audio{width:100%}summary{cursor:pointer}</style>"
        "<h1>The Daily Nexus · voice comparison</h1>"
        "<p>Local, fictional examples. No Gmail, AI editor, cloud runner or publishing. "
        "Voice quality is subjective: listen before changing a favorite or default.</p>"
        "<p>Compare clarity, numbers and versions, emphasis, fatigue, and character. "
        "The natural variant changes copy and pronunciation; it is not an emotion control.</p>"
        "<details><summary>Baseline copy</summary><p>" + html.escape(BASELINE) + "</p></details>"
        "<details><summary>Natural copy</summary><p>"
        + html.escape(NATURAL)
        + "</p></details>"
        + "".join(rows)
        + "</html>"
    )
    (root / "index.html").write_text(page, encoding="utf-8")
    print(f"Saved {len(rows)} comparisons; {failures} unavailable.", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
