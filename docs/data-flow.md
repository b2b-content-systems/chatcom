# Where your chat goes

Every run that sends anything prints this first and waits for a yes.

**Skill mode (Claude Code):** the chat text for the episode goes to the model provider your Claude Code session uses. From casting on (Next Step 3), reference photos and image prompts go to Google (Gemini) with your key. The normalized messages also stay in your local Claude Code session transcript, outside `~/.chatcom/`.

**CLI mode (`--writer anthropic`):** the chat text goes to Anthropic with your `ANTHROPIC_API_KEY`, and to Google if the Gemini fallback fires (no key, expired key, rate limit or outage). Photos and image prompts: Google, as above. Scripts answer the notice with `--accept-data-flow`.

**Never sent:** the raw export, media files, location links, anyone you `exclude` (and @mentions of them, by name or alias), phone numbers (a number-only sender, WhatsApp's number-style @mentions and numbers typed in a message in a phone format, with a `+` or in groups, or 10 to 15 digits, all become a stable `Unnamed` label or the alias you set; a bare run of 9 digits is left alone, since it is as often an order or reference number), your `aliases` list, and your API keys (sent only in request headers, redacted from every log and error).

**On disk:** everything lives in `CHATCOM_HOME` (default `~/.chatcom/`, mode 0700), outside any git checkout. CHATCOM never uploads a chat or hosts one.

You are responsible for what you upload. In a group you do not trust, prefer CLI mode: the chat then never enters an agent session that can run commands.
