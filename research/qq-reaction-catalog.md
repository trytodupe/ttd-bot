# QQ group-reaction catalog research

Date: 2026-08-17 (Asia/Shanghai)

Scope: current SnowLuma deployment for QQ account `1940196378`, with live
verification in group `1022240664`. This note distinguishes reaction IDs from
message `face` IDs. It does not claim that every reaction ID can be encoded as
a message face.

## Result

- The live `0x9154_1` system-face catalog contains 386 pack entries and 350
  unique faces: 282 numeric `qSid` values and 68 Unicode-string `qSid` values.
  For reactions, the latter map to their decimal `qCid`, producing 350 known
  reaction IDs.
- QQ's local `face_config.json` contains 282 `sysface` entries and 165 `emoji`
  entries (447 IDs), but it is older than the live catalog (mtime
  `2026-06-03 11:15:58 +0800`). Its numeric system-face set and the live numeric
  set overlap on only 235 IDs: each side has 47 IDs absent from the other. All
  68 live Unicode entries occur in its 165-entry emoji set, leaving 97 extra
  Unicode candidates.
- Successful `notice.group_msg_emoji_like.add` events in the target group now
  cover the union of the live catalog, local config, and historical successful
  IDs: **528 unique reaction IDs**. There are no union candidates lacking a
  first-hand success notice at the time of this snapshot.
- The second search round added 112 verified IDs from `face_config.json`: 82
  extra Unicode `qCid` values and 30 older numeric system-face IDs. Combined
  with the earlier 66 historical IDs outside `0x9154`, this expands the
  verified set from 350 to 528.
- `㊗` is included as Unicode code point `12951` (`U+3297`) through historical
  notice evidence, not through `0x9154` or the current local
  `face_config.json`.

The runtime evidence is stronger than an API success response: the bot log
contains the resulting `group_msg_emoji_like.add` notices. For example, the
first outside-catalog batches and their notices begin at
`data/bot.log:12141994`, `data/bot.log:12142097`,
`data/bot.log:12142192`, and `data/bot.log:12142271`; the later local-config
batches begin at `data/bot.log:12143718` through `data/bot.log:12144023`, and
the final old numeric batches begin at `data/bot.log:12144570` and
`data/bot.log:12144636`. SnowLuma independently logged the corresponding
reaction pushes, for example in
`/home/ttd/deploy/SnowLuma/snowluma-data/logs/snowluma-2026-08-17.log:79661`
through `:79846` for the first 66 outside-catalog IDs.

## First-party sources

### Live system-face catalog (`0x9154_1`)

SnowLuma documents `0x9154_1` as QQ's system-face/emoji catalog and preserves
numeric `qSid`, Unicode-string `qSid`, and optional `qCid` in
`/home/ttd/ro-repositories/SnowLuma/packages/protocol/src/oidb-services/sys-faces/fetch-sys-faces.ts:1-38`.
It extracts the common, special-big, and magic-face groups at lines 110-161.

The public OneBot actions are read-only: `fetch_sys_faces`,
`fetch_face_entity`, `search_sys_faces`, and `fetch_super_face_id` are defined
in `/home/ttd/ro-repositories/SnowLuma/packages/onebot/src/actions/system-face.ts:50-136`.
A live `fetch_sys_faces` call with `refresh=false` returned `status=ok`, ten
packs, 386 entries, and the 350/282/68 counts above. The persisted response is
`/home/ttd/deploy/SnowLuma/snowluma-data/data/sys-face-catalog.json`, fetched at
`2026-08-17 22:56:16 +0800`.

Numeric system faces are message-encoding metadata, not a complete reaction
directory. SnowLuma classifies their send wire shape using the live catalog in
`/home/ttd/ro-repositories/SnowLuma/packages/protocol/src/sys-face-store.ts:49-68`
and encodes classic, small, and super faces differently in
`/home/ttd/ro-repositories/SnowLuma/packages/protocol/src/element-builder.ts:52-90`.
An ID absent from this live catalog can still be a valid reaction while failing
as a message `face` segment.

### QQ local `face_config.json`

The deployed QQ client resource is:

`/home/ttd/deploy/SnowLuma/snowluma-qq-config/QQ/global/nt_data/Emoji/emoji-resource/face_config.json`

Its `sysface` array starts at line 2 and its `emoji` array starts at line 2455.
Emoji entries explicitly pair the display character (`QSid`) with decimal
Unicode code point (`QCid`), as shown at lines 2455-2482. This file is useful
for discovery but is not authoritative-current: its 47 numeric IDs missing
from live `0x9154` demonstrate drift. Candidates from it therefore required
the success-notice verification performed in the target group.

### Reaction summary and the hidden `0x9084_1` tail

QQ `0x9084_1` returns both reactions already used on a message and an
"available reactions" catalog tail. SnowLuma states this directly in
`/home/ttd/ro-repositories/SnowLuma/packages/protocol/src/oidb-services/reaction/fetch-reaction-summary.ts:1-11`.
The response schema says used entries have timestamp/count, tail entries omit
them, and `emojiType` is 1 for short QQ faces and 2 for Unicode code points:
`/home/ttd/ro-repositories/SnowLuma/packages/proto-defs/src/oidb-actions/base.ts:927-965`.

The current deserializer discards every entry with missing/zero count at
`fetch-reaction-summary.ts:49-61`, so the available-reaction tail is lost. The
test suite explicitly verifies this filtering at
`/home/ttd/ro-repositories/SnowLuma/packages/protocol/tests/oidb-services/reaction/fetch-reaction-summary.test.ts:45-70`.

There is no public OneBot action that exposes the raw tail. The internal bridge
method is at
`/home/ttd/ro-repositories/SnowLuma/packages/core/src/bridge/apis/interaction.ts:56-67`
and is only consumed while reconciling the count for one requested emoji at
`/home/ttd/ro-repositories/SnowLuma/packages/onebot/src/instance-context.ts:132-153`.
The public `get_emoji_likes` and `fetch_emoji_like` actions return users, not
catalog entries (`packages/onebot/src/actions/extended.ts:1317-1399`). A live
read-only `get_emoji_likes` call confirmed that its response contains only
`emoji_like_list`.

This makes the unfiltered `0x9084` tail the best remaining first-party search
surface beyond the 528 verified IDs. It may contain more choices than the
union discovered from local files and historical notices, but that has not yet
been measured.

## Reproducible set construction

Run from `/home/ttd/deploy/ttd-bot`. These commands are read-only.

```bash
catalog=/home/ttd/deploy/SnowLuma/snowluma-data/data/sys-face-catalog.json
face_config=/home/ttd/deploy/SnowLuma/snowluma-qq-config/QQ/global/nt_data/Emoji/emoji-resource/face_config.json

live_ids() {
  jq -r '.packs[].emojis[] |
    if (.qSid | test("^[0-9]+$")) then .qSid
    elif .qCid != null then (.qCid | tostring)
    else empty end' "$catalog" | sort -u
}

local_ids() {
  jq -r '.sysface[].QSid, .emoji[].QCid' "$face_config" | sort -u
}

verified_ids() {
  rg "notice\\.group_msg_emoji_like\\.add.*'group_id': 1022240664" data/bot.log |
    sed -n "s/.*'emoji_id': '\\([^']*\\)'.*/\\1/p" | sort -u
}

live_ids | wc -l                                      # 350
local_ids | wc -l                                     # 447
verified_ids | wc -l                                  # 528
sort -u <(live_ids) <(local_ids) <(verified_ids) | wc -l  # 528
comm -23 <(sort -u <(live_ids) <(local_ids)) <(verified_ids) | wc -l  # 0
```

To reproduce the live catalog count without printing the configured token:

```bash
task_onebot_token=$(jq -r '.networks.httpServers[0].accessToken' \
  /home/ttd/deploy/SnowLuma/snowluma-data/config/onebot_1940196378.json)
curl --silent --show-error --fail --max-time 15 \
  -H "Authorization: Bearer ${task_onebot_token}" \
  -H 'Content-Type: application/json' -d '{"refresh":false}' \
  http://127.0.0.1:3000/fetch_sys_faces |
  jq '{packCount:(.data.packs|length),
       entryCount:([.data.packs[].emojis[]]|length),
       uniqueQsid:([.data.packs[].emojis[].q_sid]|unique|length)}'
```

## Safe production model

Do not replace a message-face pool with all 528 reaction IDs. Keep separate
domains:

1. **Message face IDs:** only the 282 numeric IDs in the current live
   `0x9154` catalog. These have the metadata SnowLuma needs to encode a message
   face.
2. **Reaction IDs:** the 528 IDs confirmed by target-group add notices.
3. **Display value:** for Unicode reactions, the character paired with `qCid`;
   for catalog numeric faces, the catalog description or numeric ID; for
   legacy numeric reaction-only IDs, plain text is safer than a `face` segment.

Represent each choice as at least `(reaction_id, reaction_type, display,
provenance, verified_at)`. Preserve `emojiType` from a future raw `0x9084`
tail instead of inferring it from the ID alone. SnowLuma currently infers type
by string length in
`/home/ttd/ro-repositories/SnowLuma/packages/protocol/src/oidb-services/reaction/set-reaction.ts:18-42`;
the first-party tail provides the explicit type and is the safer source of
truth.

## Limitations

- The 528 count is account/runtime/date-specific. QQ can change available
  reactions, and `face_config.json` is demonstrably stale relative to
  `0x9154`.
- A success notice proves that QQ accepted and published that reaction in the
  tested target group. It does not prove availability for every account,
  group, client version, or future date.
- Historical/log union discovery cannot find an ID that has never appeared in
  local files or a successful notice. Only exposing the currently filtered
  `0x9084` catalog tail can close that gap without brute-force writes.
- Market/custom reaction IDs may be alphanumeric; SnowLuma deliberately keeps
  the notice wire string verbatim
  (`/home/ttd/ro-repositories/SnowLuma/packages/protocol/src/events.ts:441-455`).
  The current 528 observed IDs are numeric, so this search does not establish a
  complete market-face catalog.
