# HOME editorial workshop

HOME uses a silent, original 24-second cartoon illustration of the editorial
agent's work: select newsletters, inspect evidence, write a brief and prepare
a podcast with an optional, separately written newspaper. This is explanatory
art, not live progress or a claim of perfect fact-checking.

The piece is limited cel animation assembled from 16 generated acting poses
and a painted workshop, not a fully in-betweened theatrical film. Its original
sun character uses the app's warm retro-futurist palette. No third-party film
footage, character asset, music or cloned actor voice is distributed.

## Playback and cost

- H.264 MP4: 1280×720, 24 seconds, no audio track, about 1.4 MB.
- WebP poster: about 153 KB; identical aspect ratio prevents layout jumps.
- The video source is assigned only for the visible, authorized HOME.
- Navigation, sign-out, hidden tabs and an offscreen illustration stop playback.
- Reduced-motion preferences and media/autoplay failures show the still poster.
- No external player, tracking, paid API, new dependency or generation call.
- Versioned public art uses normal Hosting/browser caching, not the service
  worker's private-media path. It still consumes Hosting bandwidth when fetched;
  this change does not raise plan limits or enable billing.

Both static-release preparation and episode publishing explicitly allowlist the
two assets. Neither copies runtime files, requests, correspondence or credentials.
The illustration does not control the podcast player or alter Media Session.

## Verification

`node --test tests/web-behavior.cjs` covers the lifecycle and podcast isolation.
The optional Playwright audits use synthetic fixtures with external networking
blocked: `node scripts/audit-home-film.cjs`, its `--webkit` variant and
`node scripts/audit-web-layout.cjs` (also with `--webkit`). The film audit serves
only its fixture and two art assets on a temporary loopback server, then closes
it. It checks lazy loading, decoding, looping, view/offscreen pauses, reduced
motion, session locking, fallback and bounded layout.

Browser-engine checks are not a physical iPhone/PWA test or full accessibility
certification. The textual HOME steps and caption explain the same process
without requiring motion or sound.
