# spec/matching.md -- the shared ranking algorithm of PoX 2.0 matching

The computation every implementation of MATCHING-DESIGN.md section 4
reproduces: PoxMatchReader and PoxMatching (pox-smart-contract-litvm), the
validators' members (ethernity-cas `rank.rs`, `claim_sweep.rs`), the node
agent (mvp-pox-node `matching.py`) and the runners. `matching-rank.json`
holds the test vectors; `make/matching-rank.js` is the reference
implementation that computes them, and `test/SpecFixtures.test.js` replays
them with an implementation of its own. Section numbers in parentheses are
MATCHING-DESIGN.md's; "line N" is a line of that file. Section 16 lists every
point where the design is silent or ambiguous and the reading this
specification takes. Section 17 pins the form of the three result fields
after v5 that PoxMatching decodes, with its vectors in `result-fields.json`.
Section 18 states the inputs a node's copy of the roll holds and where it
takes them, with the reader's answers on them in `ranking.json`.

## 1. Conventions

- Every value is an unsigned integer. `/` is integer division (truncation);
  `ceil(a / b)` is `(a + b - 1) / b`. `min` and `max` are the usual ones.
- Widths: block numbers `uint48`; times `uint32` seconds; slots `uint32`;
  scores `uint16` in units of 0.0001 (0 to 50,000); GFLOPS `uint16` (100 per
  CU); cpu, memory GB, storage GB, bandwidth `uint8`; enclave memory GiB
  `uint8`; bids, bases and tips in wei per CU-hour, `uint128` once stored;
  prices `base + tip` below 2^129; the two price products of the match and
  every product of the quote on 256 bits; the fit products on at least 64 bits.
- A hash is keccak256 over `abi.encodePacked` of the listed types, each type
  at its own width, big-endian, no padding (section 3). No hash of this
  specification is over `abi.encode`.
- Addresses are compared as `uint160`. In the JSON they are lower-case hex;
  hashes and words are `0x` + 64 hex digits; wei amounts and the `uint64` code
  masks are decimal strings.

## 2. Deployment constants and parameter sets

`slotSeconds` and `sameSecondBlocks` are constants of a PoxMatching
deployment: 43,200 on a mainnet and 3,600 on a testnet (D41); `sameSecondBlocks`
is true on LitVM only (section 5).

A parameter set has the fields of `IPoxMatchingTypes.ParameterSet`, in that
order (`effectAt`, `claims`, `fitPercent`, `maxSteps`, `maxRounds`,
`fullBlockPercent`, `settleMax`, `pendingMax`, `correctionMaxEntries`,
`stepBlocks`, `leadBlocks`, `banSlots`, `gain`, `missUnits`, `lateUnits`,
`bandWidth`, `minListedSeconds`, `maxRequestAgeSeconds`,
`startTimeoutSeconds`, `minPauseSeconds`, `servingGraceSeconds`,
`correctionWindowSeconds`, `correctionAskSeconds`, `fullWindowSeconds`,
`fetchTrySeconds`, `heldMarginSeconds`, `nodeFaultCodes`,
`defaultFaultCodes`, `ambiguousCodes`, `runBilledCodes`). **A request uses,
in every round, the last set of the array whose `effectAt` is below `T0`**
(`effectAt < T0`, 4.1, line 1349); a set whose `effectAt` equals `T0` is not
the request's.

The fixture's sets (`parameterSets` of the JSON, each case carrying its own
array with `effectAt`):

| set | `stepBlocks` | `leadBlocks` | `fullBlockPercent` | `settleMax` |
|---|---|---|---|---|
| bloxberg | 1 | 2 | 90 | 1 |
| polygon | 8 | 0 | 90 | 2 |
| amoy | 12 | 0 | 90 | 2 |
| litvm | 1 | 0 | 0 | 2 |

bloxberg's `leadBlocks` is 2, the lag a client of its RPC measured at (0 or
1 block) plus 1 (build step 8; D21). `settleMax` is the bound of D176 on the
work of one heartbeat's settlement: one heartbeat charges at most
`settleMax` pending late claims, 1 on bloxberg and 2 on the other chains,
and the next goes on from the first entry not examined (section 11). The
cases written before build step 8 and D176 keep the values they state,
`leadBlocks` 1 and `settleMax` 4, in their own sets (`bloxberg-lead-1`, the
examples, the sequences but the `bound-` ones); `bloxberg-lead-2` ranks on
the bloxberg set and the `bound-` sequences run on the bloxberg set and on
Amoy's.

and in all four: `claims` true, `fitPercent` 20, `maxSteps` 8, `maxRounds` 5,
`pendingMax` 16, `correctionMaxEntries` 16, `banSlots` 60,
`gain` 137, `missUnits` 1,918, `lateUnits` 548, `bandWidth` 1,000,
`minListedSeconds` 60, `maxRequestAgeSeconds` 86,400, `startTimeoutSeconds`
900, `minPauseSeconds` 3,600, `servingGraceSeconds` 3,600,
`correctionWindowSeconds` 86,400, `correctionAskSeconds` 3,600,
`fullWindowSeconds` 120, `fetchTrySeconds` 600, `heldMarginSeconds` 300,
`nodeFaultCodes` bits 39 and 40, `defaultFaultCodes` 0 (named once G-X1 has
passed, line 1372), `ambiguousCodes` bit 47, `runBilledCodes` bits 59 and 63.
Single cases change one field (`claims` off, `maxSteps` 2, `leadBlocks` 0).

**The reader's deployment constant and the pages** (MATCHING-DESIGN.md 4.7,
section 5; build step 8). `scanMax`, the roll entries one list read of
`scheduleOf` and `mandateOutcomes` reads at most, is fixed at the reader's
deployment: 400 on bloxberg, 600 on Polygon and Amoy, 1,000 on LitVM. A
caller pages `candidates`, `turnOf`, `bidQuote` and `tierScore` (their
`count`) in pages of at most 650 entries on bloxberg, 800 on Polygon and
Amoy and 1,450 on LitVM: on bloxberg the bound is the replica's cold-read
time under the front's 15-second timeout, on the others half of the 50 M
gas a geth-based RPC lets one `eth_call` use by default, and zeeve's 50 M cap
on LitVM. The page is the callers' guidance; the reader takes any `count`.
The validators' members judge at a reading depth below the head: 8 blocks on
bloxberg, the `finalized` block on Polygon and Amoy, the `safe` block on
LitVM; it is the members' setting (ethernity-cas) and no input of this
specification.

## 3. Encodings

| name | preimage (`abi.encodePacked`) or layout | bytes |
|---|---|---|
| `seedStamp` | `bytes22(blockhash(block.number - 1))`: the first, high-order 22 bytes of the parent block hash (3.5) | 22 |
| `seed` | `keccak256(bytes22 seedStamp, uint256 doRequestId)` (line 1387) | 54 |
| `key` | `uint256(keccak256(bytes32 seed, address node))` (line 1410) | 52 |
| `roundStamp` | `keccak256(uint256 doRequestId, bytes22 seedStamp, uint8 round, uint48 roundBlock, uint32 roundTime, uint128 bid, uint128 base)` (line 1358) | 97 |
| `listHash` | `h0 = bytes32(0)`; per link `h = keccak256(bytes32 h, address node, uint8 flags)`, `flags = fit + 2 x match` (line 1455) | 53 per link |
| roll entry | `uint160(node) << 96 \| uint16(score) << 80 \| uint40(beatTime) << 40` (line 1560) | word |
| eligible word | `uint160(node) << 96 \| uint16(score) << 80 \| fit << 73 \| match << 72 \| uint24(listedOrdinal) << 48 \| uint48(dpRequestId)` (section 16, item 20) | word |
| ballot hint | `uint8(band) << 80 \| uint32(rollIndex) << 48 \| uint24(listedOrdinal) << 24 \| uint24(stepOrdinal)` (line 736) | word |
| claim `steps` | `uint24`; round `r` in bits `4r` to `4r + 3` (3.9) | 3 |
| V2 add `maxGflops` | `uint32`: required CU in GFLOPS in bits 16 to 31, the GFLOPS ceiling in bits 0 to 15 (line 443) | 4 |
| V2 offer `gflops` | GFLOPS in bits 0 to 15, enclave memory in GiB in bits 16 to 23 (D167) | |
| correction entry | `kind << 248`; kinds 1 and 6 `\| uint48(doRequestId) << 16 \| uint8(round) << 8 \| uint8(step)`; kinds 2 to 4 `\| uint48(orderId)`; kind 5 `\| uint32(slot)` (section 16, item 27) | word |
| template key | `keccak256(bytes(name))`, name = field 2 of metadata1 | 32 |
| image key | `bytes31(keccak256(bytes(field 1 of metadata1)))`: the first 31 bytes (section 16, item 3) | 31 |

Every case of the `ranking` group carries `seedPreimage` and
`roundStampPreimage` next to the hashes.

## 4. The inputs of a request and its rounds (4.2)

- `T0` = `createdAt`, `B0` = `createdBlock`, `seedStamp`, `id`.
- `ceil = maxGflops % 65536`, `needStated = maxGflops / 65536`;
  `need = needStated` when it is not 0, else `ceil` when `0 < ceil < 100`,
  else 100 (D38).
- `hours = durationSec / 3600`; `G = escrowGflops` (the ceiling when one is
  stated, the pricing's `gflopsMaxDefault` at the add otherwise).
- The template: `name` = field 2 of metadata1, split at `:` within its first
  512 bytes; `tz = 1 << bit` of the template keyed by `keccak256(name)` when it
  is registered with `registeredAt < T0`, else 0.
- Round 0 is `(B0, T0, maxPrice, basePriceWei)`; round `r >= 1` is the
  raise's `(block, time, bid, base)`. `bid = min(bid, 2^128 - 1)`;
  `base` as stored. A raise does not change `seed`.

## 5. Whether the request is ranked (4.3)

The request has a schedule when all hold, tested in this order with the
reason the fixture names when one fails (`notRankedBecause` lists every
failing one): `claims` on (`claimsOff`); added with terms (`legacy`); exactly
one instance (`instances`); not reserved (`reserved`); metadata4 empty
(`pinned`); `tz != 0` (`template`); not marked likely broken at the add
(`imageMarkedAtAdd`); not superseded at the add (`imageSupersededAtAdd`).
Otherwise: `M = 0`, no list, every node allowed from step 1. A request added
before its image was marked has the bit clear and is ranked (`imageMarkedNow`
is not an input of the ranking). The add's record holds the three image bits
in its flags byte: 1 marked, 2 superseded, 4 unread at the add. Unread is
written when the image registry's or its SessionRegistry's views could not be
read at the add (a revert, no code, an answer that does not decode; O87):
such a request has no schedule, as a superseded one, and its add stands; it
is marked at the add as well when the image's health marks it, as with no
image registry. Any of the three bits gives no schedule.

## 6. The list of a round (4.4)

```
slot     = T / slotSeconds;  rollSlot = slot - 1
for band = 49 down to 0, for each entry e of roll[rollSlot][band] in stored order:
  p        = the period of e.node with the largest ordinal among those with
             p.from + minListedSeconds <= T                     none  -> notListed
  member   = p.to == 0 or p.to > T                              false -> notOpen
  filter, in this order:  p.cpu >= cpu (cpu); p.memGB >= memGB (memory);
             p.storageGB >= storageGB (storage); p.bandwidth >= bandwidth (bandwidth);
             p.gflops >= need (belowNeed); ceil == 0 or p.gflops <= ceil (aboveCeiling);
             p.tzMask & tz != 0 (template); p.enclaveMemoryGB >= memGB (enclaveMemory);
             not skipped (skipped)
  skipped  = ( skip.until > T and skip.at + minListedSeconds <= T )
          or ( skip.prevUntil > T and skip.prevAt + minListedSeconds <= T )
             (the node's skip of the request's image; none -> false)
  fit      = p.gflops * 100 <= need * (100 + fitPercent)
         and p.memGB * 100 <= memGB * (100 + fitPercent) + 99
         and p.storageGB * 100 <= storageGB * (100 + fitPercent) + 99
  price    = base + p.tip
  match    = price <= bid  and  price * p.gflops * hours / 100 <= bid * G * hours / 100
  key      = uint256(keccak256(seed, e.node))
order: band descending, then key ascending, then address ascending; position = 1-based
```

The band of an entry is the band of the roll array it stands in, which is
`min(49, score / 1000)` of its score. The ordinal of `p` is `listedOrdinal`.
An entry that fails is `notEligible` with its reason; an eligible entry that
does not match stays on the list (it is allowed, never mandated).

## 7. The schedule (4.5)

- `F(b)`: the nodes of band `b` with fit and match, in the order; `R(b)`: with
  match and without fit. `mandated` = the first `maxSteps` of `F(49), R(49),
  F(48), R(48), ..., F(0), R(0)`; `M` = its length; `mandateStep(m[j]) = j`.
- The counted form, which gives the same step: for a matching node of band
  `b`, `A` = matching nodes of the bands above, `fb`, `rb` = nodes of `F(b)`,
  `R(b)` before it, `Fb = |F(b)|`; a fitting node's step is `A + fb + 1`, a
  non-fitting one's `A + Fb + rb + 1`, when that is at most `maxSteps`, else 0.
  `turns` gives, per listed node, `mandateStep`, `allowedFrom`, `matchAbove`
  (= `A`), `fitBefore`, `restBefore`, `fitInBand`, `restInBand`.
- `allowedFrom(node of the list)` = the smallest `j` with
  `position(m[j]) >= position(node)`, `M + 1` when there is none;
  `allowedFrom(node outside the list) = M + 1`. `allowedByStep` lists, for
  `s = 1..M`, the mandated node and the list's nodes with `allowedFrom <= s`,
  and for `M + 1` "every node".
- Steps of a round opened in block `B`:
  `step(C) = 0` when `C <= B + leadBlocks`, else
  `1 + (C - B - leadBlocks - 1) / stepBlocks`;
  `closedStep(C) = max(0, step(C + 1) - 1)`;
  the stored step is `min(step, maxSteps + 1)`. `steps` gives the three for
  the case's `stepBlocks`.
- `stopBand`: reading the eligible entries in roll order (bands 49 to 0, each
  in stored order), the band of the `maxSteps`-th matching entry; 0 when fewer
  than `maxSteps` match. `listHash` links every eligible entry of the bands
  `49` down to `stopBand`, in roll order (not in key order).
  `matchFound = min(maxSteps, matching entries)`.
- `settled = readTime > T`, or true when `sameSecondBlocks` is false (4.1).

## 8. The paged read (`candidates`, 4.5, 4.6)

```
page(fromBand, from, count, h, matchFound):
  band = fromBand; i = from; read = 0; done = false
  loop:
    if i >= length(band):
      if matchFound >= maxSteps or band == 0:  done = true; stop     (the stop band's end, the roll's end)
      band = band - 1; i = 0; continue
    if read == count: stop
    e = roll[band][i]; i += 1; read += 1
    if e is eligible:  h = keccak256(h, e.node, flags);
                       if e.match and matchFound < maxSteps: matchFound += 1;  return e's word and tip
  return eligible words, tips, places (band << 32 | index), hOut = h, matchFoundOut = matchFound,
         nextBand = band, nextFrom = i, done
```

The first page is `(49, 0, count, bytes32(0), 0)`; each next page starts
from the previous page's `(nextBand, nextFrom, hOut, matchFoundOut)`. A page
walks past every exhausted band before it checks `count`, so a page whose
last entry is the last of the roll, or of the stop band once `matchFound =
maxSteps`, reports `done`. The last page's `hOut` and `matchFoundOut` are the
single read's `listHash` and `matchFound`. The fixture holds no page with
`beyond` set (section 16, item 19).

## 9. The quote (`bidQuote`, `bidQuoteFor`, 4.6)

Over the roll of the slot before the block of the call (`readTime /
slotSeconds - 1`), a node is counted when its newest period has `to == 0`,
`from + minListedSeconds <= readTime`, passes the filter of section 6 (with
`T` = `readTime` for the skip), and its current score is at least `minScore`.

```
price  = baseNow + p.tip
w      = price * p.gflops * hours / 100
needBid = price                                       if p.gflops <= G
        = max( price, ceil( 100 * w / (G * hours) ) )  otherwise
minBidWei = min( carry, every counted needBid );  carry 0 is read as none;
            type(uint256).max when nothing is counted
canClaimNow = counted nodes whose period matches at the current round's bid and base (section 6)
sendBidWei  = max( minBidWei, bid + 1 )
workWei     = bid * G * hours / 100;   workAfterRaiseWei = sendBidWei * G * hours / 100
```

`bidQuoteFor` takes the terms directly: `need` by the rule of section 4 from
`needGflops` and `ceilGflops`, `hours = hoursWanted`, `G = ceilGflops` when
stated else `gflopsMaxDefault`, `tz` from the template keyed by
`templateHash` with `registeredAt < readTime`, `memGB` as the enclave memory
need, no skip test. The work part `workWei` is the escrow's `W` (3.15);
`addedEscrowWei` adds the pricing's fees and is not in the fixture.

## 10. The miss predicate (4.8)

A `charges` case names its ranking case (`setup`) and its `round`, and gives:
the claims (`node`, `round` mined in, `block`, `steps`), the approval (`node`,
`block`) or none, `status` (`AVAILABLE`, `BOOKED` or `CANCELED`),
`judgedMark` (a charge recorded for the request), `latestTime`, `periods`
that replace the setup's (ends and later periods), `recordedLate` (the units
of a node's LATE record for the round, present only when a record exists)
and `blocks` (every existing block above `B` with `gasUsed` and `gasLimit`;
a number missing is a block that does not exist). The approval's round is the
request's last round; `fill = (node, claimBlock = its claim's block, round,
step = stated(claim, round))`. With `step` on round `r`'s clock:

```
stated(c, x)  = (c.steps >> 4x) & 15 for x <= c.round, 0 above it
judgeable     = filled or judgedMark or (latestTime > T0 + maxRequestAgeSeconds and status != CANCELED)
K             = max over the claims mined in round r, the approval when fill.round == r and the raise
                that closed r, of min(step(block), maxSteps + 1);  maxSteps + 1 for the last round
                when judgeable and not filled
full(q)       = fullBlockPercent != 0 and every block with step q has gasUsed * 100 >= gasLimit * fullBlockPercent
counted(q)    = q > maxSteps or (a block with step q exists and not full(q))
aj            = the mandate step of the fill's node in round r when fill.round == r, else 0
per mandated node n, s = mandateStep(n), c = its claim or none:
  f      = its listed period; while f ended REPLACED and step(f.toBlock) <= s: f = the period of ordinal + 1
  ended  = f.to != 0 and f.endReason != REPLACED and step(f.toBlock) <= s
  E0     = step(c.block) if c.round <= r;  step(B(r + 1)) if c.round > r;  K without a claim
  E      = min(E0, step(fill.claimBlock) + 1) if fill.round == r and 0 < aj < s;  E0 otherwise
  passed = the q in [s, E) with counted(q)
  charge = 0 if ended or passed == 0;  missUnits without a claim;  min(missUnits, lateUnits * passed) with one
  reached = K >= s;  answeredInRound = reached and c and c.round == r and step(c.block) <= s
  real   = 0 if c and ended and f.toBlock <= c.block;  s otherwise
per claimer n with c.round >= r:  d = stated(c, r);  real = the mandated node's real, 0 when not mandated
  late(x)  = 0 if x == 0;  E(x) as E above with x for s;  0 if E(x) <= x;  min(missUnits, lateUnits * (E(x) - x))
  isFalse  = late(real) > late(d)  or  (n is the fill's node, fill.round == r and d != real)
  recount  = not isFalse and a LATE record exists and recordedLate < late(d)
```

**mandateOutcomes** (`outcomes`, codes of line 1574): 0 when `K < s`; else 4
when `ended` and (no claim, or `f.toBlock <= c.block`); else 1 when the claim
is of an earlier round, or of round `r` with `step(c.block) <= s`; else 0
when `E <= s`; else 2 with a claim, 3 without. `passed` is `E - s` for
outcomes 2 and 3, else 0. `stated` is `stated(c, r)`, 255 without a claim.
`falseNodes` and `realSteps`: the claimers with `isFalse`, in ascending real
step, then address.

**The ballot** (`recordMisses`): none when the request is not judgeable, or
when it has no entry and round `r` was not filled or `fill.step == aj`.
`approvedNode` = the fill's node when `fill.round == r`, else zero;
`approvedStep = aj`. Entries: each mandated node without a claim with
`charge > 0` (`absent`, its `s`, `uncounted = (E - s) - passed`); each
claimer with `isFalse` (`false`, its `real`, 0); each with `recount`
(`recount`, its `d`, 0); sorted by step, then address; each with its hint
(`band`, `rollIndex` and `listedOrdinal` of its list entry, `stepOrdinal` =
the ordinal of `f`, or `listedOrdinal` for a claimer without a mandate).

## 11. The contract's arithmetic (4.9)

A `sequences` case is a run of operations on PoxMatching's records with the
parameters of its `setup` (`slotSeconds`, the set, the nodes' starting
scores, the requests' `createdAt` and blocks). It does not rank: ballots give
their entries. `slot(id) = createdAt / slotSeconds`; `step_r` is `step` on
round `r`'s clock. State: per node `score`, `pending` (request ids),
`settleAt`, `beats`, `lates`, `misses`, `failures`, `bannedUntil`,
`firstBeatSlot`, `lastBeatSlot`, `lastFailureSlot`, `lastAskAt`; per node and
slot `chargedIn = {sum, taken}`; per (request, round, node) a charge record
`{kind LATE|MISS|FALSE, step, passed (at most 255), units, at, undone}`; per
order a failure record `{kind, slot, at, units, corrected}`; per request
`status`, `filled`, `judged`, its rounds with `lastOpenSteps`, the fill
record; `minimumOnAt`, `farmMinOnAt`.

```
apply(n, slot, units):   before = sum; after = before + units
                         cut = min(missUnits, after) - min(missUnits, before);  cut = min(cut, score)
                         sum = after; taken += cut; score -= cut
giveBack(n, slot, units, at):  after = sum - units;  due = max(0, taken - min(missUnits, after))
                         sum = after; taken -= due
                         back = 0 if bannedUntil > at, else min(due, 50,000 - score);  score += back
switch:  a score that goes from below 10,000 to 10,000 or more writes minimumOnAt once, from below
         45,000 to 45,000 or more farmMinOnAt once (a heartbeat's gain, a correction's give-back)

claim(n, id, block, time, steps), rc = the current round:
  refused  filled | time > createdAt + maxRequestAgeSeconds (age) | steps >> 4(rc + 1) != 0 or a value
           above maxSteps (steps) | a claim of n exists (claimed) | pending and |pending| >= pendingMax (pendingFull)
  pending  = (stated(rc) != 0 and step_rc(block) > stated(rc)) or stated(r) != 0 for an r < rc
  lastOpenSteps[rc] = max(.., min(step_rc(block), maxSteps + 1));  claimedInRound; append id when pending
approve(n, id, block): filled; fill = (n, claim block, rc, stated(claim, rc)); lastOpenSteps[rc] as a claim
raise(id, block, time): refused filled | age | rounds == maxRounds | claimedInRound and step_rc(block) <= maxSteps;
                       lastOpenSteps[rc] as a claim; a new round (block, time)
cancel(id): status CANCELED (refused when filled)
beat(n, time): refused while time < bannedUntil; the first beat of a slot adds gain (at most 50,000),
  counts the beat, writes lastBeatSlot (and firstBeatSlot once); then the walk of line 1730:
  len = |pending|; count = 0; k = 0; i = settleAt mod len
  while k < len and count < settleMax:
    id = pending[i]; k += 1
    final when filled or judged; open when time <= createdAt + maxRequestAgeSeconds (i += 1, continue);
    cancelled when CANCELED (mark i, i += 1, continue); final otherwise
    for each r <= c.round with s = stated(c, r) != 0 and no record (id, r, n):
      E0 = step_r(c.block) if r == c.round, step_r(B(r + 1)) if r < c.round
      E  = min(E0, step_r(fill.claimBlock) + 1) if filled, fill.round == r, fill.node != n, 0 < fill.step < s; else E0
      units = min(missUnits, lateUnits * max(0, E - s));  when units > 0: LATE record, apply(n, slot(id), units),
              lates += 1, the request judged
    a record written: count += 1;  mark i; i = (i + 1) mod len
  the marked entries leave, the others keep their order; settleAt = the place of entry i when k < len, else 0
  (settleMax is the bound of D176: a final entry the walk does not reach is charged at a later heartbeat, on
  the records as they stand then; addParameterSet refuses a settleMax of 0, ParameterSetRefused reason 7)
recordMisses(id, r, approvedStep, entries, time): refused CANCELED and not judged (cancelled) | not filled,
  not judged and time <= createdAt + maxRequestAgeSeconds (notFinal) | a second record of r (recorded);
  fill.step = approvedStep when fill.round == r; per entry (node, step x, uncounted):
    no claim:  K = maxSteps + 1 for the last round of an unfilled request, else lastOpenSteps[r];
               E = min(K, step_r(fill.claimBlock) + 1) when fill.round == r and 0 < fill.step < x; passed = (E - x) - uncounted;
               skipped when a record exists, uncounted > min(E, maxSteps + 1) - x or passed < 1;
               else MISS record, apply(missUnits), misses += 1, the period ends MISS
    a claim, the contract's isFalse (late() of section 10 on the fill record as it now stands):
               skipped when a MISS or FALSE record exists;
               old = the LATE record's units or 0; FALSE record; apply(missUnits - old); misses += 1;
               lates -= 1 when old > 0; the period ends MISS when x != 0
    a claim, a true statement: recount upward
  the first record written marks the request judged
recount(id, r, n, direction): passed = the settlement's steps on the fill record as it stands;
  units = lessUndone(passed, the LATE record's undone, 0 without one); old = the LATE record's units, 0 without one
  upward and units > old: the record raised (or written), its undone kept, at = time; apply(units - old);
                          lates += 1 when old == 0
  downward and units < old: giveBack(old - units, record.at); the record lowered; lates -= 1 when units == 0
lessUndone(passed, undone) = min(missUnits, lateUnits * (passed - the bits b < min(passed, 8) set in undone))  (O89)
askCorrection(n, time, entries): refused more than correctionMaxEntries (entries) | time < lastAskAt + correctionAskSeconds
  (askedInWindow); lastAskAt = time; each entry admitted or skipped with its reason:
    kind 1 (id, r, q): a LATE record k; k.step <= q < k.step + k.passed; q <= maxSteps; q - k.step <= 7;
                       bit q - k.step of k.undone clear; taken of slot(id) > 0; time <= k.at + correctionWindowSeconds
    kinds 2, 3, 4 (orderId): a failure of n of kind NEVER_STARTED, EXPIRED, NODE_FAULT with code 37 or 39;
                       units > 0; not corrected; time <= at + correctionWindowSeconds
    kind 5 (slot s):   end = (s + 1) * slotSeconds; end <= time <= end + correctionWindowSeconds; s > firstBeatSlot;
                       lastBeatSlot != s; not corrected; a period of n open at end
    kind 6 (id, r):    a LATE record: recount downward at once, not listed
correct(n, entries, stand, time): each entry i with bit i set that is still admitted:
    kind 1:  undone |= 1 << (q - k.step); units = lessUndone(k.passed, undone);
             giveBack(k.units - units, k.at); k.units = units; lates -= 1 when units == 0
    kinds 2 to 4: corrected; giveBack(missUnits, f.slot, f.at); failures -= 1; lastFailureSlot = 0 when it is f.slot
    kind 5:  corrected; score = min(50,000, score + gain)
fail(n, orderId, kind, id): apply(n, slot(id), missUnits) (a STRIKE: the slot of its time); the record's units =
  what apply took; failures += 1; lastFailureSlot = that slot
ban(n, time): score = 0; bannedUntil = time + banSlots * slotSeconds; pending emptied; settleAt = 0
```

Each step of a sequence gives the op's `result` and, after it, every node's
account and every `chargedIn`; `final` is the whole state after the last op.

## 12. The members' full-window test (3.12)

A window of kind 2, 3 or 5 is every block with `end - fullWindowSeconds <=
timestamp < end`; a window of kind 4 is every block numbered `fromBlock` to
`toBlock`. It is full when it holds no block, or when `fullBlockPercent != 0`
and every block has `gasUsed * 100 >= gasLimit * fullBlockPercent`.

## 13. The runner's approval rule (3.10)

At a read answered at block `readBlock`, `closed = closedStep(readBlock)` on
the round's clock; only claims mined at or before `readBlock` exist. A node
absent from `nodes` can answer, has approvable claims, a score of 50,000 and no
failed order. `minScore` is the developer's when set, else 5,000 when
`minimumOn`, else 0. `recent(n)`: `lastFailureSlot != 0` and `slotNow -
lastFailureSlot < recentFailureSlots`.

```
stands(n)     = n has a claim, it is approvable and score(n) >= minScore
passedOver(j) = m[j] cannot answer, or it has a claim that is not approvable or recent(m[j])
done(j)       = j <= closed or passedOver(j)
rule 1: among j = 1..M with stands(m[j]), not recent(m[j]) and done(i) for every i < j:
        the lowest j whose claim was mined at step(block) <= j; else the lowest j  -> approve
wait while some j <= M is not done
rule 3: the standing claims, not recent first: mandated by step, then list nodes by position,
        then nodes outside the list by claim block; a recent one only when no other stands -> approve
```

## 14. The fixture

`matching-rank.json`: `description`, `generator`, `specification`,
`encoding` (section 3 in words), `constants`, `parameterSets`,
`deployments`, `labels` (each node address with the label it is derived
from: `keccak256("matching-rank node <label>")`, its last 20 bytes), `groups`
(the case count of each group) and the groups:

| group | a case's inputs | `expected` |
|---|---|---|
| `ranking` | `deployment`, `parameterSets`, `templates`, `request`, `rounds`, `round`, `roll`, `periods`, `skips`, `readTime`, `stepBlocks` | sections 3 to 7 |
| `paging` | `setup`, `count` | `pages`, `listHash`, `matchFound`, `stopBand` (section 8) |
| `charges` | `setup`, `round` and section 10's inputs | `judgeable`, `lastOpenStep`, `fill`, `counted`, `mandated`, `claimers`, `mandateOutcomes`, `ballot`, `anchorScore` |
| `windows` | `kind`, `parameters`, `end` or `fromBlock`/`toBlock`, `blocks` | `inWindow`, `full` |
| `sequences` | `setup`, `ops` | `steps` (`result`, `after`), `final` |
| `approvals` | `setup`, `readBlock`, `claims`, `nodes`, `minimumOn`, `developerMinScore`, `slotNow`, `recentFailureSlots` | `closedStep`, `approve`, `rule`, `inTime` or `wait` |
| `quotes` | `setup`, `readTime`, `baseNowWei`, `minScore`, `carry`, `currentScores`, `terms`, `gflopsMaxDefault` | section 9 |

The design's worked examples are the cases `example-1` (3.8 Example 1, and
`example-1-bid-0.95`, `example-1-bid-0.65` for its lower bids, and the
`ex1-*` charges and approvals for its table of what happens), `example-2`,
`example-3` with `example-3-one-band`, and `raise-round-0`, `raise-round-1`
with `quote-raise-example` (3.15). The design states no enclave memory for
them; every node of them offers 64 GiB.

## 15. Gas and widths the fixture pins

`bid-above-u128-and-65535-gflops` holds a bid of `2^130 + 12,345` read as
`2^128 - 1`, an offer at 65,535 GFLOPS and a price of `2^128 - 1` whose
products need 256 bits. `ex1-step-above-255` holds a claim in step 300: the
claim's step is computed from its block without the bound, the stored step is
`maxSteps + 1`, a LATE record keeps `passed` at most 255.

## 16. Readings: where the design is silent or ambiguous

Each item: the point, the design lines it rests on, the reading this
specification and the fixture take.

1. **Hash encoding.** The design writes `abi.encodePacked` for the round
   stamp (line 1358), the seed (1387), the key (1410) and the list hash
   (1455). The provisional code computes the stamp and the seed with
   `abi.encode` (PoxMatchingState.sol `_stampOf`; PoxMatchReader.sol
   `requestInfo`, `roundAt`). The design binds: `abi.encodePacked`; the
   provisional code differs.
2. **The seed stamp's bytes.** "The first 22 bytes of blockhash(block.number
   - 1)" (line 415) is `bytes22(blockhash(...))`, the high-order bytes.
3. **The image key.** `keccak256(field 1 of metadata1)` (line 1864) stored in
   a `bytes31` (`RequestImage.image`): the first 31 bytes of the hash.
4. **The enclave memory need.** The request's memory field carries the dApp's
   enclave memory (line 452; section 12 item 74), so the filter tests
   `p.enclaveMemoryGB >= memGB` beside `p.memGB >= memGB` (line 1401). Line 449
   says the fit rule's memory test "compares the two" (the offer's enclave
   size and the request's size); line 1405 tests `p.memGB`. Contradiction: the
   fixture follows line 1405.
5. **The skip test.** `at <= T - minListedSeconds` (line 1402) is evaluated as
   `at + minListedSeconds <= T`.
6. **The reason of a refusal in the list.** The filter is one conjunction
   (lines 1395-1402); the fixture's `why` is the first test that fails in the
   order of those lines. It is the fixture's label only.
7. **An outcome of `mandateOutcomes` for a reached mandate no step of which
   counts.** The codes of line 1574 have no value for a reached node with no
   in-time answer and `E <= s` (the approval's own blocks shortened it to
   nothing, or a raise closed the round in its step): 0, as line 538 ("N5 and
   N2, whose steps pass meanwhile, are not charged") and line 1700 treat it.
   Precedence: not reached, excused, answered, then the rest.
8. **A claim of an earlier round.** The pseudo-code's `answered` (line 1675)
   requires `c.round == r`; line 1700 ("a claim standing from an earlier
   round answers the mandate too") and build step 4 (line 2830) count it as
   answered. Contradiction: the outcome is 1; `answeredInRound` keeps the
   pseudo-code's value.
9. **Excused or answered.** A node whose period ended in its step after its
   in-time claim (the approved node's period ends BUSY at the approval) has
   `ended` and no charge; its outcome is answered when its claim was mined
   before `f` ended, excused otherwise (lines 1678, 1702).
10. **`approvedNode` of a round filled by a node without a mandate.** Lines
    735 and 1689-1690 make it zero; line 1701 has the contract check that
    `approvedNode` is the fill record's node. Contradiction: `approvedNode` is
    the fill's node whenever round `r` was filled, `approvedStep` 0 when it
    holds no mandate.
11. **Which claimers a ballot recounts.** "Whose recorded late charge is below"
    (line 1692) and "one that was settled before" (line 682): only a claimer
    with a LATE record; one without a record is settled by its heartbeat on
    the written step.
12. **A claim-record entry with a true statement.** Line 680 skips an entry
    that is neither false case; lines 1701 and 1782 recount it. Contradiction:
    the fixture recounts.
13. **The units a corrected failed order gives back.** A failure record keeps
    "units = what apply takes" (lines 1794, 1804) and `correct` gives back
    `f.units` (line 1898); `giveBack` lowers the sum of the charges (lines
    1765-1771), which holds the full 1,918, and line 702 "takes its units off.
    The difference leaves the sum". Contradiction: the sum is lowered by
    `missUnits`; `f.units > 0` stays the admission test. With a late charge
    in the same slot, `f.units` would leave 548 taken after both are undone
    (`correction-under-cap`).
14. **The cap counts the sum.** `apply` (lines 1759-1763, 1912) caps the sum of
    the charges; the provisional `_take` (PoxMatchingState.sol) caps the units
    taken, which differs once the score's floor stopped a charge
    (`score-floor-and-cap`). The design binds.
15. **The slot of a failed order.** The cap of a failed order counts in the
    request's slot (line 715; `notRun`, line 1804); a strike in the slot of its
    record (line 1793). The provisional `_fail` uses the current slot.
16. **The records a recount writes.** A raised record takes `at = time` (line
    1788). A record lowered to 0 units decrements `lates`, as a kind 1
    correction does (line 1897), and a record raised from 0 units increments
    it, as a record written does. A recount counts the steps passed less the
    steps the record marks undone, as `correct` counts kind 1 (O89), and keeps
    the marks: an undone step stays undone (`recount-up-keeps-undone`,
    `recount-down-keeps-undone`).
17. **Charge and failure kinds** are named (`LATE`, `MISS`, `FALSE`;
    `NEVER_STARTED`, `EXPIRED`, `NODE_FAULT`, `STRIKE`); the design gives no
    numbers.
18. **The page's cursor.** Line 1461 ends a read at the end of the stop band or
    of the roll. A page walks past exhausted and empty bands before it checks
    `count`, so the cursor is always an unread entry or the end, and the page
    that reads the last entry reports `done`. The provisional `rollPage`
    reports `done` only on a page that reaches band 0's end inside its loop
    and leaves the cursor on an empty band after a page that ends at a band's
    end.
19. **`beyond`.** The carried `h` and `matchFound` (bounded by `maxSteps`, line
    1461) do not tell a page that starts inside a band below `stopBand` that
    it is below it (lines 1492-1495). The fixture holds no `beyond` page. The
    reading: a caller pages without `beyond` until `done`, then reads on from
    that cursor with `beyond` set and `matchFound = maxSteps`, and a `beyond`
    page that starts with `matchFound = maxSteps` at index 0 of a band hashes
    nothing. Open for the reader's engineer.
20. **The eligible word** (line 1489 names its fields, not their places): the
    layout of section 3, with the widths of `PoxMatchingState.Period`.
21. **`turnOf`'s carry** (line 1528: "the five counts so far, packed") has no
    layout in the design; the fixture gives the counts of one unpaged read.
22. **`bidQuote`'s first carry.** "The lower of carry and what this page
    found" (line 1545) with the other views' "zero for the first page" (lines
    1490, 1528): carry 0 is read as none.
23. **`canClaimNow`** counts the counted nodes that pass the claim's price test
    at the current round; the claim's "no claim yet" test is not applied, since
    rule 4 speaks of nodes that "can claim" and "have claimed" (line 600).
24. **`bidQuoteFor`'s template and memory** (lines 1553-1558, 1613): the
    template registered before the block of the call, `memGB` the enclave
    memory need, the need rule of 4.2 on `needGflops`, no skip test (no image
    argument).
25. **The window of kinds 2, 3 and 5** ("the blocks whose time lies in the last
    fullWindowSeconds before", lines 689-692): `end - fullWindowSeconds <=
    timestamp < end`.
26. **Kind 5's admission.** Line 697 requires that a heartbeat at the slot's
    end would have counted; the pseudo-code (lines 1886-1887) omits it. The
    fixture applies it as "a period of the node was open at the slot's end";
    the serving-order leg is not in the fixture.
27. **The correction entry word** is the provisional code's
    (PoxMatchingJudged.sol `correct`); the design names an entry's kind and
    record (line 697) and no layout.
28. **The runner's rule details.** A claim below the minimum is treated as
    absent (line 601: "as if it did not stand"), so its node's step must close;
    a recent failure's claim is passed over at once (line 602). "In the last
    `recentFailureSlots` slots" is `slotNow - lastFailureSlot <
    recentFailureSlots`. Nodes outside the list are ordered by their claim's
    block (line 599); the fixture holds no two in one block.
29. **`stated` of a mandated node whose claim is of an earlier round** is 0
    (line 1718), not 255.
30. **`falseNodes`'s order** (lines 1580-1583) is not given: ascending real
    step, then address, the ballot's order.
31. **The hint of a claimer without a mandate** (line 736): `stepOrdinal` is its
    `listedOrdinal`.
32. **What a charge carried by D176's bound is counted on** (the owner's "Yes"
    of 2026-11-23 to the five timing effects; MATCHING-DESIGN.md D184). A final
    entry that one heartbeat's walk does not charge because `settleMax` was
    reached is charged at a later heartbeat of the node, on the records as
    they stand then, and that is the rule, not a defect: (1) on the fill
    record as the ballot has written it, so a ballot between the two
    heartbeats sets the count with no recount (`bound-carry-past-ballot`);
    (2) a cancel of an unfilled request before any charge of it drops the
    entry with no charge (D58; `bound-carry-past-cancel`); (3) a ban empties
    the pending list, carried entries with it (`bound-carry-past-ban`); (4)
    the record's `at` is the later heartbeat's time, from which its
    correction window counts (`bound-carry-window`); (5) the cut is taken
    from the score as it stands after the later heartbeat's gain, so at the
    floor of 0 the carried charge takes that gain (`bound-carry-floor`).
    `bound-one-a-heartbeat` and `bound-two-a-heartbeat` show the bound on
    bloxberg's set and on Amoy's.

## 17. The result fields after v5 (3.19; SECURELOCK-HARDENING-SPEC.md 13.4)

The result string the trustedzone signs (`_addResultToOrder(orderId,
result)`, spec/result-v5.json) carries three fields after the fifteen of v5,
in this order: field 15 `landedAt`, field 16 `ranUntil`, field 17 `slClass`.
Fields are split at `:`; field `i` is the bytes after the `i`-th colon up to
the next colon or the end of the string, so the eighteenth colon ends field
17 and anything after it is not read.

**The version.** A result that carries the three fields is version `v6`:
field 0 is `v6`, and fields 1 to 14 are those of v5. A `v5` result has the
fifteen fields of v5 and no more. The format takes a version of its own
because a v5 reader refuses a v5 result with more fields: the node agent
requires exactly fifteen (mvp-pox-node `node/utils.py` `result_fields`,
lines 1403-1405 at `0e122e3`). PoxMatching's decoder does not read field 0:
it tells the two apart by the field count, as the reading below states.

**The form.** Each of the three fields is the whole of its bytes, written as
a plain unsigned decimal number:

```
field   = "0" | nonzero *digit
nonzero = "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9"
digit   = "0" | nonzero
```

ASCII digits only: no prefix or label (`landed=`, `0x`), no sign, no space or
other white space, no leading zero, no decimal point or exponent, nothing
before or after the digits. Each value is bounded by its type:

| field | name | type | values |
|---|---|---|---|
| 15 | `landedAt` | `uint64` seconds | 0 to 18446744073709551615 |
| 16 | `ranUntil` | `uint64` seconds | 0 to 18446744073709551615 |
| 17 | `slClass` | the class | 0 (the custom class) or 1 (the default class) |

The trustedzone writes 0 for a time it has none of (`landedAt` with no row or
commit verified as landed, `ranUntil` with no advance naming a written
anchor).

**Reading.** Of the result string, PoxMatching's decoder
(PoxMatchingResults.sol) reads field 1, the task code, and fields 15 to 17,
and no other field. A result carries the three values only when it has at
least eighteen fields and each of fields 15, 16 and 17 is in the form and at
most its bound; otherwise it carries none of them: it has no class, the
default-class rules of `nodeFault` do not apply to it, and `noteAmbiguous`
refuses it (`NotAmbiguous`, reason 4). The task code is read whatever fields
15 to 17 hold.

**The vectors.** `result-fields.json` (`make/result-fields.js`, the reference
implementation) holds results whose three fields are read (`accepted`, with
the code and the decoded values; times as decimal strings) and results whose
fields are not read (`refused`); every result with a field after the fifteen
of v5 is `v6` in field 0, the one without (`v5-only`) is `v5`; each with its stored form `v3:<signed
transaction>:<cid>` for the fixture's order id, chain and key. A refused
case names the first field that fails, in the order 15, 16, 17, and the
first test it fails, in this order: `empty`; `leadingZero` (a `0` followed by
anything, `0x` included); `notDigit` (any other byte than `0` to `9`, a
non-ASCII digit included); `aboveMax`. `absent`, with no field, is a result
of fewer than eighteen fields. The test names are the fixture's labels; the
decoder answers only whether the three fields are read.

## 18. A node's copy of the roll it ranks on (4.7, 7.5, 7.6; O92, D183)

A node agent ranks itself on a copy of the roll that it holds in memory
only, never written to disk: the roll entries of each slot an open round
ranks on (the slot before the round's, up to 3 slots back on a mainnet and
25 on a testnet), every period of each node of those rolls that one of
those rounds can find in force at its time `T` (4.4: the newest whose
`from + minListedSeconds <= T`, with its end), and each node's skip of each
image the node has seen requested. Per request it reads `requestInfo`, and
the request's resources and terms once, as its present request read does;
all of them are written once at the add or by a raise (4.1). It ranks with
the rule of sections 6 and 7 and claims as MATCHING-DESIGN.md 7.6 states.
The claim is checked on chain (3.11), so a wrong local rank costs only that
node a refused or a late claim.

**Every input of the ranking rule for an entry, and where the copy takes it:**

| input | fixed in the closed roll (`rollPage`, `rollSlice`) or at its write | the event when it is written |
|---|---|---|
| the roll entry: node, score, band, index, beat time | a closed slot's roll never changes | `Beat(node, slot, score, band, index)`; the beat time is the block's |
| the period: `from`, offer, cpu, memory, storage, bandwidth, GFLOPS, tip, template mask, enclave memory | written once at the announce | `Announced(node, dpRequestId, from, cpu, memGB, storageGB, bandwidth, gflops, tipWei, tzMask, enclaveMemoryGB)`; the ordinal is the node's previous period's plus one, 0 for its first |
| the period's end: `to`, the reason, `toBlock` | written once | `Left(node, reason, at)`; `toBlock` is the block's number (a replacement, a pause, a placement, an offer's removal, an expiry, a missed mandate, a ban) |
| a skip: `at`, `until`, `prevAt`, `prevUntil` | changed by `skipImage` alone | `Skipped(node, imageKey, at, until, prevAt, prevUntil)`, the skip as the call leaves it |
| the parameter set of the request | written once (`parameterSet(index)`) | `ParametersAdded(index, effectAt)` |
| the template's bit and registration | written once (`templateOf`) | `TemplateRegistered(nameHash, bit, at)` |
| the key | `keccak256(seed, node)` of the request's seed (section 3) | none: computed |

The account's score of the moment, a heartbeat that does not write the
roll, a charge or a correction change no input of the rule: the band and
the order read the score the roll entry holds.

**The rebuild at every start.** The copy is not kept across a restart. At
start the agent reads, for each roll an open round ranks on, the bands'
lengths (`rollLengths`, `bandLengths`) and the roll in pages of
`rollPeriodsOf(slot, band, from, count, imageKey)`, which answers each
entry with its node's current period and the node's skip of one image; an
earlier period of a node with `periodAt(node, ordinal)`; the skips of other
images with `skipOf(node, imageKey)` or another `rollPeriodsOf` pass with
that image's key. From the block of that scan on, the copy follows the logs
of every block: `Beat`, `Announced`, `Left` and `Skipped`. It lags the head
by the replica lag of its RPC, which `leadBlocks` covers (section 2).

**The vectors.** `ranking.json` (`make/ranking.js`, run under Hardhat on
each `ranking` case of `matching-rank.json` replayed by
`test/lib/matching-reader.js`) holds, per case, the inputs as the reader and
PoxMatching answer them at the case's read block (`request`, `rounds`,
`roll`, `periods`, `skips`), with every time and block number in the case's
coordinates (the replayed chain's values: a period that starts in the
roll's slot after its entry's beat time starts at that beat time, as the
replay writes it), and the reader's answers on them: `candidates` in one read from
the top of the roll (`read`) and, for a ranked request whose read stops
above the roll's end, on with `beyond` set from its cursor (`beyond`), and
each roll node's turn as `turnOf` answers it in one page (`turns`). A port
of the rule that holds the inputs computes the same eligible words, list
hash, matching count and turns. The round stamps are `matching-rank.json`'s.
`test/RankingVectors.test.js` regenerates the file and compares it byte for
byte.
