# The open formats

CHATCOM's memory is plain JSON the group owns. The schemas are JSON Schema (draft 2020-12) in [`src/chatcom/schemas/`](../src/chatcom/schemas/):

| File | Schema | What it holds |
|---|---|---|
| `messages.jsonl` | [message](../src/chatcom/schemas/message.schema.json) | one normalized message per line, from any adapter |
| `bible.json` | [bible](../src/chatcom/schemas/bible.schema.json) | show, cast (up to 8), chorus, running-joke ledger, storylines, episode log with the window cursor |
| `cast.json` | [cast](../src/chatcom/schemas/cast.schema.json) | pinned image model, pose vocabulary, pose sets, background library, recast takes (`take`, `take_id`) |
| `beats.json` | [beats](../src/chatcom/schemas/beats.schema.json) | the writer's output for one episode, plus its bible update |
| `setup.json` | [setup](../src/chatcom/schemas/setup.schema.json) | first-run answers ([example](../examples/setup.json)) |

Rules shared by all of them: an unknown key is an error (a typo cannot silently drop a field); a key starting with `_` is a comment; `null` means intentionally empty; `schema_version` (now 2) is checked on load: a newer one is refused, and an older one is refused with a pointer to `chatcom migrate <show>`, which backs the show up and upgrades it.

The bible changes only when an episode commits; until then the writer's update waits in the episode folder as `bible.next.json`. At commit (arriving with the render step) the previous bible is kept as `snapshots/bible.S01E03.pre.json`.

## The cursor
Each episode logs `window.cursor = {minute, hash, adapter, precision, signature}`: the last message it read (its
UTC minute, or `null` when the source shows no time), which source it came from, and two short signatures of the
last rows (one on the names as the source showed them, one on the names after aliases). The next episode resumes
after that exact message; if the new import does not contain it, by time; if the source has no times (screenshots,
an undated transcript), by finding those last rows again. Only keyed hashes are stored, never text or numbers.
Ids and signatures are HMACs keyed by the show's secret (`secret`, mode 0600), built from the sender as the source
showed it, the time and the message text with case, punctuation and spacing removed, so three "k"s in one minute
stay three messages and a screenshot re-read as "K" still matches. The full rules are in
[`design/any-group-chat.md`](design/any-group-chat.md).
