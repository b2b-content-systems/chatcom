# The open formats

CHATCOM's memory is plain JSON the group owns. The schemas are JSON Schema (draft 2020-12) in [`src/chatcom/schemas/`](../src/chatcom/schemas/):

| File | Schema | What it holds |
|---|---|---|
| `messages.jsonl` | [message](../src/chatcom/schemas/message.schema.json) | one normalized message per line, from any adapter |
| `bible.json` | [bible](../src/chatcom/schemas/bible.schema.json) | show, cast (up to 8), chorus, running-joke ledger, storylines, episode log with the window cursor |
| `cast.json` | [cast](../src/chatcom/schemas/cast.schema.json) | pinned image model, pose vocabulary, pose sets, background library |
| `beats.json` | [beats](../src/chatcom/schemas/beats.schema.json) | the writer's output for one episode, plus its bible update |
| `setup.json` | [setup](../src/chatcom/schemas/setup.schema.json) | first-run answers ([example](../examples/setup.json)) |

Rules shared by all of them: an unknown key is an error (a typo cannot silently drop a field); a key starting with `_` is a comment; `null` means intentionally empty; `schema_version` is checked on load and a newer one is refused.

The bible changes only when an episode commits; until then the writer's update waits in the episode folder as `bible.next.json`. At commit (arriving with the render step) the previous bible is kept as `snapshots/bible.S01E03.pre.json`.

## The cursor
Each episode logs `window.cursor = {minute, hash}`: the UTC minute and content hash of the last message it read. The next episode starts right after that exact message, so overlapping exports never repeat a week. A message's hash is `(sender, UTC minute, ordinal within that sender-minute, normalized text)`, so the same chat exported from iOS and Android agrees, and three "k"s in one minute stay three messages.
