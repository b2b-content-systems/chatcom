# Where your chat goes

Every run that sends anything from your chat prints this first and waits for a yes.

**Skill mode (Claude Code):** the chat text for the episode goes to the model provider your Claude Code session uses. From casting on (Next Step 3), reference photos and image prompts go to Google (Gemini) with your key. The normalized messages also stay in your local Claude Code session transcript, outside `~/.chatcom/`.

**CLI mode (`--writer anthropic`):** the chat text goes to Anthropic with your `ANTHROPIC_API_KEY`, and to Google if the Gemini fallback fires (no key, expired key, rate limit or outage). Photos and image prompts: Google, as above. Scripts answer the notice with `--accept-data-flow`.

**Never sent:** the raw export, media files, location links, anyone you `exclude` (and @mentions of them, by name or alias), phone numbers (a number-only sender, WhatsApp's number-style @mentions, and in message text any run of 9 or more digits, a 7 or 8 digit number grouped like a phone or after a word like "tel" or "call", and the phone part of any link, become a stable `Unnamed` label or the alias you set; dates, short decimals such as 3.14, and ids longer than 15 digits inside links are left as written. The matching is deliberately broad, so some non-phone numbers are hidden too), your `aliases` list, and your API keys (sent only in request headers, redacted from every log and error).

**The render and the encode** (`chatcom render`) and **the commit** send nothing. It runs on your computer with no keys, and the video page allows no outside request. **The one-time render install** (`chatcom doctor --install`) only downloads: HyperFrames and its packages from the npm registry, its Chrome, and the emoji and CJK fonts from GitHub. It sends no chat data. npm and HyperFrames get no key or token variable from your shell, and npm does not read your `~/.npmrc`.

**On disk:** everything lives in `CHATCOM_HOME` (default `~/.chatcom/`, mode 0700), outside any git checkout. CHATCOM never uploads a chat or hosts one.

**What the skill may read:** in skill mode the agent types the commands. `chatcom ingest` in skill mode and `chatcom episode --dry-run` without a terminal read only the export you gave `chatcom new` or a file in the show's inbox (`~/.chatcom/<show>/inbox/`). Save a new week's export there. A text file is read as a chat only when it is a real export or starts with a `# source:` header. The other commands that read an export (`chatcom new`, and `chatcom episode` with another writer) do not check the path: they send nothing until someone passes `--accept-data-flow` after reading the notice, so that flag is the boundary. Read the notice before you say yes.

**Screenshots in CLI mode** go to Google (Gemini) with your `GEMINI_API_KEY`, and to Anthropic with your `ANTHROPIC_API_KEY` if Gemini cannot answer. **Screenshots** are copied and re-encoded with no metadata first; only the copies are read. Reads are cached inside the show (`~/.chatcom/<show>/cache/reads/`, unmasked, like the copies) so a re-run never pays twice; they go when the show folder goes. Reads for a show `chatcom new` has not made yet wait in `~/.chatcom/imports/cache/`, so a failed `new` can be retried without paying again; they move into the show when it is made. Recorded (replay) reads are never cached. The notice for screenshots differs from the one for exports: in skill mode the session sees the screenshots in full, including phone numbers and anyone you exclude (prefer CLI mode for a group you do not fully trust); in CLI mode, numbers and excluded people are removed after reading, before the writer.

**A pasted chat:** in skill mode, a chat you paste is already in your Claude Code session. The skill saves it as a transcript (`docs/transcript-format.md`) under `~/.chatcom/` and imports it like an export; the same notice and `--accept-data-flow` apply.

**Ids and labels:** each show has a random secret (`~/.chatcom/<show>/secret`, mode 0600). Message ids, the "where the last episode ended" marker and the `Unnamed` labels are keyed hashes made with it, so no phone number can be worked back out of them.

You are responsible for what you upload. In a group you do not trust, prefer CLI mode: the chat then never enters an agent session that can run commands.
