# Spatial brand review

Run `npm --prefix ngramAR run preview:spatial`, then open
`http://127.0.0.1:4173/spatial-preview.html`.

The fixture bundles the production Three.js components. It does not connect to an
Entity, request microphone access, or alter a saved workspace. It is separate from
the production surface build. Run
`node ngramAR/tests/serve-spatial-preview.mjs --build-only` and refresh after
changing component sources; rebuild the surface after changing desktop styles.

Review each scene against both light and dark backgrounds:

- Welcome: heading, controller and hand instructions, row spacing at 0.75 m.
- Controller menu: all six actions, neutral and selected states, longest label.
- Wrist menu: header, all five rows, gaze focus, footer, selected state.
- Conversation: multiline speech, placement guidance, recording indicator.
- Microphone: monochrome off and periwinkle recording, audio bars, actual desktop
  button styles. Use **Simulate recording** to change microphone state in the
  conversation, controller, wrist, and microphone scenes without capturing audio.
- Content: headings, body, code and branded panel frames.
- Desktop (link at the bottom): Azeret Mono across the existing layout.

The visible runtime checks cover font loading, sRGB texture colors, controller
selection, wrist targets, text wrapping, and panel dimensions across XR transitions.
`spatial-menu.test.mjs` additionally tests gaze with head pitch, roll, and a
transformed camera rig without requiring a browser.
`spatial-panel-lifecycle.test.mjs` checks that appearance changes retain video
playback and sandbox state, and that stale loads or disposal cannot revive old
content. These lifecycle doubles do not verify pixels or visual quality.

Headset follow-up should confirm readability and comfort against real passthrough,
controller selection, hand menu activation and pinch, placement, microphone state,
panel grabbing/resizing, and return to desktop. The fixture does not replace that
hardware check.

The shared identity lives in `src/spatial-design.ts`: periwinkle `#6E7DFF`, navy
`#152064`, white text, pale secondary text, thin rules and restrained corners.
Azeret Mono is bundled locally with its SIL Open Font License in `public/fonts`.
Body tracking is relaxed to -0.035em for spatial legibility; display text uses
-0.065em and small uppercase labels use positive tracking. Code stays untracked.

Browser review completed on 2026-09-07: welcome, controller and wrist selections,
conversation spacing, panel frames, desktop typography, and microphone states.
Recording uses exactly #6E7DFF with white text, icons, and audio bars; off uses
white and black, including menu focus.
The floating control has a readable state label and five bars driven by audio,
with no decorative idle animation. Desktop button geometry remains 40 × 40 px.
All 30 visible runtime checks and all 65 regression tests passed. Physical
headset testing has not been performed in this session.
