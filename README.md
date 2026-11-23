<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/assets/header-chatcom-dark.png">
  <img alt="CHATCOM: turn your group chat into your favorite TV series. A Claude skill by Bryan Bartlett, B2B Content Systems." src=".github/assets/header-chatcom-light.png">
</picture>

**Turn your group chat into your favorite TV series.** CHATCOM reads your group chat and writes an episode
starring the people in it, in the style of a show you pick. Next week it writes the next one, and it remembers
the running jokes.

**Current status: v0.1 is in development.** Today CHATCOM reads the chat, writes the episode's script
(`beats.json`) and the show's memory, and casts the show (each member in 6 poses, and the sets). The video
render is next. The sample episodes on the site
were made with the same pipeline.

<p align="center"><img src=".github/assets/demo.gif" alt="One group chat becomes an episode in several styles" width="640"></p>

<p align="center"><a href="https://b2b-content-systems.github.io/chatcom/"><b>Watch the trailer and a full episode →</b></a></p>

## What it reads

| Your chat | How to get it in |
| --- | --- |
| WhatsApp | The chat's **Export chat** button (.zip or .txt). [How](docs/whatsapp-export.md) |
| Telegram | Telegram Desktop, **Export chat history**, machine-readable JSON. [How](docs/telegram-export.md) |
| Signal, iMessage, Discord, Google Messages, Snapchat, any app | Screenshots. [How](docs/screenshots.md) |
| Any app | Paste the chat, or type it. [Format](docs/transcript-format.md) |

Exports from Messenger, Instagram, GroupMe and iMessage are planned, not built.

## Install

In Claude Code:

```
/plugin marketplace add b2b-content-systems/skills
/plugin install chatcom@b2b-content-systems
```

Then say **"make our group chat a show"** and give it the export, the screenshots folder, or paste the chat.

v0.1 is not released, so these commands are the release path, not a working install yet. To try the engine as
it stands:

```bash
pipx install git+https://github.com/b2b-content-systems/chatcom
chatcom doctor
```

## Two ways to run it

| | Skill mode (Claude Code) | CLI mode (`chatcom episode`) |
| --- | --- | --- |
| Reads screenshots | Your Claude Code session | Gemini, with your `GEMINI_API_KEY`; Claude if Gemini cannot answer |
| Writes the episode | Your Claude Code session | Claude, with your `ANTHROPIC_API_KEY`; Gemini if Claude cannot answer |
| Keys | None beyond your Claude Code plan | Your own. CHATCOM has none |
| Sees phone numbers and people you exclude | Yes, when it reads screenshots or a paste | Only the reader; both are removed before the writer |

For a group you do not fully trust, use CLI mode.

## Pick your show

| The show | Looks like |
| --- | --- |
| The sitcom | 3D feature, family-film warm |
| The anime | City-pop, bright and flat |
| The 80s cartoon | Glossy airbrush, Saturday morning |
| The true-crime doc | Grainy documentary |
| The video-game cutscene | PS1-era 3D |
| The family FaceTime | Video-call grid |
| The 2006 flip-phone | Low-res phone camera |
| The reality show | Phone snapshots |

## Where your chat goes

| Data or action | Where it goes |
| --- | --- |
| Your export, screenshots and every file CHATCOM makes | Your computer, under `~/.chatcom/<show>/` |
| Screenshots | Copied and re-encoded with no metadata first. Only the copies are read |
| Chat text for the episode, skill mode | The model provider your Claude Code session uses |
| Chat text, CLI mode | Anthropic with your key, and Google if the fallback fires |
| Screenshots, CLI mode | Google (Gemini) with your key, and Anthropic if the fallback fires |
| Phone numbers | Replaced with `Unnamed xxxxxx` in the messages the writer gets |
| People you exclude | Removed before the writer, with their names in reactions and quotes |
| Reference photos and image prompts (from casting on) | Google (Gemini) with your key |

There is no CHATCOM server. Before the first model call, CHATCOM prints where the chat will go and waits for a
yes. Stored on your computer is not the same as processed on your computer: what a model reads, its provider
processes under its own terms. This project makes no promise about provider retention. Full detail:
[data-flow.md](docs/data-flow.md).

## What it costs

CHATCOM is free. Model calls are billed to your own provider accounts. In CLI mode, `chatcom episode` reports
how many it made (`calls_billed`). The limits:

| Step | Limit |
| --- | --- |
| Reading screenshots | 3 tries per image, 3 at a time, 40 images per import (`--max-images` raises it) |
| Writing an episode | 3 attempts per prep, 3 preps per episode |
| Casting | You approve the exact number of image calls first. A 5-person cast is 35 for the first episode (6 poses each, the style frame, the sets); later episodes at most 2 new sets |
| Re-runs | Reads are cached in the show, so the same screenshots are never paid for twice |

## Commands

| Command | What it does |
| --- | --- |
| `chatcom new <export>` | Make a show from a chat and pick the cast |
| `chatcom episode <show> --export <file>` | Make this week's episode (CLI mode) |
| `chatcom ingest`, `write-prep`, `write-submit` | The skill's writing steps |
| `chatcom read-prep`, `read-submit` | The skill's screenshot-reading steps |
| `chatcom cast <show>` | Draw the cast (6 poses each) and the sets for this episode |
| `chatcom status <show>` | Where the episode stands, and the next command |
| `chatcom migrate <show>` | Upgrade a show made by an older version (backs it up first) |
| `chatcom shows`, `chatcom doctor` | List shows; check this computer |

An episode moves through `copied` → `read` → `ingested` → `drafted` → `cast` → `rendered` → `encoded` →
`committed`. This release stops at `cast`. The show's memory changes only at `committed`.

## Remove a show

1. Delete `~/.chatcom/<show>/`. That removes its copies, reads, transcripts, episodes and memory.
2. `~/.chatcom/imports/` holds screenshot imports for shows not made yet. Delete what you do not need.
3. In skill mode, the chat also stays in your Claude Code session history. That is separate from CHATCOM.
4. Revoke API keys at the provider if no other tool needs them.

## Docs

| Doc | For |
| --- | --- |
| [whatsapp-export.md](docs/whatsapp-export.md), [telegram-export.md](docs/telegram-export.md) | Getting a chat out of an app |
| [screenshots.md](docs/screenshots.md), [transcript-format.md](docs/transcript-format.md) | Screenshots, pasting and typing a chat |
| [data-flow.md](docs/data-flow.md) | What goes where, in detail |
| [errors.md](docs/errors.md) | Every error code and its fix |
| [bible-schema.md](docs/bible-schema.md), [reader-eval.md](docs/reader-eval.md) | The show's memory; how readers are measured |
| [CONTRIBUTING-adapters.md](CONTRIBUTING-adapters.md), [AGENTS.md](AGENTS.md) | Adding a chat app; working in this repo |

## Share it

The trailer in every format and four languages, posts ready to paste and a "Made with CHATCOM" badge:
**[the share kit](https://b2b-content-systems.github.io/chatcom/share/)**. Please credit: *CHATCOM, a free
Claude skill by Bryan Bartlett*.

## Use it with care

Make a show only from a chat you are in, and tell the group before you do. The episode is about real people:
ask before you post it anywhere they did not choose. Exclude anyone who does not want to be in it.

---

Made by **[Bryan Bartlett](https://bryanbartlett.com)**, B2B Content Systems, Boston, MA. MIT licence. Every show,
person and chat in the samples and tests is fictional.
