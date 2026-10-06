# Natural delivery and voice comparison

The Daily Nexus still uses Kokoro 0.9.4 and the official Kokoro-82M model locally
on the generation machine. No paid speech API, voice cloning, or new provider is
required. Existing favorites, voice IDs and host defaults remain unchanged.

## What changes for new episodes

- The existing script-writing prompt asks for spoken English, complete but
  breathable sentences, useful questions and distinct answers. It avoids forced
  alternating blocks, repeated host names, fake hesitation and laugh directions.
- The existing verifier reviews substantial repetition and listening clarity.
  It requests local edits while preserving coverage, evidence and exact quotations.
  There is no additional mandatory LLM call or separate "naturalization" pass.
- Quiet gaps differ for host responses, headings and program boundaries. Model
  silence at both sides counts toward the gap; quiet speech is never trimmed.
- A synthesis-only dictionary uses Misaki phonemes for exact technical acronyms.
  Known software-version contexts read dots as "point". Ordinary figures, dates,
  uncertainty and unknown names are not guessed or rounded.
- The stored script and transcript keep their original text. Transcript timings
  follow the resulting WAV chunks and inserted gaps, including the concat fallback.

The tone selector controls writing, not a Kokoro emotional-acting instruction.
Voice/personality selects a voice pack; tone shapes the host's words. Sarcasm is
therefore expressed through wording, not guaranteed through vocal performance.

## Local comparison before choosing voices

After installing audio dependencies, use the project Python environment:

```powershell
python scripts/preview-kokoro-voices.py --voices af_heart am_michael am_eric
```

It creates `runtime/voice-preview/index.html` and WAV comparisons with fictional
copy, not newsletter content. Open the HTML locally. Outputs are Git-ignored and
are never put in the feed or deployed. Offline is the default; missing weights
produce a clear warning rather than an authenticated request.

To compare all current voices plus a male candidate using official public weights:

```powershell
python scripts/preview-kokoro-voices.py --download-models --voices af_heart af_bella bf_emma am_michael am_eric am_puck am_fenrir
```

Downloads go to the normal local Hugging Face cache. No Hugging Face token is
needed. Speech stays local. Do not schedule these comparisons in GitHub Actions.

Compare the same content for intelligibility, technical terms, pacing, listener
fatigue and personality. `am_fenrir` is preview-only: adding or replacing a
production option should follow listening, not an upstream training-data grade.
Newer `v1.1-zh` is not a drop-in replacement for the current English voices.

Optional speed comparison (separate output prevents mixing old and new samples):

```powershell
python scripts/preview-kokoro-voices.py --voices af_heart am_michael --speeds 0.98 1.0 --output-dir runtime/voice-speed-preview
```

Generation speed is optional local/cloud TOML configuration, not player speed:

```toml
[audio]
synthesis_speed = 1.0 # Allowed 0.90–1.10. Default remains 1.0.

# Optional trusted pronunciation overrides in Misaki phoneme notation.
# Do not commit personal names or private product names in deployment settings.
[audio.pronunciations]
Kokoro = "kˈOkəɹO"
```

Overrides match exact names, do not modify the source script, and reject markup,
directions and URLs. Do not use broad phonetic substitutions or guess the
pronunciation of people mentioned in a newsletter.

Official references: [voices](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md),
[Kokoro](https://github.com/hexgrad/kokoro), [Misaki](https://github.com/hexgrad/misaki).
