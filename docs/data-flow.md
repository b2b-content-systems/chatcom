# Where your chat goes

Every run that sends anything prints this first and waits for a yes.

**Skill mode (Claude Code):** the chat text for the episode goes to the model provider your Claude Code session uses. From casting on (Next Step 3), reference photos and image prompts go to Google (Gemini) with your key. The normalized messages also stay in your local Claude Code session transcript, outside `~/.chatcom/`.

**CLI mode (`--writer anthropic`):** the chat text goes to Anthropic with your `ANTHROPIC_API_KEY`, and to Google if the Gemini fallback fires (no key, expired key, rate limit or outage). Photos and image prompts: Google, as above. Scripts answer the notice with `--accept-data-flow`.

**Never sent:** the raw export, media files, location links, anyone you `exclude` (and @mentions of them, by name or alias), phone numbers (a number-only sender, WhatsApp's number-style @mentions, and in message text any run of 9 or more digits, a 7 or 8 digit number grouped like a phone or after a word like "tel" or "call", and the phone part of any link, become a stable `Unnamed` label or the alias you set; dates, short decimals such as 3.14, and ids longer than 15 digits inside links are left as written. The matching is deliberately broad, so some non-phone numbers are hidden too), your `aliases` list, and your API keys (sent only in request headers, redacted from every log and error).

**On disk:** everything lives in `CHATCOM_HOME` (default `~/.chatcom/`, mode 0700), outside any git checkout. CHATCOM never uploads a chat or hosts one.

**What the skill may read:** in skill mode the agent types the commands. `chatcom ingest` in skill mode and `chatcom episode --dry-run` without a terminal read only the export you gave `chatcom new` or a file in the show's inbox (`~/.chatcom/<show>/inbox/`). Save a new week's export there. A text file is read as a chat only when it is a real export or starts with a `# source:` header. The other commands that read an export (`chatcom new`, and `chatcom episode` with another writer) do not check the path: they send nothing until someone passes `--accept-data-flow` after reading the notice, so that flag is the boundary. Read the notice before you say yes.

**Ids and labels:** each show has a random secret (`~/.chatcom/<show>/secret`, mode 0600). Message ids, the "where the last episode ended" marker and the `Unnamed` labels are keyed hashes made with it, so no phone number can be worked back out of them.

You are responsible for what you upload. In a group you do not trust, prefer CLI mode: the chat then never enters an agent session that can run commands.
