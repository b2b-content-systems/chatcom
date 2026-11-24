<!-- /autoplan restore point: "/Users/bryanbartlett/github-profile/chatcom/.gstack/tmp/autoplan/main-autoplan-restore-20261123-110141.md" -->
# Render: from a cast episode to a 60-second video (Next Step 4)

Status: R1 (timeline) and R2 (composition, runtime, `chatcom render`) built; R3-R5 planned (2026-11-23). Source of the spec: PLAN.md (Render, v0.1 additions, developer
surface, render safety, segment budget). Branch base: main at 4d9b6a2 (casting parts 1 and 2 merged).

## Implementation plan

### Goal

`chatcom render <show>` turns the episode that `chatcom cast` finished into a 60-second vertical MP4 (1080x1920,
H.264) that fits in the group chat (16 MB default cap). The episode then commits: the show's memory and cast sheet
change, and next week's episode opens with "Previously on". A stranger can run `chatcom demo` with no keys and get a
finished episode.

### What exists

| Piece | Where | State |
| --- | --- | --- |
| Episode stages `cast -> rendered -> encoded -> committed` | `src/chatcom/core/state.py` | names exist; nothing advances past `cast` |
| Beats (cold open, 3 scenes of 1-6 bubbles, credits, TV Guide line, synopsis) | `schemas/beats.schema.json`, `episodes/<code>/beats.json` | built and validated |
| Cast poses (6 per member, transparent PNG), style frame, backgrounds | `casting.py`, `episodes/<code>/cast.next.json` | built (parts 1 and 2) |
| Episode log with `synopsis` and `stills` fields | `schemas/bible.schema.json` | schema only; nothing writes `stills` |
| Prototype episode builder (same segment budget, bubbles, stickers, cards) | `marketing/episode/build.py` (untracked), HyperFrames 0.9.70 | works for one hand-made mock episode; hard-coded names, colours, positions |
| Music beds and SFX (laugh, applause, groan, chime) | `marketing/audio/music`, `marketing/audio/sfx` (untracked) | licence and origin not recorded |
| Fonts used by the prototype | Space Grotesk, DM Serif Display, JetBrains Mono | all SIL OFL |
| Toolchain on Bryan's Mac | Node 25.6.1, ffmpeg 8.0.1 with libx264; `hyperframes` latest 0.9.86 (PLAN.md pins 0.9.69) | not installed as a chatcom runtime |

### Segment budget (PLAN.md, binding)

Previously on 5 s (episode 2 on), cold open 6 s, title and theme 4 s, credits 8 s (up to 4 cards, 2 roles per card),
three scenes 32 s (37 s in episode 1), TV Guide card 2 s, renewal card 3 s. Both versions sum to 60 s. Dialogue is
only in speech bubbles, readable on a 360-px-wide phone, so the episode works with the sound off.

### PR stack (5 stacked PRs, D3; each ships green with real renders in CI from R2 on)

**R1. Timeline (core, pure).** `core/render.py` `timeline(beats, sheet, prev_log)`: segments, shots (background id,
member pose ids), bubbles with on/off times, stickers, credit cards. The budget comes from `core/budget.py` (segments,
scene lengths, words per second, credit limits; O3); R1 adds only bubble hold times (reading speed, a floor and a
cap). A long scene shortens holds and never drops a bubble. The same fit check runs inside `beats.validate`, so an
unfittable draft fails at write time and the writer retries (O2); `timeline()` never fails on validated beats.
"Previously on" uses the previous episode's log entry, not the current one (O1). Every id resolves against
`cast.next.json`. No I/O, no HTML.

**R2. Composition, runtime and `chatcom render`.** `adapters/composer.py` writes a HyperFrames project under
`episodes/<code>/render/` from the timeline: `index.html` plus one composition per segment, from templates shipped as
package data (ported from the prototype; every name, colour and position comes from the timeline). Layout rules are
written down: who is in each shot, where they stand, how up to 6 bubbles of 90 characters stack, how chorus speakers
are labelled; a 360 px readability snapshot checks them (O9). Motion uses CSS keyframes and the Web Animations API;
no GSAP or other animation library (O6). Render safety: chat- and model-derived text enters only as escaped text
nodes; no external URL anywhere; a CSP meta tag; pose and asset paths outside the show folder are refused. Small
fonts (with OFL texts) ship as package data; Noto Color Emoji and the CJK fallback download with the runtime, checked
by sha256 (O13); builds link assets instead of copying them. `adapters/tools.py` gives one `ffmpeg()` / `ffprobe()`
lookup, used by casting too (C2). A minimal runtime installer puts the exact pinned HyperFrames (latest on build day,
0.9.86 on 2026-11-23, with a lockfile; S3) and its Chrome into `CHATCOM_HOME/runtime/<version>/` (O8).
`adapters/hyperframes.py` runs it with `HYPERFRAMES_NO_TELEMETRY=1`, then checks the master's ffprobe duration is
60 s +- 0.1. `chatcom render <show>` moves the current episode `cast -> rendered`; `--episode N` re-renders a finished
one (O5). The CI render job lands here: it renders a fixture inside a no-network sandbox, and a test-only template
with a real `<img src=http://127.0.0.1:PORT>` proves the CSP blocks it (O7). R2 also times a 60 s render on the CI
runner against the 5-minute target (O14) and checks the stills question (O15, pending).

**R3. Encode and commit.** `adapters/encoder.py`: one fixed-quality H.264 encode; only if the file is over
`--max-size` (default 16 MB), two-pass at lower bitrates up to 3 tries, then exit 5 naming the size, the cap and the
bitrates; the master is kept (O16). Output `episodes/<code>/episode_001.mp4`, `_002` on re-encode; the absolute path is
the last stdout line. Stage `rendered -> encoded`; `chatcom render --episode N --max-size SIZE` re-encodes only.
`chatcom episode` runs render and encode at the end; `status` names the next command (O5). Commit (`encoded ->
committed`), under the show lock: snapshot `bible.<code>.pre.json` (O11), write `cast.json`, then `bible.json` with
the log entry's synopsis and stills (the done marker), then the state; a rerun finds this episode in `bible.json` and
only advances the state (C1, O4). When the commit runs (right after encode, or later) is pending (O12).

**R4. Sound.** A music bed and one SFX per sticker (laugh, applause, gasp, aww, groan), mixed in the composition.
CC0 only, new sounds from Freesound's CC0 filter or made with ffmpeg; each in the package's asset manifest with
source URL, licence and sha256; the CI licence check rejects anything else (S1). A test fails when the sticker enum,
the SFX list and the sticker templates disagree. The CI render job asserts an audio stream.

**R5. Doctor, demo and the timed job.** `chatcom doctor [--install]` reports Node, the pinned HyperFrames, Chrome,
the downloaded fonts and ffmpeg with libx264, and `--install` runs R2's runtime installer. Demo show "THE LAKE HOUSE
BOYS": committed poses and backgrounds (cast once by Bryan, about 35 Gemini calls he approves; model id in the
manifest), recorded writer responses; `chatcom demo` renders episode 2 with no keys and prints the MP4 path last.
The timed demo job runs separately from the fast CI render job.

### Out of scope here

The first live run on the League Guys chat and the side-by-side comparison (manual, Bryan, after R5); per-stage time
and cost measurement and `docs/costs.md`; voices; the reaction loop; the long cut (`--long`); per-member credit
stills for sharing; the skill's render steps beyond one "render" step.

### Open gates

- **P0, before any user runs CHATCOM with Bryan's keys:** decide bring-your-own-key versus a paywalled hosted key.
  Render itself makes no paid model calls; casting and the writer do.
- Check WhatsApp's current video size limit before v0.1 and set the default cap from it.
- Casting the demo show with Gemini costs real image calls (about 35); Bryan approves that spend.

## R2 as built (2026-11-23)

What R2 shipped, and where it differs from the plan text above:

- **One standalone page, no sub-compositions.** `adapters/composer.py` writes `render/index.html` with each segment
  as a timed clip and all motion through the Web Animations API (`render_templates/motion.js`), driven by data
  attributes holding absolute times. Sub-compositions would be fetched at render time; one page keeps the CSP
  simple and the chat text in one escaped document. HyperFrames lint warns `nested_structure_needs_subcomposition`
  and `timeline_track_too_dense`; they are advice for Studio editing, not render errors. The root carries
  `data-no-timeline` (no GSAP timeline; without it HyperFrames waits 45 s for one).
- **Pictures are CSS backgrounds, not `<img>`.** HyperFrames' media scanner treats every `<img>` as a clip and
  warns on repeats; poses, sets and stills are `background-image` on sized boxes.
- **Assets are hard links (copies across disks)** inside `render/assets/`, never symlinks: the build folder holds
  everything the page reads.
- **Bubbles:** one on screen at a time (the timeline never overlaps them), as wide as their text, on the
  speaker's side with the tail on that side; a sticker sits on the bubble's top edge. Dialogue type is 52 px
  (17 px on a 360-px phone).
- **Fonts:** six small OFL Latin subsets ship in `assets/fonts/` with `assets/manifest.json`. The runtime downloads
  Noto Color Emoji, Noto Sans SC (Chinese and Japanese kana) and **Noto Sans KR** (added: SC has no Hangul) from a
  pinned google/fonts commit, each checked by sha256.
- **Runtime:** `chatcom doctor --install` (R5 keeps the reporting). Chrome lives in the runtime folder through
  `HYPERFRAMES_CACHE_DIR`. About 330 MB installed. Some of HyperFrames' own npm dependencies are LGPL-3.0 (image
  libraries); they install on the user's computer and are not shipped in the chatcom package.
- **The way back for an old draft is `chatcom rewrite <show>`** (the PLAN.md command name), for this week's episode:
  drafted, cast or rendered -> ingested; `--episode N` for a committed episode arrives with the commit (R3).
- **`chatcom render --episode N` (O5) moves to R3**, with the commit: until an episode commits there is only one
  renderable episode, and re-rendering it is a plain `chatcom render <show>`.
- **O14 (render time):** 60 s rendered in 30-35 s on an M-series Mac (8 cores, hardware GPU). The CI job prints its
  time with `--durations`.
- **O7 proof:** the probe test renders a page with a real `<img src="http://127.0.0.1:PORT/...">` twice: with the
  CSP the listener sees 0 requests; without it, it sees some. So the test can fail. The CI job renders inside a
  network namespace with only loopback, after checking it cannot reach github.com.

## Review record

Target: `docs/design/render.md` (this plan). Review: /plan-eng-review, 2026-11-23, engineering only (CEO, design and
DX phases skipped by the owner's choice: D2 of the stopped /autoplan run). Report file: this file.

### Scope Challenge: assessment

**What already exists.** Stages `cast -> rendered -> encoded -> committed` (`core/state.py:17`); the episode log's
empty `stills` (`core/bible.py:202`); atomic writes (`adapters/store.py:41`, temp file + fsync + replace); the
untracked prototype `marketing/episode/build.py` (one mock episode, same segment budget, HyperFrames 0.9.70,
hard-coded names and positions); fonts in `marketing/episode/assets/fonts` (10 Google Fonts families, OFL, no
licence texts bundled); audio in `marketing/audio` and `marketing/episode/assets`.

**Minimum change.** R1-R5 make a real episode render, fit the cap and commit. R6 (sound) and R7 (doctor, demo, CI)
are needed for v0.1 but not for the first rendered episode.

**Complexity.** About 20+ files (4 new modules: `core/render.py`, `adapters/composer.py`, `adapters/hyperframes.py`,
`adapters/encoder.py`; segment templates; asset manifest, audio and fonts; `cli.py`, `app.py`; tests; CI; a
`package.json` + lockfile). Over the gate (8 files / 2 new modules) [estimated from the plan's file list].

**Scope findings.**
1. [CRITICAL] (confidence: 9/10) `docs/design/render.md` R6 + `marketing/episode/assets/sfx/CREDITS.md:3` — the
   existing SFX are CC BY ("licensed CC BY: credit them wherever the video is published"), and
   `marketing/audio/eleven.py:1` shows the rest were generated with ElevenLabs on Bryan's key. Neither is CC0, which
   PLAN.md requires; CC BY would oblige every user to credit five Freesound authors in every episode they post.
2. [CRITICAL] (confidence: 8/10) R3 "network requests blocked" — HyperFrames has no flag for this
   (`hyperframes-cli/references/preview-render.md:128-146` lists the render flags), and its CLI sends usage
   telemetry, tagged with the agent runtime, unless `HYPERFRAMES_NO_TELEMETRY=1`
   (`references/upgrade-info-misc.md:108`). A page CSP may or may not hold (Puppeteer can bypass CSP).
3. [INFORMATIONAL] (confidence: 9/10) Version pin — PLAN.md says `hyperframes@0.9.69`, the prototype used 0.9.70,
   the npx cache holds 0.9.65, the registry's latest is 0.9.86 [measured: `npm view hyperframes version`].
4. [INFORMATIONAL] (confidence: 6/10) 16 MB cap — sources disagree (16 / 64 / 100 MB in-chat); no official page
   found. 16 MB is safe under every figure; the plan's open gate (verify on a device before v0.1) stands.
5. [INFORMATIONAL] (confidence: 8/10) Fonts — the 10 families are OFL but no licence texts ship; the asset manifest
   needs them, plus Noto Color Emoji and a CJK fallback (PLAN.md render safety), which are not in the prototype.

### Reality rows

| premise | claim | receipt | verdict |
| --- | --- | --- | --- |
| prototype builds this format | 60 s, same 7 segments | `marketing/episode/build.py:1-2,10` | true |
| stages exist | cast -> rendered -> encoded -> committed | `src/chatcom/core/state.py:17` | true |
| stills field exists, unwritten | `"stills": []` | `src/chatcom/core/bible.py:202` | true |
| audio licence | "origin and licence not recorded" (plan) | `marketing/episode/assets/sfx/CREDITS.md:3`, `marketing/audio/eleven.py:1` | false: CC BY and ElevenLabs |
| fonts OFL | prototype fonts | `marketing/episode/assets/fonts/` (family names) | unverified (no licence files) |
| WhatsApp 16 MB | default cap | web sources conflict | unverified |
| network blocked | HyperFrames render | `hyperframes-cli/references/preview-render.md:128-146` | false: no such flag |

REALITY: premise-table finding 7 premises, 2 false, unverified=2 `docs/design/render.md:24`
REALITY: already-done pass no render or encode code on origin/main since 2026-11-20 `src/chatcom/core/state.py:17`
REALITY: surface-check finding no HyperFrames network-block flag; `render --strict`, `check` exist `hyperframes-cli/references/preview-render.md:142`
REALITY: binding-decisions pass wave 1 approved as 7 PRs; eng-only review chosen this session; plan agrees `docs/design/render.md:38`
REALITY: numbers pass segment budget re-derived 5+6+4+8+32+2+3=60 and 6+4+8+37+2+3=60 [measured: PLAN.md:112]; ~35 casting calls [measured: test count]; 16 MB [estimated] `PLAN.md:112`
REALITY: deferred-asks pass no linked issue `docs/design/render.md:1`
REALITY: pr-count finding 7 PRs; each /ship cycle on PR #10 took about 2 h with 5-6 reviewer agents [measured: this session] `docs/design/render.md:38`

### Scope record

feature answers: none proposed (no cuts); structure: B, 5 PRs (D3, answered 2026-11-23: "5 PRs (Recommended)");
accepted scope: R1 timeline; R2 composition + HyperFrames runner + `chatcom render`; R3 encode + commit; R4 sound;
R5 doctor + demo + CI. Same features, files and tests as the 7-PR draft. pending remedies: S1, S2, S3, S5.
Scope Challenge result: scope accepted as-is (smaller arrangement, same scope).

## Decision ledger

### S1: Audio licences for the music bed and sticker SFX
Finding: 1, CRITICAL, 9/10, `marketing/episode/assets/sfx/CREDITS.md:3` + `marketing/audio/eleven.py:1`, Scope Challenge
Plan baseline: R6 (now R4) "CC0 only ... marketing/audio files used only if origin and licence can be shown".
Runtime evidence: existing SFX are CC BY 3.0/4.0 (Freesound) or ElevenLabs-generated; none CC0.
Comparison grid:
| Choice | Current | A | B | C |
| --- | --- | --- | --- | --- |
| S1 audio source | unverified mix | new CC0 assets only (Freesound CC0 filter, or made with ffmpeg), each in the manifest with source URL + sha256 | CC BY allowed, with a credits line burned into the renewal card of every episode | no sound in v0.1 (R4 deferred) |
| PR shape (D3) | 5 PRs | 5 PRs | 5 PRs | 4 PRs (R4 dropped) |
| S2, S3, S5 | pending | pending | pending | pending |
Question D4:
D4 — Which sounds may ship inside every episode? <gstack-qid:plan-eng-review-s1-audio-licence>
Project: chatcom main, render plan, PR R4 (sound).
ELI10: Every episode a user makes will carry our music and laugh sounds. The sounds we have are either CC BY, which means every user must credit five Freesound authors wherever they post, or made with ElevenLabs on your account. PLAN.md asks for CC0: free to use with no credit. We can find or make CC0 sounds, allow CC BY with an automatic credit line, or ship without sound.
Stakes if we pick wrong: users post videos that break a licence they never saw, or v0.1 ships silent.
Recommendation: A, because CC0 sounds put no duty on users and are easy to find or make for laughs, applause and a short music bed.
Completeness: A=10/10, B=7/10, C=4/10
Pros / cons:
A) CC0 only, new assets (recommended) (human: ~1 day / CC: ~1 h)
  ✅ No credit duty on any user; the licence check in CI can enforce it from the manifest.
  ❌ New sounds may be less polished than the ElevenLabs set used in the marketing trailer.
B) Allow CC BY, auto-credit line (human: ~4 h / CC: ~30 min)
  ✅ Keeps the existing Freesound laugh track and statics, which already sound right in the prototype.
  ❌ Every episode carries a credit line, and a user who trims it breaks the licence without knowing.
C) No sound in v0.1 (human: 0 / CC: 0)
  ✅ Removes the licence question and one PR from the render wave entirely.
  ❌ Breaks PLAN.md's v0.1 promise of a music bed and sound effects; episodes feel flat.
Net: user freedom and a clean licence versus reusing the sounds we already have.
Header: Audio licence
Options:
A) CC0 only (Recommended)
New CC0 sounds (Freesound CC0 filter, or made with ffmpeg), each in the asset manifest with source and sha256.
B) CC BY + credit line
Keep the Freesound CC BY sounds; burn a credit line into every episode's renewal card.
C) No sound in v0.1
Defer R4; episodes are silent with speech bubbles only.

State: approved
Actual answer: A) CC0 only (D4, answered 2026-11-23)
Accepted scope: R4 ships only CC0 audio: new sounds from Freesound's CC0 filter or made with ffmpeg; each file in `assets/manifest.json` with source URL, licence and sha256; the CI licence check rejects any audio not listed as CC0. The `marketing/audio` and `marketing/episode/assets` sounds are not shipped.
History: none


### S2: Network block and telemetry during the render
Finding: 2, CRITICAL, 8/10, plan R3 "network requests blocked" vs `hyperframes-cli/references/preview-render.md:128-146` (no such flag) and `references/upgrade-info-misc.md:108` (telemetry on by default), Scope Challenge
Plan baseline: "runs ... with network requests blocked and no `file://` access outside the build directory" (R3).
Runtime evidence: HyperFrames exposes no network-block flag; telemetry is opt-out via `HYPERFRAMES_NO_TELEMETRY=1`; whether a page CSP holds under HyperFrames's Chrome is unknown (Puppeteer can bypass CSP).
Comparison grid:
| Choice | Current | A | B |
| --- | --- | --- | --- |
| S2 how the render stays offline | claimed, no mechanism | composer writes no external URL + page CSP meta (`default-src 'self' data:` with what the runtime needs) + `HYPERFRAMES_NO_TELEMETRY=1` + a test that renders a fixture with a hostile URL to a local listener and asserts zero hits; if CSP does not hold, the test still proves the composer emits no fetchable URL and the doc says so | composer writes no external URL + a static scan of the built project for `http`, `//` and `file:` + `HYPERFRAMES_NO_TELEMETRY=1`; drop the "network blocked" claim |
| S1 | A approved | A | A |
| S3, S5 | pending | pending | pending |
Question D5:
D5 — How does the render prove it sends nothing out? <gstack-qid:plan-eng-review-s2-render-offline>
Project: chatcom main, render plan, PR R2 (composition + runner).
ELI10: The video page contains words from the group chat. PLAN.md promises the render makes no network requests, but HyperFrames has no switch for that, and it sends usage counts unless told not to. We can add a page rule that blocks fetches and prove it with a test that listens for any request, or just guarantee and scan that the page links nothing outside.
Stakes if we pick wrong: chat text or a tracking ping could leave the computer during a render, against what the notice tells the group.
Recommendation: A, because a test that catches a real request is proof, and the page rule costs one line.
Completeness: A=10/10, B=7/10
Pros / cons:
A) Page rule + listener test (recommended) (human: ~4 h / CC: ~30 min)
  ✅ A real request to a local listener fails the build, so a future template change cannot leak quietly.
  ❌ The page rule may conflict with how HyperFrames loads its own scripts; that needs a short spike in R2.
B) No-link guarantee + static scan (human: ~1 h / CC: ~10 min)
  ✅ Simple and fast; no risk of breaking how HyperFrames loads its runtime scripts.
  ❌ Proves only that our HTML has no links, not that nothing fetches; the "blocked" promise is dropped.
Net: a tested block versus a simpler guarantee with a weaker promise.
Header: Render offline
Options:
A) Page rule + listener test (Recommended)
No external URLs, a CSP meta tag, telemetry off, and a render test with a local listener that must see zero requests.
B) No-link guarantee + scan
No external URLs, a static scan of the built project, telemetry off; drop the "network blocked" claim.

State: approved
Actual answer: A) Page rule + listener test (D5, answered 2026-11-23)
Accepted scope: R2's composer writes no external URL; `index.html` carries a CSP meta tag allowing only what the HyperFrames runtime needs (found by a short spike in R2); the runner sets `HYPERFRAMES_NO_TELEMETRY=1`; a render test builds a fixture whose bubble holds a hostile URL to a local listener and asserts the listener saw zero requests. If the spike shows HyperFrames bypasses CSP, the test still runs and the doc states the guarantee is the no-URL rule.
History: none

### S3: Which HyperFrames version to pin
Finding: 3, INFORMATIONAL, 9/10, `PLAN.md:105` (0.9.69) vs `marketing/episode/package.json` (0.9.70) vs registry latest 0.9.86, Scope Challenge
Plan baseline: PLAN.md pins `hyperframes@0.9.69`.
Runtime evidence: `npm view hyperframes version` = 0.9.86; npx cache holds 0.9.65; prototype rendered on 0.9.70.
Comparison grid:
| Choice | Current | A | B |
| --- | --- | --- | --- |
| S3 pinned version | 0.9.69 (PLAN.md) | the latest release on the day R2 is built (0.9.86 today), pinned exactly in `package.json` + lockfile; PLAN.md updated | 0.9.70, the version the prototype rendered on |
| S1, S2 | approved | approved | approved |
| S5 | pending | pending | pending |
Question D6:
D6 — Which HyperFrames version does CHATCOM pin? <gstack-qid:plan-eng-review-s3-hyperframes-pin>
Project: chatcom main, render plan, PR R2.
ELI10: HyperFrames turns our page into video. PLAN.md names 0.9.69, the prototype used 0.9.70, and today's release is 0.9.86. Whichever we pick is locked in a lockfile, so every user renders with exactly that version.
Stakes if we pick wrong: an old pin misses fixes and may stop installing; a new pin may render the prototype's look slightly differently.
Recommendation: A, because R2 renders and tests against the pin anyway, and the newest release stays installable longest.
Note: options differ in kind, not coverage — no completeness score.
Pros / cons:
A) Latest on build day, pinned exactly (recommended)
  ✅ Gets 16 releases of fixes and stays installable longer; the monthly CI check watches it.
  ❌ The prototype's look was only seen on 0.9.70; R2 must recheck frames on the new version.
B) 0.9.70, the prototype's version
  ✅ The look is known to render correctly; no surprises when porting the prototype's templates.
  ❌ Already 16 releases old; more likely to fail to install or miss Chrome fixes over v0.1's life.
Net: newest fixes versus a version whose output we have already seen.
Header: HyperFrames pin
Options:
A) Latest, pinned exactly (Recommended)
Pin the release current when R2 is built (0.9.86 today) with a lockfile; update PLAN.md.
B) 0.9.70 (prototype)
Pin the version the marketing prototype rendered on.

State: approved
Actual answer: A) Latest, pinned exactly (D6, answered 2026-11-23)
Accepted scope: R2 pins the HyperFrames release current on the day it is built (0.9.86 on 2026-11-23) exactly, in a shipped `package.json` + lockfile, and updates PLAN.md's pin; R2 checks rendered frames on that version.
History: none

### S5: Fonts (carried, no question)
Finding: 5, INFORMATIONAL, 8/10, `marketing/episode/assets/fonts/` has no licence texts, no Noto Color Emoji, no CJK fallback.
Plan baseline: PLAN.md render safety (binding): "Bundled fonts include Noto Color Emoji and a CJK fallback (OFL, in the asset manifest)".
Accepted scope: required proof of that approved contract, carried without a new question: R2 ships each font with its OFL text and a manifest row; adds Noto Color Emoji and a CJK fallback (Noto Sans CJK subset or equivalent OFL).

## Section 1: Architecture

```
beats.json ─┐
cast.next.json ─┼─> core/render.py: timeline(beats, sheet, prev_log) ──> Timeline (pure data)
bible.next.json (last log) ─┘        budget, bubble timing, id checks        │
                                                                              v
                       adapters/composer.py: write_project(timeline, show) ──> episodes/<code>/render/
                       templates (package data), html.escape text only,       (index.html + 7 segments
                       CSP meta, assets copied by id, fonts + CC0 audio        + assets/)
                                                                              │
                       adapters/hyperframes.py: render(dir) ── pinned binary, HYPERFRAMES_NO_TELEMETRY=1
                                                                              v
                                       render/master.mp4 ── ffprobe 60 s +- 0.1 ── stage rendered
                                                                              │
                       adapters/encoder.py: two-pass H.264 to --max-size, 3 tries
                                                                              v
                                       episodes/<code>/episode_NNN.mp4 ── stage encoded
                                                                              │
                       commit: cast.next -> cast.json, bible.next (+stills) -> bible.json ── stage committed
```

**Two shapes.** A (plan): a pure `timeline()` in core owns all time policy (budget, reading speed, compression,
id resolution); the composer only places what the timeline says. B: the composer reads beats directly and computes
times while writing HTML (the prototype's shape). A hides the budget policy behind one function and is testable
without Chrome or HTML; B spreads timing across seven template writers. Pick A.

**Red flags.** (2) Information leakage: segment lengths live in PLAN.md, the beats schema (3 scenes) and the new
timeline; keep them in one constant in `core/render.py` and test the sums. (8) Hand-synced list: the beats schema's
sticker enum (`laugh, applause, gasp, aww, groan`), the SFX files and the sticker templates must agree; a test fails
the build when one lacks another. (6) Two ways to build an episode: `marketing/episode/build.py` stays untracked
marketing code; nothing in `src/` imports it.

**Production failures per path.** HyperFrames or Chrome missing: preflight error before any work (exit 3). Render
crash: exit 5 with the HyperFrames log path, master not written. Wrong duration: exit 5. Cap missed after 3 tries:
exit 5 naming size, cap and bitrates; master kept. Crash during commit: see C1.

Findings:
- C1. [INFORMATIONAL] (confidence: 8/10) plan R3/R5 "become `bible.json` and `cast.json` (temp file, fsync,
  `os.replace`)" — each file is atomic, the pair is not. A crash between the two replaces leaves one committed.
  The repo already orders such pairs: `app.py:836` writes `cast.json` first and `bible.json` last "the marker that
  migrate finished".
- C2. [INFORMATIONAL] (confidence: 9/10) `adapters/cutout.py:25`, `adapters/images.py:233`, `adapters/painter.py:172`
  each find ffmpeg their own way; the encoder and runner would add a fourth and fifth (`ffprobe`).

### C1: Commit order for the two files
Finding: C1, INFORMATIONAL, 8/10, `docs/design/render.md` R5 + `src/chatcom/app.py:836`, Architecture
Plan baseline: PLAN.md:187 "written only at committed (temp file, fsync, `os.replace`)"; pair order unspecified.
Runtime evidence: `app.py:836` already writes cast first and bible last as the completion marker (migrate).
Comparison grid:
| Choice | Current | A | B |
| --- | --- | --- | --- |
| C1 commit order + resume | unspecified | cast.json first, bible.json last (the marker), then state `committed`; a rerun at `encoded` whose bible already holds this episode just advances the state | write both to a staging folder, then two renames, with a journal file to finish or roll back |
| S1, S2, S3 | approved | approved | approved |
| C2 | pending | pending | pending |
Question D7:
D7 — How does commit survive a crash between its two file writes? <gstack-qid:plan-eng-review-c1-commit-order>
Project: chatcom main, render plan, PR R3 (encode + commit).
ELI10: Committing an episode replaces two files: the cast sheet and the show's memory. Each replace is safe on its own, but a crash between them could leave one updated and one not. The code already handles this for upgrades by writing the memory file last as the "done" marker, so a rerun can tell where it stopped.
Stakes if we pick wrong: a crash could leave next week's episode with the new memory but old pictures, or the reverse.
Recommendation: A, because it reuses the order `migrate` already proves (app.py:836) and needs no new file.
Note: options differ in kind, not coverage — no completeness score.
Pros / cons:
A) Cast first, memory last, rerun finishes (recommended) (human: ~2 h / CC: ~15 min)
  ✅ Same pattern as `chatcom migrate`; one rule, tested by killing the run between the two writes.
  ❌ Between the writes, cast.json is ahead of bible.json until the rerun; old episodes still render fine.
B) Staging folder + journal (human: ~1 day / CC: ~1 h)
  ✅ The pair moves together; a reader never sees one file ahead of the other, even mid-crash.
  ❌ A new journal format and recovery code for a two-file case the existing pattern already covers.
Net: reuse a proven ordered write versus a heavier all-or-nothing journal.
Header: Commit order
Options:
A) Cast first, memory last (Recommended)
Write cast.json, then bible.json as the done marker, then the state; a rerun at `encoded` finishes the commit.
B) Staging + journal
Stage both files, rename them in turn, and keep a journal to finish or roll back after a crash.

State: approved
Actual answer: A) Cast first, memory last (D7, answered 2026-11-23)
Accepted scope: R3's commit writes cast.json, then bible.json (the done marker), then the state `committed`, all under the show lock; a rerun at `encoded` whose bible already holds this episode's log entry only advances the state. Test: stop the run after the cast write and assert a rerun completes the commit with no duplicate log entry.
History: none

## Section 2: Code quality

Reuse: `store.write_atomic` for every file the stage writes; `ChatcomError` with new codes for exit 5 (a render
failure and a missed size cap), each with a `docs/errors.md` anchor and a row in the spec's error table (PLAN.md error
contract, carried); templates ship as package data under `src/chatcom/render_templates/`, ported from the prototype
with every hard-coded name, colour and position replaced by timeline values. Error gaps: a pose file that `cast.next.json`
points outside the show folder must be refused (render safety: no file access outside the build directory, carried).

### C2: One place that finds ffmpeg and ffprobe
Finding: C2, INFORMATIONAL, 9/10, `src/chatcom/adapters/cutout.py:25` `def _ffmpeg() -> str:`, `src/chatcom/adapters/images.py:233` `def _ffmpeg() -> str:`, `src/chatcom/adapters/painter.py:172` `exe = shutil.which("ffmpeg")`, Code quality
Plan baseline: the plan adds an encoder and a duration check (ffmpeg, ffprobe) with no shared lookup named.
Runtime evidence: three copies today, each with its own preflight message; R2 and R3 would add ffprobe and a fourth ffmpeg lookup.
Comparison grid:
| Choice | Current | A | B |
| --- | --- | --- | --- |
| C2 tool lookup | 3 copies | new `adapters/tools.py` with `ffmpeg()` and `ffprobe()` (one `CHATCOM_E_PREFLIGHT` message each); cutout, images and painter move to it in R2; about 25 lines removed, 20 added | encoder and runner add their own copies like the others |
| C1, S1, S2, S3 | approved | approved | approved |
Question D8:
D8 — Should the render share one ffmpeg finder with the casting code? <gstack-qid:plan-eng-review-c2-ffmpeg-helper>
Project: chatcom main, render plan, PR R2.
ELI10: Three files each look for ffmpeg in their own way, each with its own "install ffmpeg" message. The render adds two more lookups, for ffmpeg and ffprobe. One small shared helper would give one message and one place to fix.
Stakes if we pick wrong: five copies drift, and a user who lacks ffprobe gets a raw crash instead of the install hint.
Recommendation: A, because it removes copies the render would otherwise multiply, at about net zero lines.
Completeness: A=10/10, B=7/10
Pros / cons:
A) One shared helper (recommended) (human: ~1 h / CC: ~10 min)
  ✅ One install message for ffmpeg and ffprobe, tested once; five call sites use it.
  ❌ Touches three casting files in a render PR; their tests must still pass unchanged.
B) Keep separate copies
  ✅ The render PR touches no casting code, so the change stays smaller and isolated.
  ❌ Five drifting copies; ffprobe has no install hint unless each copy adds one.
Net: one consistent preflight versus a render PR that leaves casting files alone.
Header: ffmpeg helper
Options:
A) One shared helper (Recommended)
Add adapters/tools.py with ffmpeg() and ffprobe(); cutout, images and painter use it.
B) Separate copies
The encoder and runner each find ffmpeg and ffprobe themselves.

State: approved
Actual answer: A) One shared helper (D8, answered 2026-11-23)
Accepted scope: R2 adds `adapters/tools.py` with `ffmpeg()` and `ffprobe()`, each raising `CHATCOM_E_PREFLIGHT` with the install hint; `cutout.py`, `images.py` and `painter.py` call it; the encoder and runner use it. Test: a missing ffprobe gives the coded preflight error; existing casting tests pass unchanged.
History: none

## Section 3: Tests

Framework: pytest (`CLAUDE.md` `## Testing`: `.venv/bin/python -m pytest -q`). All paths below are proposed code.

```
CODE PATHS                                                    USER FLOWS
[+] core/render.py (R1, pure)                                 [+] chatcom render <show>
  ├── timeline()                                                ├── [GAP] cast -> rendered -> encoded -> committed, one command
  │   ├── [GAP] ep1 budget = 60 s (no "Previously on")          ├── [GAP] stage not `cast` -> CHATCOM_E_STATE, nothing written
  │   ├── [GAP] ep2+ budget = 60 s with 5 s "Previously on"     ├── [GAP] rerun after a crash in each stage resumes, no repaint
  │   ├── [GAP] bubble hold from reading speed, floor and cap   └── [GAP] --max-size SIZE re-encodes only, master reused
  │   ├── [GAP] long scene compresses holds, keeps every bubble
  │   ├── [GAP] scene that cannot fit -> coded error naming it [+] chatcom demo (R5)
  │   ├── [GAP] unknown pose / background id -> coded error     └── [GAP] [→E2E] no keys -> episode 2 MP4, path last line
  │   └── [GAP] credits: up to 4 cards, 2 roles each
[+] adapters/composer.py (R2)                                 [+] Error states
  ├── [GAP] hostile bubble text renders as literal text          ├── [GAP] hyperframes/Chrome missing -> exit 3 + install hint
  ├── [GAP] no external URL; CSP meta present                    ├── [GAP] render crash -> exit 5 + log path
  ├── [GAP] pose path outside the show folder -> refused         └── [GAP] cap missed -> exit 5, size/cap/bitrates, master kept
  └── [GAP] sticker enum = SFX manifest = templates (hand-synced list)
[+] adapters/hyperframes.py (R2)
  ├── [GAP] [→E2E] render fixture; ffprobe duration 60 s +- 0.1
  ├── [GAP] [→E2E] local listener sees zero requests (S2)
  └── [GAP] HYPERFRAMES_NO_TELEMETRY=1 set on the child process
[+] adapters/encoder.py (R3)
  ├── [GAP] fits cap on first try
  ├── [GAP] tiny cap -> 3 tries then exit 5
  └── [GAP] auto-versioned episode_001 / _002
[+] commit (R3)
  ├── [GAP] cast first, bible last, state committed (C1)
  └── [GAP] crash after cast write -> rerun completes, one log entry
[+] adapters/tools.py (R2, C2)
  └── [GAP] missing ffprobe -> CHATCOM_E_PREFLIGHT
[+] assets (R4)
  ├── [GAP] every audio file CC0 in the manifest; CI licence check fails otherwise (S1)
  └── [GAP] [→E2E] master has an audio stream

COVERAGE: 0/33 paths tested (all proposed)  |  QUALITY: n/a  |  GAPS: 33 (4 E2E, 0 eval)
```

No LLM or prompt change: render makes no model calls, so no eval. Tests made obsolete: none.
Required tests are in the Test Plan artifact (`~/.gstack/projects/b2b-content-systems-chatcom/`), one value card per
critical path. The E2E renders need Node and Chrome; they run in the CI render job (R5) and are skipped locally when
HyperFrames is not installed, with the skip reason printed.

## Section 4: Performance

A 60 s 1080x1920 render is 1,800 frames at 30 fps [estimated]; the prototype rendered in a minute or two
[measured: hyperframes-cli docs, "a 30 s film renders in a minute or two"]. Two-pass encode of 60 s adds seconds.
Asset copying is per episode (poses of up to 8 members, 3-5 backgrounds, CC0 audio, fonts): a few MB. No loops over
network calls; no model calls. One risk: the CI render job costs minutes per run, so it runs on main and on PRs that
touch render code, not on every PR. No issues found beyond that.

## Outside Voice

Codex: not installed (`CODEX_MODE: not_installed`). Native fallback: Plan agent, completed inside the 5-minute timer. 16 findings, `VERDICT: FINDINGS`. Verified against code: 1 (`core/bible.py:200-204` appends the current episode and bumps `next_episode`), 3 (`core/budget.py:5-17`), 4 (`app.py:488` looks up `current_code`), 6 (`marketing/episode/build.py:213`), 10 (`PLAN.md:653` still says credits 6 s, scenes 34/39 s), 11 (`PLAN.md:163`).

### Carried corrections (required by PLAN.md or the approved C1, no question)
- O1: 'Previously on' reads the previous episode: the last entry of committed `bible.json` (or `bible.next.json` episodes[-2]); test: episode 2 shows episode 1's synopsis and stills (`PLAN.md:659`).
- O3: R1 imports `core/budget.py` (segments, scene lengths, words per second, credit limits) and adds only bubble hold times; no second copy of the budget.
- O4: commit treats `bible.json` holding this episode code as committed (C1's rerun contract); test the crash after the bible write as well as after the cast write.
- O5: CLI follows PLAN.md's binding tree: `chatcom render <show>` for the current episode, `--episode N` to re-render or re-encode a finished one, `--max-size`; `chatcom episode` runs render and encode at the end; `status` names the next command at `cast`.
- O9: R2 specifies layout (who is in each shot, positions, bubble stacking for up to 6 bubbles of 90 characters, chorus labels) and adds the 360 px readability snapshot (`PLAN.md:650`).
- O10: the plan body is renumbered to the approved 5 PRs; `PLAN.md:653`'s stale budget is corrected to match `budget.py` and `PLAN.md:113`.
- O11: commit snapshots `bible.<code>.pre.json` under the lock before the first write (`PLAN.md:163`).
- O14: R2 times a real 60 s render on the CI runner and records it against the 5-minute target (`PLAN.md:169`).

### O2: Where the scene-fit check runs
Finding: 2, Critical, outside voice (native fallback), `src/chatcom/core/beats.py:73` checks words per segment only; R1 fails after paid casting, and no command rewrites beats from `cast`
Plan baseline: R1 `timeline()` fails before render with a coded error
Runtime evidence: `src/chatcom/core/beats.py:73` checks words per segment only; R1 fails after paid casting, and no command rewrites beats from `cast`
Comparison grid:
| Choice | Current | A | B | C | D |
| --- | --- | --- | --- | --- | --- |
| O2 | R1 `timeline()` fails before render with a coded error | the fit check moves into `beats.validate` (one shared `timeline()` call), so an unfittable draft becomes a writer retry at write time and `timeline()` never fails on validated beats | keep current | investigate in R2, then ask again | defer this proposal |
| S1, S2/O7, S3, C1, C2, other O-rows | as approved / pending | unchanged | unchanged | unchanged | unchanged |
Question: Where should the 60-second fit check run? <gstack-qid:plan-eng-review-o2>
ELI10: The writer can produce a scene that passes today's word check but still cannot fit its time slot. The plan only finds out at render time, after the paid casting, when no command can fix the script. Running the same fit check while the script is validated turns that dead end into an ordinary writer retry.
Stakes: an episode can get stuck at `cast` with paid images and no way forward.
Header: O2
Options:
A) Apply: check fit at validation (Recommended)
Run the timeline fit check inside beats validation; render never meets an unfittable script.
B) Keep current
R1 `timeline()` fails before render with a coded error.
C) Investigate first
Spike it in R2 and bring the result back; nothing changes until then.
D) Defer this
Leave the plan as is for now and record the proposal in TODOS.md.

State: approved
Actual answer: A) Apply: check at validation (D9, 2026-11-23)
Accepted scope: R1 adds the fit check to `beats.validate` through one shared `timeline()` call (budget from `core/budget.py`); an unfittable draft fails validation and the writer retries; `timeline()` raises only on unvalidated input. Test: six one-word bubbles in the 6 s cold open fail validation, not render.
History: none

### O6: Where GSAP comes from
Finding: 6, High, outside voice (native fallback), `marketing/episode/build.py:213` loads GSAP from cdn.jsdelivr.net; S2 forbids external URLs; GSAP is under Webflow's own licence, not OSI
Plan baseline: unspecified (prototype uses the CDN)
Runtime evidence: `marketing/episode/build.py:213` loads GSAP from cdn.jsdelivr.net; S2 forbids external URLs; GSAP is under Webflow's own licence, not OSI
Comparison grid:
| Choice | Current | A | B | C | D |
| --- | --- | --- | --- | --- | --- |
| O6 | unspecified (prototype uses the CDN) | the templates drop GSAP and animate with CSS keyframes / Web Animations, which HyperFrames supports natively; no third-party animation library ships | keep current | investigate in R2, then ask again | defer this proposal |
| S1, S2/O7, S3, C1, C2, other O-rows | as approved / pending | unchanged | unchanged | unchanged | unchanged |
Question: How do the templates animate without a CDN? <gstack-qid:plan-eng-review-o6>
ELI10: The prototype animates with GSAP loaded from a public CDN. The offline rule bans that URL, and GSAP's licence is its own, not an open-source one our licence check accepts. The templates can animate with plain CSS and the browser's built-in animation API instead.
Stakes: every animation breaks under the offline rule, or a non-OSI licence ships inside the package.
Header: O6
Options:
A) Apply: CSS/Web Animations (Recommended)
Rewrite the template motion with CSS keyframes and the Web Animations API; ship no GSAP.
B) Keep current
unspecified (prototype uses the CDN).
C) Investigate first
Spike it in R2 and bring the result back; nothing changes until then.
D) Defer this
Leave the plan as is for now and record the proposal in TODOS.md.

State: approved
Actual answer: A) Apply: CSS/Web Animations (D10, 2026-11-23)
Accepted scope: R2's templates animate with CSS keyframes and the Web Animations API only; no GSAP or other animation library ships; the licence check lists no animation dependency. Frames are checked against the prototype by eye.
History: none

### O7: Offline proof that can actually fail (reopens S2)
Finding: 7, High, outside voice (native fallback), bubble text is escaped, so a URL in a bubble is never fetched; telemetry goes to remote hosts a local listener never sees
Plan baseline: S2 A: CSP + local-listener test on a hostile bubble
Runtime evidence: bubble text is escaped, so a URL in a bubble is never fetched; telemetry goes to remote hosts a local listener never sees
Comparison grid:
| Choice | Current | A | B | C | D |
| --- | --- | --- | --- | --- | --- |
| O7 | S2 A: CSP + local-listener test on a hostile bubble | keep S2's CSP and no-URL rule; replace the listener test with (1) a CI render inside a no-network sandbox (`unshare -n` or a container with `--network none`) that must still succeed, and (2) a test-only template with a real `<img src=http://127.0.0.1:PORT>` proving the CSP blocks it | keep current | investigate in R2, then ask again | defer this proposal |
| S1, S2/O7, S3, C1, C2, other O-rows | as approved / pending | unchanged | unchanged | unchanged | unchanged |
Question: Strengthen the offline proof? <gstack-qid:plan-eng-review-o7>
ELI10: The approved test puts a link inside a bubble, but bubbles are escaped text, so nothing could ever fetch it and the test always passes. A render inside a box with no network at all proves nothing leaves; a deliberate test-only image link proves the page rule works.
Stakes: the promise to the group is backed by a test that can never fail.
Header: O7
Options:
A) Apply: no-network render (Recommended)
CI renders inside a no-network sandbox, plus a test-only template proving the CSP blocks a real image link.
B) Keep current
S2 A: CSP + local-listener test on a hostile bubble.
C) Investigate first
Spike it in R2 and bring the result back; nothing changes until then.
D) Defer this
Leave the plan as is for now and record the proposal in TODOS.md.

State: approved
Actual answer: A) Apply: no-network render (D11, 2026-11-23; supersedes D5's listener test, keeps D5's CSP + no-URL rule + telemetry off)
Accepted scope: R2 keeps the CSP meta, the no-external-URL rule and HYPERFRAMES_NO_TELEMETRY=1; the CI render job runs one render inside a no-network sandbox (`unshare -n` or a container with `--network none`) that must succeed; a test-only template with a real `<img src=http://127.0.0.1:PORT>` proves the CSP blocks it (listener sees zero hits). D5's bubble-URL listener test is dropped.
History: none

### O8: Runtime install and CI render job move into R2
Finding: 8, High, outside voice (native fallback), R2's runner needs the pinned runtime, but `doctor --install` and the CI render job land in R5; R2-R4 end-to-end tests would all skip in CI
Plan baseline: runtime install + CI render job in R5
Runtime evidence: R2's runner needs the pinned runtime, but `doctor --install` and the CI render job land in R5; R2-R4 end-to-end tests would all skip in CI
Comparison grid:
| Choice | Current | A | B | C | D |
| --- | --- | --- | --- | --- | --- |
| O8 | runtime install + CI render job in R5 | R2 ships a minimal runtime installer (pinned hyperframes + Chrome into `CHATCOM_HOME/runtime/<version>/`) and the CI render job; R5 keeps `doctor` reporting, the demo show and the timed demo job | keep current | investigate in R2, then ask again | defer this proposal |
| S1, S2/O7, S3, C1, C2, other O-rows | as approved / pending | unchanged | unchanged | unchanged | unchanged |
Question: Move the render runtime and CI render job into R2? <gstack-qid:plan-eng-review-o8>
ELI10: Each PR is supposed to ship green on its own. But the tools that render video only get installed in the last PR, so the three PRs before it would skip every real render test in CI and look green without proving anything.
Stakes: the riskiest code merges three times without one real render in CI.
Header: O8
Options:
A) Apply: runtime + CI in R2 (Recommended)
R2 adds the pinned runtime install and the CI render job; R5 keeps doctor, demo and the timed job.
B) Keep current
runtime install + CI render job in R5.
C) Investigate first
Spike it in R2 and bring the result back; nothing changes until then.
D) Defer this
Leave the plan as is for now and record the proposal in TODOS.md.

State: approved
Actual answer: A) Apply: runtime + CI in R2 (D12, 2026-11-23)
Accepted scope: R2 adds a minimal runtime installer (pinned hyperframes + Chrome into CHATCOM_HOME/runtime/<version>/, lockfile) and the CI render job (runs on main and on PRs touching render code); R3 and R4 extend that job (encode, audio stream); R5 keeps `doctor` reporting, the demo show and the timed demo job.
History: none

### O12: Commit only after the episode is watched
Finding: 12, Medium, outside voice (native fallback), one command goes `cast` to `committed`; a bad frame is baked into the bible and `recast` is blocked after `cast`
Plan baseline: `chatcom render` runs through `committed`
Runtime evidence: one command goes `cast` to `committed`; a bad frame is baked into the bible and `recast` is blocked after `cast`
Comparison grid:
| Choice | Current | A | B | C | D |
| --- | --- | --- | --- | --- | --- |
| O12 | `chatcom render` runs through `committed` | `chatcom render` stops at `encoded` and prints the MP4 path; the episode commits when the next episode starts (or on `chatcom posted`); until then `recast` and re-render still work | keep current | investigate in R2, then ask again | defer this proposal |
| S1, S2/O7, S3, C1, C2, other O-rows | as approved / pending | unchanged | unchanged | unchanged | unchanged |
Question: Should the episode commit right after encode, or wait? <gstack-qid:plan-eng-review-o12>
ELI10: Committing locks the episode into the show's memory. If it happens right after encoding, nobody has looked at the video yet, and a bad frame or overflowing bubble can no longer be fixed with recast. Waiting until the next episode starts, or until you mark it posted, keeps the fix window open.
Stakes: a bad episode is locked in before anyone watches it.
Header: O12
Options:
A) Apply: commit later (Recommended)
Stop at `encoded`; commit when the next episode starts or on `chatcom posted`.
B) Keep current
`chatcom render` runs through `committed`.
C) Investigate first
Spike it in R2 and bring the result back; nothing changes until then.
D) Defer this
Leave the plan as is for now and record the proposal in TODOS.md.

State: pending (investigate)
Actual answer: C) Investigate first (D13, 2026-11-23)
Accepted scope: investigation only: before R3 builds the commit step, try both flows (commit at encode vs commit when the next episode starts or on `chatcom posted`) on a rendered fixture episode and bring the result back as a new question. Until then the plan's baseline (commit after encode) stays unchanged and the remedy stays pending. No implementation approved.
History: none

### O13: How fonts and big assets reach users
Finding: 13, Medium, outside voice (native fallback), Noto Color Emoji ~10 MB and a CJK fallback 15-20 MB per weight would bloat the wheel and be copied into every episode
Plan baseline: fonts bundled in the package, copied per episode
Runtime evidence: Noto Color Emoji ~10 MB and a CJK fallback 15-20 MB per weight would bloat the wheel and be copied into every episode
Comparison grid:
| Choice | Current | A | B | C | D |
| --- | --- | --- | --- | --- | --- |
| O13 | fonts bundled in the package, copied per episode | small fonts and CC0 audio ship as package data; Noto Color Emoji and the CJK fallback download in the runtime install, checked by sha256; the composer links (not copies) assets into each build; the docs list scripts with no fallback (Arabic, Hebrew, Devanagari, Thai) | keep current | investigate in R2, then ask again | defer this proposal |
| S1, S2/O7, S3, C1, C2, other O-rows | as approved / pending | unchanged | unchanged | unchanged | unchanged |
Question: Where do the large fonts come from? <gstack-qid:plan-eng-review-o13>
ELI10: Emoji and Chinese/Japanese/Korean fonts are large: about 10 MB and 15-20 MB. Putting them in the Python package makes every install heavy, and copying them into every episode folder fills the disk. Downloading them once in the runtime install, checked by a fingerprint, keeps the package small.
Stakes: a heavy install and per-episode disk bloat, or missing glyphs in group chats with emoji.
Header: O13
Options:
A) Apply: download in runtime (Recommended)
Big fonts download once with the runtime (sha256-checked); small assets ship in the package; builds link assets.
B) Keep current
fonts bundled in the package, copied per episode.
C) Investigate first
Spike it in R2 and bring the result back; nothing changes until then.
D) Defer this
Leave the plan as is for now and record the proposal in TODOS.md.

State: approved
Actual answer: A) Apply: download in runtime (D14, 2026-11-23)
Accepted scope: Small fonts (with OFL texts) and CC0 audio ship as package data under src/chatcom; Noto Color Emoji and the CJK fallback download once in the R2 runtime install into CHATCOM_HOME/runtime/<version>/, each checked by sha256 with a coded error naming the fix; the composer links assets into each build instead of copying; docs list the scripts with no fallback (Arabic, Hebrew, Devanagari, Thai).
History: none

### O15: Where the two stills come from
Finding: 15, Low, outside voice (native fallback), PLAN.md says first frame of scenes 1 and 3; the prototype's shots fade in from opacity 0, so the first frame can be black; stills under `render/` are lost if that folder is deleted
Plan baseline: first frame of scenes 1 and 3 (PLAN.md)
Runtime evidence: PLAN.md says first frame of scenes 1 and 3; the prototype's shots fade in from opacity 0, so the first frame can be black; stills under `render/` are lost if that folder is deleted
Comparison grid:
| Choice | Current | A | B | C | D |
| --- | --- | --- | --- | --- | --- |
| O15 | first frame of scenes 1 and 3 (PLAN.md) | take each still 0.5 s into the scene and save it to `episodes/<code>/stills/`; episode 2+ with no previous stills shows the synopsis card only | keep current | investigate in R2, then ask again | defer this proposal |
| S1, S2/O7, S3, C1, C2, other O-rows | as approved / pending | unchanged | unchanged | unchanged | unchanged |
Question: Take the 'Previously on' stills a moment into the scene? <gstack-qid:plan-eng-review-o15>
ELI10: Next week's episode opens with two stills from this one. The plan takes the very first frame of a scene, but scenes fade in, so that frame can be black. Taking it half a second in, and keeping it outside the build folder, gives a real picture that survives cleanup.
Stakes: 'Previously on' shows a black frame, or loses its stills when the build folder is cleaned.
Header: O15
Options:
A) Apply: 0.5 s in, kept (Recommended)
Still at 0.5 s into scenes 1 and 3, saved under episodes/<code>/stills/; synopsis-only when none exist.
B) Keep current
first frame of scenes 1 and 3 (PLAN.md).
C) Investigate first
Spike it in R2 and bring the result back; nothing changes until then.
D) Defer this
Leave the plan as is for now and record the proposal in TODOS.md.

State: approved
Actual answer: A) 0.5 s in, kept (D15 brought back after the R2 evidence, answered 2026-11-23; first answer C, investigate)
Accepted scope: R3's commit takes each still 0.5 s into scenes 1 and 3 of the master and saves it under
`episodes/<code>/stills/`, recording the paths in the log entry; an episode whose previous entry has no stills shows
the synopsis card only (R2's composer already does). PLAN.md's "first frame" rule is updated.
Evidence (R2, 2026-11-23): with the R2 templates the first frame of a scene is not black. Scenes do not fade in;
the set and the cast (in their resting pose) are on screen from frame 1, with no words yet. At 0.5 s the first
line is on screen (lead-in 0.3 s + pop 0.24 s) and its speaker is in the line's pose. Stills are written at commit
(R3), so nothing in R2 depends on the answer.
Accepted scope: investigation only: in R2, look at the real first frames of scenes 1 and 3 in a rendered fixture and bring the result back as a new question. Until then PLAN.md's rule (first frame of scenes 1 and 3) stays unchanged and the remedy stays pending. No implementation approved.
History: none

### O16: Encode once at fixed quality first
Finding: 16, Low, outside voice (native fallback), 16 MB over 60 s is about 2.1 Mb/s, plenty for stills-based motion; two-pass with 3 retries is the common path
Plan baseline: two-pass to the cap, up to 3 tries (PLAN.md)
Runtime evidence: 16 MB over 60 s is about 2.1 Mb/s, plenty for stills-based motion; two-pass with 3 retries is the common path
Comparison grid:
| Choice | Current | A | B | C | D |
| --- | --- | --- | --- | --- | --- |
| O16 | two-pass to the cap, up to 3 tries (PLAN.md) | encode once at a fixed quality (CRF); only when the file is over the cap, fall back to two-pass, up to 3 tries; same exit 5 contract | keep current | investigate in R2, then ask again | defer this proposal |
| S1, S2/O7, S3, C1, C2, other O-rows | as approved / pending | unchanged | unchanged | unchanged | unchanged |
Question: Encode once at fixed quality, two-pass only if too big? <gstack-qid:plan-eng-review-o16>
ELI10: The size cap allows about 2 megabits a second, which is plenty for this kind of video, so most episodes will fit with one simple encode. The plan always runs the slower two-pass method. Encoding once first and using two-pass only when the file is too big makes the common case faster.
Stakes: every render pays for two encode passes it rarely needs, or a fixed-quality file misses the cap.
Header: O16
Options:
A) Apply: CRF first (Recommended)
One fixed-quality encode; two-pass (3 tries) only when the file is over the cap.
B) Keep current
two-pass to the cap, up to 3 tries (PLAN.md).
C) Investigate first
Spike it in R2 and bring the result back; nothing changes until then.
D) Defer this
Leave the plan as is for now and record the proposal in TODOS.md.

State: approved
Actual answer: A) Apply: fixed quality first (D16, 2026-11-23)
Accepted scope: R3's encoder runs one fixed-quality (CRF) H.264 encode; only when the file is over --max-size does it fall back to two-pass at lower bitrates, up to 3 tries, then exit 5 naming size, cap and bitrates, master kept. Tests: a normal fixture fits in one pass; a tiny cap walks the fallback to exit 5. PLAN.md's encode wording is updated.
History: none

## TODOS.md updates

No new TODO proposed: the recast notes already in TODOS.md cover the follow-ups this plan touches, and the two open
investigations (O12, O15) are pending decisions inside this plan, not deferrals.

Approval readiness: PASS — S1 (D4), S2 (D5, test superseded by O7/D11), S3 (D6), C1 (D7), C2 (D8), O2 (D9),
O6 (D10), O7 (D11), O8 (D12), O13 (D14), O16 (D16) each cite their answer; S5 and O1, O3, O4, O5, O9, O10, O11, O14
are carried requirements of PLAN.md or of C1; O12 (D13) and O15 (D15) are pending investigations with no
implementation approved.

## NOT in scope

- First live run on the League Guys chat and the side-by-side comparison: manual, Bryan, after R5.
- Per-stage time and cost measurement and `docs/costs.md`: after the first live run (R2 measures render time only).
- Voices: v0.1 has music and SFX only (PLAN.md).
- The reaction loop and `chatcom posted` behaviour beyond what O12's investigation needs.
- The long cut (`--long`) and per-member credit stills for sharing: later.
- Skill steps beyond one render step: the skill gains `chatcom render` only.
- Fallback fonts for Arabic, Hebrew, Devanagari and Thai: documented as not covered (O13).

## What already exists

`core/budget.py` (segment budget, reused by R1, O3); `core/beats.py` validate (gains the fit check, O2);
`core/state.py` stages (R2/R3 advance them); `core/bible.py` episode log with `synopsis` and `stills` (O1 reads the
previous entry); `adapters/store.py` atomic writes (every file the stages write); `app.py:836` ordered two-file write
(C1 reuses the pattern); the three ffmpeg lookups in casting (folded into `adapters/tools.py`, C2); the prototype
`marketing/episode/build.py` (look and segment structure ported; its GSAP, CDN and hard-coded layout are not).

## Failure modes

| Path | Realistic failure | Covered by | User sees |
| --- | --- | --- | --- |
| R1 timeline | scene cannot fit | O2: validation fails, writer retries | writer retry, not a stuck episode |
| R2 composer | bubble with markup or a URL | escaping + CSP + no-network CI render (O7) | literal text |
| R2 runtime | HyperFrames, Chrome or fonts missing or wrong sha256 | runtime installer + preflight error | exit 3 with the install command |
| R2 render | Chrome crash or timeout | runner checks exit and duration | exit 5 with the log path |
| R3 encode | cap cannot be met | fallback, 3 tries | exit 5 with size, cap, bitrates; master kept |
| R3 commit | crash between the two writes | C1 + O4 rerun detection, tests for both crash points | rerun completes silently |
| R4 sound | a sticker with no SFX | sync test (enum = manifest = templates) | build fails, not a silent gap |
| Previously on | wrong log entry | O1 test: episode 2 shows episode 1 | correct recap |

Critical gaps (no test, no handling, silent): none.

## Worktree parallelization strategy

| Step | Modules touched | Depends on |
| --- | --- | --- |
| R1 timeline | core/ (render, budget, beats) | — |
| R2 composition + runtime | adapters/, render_templates/, cli, CI | R1 |
| R3 encode + commit | adapters/, app, core/state | R2 |
| R4 sound | assets, render_templates/, CI licence check | R2 |
| R5 doctor + demo | cli, examples/, CI | R2, R3, R4 |

Lane A: R1 → R2 → R3 → R5. Lane B: R4 (after R2, independent of R3). Execution order: R1, then R2; then R3 and R4 in
parallel worktrees; then R5. Conflict flag: R3 and R4 both touch `render_templates/` only if R3 changes stills
capture (O15); coordinate there.

## Implementation Tasks
Synthesized from this review's findings. Each task derives from a specific finding above. Run with Claude Code or
Codex; checkbox as you ship.

- [ ] **T1 (P1, human: ~1d / CC: ~40min)** — core — Timeline from budget.py, bubble holds, previous-episode recap, fit check in validation
  - Surfaced by: O1, O2, O3 and Section 1 shape A
  - Files: src/chatcom/core/render.py, src/chatcom/core/beats.py, tests/test_render_timeline.py
  - Verify: `.venv/bin/python -m pytest -q tests/test_render_timeline.py`
- [ ] **T2 (P1, human: ~2d / CC: ~2h)** — render — Composer, layout rules, CSS motion, CSP, runtime installer, runner, `chatcom render`, CI render job
  - Surfaced by: S2/O7, S3, O5, O6, O8, O9, O13, O14, C2
  - Files: src/chatcom/adapters/{composer,hyperframes,tools}.py, src/chatcom/render_templates/, src/chatcom/cli.py, package.json, .github/workflows/ci.yml
  - Verify: CI render job (no-network sandbox), 360 px snapshot, CSP image test, duration 60 s +- 0.1
- [ ] **T3 (P1, human: ~1d / CC: ~45min)** — encode/commit — Fixed-quality encode with two-pass fallback; ordered commit with pre-snapshot and rerun detection
  - Surfaced by: O16, C1, O4, O11, O5
  - Files: src/chatcom/adapters/encoder.py, src/chatcom/app.py, src/chatcom/core/state.py
  - Verify: tiny-cap test hits exit 5; crash after cast write and after bible write both recover
- [ ] **T4 (P1, human: ~1d / CC: ~1h)** — sound — CC0 music bed and sticker SFX, manifest, licence check, sync test
  - Surfaced by: S1, Section 1 red flag 8
  - Files: src/chatcom/assets/, scripts/check_licences.py, render templates
  - Verify: licence check passes; audio stream present in the CI render
- [ ] **T5 (P2, human: ~1d / CC: ~1h)** — demo — doctor reporting, demo show, keyless `chatcom demo`, timed job
  - Surfaced by: plan R5, O14
  - Files: src/chatcom/cli.py, examples/lake-house/, .github/workflows/
  - Verify: `chatcom demo` with no keys prints the MP4 path last
- [ ] **T6 (P2, human: ~30min / CC: ~5min)** — docs — Fix PLAN.md:653's stale budget; update the pin and encode wording when R2/R3 land
  - Surfaced by: O10, S3, O16
  - Files: PLAN.md
  - Verify: `.venv/bin/python scripts/check_spec.py`
- [ ] **T7 (P2, human: ~1h / CC: ~10min)** — decisions — Bring back O12 (when to commit) and O15 (stills frame) with evidence before R3 and in R2
  - Surfaced by: D13, D15
  - Files: docs/design/render.md
  - Verify: both ledger rows answered

### Unresolved decisions that may bite you later
- O12 (D13): when the episode commits — investigate before R3 builds the commit step.
- O15 (D15): answered after the R2 evidence: stills 0.5 s into scenes 1 and 3, kept under `episodes/<code>/stills/` (R3).

### Completion summary
- Step 0: Scope Challenge — scope accepted as-is (5-PR arrangement, same scope)
- Reality rows: 7/7 applicable rows emitted (`PLAN_REALITY: phase=eng rows=7/7 verdict=complete`), 2 unverified premises
- Architecture Review: 2 issues found (C1, plus the red flags folded into required work)
- Code Quality Review: 1 issue found (C2)
- Test Review: diagram produced, 33 gaps identified (all proposed code)
- Performance Review: 0 issues found
- NOT in scope: written
- What already exists: written
- TODOS.md updates: 0 items proposed to user
- Failure modes: 0 critical gaps flagged
- Unresolved decisions: 2 in this review
- Outside voice: codex not installed; native fallback (Plan agent) completed, 16 findings, all dispositioned
- Parallelization: 2 lanes, 1 parallel step (R3 with R4) / 4 sequential
- Lake Score: 9/9 = answers picking the 10/10 option / answers scored for completeness (D4, D5, D8, D9, D10, D11, D12, D14, D16)

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | ISSUES OPEN (2026-11-23, wave-1 plan; not run for this plan by owner choice) | mode: SELECTIVE_EXPANSION, 0 critical gaps |
| Outside Review | native fallback (Plan agent; Codex not installed) | Independent 2nd opinion | 1 | unavailable (outside); native completed | 16 findings, all dispositioned (8 carried, 6 applied, 2 investigate) |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 4 | ISSUES OPEN (this run, not yet logged) | 36 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — (skipped by owner choice; check frames later with /design-review) | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 1 | ISSUES OPEN (2026-11-23, wave-1 plan) | score: 6/10 → 7.6/10 |

- **OUTSIDE COVERAGE:** eng phase — provider codex, not installed: outside coverage unavailable; native Plan-agent fallback completed inside its 5-minute timer with 16 findings. CEO, design and DX phases did not run for this plan.
- **VERDICT:** No review CLEAR for this plan: Eng Review has 2 open decisions (O12, O15) and mapped work. Ready to build R1, whose scope is fully decided.

**UNRESOLVED DECISIONS:**
- O12 (D13): when the episode commits — investigate before R3's commit step
- O15 (D15): which frame the 'Previously on' stills use — investigate in R2
