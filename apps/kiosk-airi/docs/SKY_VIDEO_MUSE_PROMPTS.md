# Mai video assets

The kiosk uses one silent MP4 per interaction state. The app maps `idle`, `listening`, `transcribing`, and `thinking` to the first three clips, and `speaking` to the fourth clip. The same reference image must be used for all clips.

Reference image: `/avatars/sky-anime-reference.png`

## Shared constraints

- Keep exactly one character and exactly one outfit: teal long-sleeve jacket, cream front panel, teal inner shirt, one coral star badge on the left chest, and one coral star hair clip.
- Keep the same face, hair, colors, background, framing, and fixed camera.
- No text, logos, watermarks, props, microphones, headphones, extra people, or scene changes.
- Export silent H.264 MP4, 16:9, 1280x720 when available, 5 seconds, 24 or 30 fps.
- Remove generated audio. The kiosk already plays TTS and must not play two audio tracks.

## Prompts

### `sky-clip-01-idle.mp4`

Subtle breathing and one natural blink. Closed mouth, neutral friendly expression, almost no head movement, fixed camera.

### `sky-clip-02-listening.mp4`

Attentive listening pose. Look at the camera, blink once, make one very small nod, closed mouth, fixed camera.

### `sky-clip-03-thinking.mp4`

Thinking pose. Look slightly upward for a moment, blink once, return eyes to camera, closed mouth, tiny shoulder movement, fixed camera.

### `sky-clip-04-speaking.mp4`

Natural gentle Vietnamese speaking motion. Small mouth movement, occasional blink, tiny nods, stable face and outfit, fixed camera, no sound.

## Runtime behavior

`SkyVideoAvatar.vue` keeps one `<video>` element, loads only the active clip, uses `muted`, `playsinline`, `loop`, and `preload="metadata"`, and falls back to the reference poster when a Muse clip is unavailable or autoplay is blocked.

## Current demo assets

Muse.ai accepted the reference image but reported that video generation is unavailable for the current account region. The four MP4 files currently in `public/avatars/` are user-provided Gemini videos, normalized to silent H.264 MP4 for the kiosk. They use the same Mai reference and map to idle, listening, thinking and speaking. Replace those four files with Muse exports using the same names when the account can render video; no Vue code change is required.
