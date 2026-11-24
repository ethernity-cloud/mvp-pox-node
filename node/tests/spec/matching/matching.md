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
specification takes. Section 17 pins the form of the five result fields
after v5 that PoxMatching and the members read and the grammar of the stored
result that carries them, with its vectors in `result-fields.json`.
Section 18 states the inputs a node's copy of the roll holds and where it
takes them, with the reader's answers on them in `ranking.json`. Section 19
gives the stored words' layouts (`words.json`) and section 20 the grammar of
a session's rows (`session-rows.json`).

## 1. Conventions

- Every value is an unsigned integer. `/` is integer division (truncation);
  `ceil(a / b)` is `(a + b - 1) / b`. `min` and `max` are the usual ones.
- Widths: block numbers `uint48`; times `uint32` seconds; slots `uint32`;
  scores `uint16` in units of 0.0001 (0 to 50,000); GFLOPS `uint16` (100 per
  CU); cpu, memory GB, storage GB, bandwidth `uint8`; enclave memory GiB
  `uint8`; EPC `uint16` in units of 64 MiB (1 to 65,535 units, 64 MiB to
  4 TiB less 64 MiB; 0 for an offer that states none and for a request that
  states no minimum, section 16 item 33); bids, bases and tips in wei per
  CU-hour, `uint128` once stored;
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

**The owner's writes of the sets.** `addParameterSet` appends a set and
refuses it (`ParameterSetRefused(index, reason)`, `index` the place it would
take) for: 1 an `effectAt` below the block's time; 2 an `effectAt` at or
below that of the set before it; 3 a 257th set; 4 a `maxSteps` of 0 or above
14; 5 a `maxRounds` above 5; 6 a `stepBlocks` or a `bandWidth` of 0; 7 a
`settleMax` of 0; 8 a `startTimeoutSeconds` of 0. `replaceParameterSet` puts
a set in the place of the last one while that one's `effectAt` is above the
block's time, under the same tests at that place, and emits
`ParametersReplaced(index, effectAt)`; it is refused with reason 9, and the
number of sets as `index`, when there is no set or the last one's `effectAt`
is at or below the block's time. A set whose `effectAt` is above the block's
time is no request's and is not in force (a request's set has `effectAt <
T0`), so a replacement changes no request's set and no set in force; a reader
that holds a set by its index reads it again at `ParametersReplaced`. Only
the last set is replaced. The owner is changed in two steps:
`transferOwnership(newOwner)` names the owner to be (`pendingOwner()`,
`OwnershipTransferStarted`), who becomes the owner with `acceptOwnership()`
(`OwnershipTransferred`); the owner stays until then, and naming the zero
address withdraws the transfer.

**While no set is in force** (before the first set's `effectAt`) no request
has a set, so there is no claim, approval, ballot or charge of a request.
`strike` returns without effect and records nothing, so that the vote that
calls it does not revert; the strike is not recorded later. `ban`, `correct`
and `askCorrection` revert with `ParameterSetRefused(0, 0)`, and so does
`announce`; a `beat` reverts with it on the order leg and with
`BeatNotCounted` otherwise (no period is open): no node sends a heartbeat
before the first set is in force.

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
| V2 add `maxGflops` | `uint48`: the minimum EPC in units of 64 MiB in bits 32 to 47 (D191), required CU in GFLOPS in bits 16 to 31, the GFLOPS ceiling in bits 0 to 15 (line 443) | 6 |
| V2 offer `gflops` | `uint40`: GFLOPS in bits 0 to 15, enclave memory in GiB in bits 16 to 23 (D167), EPC in units of 64 MiB in bits 24 to 39 (D191) | 5 |
| correction entry | `kind << 248`; kinds 1 and 6 `\| uint48(doRequestId) << 16 \| uint8(round) << 8 \| uint8(step)`; kinds 2 to 4 `\| uint48(orderId)`; kind 5 `\| uint32(slot)` (section 16, item 27) | word |
| template key | `keccak256(bytes(name))`, name = field 2 of metadata1 | 32 |
| image key | `bytes31(keccak256(bytes(field 1 of metadata1)))`: the first 31 bytes (section 16, item 3) | 31 |

Every case of the `ranking` group carries `seedPreimage` and
`roundStampPreimage` next to the hashes.

## 4. The inputs of a request and its rounds (4.2)

- `T0` = `createdAt`, `B0` = `createdBlock`, `seedStamp`, `id`.
- `ceil = maxGflops % 65536`, `needStated = (maxGflops / 65536) % 65536`;
  `need = needStated` when it is not 0, else `ceil` when `0 < ceil < 100`,
  else 100 (D38). `epc = maxGflops / 2^32`, the request's minimum EPC in
  units of 64 MiB, 0 for none (D191); the PoX's `doTerms` answers the low 32
  bits of the word and `doStamp` the EPC, which PoxMatching copies with the
  request's record.
- `hours = durationSec / 3600`; `G = escrowGflops` (the ceiling when one is
  stated, the pricing's `gflopsMaxDefault` at the add otherwise).
- The template: `name` = field 2 of metadata1, split at `:` within its first
  512 bytes; `tz = 1 << bit` of the template keyed by `keccak256(name)` when it
  is registered with `registeredAt < T0`, else 0.
- Round 0 is `(B0, T0, maxPrice, basePriceWei)`; round `r >= 1` is the
  raise's `(block, time, bid, base)`. `bid = min(bid, 2^128 - 1)`;
  `base` as stored. The PoX refuses a V2 add, a reserved add and a raise
  whose bid is above 2^128 - 1 (`price invalid`), so the bound changes no
  bid the PoX holds. A raise does not change `seed`.

**The PoX's refusals of a request's terms** (SF11), each with the call that
raises it; the runners and the SDKs refuse the same terms before they send
(`runner-terms.json`):

| revert string | refused | call |
|---|---|---|
| `price invalid` | a bid of 0 or above 2^128 - 1 | the V2 add, the reserved add, `increaseBid` |
| `gasLevel invalid` | a gasLevel above 2^128 - 1 | the V2 add, `increaseChainFee` |
| `gasLevel below average` | a gasLevel below 1e18 | the pricing's `chainFeeWei`, inside the V2 add |
| `maxGflops invalid` | a ceiling above the pricing's `gflopsMax`, or a required GFLOPS above a stated ceiling; on the reserved add a word of 0 or above 16 bits | the V2 add, the reserved add |
| `instance invalid` | any instance count but 1 on the V2 add (0 or above 10 on the legacy add) | the V2 add, the legacy add |
| `duration invalid` | 0 hours or above the pricing's `maxOrderHours` | the V2 add, the legacy add |
| `cpu invalid`, `mem invalid`, `strg invalid`, `bw invalid` | a resource of 0 (or above 255) | every add |
| `metadata1 too long`, `metadata too long` | a metadata string above 2,048 bytes on a DO add or a reserved template; DP adds are not bounded | DO adds, the reserved add |
| `tip invalid`, `metadata2 required` | a tip above 2^128 - 1; an empty metadata2 | the DP V2 add (`_insertDPRequest`) |
| `payout invalid`, `broker only`, `placement out of gas`, `SeedingOpen` | a payout address of the zero address or the node; a reserved add from another than the broker; a placement whose hook ran out of gas; a securelock registration while V4's seeding is open | `setPayoutAddress`, the reserved add, `_placeOrder`, V4 `registerImage` |

The node agent bounds a DP add's metadata strings itself (its own rule, not
the contract's).

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

A request with no schedule whose set has `claims` on, neither pinned nor
reserved, is served by claims all the same: a claim on it states 0 and is
approved by its data owner's placement. The first approval marks a request
of one instance filled. A request of several instances (the legacy add
alone makes one: up to 10 instances, no raise, so round 0 only) is never
marked filled (section 12, item 17: "filled" and "still open" have no single
meaning for it): it takes claims and approvals, one approval a claim, while
the PoX holds it AVAILABLE, that is until every instance is booked, and it
is at most `maxRequestAgeSeconds` old; `Approved` is emitted for each
approval and the fill record holds the last one. An order is "of a claim's
approval" when its node's claim on its request is APPROVED (a node has one
claim on a request and a claim is approved once): `expireUnstarted`,
`notRun`, `nodeFault` and `noteRefund` test that, on every request, and not
the fill record's node. The health count (`counted`) and the proof of three
serving nodes read the fill record, so on a request of several instances
they hold for the order of the last approval alone; an order whose fill
record holds step 0, which the approval of a claim that states 0 leaves on
a request with no schedule (no ballot writes a step there), is not counted.

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
             p.epcUnits >= epc (epc); not skipped (skipped)
  skipped  = ( skip.until > T and skip.at + minListedSeconds <= T )
          or ( skip.prevUntil > T and skip.prevAt + minListedSeconds <= T )
             (the node's skip of the request's image; none -> false)
  fit      = p.gflops * 100 <= need * (100 + fitPercent)
         and p.memGB * 100 <= memGB * (100 + fitPercent) + 99
         and p.storageGB * 100 <= storageGB * (100 + fitPercent) + 99
         and ( epc == 0 or p.epcUnits * 100 <= epc * (100 + fitPercent) + 99 )
  price    = base + p.tip
  match    = price <= bid  and  price * p.gflops * hours / 100 <= bid * G * hours / 100
  key      = uint256(keccak256(seed, e.node))
order: band descending, then key ascending, then address ascending; position = 1-based
```

The band of an entry is the band of the roll array it stands in, which is
`min(49, score / 1000)` of its score. The ordinal of `p` is `listedOrdinal`.
An entry that fails is `notEligible` with its reason; an eligible entry that
does not match stays on the list (it is allowed, never mandated). The fit
tests the GFLOPS, the memory, the storage and, on a request that states a
minimum EPC, the EPC (D198); the EPC is a count of whole 64 MiB units and
is rounded up as the memory and the storage are (section 16, item 33).

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
takesClaims = `claims` on in the request's set  and  status AVAILABLE  and  not pinned, reserved or filled
              and  readTime <= T0 + maxRequestAgeSeconds
canClaimNow = 0 when not takesClaims;  else the counted nodes whose period matches at the current
              round's bid and base (section 6), every counted node for a request added without terms
sendBidWei  = max( minBidWei, bid + 1 )
workWei     = bid * G * hours / 100;   workAfterRaiseWei = sendBidWei * G * hours / 100
```

`bidQuoteFor` takes the terms directly: `need` by the rule of section 4 from
`needGflops` and `ceilGflops`, `hours = hoursWanted`, `G = ceilGflops` when
stated else `gflopsMaxDefault`, `tz` from the template keyed by
`templateHash` with `registeredAt < readTime`, `memGB` as the enclave memory
need, `epcUnits` as the minimum EPC (0 for none), no skip test. The work part `workWei` is the escrow's `W` (3.15);
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
step = stated(claim, round))`. The approval ends the approved node's period
BUSY in its block; a case states that end in `periods` where it changes an
answer, which is when the approval is mined in the block of its claim. With
`step` on round `r`'s clock:

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
  isFalse  = late(real) > late(d)  or  (n is the fill's node, fill.round == r, d != real and d != aj)
  recount  = not isFalse and a LATE record exists and recordedLate < late(d)
```

**The approved claim's statement.** The approval of a claim ends the approved
node's period BUSY in the approval's block. An approval mined in the block
number of the claim it takes (the usual case where one block number holds
several of the chain's blocks, as on LitVM) gives that end `f.toBlock ==
c.block`, which reads as an excuse: `real` is 0 for a node that claimed from
`f` while it was open and stated its list step. Block numbers do not order a
claim and a period's end of one block, so the approved claim states a true
place when it states its real step or its list step `aj`, and a false one
only when it states neither (`approval-in-claim-block` and its two
variants). The ballot's `approvedStep` writes `aj` into the fill record in
either case. A claimer that holds no mandate has `real` 0 and `late(0)` 0,
so its statement is false only on the approved claim: the false statements
of a round are among its mandated nodes and the fill's node
(`approved-not-mandated-states-step`).

**mandateOutcomes** (`outcomes`, codes of line 1574): 0 when `K < s`; else 4
when `ended` and (no claim, or `f.toBlock <= c.block`); else 1 when the claim
is of an earlier round, or of round `r` with `step(c.block) <= s`; else 0
when `E <= s`; else 2 with a claim, 3 without. `passed` is `E - s` for
outcomes 2 and 3, else 0. `stated` is `stated(c, r)`, 255 without a claim.
`falseNodes` and `realSteps`: the claimers with `isFalse`, in ascending real
step, then address. The view reads the claims of the mandated nodes and of
the fill's node and no list of claimers: one call answers a round whatever
the number of its claimers, from at most `scanMax` roll entries, and
`complete` is false only when the round's list holds more entries than that
down to its stop band. A caller then reads the list in pages with
`candidates` (section 8) and applies this section to it.

**The reads of a round's ballot** (SF18, OD15 A). A member reads, for a
round's ballot, the claims of the mandated nodes and of the fill's node
(`mandateOutcomes`, one call) always, and a LATE record only for a claim that
states a step for the round. It reads those records in claim order (the
order PoxMatching recorded the claims, `claimerSlice`), at most 64 a tick
(`recounts` of the fixture: `bound` 64): a tick reads the first 64 not read
yet, and the claims above the bound are carried to the next tick's read and
never dropped, so a late claim above the bound is still recounted. Every
address is a claim of its own, whoever operates it. A round whose reads are
not complete is not judged and keeps its charges open: `recordMisses` takes
one ballot per request and round (`RecordRefused` reason 4 for a second), so
a round's ballot is sent only once every read it depends on has answered,
and an entry it skips or an upward recount it does not name is never sent
again (item 38). Every member reads the same set, so their ballot ids agree.
The contract applies no such bound; the bound is the members'.

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
`firstBeatSlot`, `lastBeatSlot`, `lastFailureSlot`, `failuresInLastSlot`
(the node's charged failed orders recorded in slot `lastFailureSlot` and
not corrected, item 36), `lastAskAt`; per node and slot `chargedIn = {sum,
taken}`; per (request, round, node) a charge record
`{kind LATE|MISS|FALSE, step, passed (at most 255), units, at, undone}`; per
order a failure record `{kind, slot, at, units, corrected, charged}`
(`charged`: its charge was added to the slot's sum; `units`: what the cap
and the score let it take); per request
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
approve(n, id, block): the claim APPROVED; filled (a request of one instance: section 5);
                       fill = (n, claim block, rc, stated(claim, rc)); lastOpenSteps[rc] as a claim
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
    a claim mined in a round before r: skipped (it answers the round)
    a claim, the contract's isFalse (late() of section 10 on the fill record as it now stands; the
               approved claim's statement is false when it is neither x nor fill.step, which holds approvedStep):
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
                       charged (its charge is in its slot's sum, whatever the cap let it take); not corrected;
                       time <= at + correctionWindowSeconds
    kind 5 (slot s):   end = (s + 1) * slotSeconds; end <= time <= end + correctionWindowSeconds; s > firstBeatSlot;
                       lastBeatSlot != s; bannedUntil < end; not corrected; a period of n open at end
    kind 6 (id, r):    a LATE record: recount downward at once, not listed
correct(n, entries, stand, time): each entry i with bit i set that is still admitted:
    kind 1:  undone |= 1 << (q - k.step); units = lessUndone(k.passed, undone);
             giveBack(k.units - units, k.at); k.units = units; lates -= 1 when units == 0
    kinds 2 to 4: corrected; giveBack(missUnits, f.slot, f.at); failures -= 1;
             when lastFailureSlot == f.at / slotSeconds: failuresInLastSlot -= 1, and lastFailureSlot = 0 at 0
    kind 5:  corrected; score = min(50,000, score + gain)
fail(n, orderId, kind, id, time): apply(n, slot(id), missUnits) (a STRIKE: the slot of its time); the record's
  units = what apply took; charged; failures += 1; s = time / slotSeconds;
  failuresInLastSlot += 1 when lastFailureSlot == s, else lastFailureSlot = s and failuresInLastSlot = 1
ban(n, time[, orderId]): refused when a failure other than a STRIKE is recorded for orderId; the order's record
  BANNED (no units, not charged) in place of a strike's; score = 0; bannedUntil = time + banSlots * slotSeconds;
  pending emptied; settleAt = 0
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
| `quotes` | `setup`, `readTime`, `baseNowWei`, `minScore`, `carry`, `currentScores`, `status` (the request's status at the read, AVAILABLE when absent), `terms`, `gflopsMaxDefault` | section 9 |
| `recounts` | `bound`, `claims` (each its `node`, `claimOrder`, `statesStep`, `lateRecord`, `recordedLate`, `late`) | `reads` per tick, `readsPerTick`, `ballotTick`, `recounts` (section 10, "The reads of a round's ballot") |

The design's worked examples are the cases `example-1` (3.8 Example 1, and
`example-1-bid-0.95`, `example-1-bid-0.65` for its lower bids, and the
`ex1-*` charges and approvals for its table of what happens), `example-2`,
`example-3` with `example-3-one-band`, and `raise-round-0`, `raise-round-1`
with `quote-raise-example` (3.15). The design states no enclave memory for
them; every node of them offers 64 GiB. EPC (D191): a period states its
`epcUnits` and its `gflopsArg` carries them in bits 24 to 39; a request's
`maxGflopsArg` carries its minimum in bits 32 to 47 and `expected.epcUnits`
gives it. Every period states none (0) and every request states no minimum
but in `epc-below-at-above` (a minimum of 2 units against offers of 0, 1, 2,
3 and 65,535 units: 2 and 3 fit, 65,535 does not), `epc-any` (the same
offers, no minimum: all fit), `epc-fit-bound` (a minimum of 10 units against
offers of 10 and 12 units, which fit, and 13, which does not and is
mandated after them although its key stands first) and `epc-fit-any` (the
same offers, no minimum: all fit, mandated in key order), and in the quotes
on the first two (`quote-epc`, `quote-for-terms-epc`).

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
   (1455). The design binds, `abi.encodePacked` and not `abi.encode`, and
   the code computes all four with it (PoxMatchingState.sol `_stampOf`;
   PoxMatchReaderBase.sol the seed, the round's stamp, the key and the list
   hash).
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
   before `f` ended, excused otherwise (lines 1678, 1702). "Before" is read
   on block numbers: an end in the claim's block (`f.toBlock == c.block`,
   an approval mined in the block of its claim) gives the outcome excused
   (4) and `real` 0, and the approved node's statement of its list step is
   true all the same (section 10, "The approved claim's statement").
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
    `missUnits`. With a late charge in the same slot, `f.units` would leave
    548 taken after both are undone (`correction-under-cap`). The admission
    test is that the failure was charged, its charge added to the slot's sum
    (the record's `charged`), and not `f.units > 0` (line 1995): a failure
    that took nothing because an earlier charge of the slot had taken the
    cap is corrected like any other, and what the cap let be taken goes
    back whichever charge took it (3.12; `two-failures-one-slot-both-corrected`).
    A failure with no charge (on a request marked at its add, or the
    never-started record before the not-run ballot charges it) is skipped.
    `lastFailureSlot` is cleared when it names the corrected failure's slot
    and that slot holds no other uncorrected charged failure of the node
    (`chargedIn.failures`; `two-failures-one-slot-one-stands`).
14. **The cap counts the sum.** `apply` (lines 1759-1763, 1912) caps the sum of
    the charges, and PoxMatchingState.sol `_apply` does; a cap on the units
    taken differs once the score's floor stopped a charge
    (`score-floor-and-cap`).
15. **The slot of a failed order.** The cap of a failed order counts in the
    request's slot (line 715; `notRun`, line 1804); a strike in the slot of its
    record (line 1793). PoxMatchingState.sol `_failOrder` takes the request's
    slot and PoxMatchingJudged.sol `strike` the slot of the block's time.
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
    that reads the last entry reports `done`. PoxMatchReader.sol `rollPage`
    and PoxMatchReaderBase.sol `_scan` (the pages of `candidates` and
    `turnOf`) walk that way.
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
23. **`canClaimNow`** counts the counted nodes that could claim at the read:
    none unless the request takes claims then (`takesClaims` of section 9:
    its set has `claims` on, the storage holds it AVAILABLE, it is neither
    pinned, reserved nor filled, and it is at most `maxRequestAgeSeconds`
    old), else those that pass the claim's price test at the current round,
    which a request added without terms does not have (3.11). The claim's
    "no claim yet" test is not applied, since rule 4 speaks of nodes that
    "can claim" and "have claimed" (line 600). Each `bidQuote` case states
    `takesClaims`: `quote-open-request` (five nodes can claim) against
    `quote-claims-off`, `quote-pinned-request` and `quote-cancelled-request`
    (the same five counted at the same lowest bid, none can claim). The
    fixture holds no quote of a filled or a reserved request, of one past
    its age or of one added without terms.
24. **`bidQuoteFor`'s template and memory** (lines 1553-1558, 1613): the
    template registered before the block of the call, `memGB` the enclave
    memory need, `epcUnits` the minimum EPC, the need rule of 4.2 on
    `needGflops`, no skip test (no image argument).
25. **The window of kinds 2, 3 and 5** ("the blocks whose time lies in the last
    fullWindowSeconds before", lines 689-692): `end - fullWindowSeconds <=
    timestamp < end`.
26. **Kind 5's admission.** Line 697 requires that a heartbeat at the slot's
    end would have counted; the pseudo-code (lines 1886-1887) omits it. The
    fixture applies it as "a period of the node was open at the slot's end";
    the serving-order leg is not in the fixture. A slot that did not end
    after the end of the node's ban (`bannedUntil >= end`: a ban in force
    through the slot, or recorded since it) is skipped, at the ask and at
    the vote: the gain is not added to a score a ban has set to 0 (line 730,
    "not at all when a ban has set it to 0 since the charge";
    `heartbeat-correction-after-ban`).
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
33. **The EPC** (D191: both requests carry an EPC size, the bench proves the
    node's). The design gives it no unit or width. The reading: one unit is
    64 MiB and the field is `uint16`, the 16 free bits of the offer record's
    first slot (1 to 65,535 units: 64 MiB to 4 TiB less 64 MiB). An offer
    states at most the whole units of the EPC its bench record proves,
    `floor(<MiB> / 64)` of the record's `epc=<MiB>` (spec/metadata-keys.md,
    reason 12). On an offer 0 states none and covers no request that states
    a minimum; on a request 0 states no minimum and every offer covers it.
    The filter tests `p.epcUnits >= epc` after the enclave memory. The fit
    tests the EPC as it tests the memory and the storage (D198: the EPC
    within 20% of the need, so that a node of a large EPC is not mandated
    first on a request of a small one):
    `p.epcUnits * 100 <= epc * (100 + fitPercent) + 99`, at most 1.2 x the
    minimum rounded up to a whole unit (1 unit: up to 2; 2: 3; 10: 12;
    1,024: 1,229). A request that states no minimum (`epc` 0) leaves the EPC
    out of its fit, which is then that of a request without the EPC; an
    offer that states none covers only such a request. The price match and
    the quote's arithmetic do not test the EPC.
34. **The proof that three nodes could serve a request** (`proveWide`, and
    the `hints` of `noteAmbiguous` and `nodeFault`; 3.7, 3.19, D120). Each
    of three hints names a roll entry of the approval's round and a listed
    period, of three different nodes, and each is tested as a member of
    that round's list is (section 6): the period is the node's newest with
    `from + minListedSeconds <= T`, open at `T`, passes the filter with the
    template (`p.tzMask & tz != 0`, `tz` of section 4) and the skip, and
    matches the round's bid and base. No ballot stands behind this proof,
    so PoxMatching tests the template itself, where a ballot's hint leaves
    it to the quorum. On a request with `tz = 0` (a template not registered
    before the add) no node declares the template and no proof holds:
    `WideRefused` reason 3.
35. **One failure record per order, and the ban over a strike** (OD13). An
    order holds one failure record (`failureOf`). The call that records it
    first, `strike`, `noteRefund`, the hook of `expireUnstarted`,
    `nodeFault`, `notRun` or `ban`, makes each later one refuse the order
    (`NodeFaultRefused` reason 7, `NotRunRefused` 2, `BanRefused` 3,
    `RefundRefused` 3, `NotUnstarted` 3) or return without effect
    (`strike`, and `noteRefund` from the judge), with one exception: `ban`
    writes over a STRIKE record of the same order. The ban's record (kind
    BANNED, no units, `charged` false) takes the strike record's place; the
    strike's charge stays in its slot's sum, and the node's count of failed
    orders and the count of `lastFailureSlot` (item 36) keep it, since no
    correction undoes a strike or a ban. A strike recorded first therefore
    does not shield its order from the ban; it still shields it from a node
    fault and a not-run charge. `notRun` alone charges the never-started
    record `expireUnstarted` left uncharged (`strike-then-ban`).
36. **`lastFailureSlot`** (OD12) is the slot of the block that recorded the
    node's last charged failed order, `block.timestamp / slotSeconds` at the
    record; the charge itself counts in the slot of its request's add
    (`createdAt / slotSeconds`, item 15; a strike's in the slot of its
    record), whose cap it takes. The recorded slot never moves back, so an
    order's failure recorded `recentFailureSlots` slots or more after its
    request's add is recent for the runner's rule (item 28) from its record
    on (`failure-recorded-slots-after-add`, the approval
    `recent-failure-recorded-after-add`). Beside it the node's serving word
    counts its charged failed orders recorded in that slot and not corrected
    (`failuresInLastSlot`, bits 144 to 159 of `servingOf`). A correction of
    kinds 2 to 4 of a failure recorded in the slot `lastFailureSlot` names
    takes it off that count, and `lastFailureSlot` is cleared when the count
    reaches 0; a correction of a failure recorded in an earlier slot leaves
    both. A successor that copies a nonzero `lastFailureSlot` from its
    predecessor (`accountInChain`) counts one failed order in it, which no
    correction of the successor undoes.
37. **`counted` before and after the ballot.** `counted` (D120) reads the
    fill record's step, which is the approved claim's own statement until
    the round's ballot writes the list's step into it (`approvedStep`,
    section 11). An order settled, or a node fault counted, before the
    ballot is counted on the statement, one after it on the list's step, so
    S and F of an image depend on which comes first.
38. **One ballot per round.** `recordMisses` records one ballot for a request
    and round (`RecordRefused` reason 4 for a second): an entry the call
    skips and an upward recount it does not name are not sent again. A
    downward recount stays open to the node (kind 6).
39. **A skip's two entries.** A skip holds its entry in force and one
    previous entry; a new skip drops the previous one, refused while that
    one ended less than 48 hours before (`SkipRefused` reason 7).
    `recordMisses` and the proof of item 34 have no time limit: a hint for a
    round whose time lies inside an entry already dropped reads the node as
    not skipped at that round.
40. **Paid work is carried over a cut** (OD14). Each PoxMatching counts the
    paid work of the orders it settles (`onOrderSettled`, at most 2^32 - 1
    CU-hours); `paidWorkOf(node)` and `paidWorkOfNodes(nodes)` answer that
    count plus the predecessor's answer for the node, so through the whole
    chain of predecessors, the sum at most 2^32 - 1. The floor of 100
    CU-hours of paid work the tier reads (`tierScore`, through
    `paidWorkOfNodes`) holds across a cut as the account does.
41. **The minimum EPC a runner states** (D200: 64 MiB on every request it
    adds, and the dApp's recorded need when that is larger). The design
    names the record, "with its image in ECImageRegistryV4 at publish", and
    neither its place nor its width. The reading: ECImageRegistryV4 holds
    it as a word of its own beside the image's records, in the unit and the
    width of item 33 (`uint16` units of 64 MiB), and
    `imageEpcUnits(ipfsHash)` answers it: 0 for a securelock image with none
    recorded and for a hash the securelock table does not hold. The wallet
    that registered the image writes it once, while it owns the image's
    name: `setImageEpcUnits(ipfsHash, epcUnits)`, 1 to 65,535 units, with
    `ImageEpcSet(ipfsHash, epcUnits)`; `ImageEpcRefused(ipfsHash, reason)`
    refuses it for 1 a hash outside the securelock table or a sender that
    is not that wallet, 2 a need recorded already, 3 a need of 0 units.
    `setImageRecords` and `imageRecords` keep their eight words. A dApp
    whose work fits one unit records none. A runner states, in bits 32 to
    47 of the V2 add's `maxGflops` (section 3),
    `max(1, imageEpcUnits(image), client)`: `image` is field 1 of the
    request's metadata1 and `client` the client's own figure in whole units
    of 64 MiB, 0 when it gives none. No contract tests a request's minimum
    against the record: a request added with a minimum of 0 (a reserved
    order, a legacy add) stays one that every offer's EPC covers (item 33).
    The reservation broker reads the record for its placements (item 42).
42. **The enclave memory and the EPC of a reserved placement** (OD49; EPC-C
    Q4). A reserved order's DO request states no minimum EPC, so the PoX and
    PoxMatching test none at its placement; the broker tests them when it
    chooses the offer. Its need is the template's enclave memory, the
    template's `mem` (the request's memory field carries the dApp's enclave
    memory, item 4), and `max(1, imageEpcUnits(image))` units of EPC for
    `image` field 1 of the template's metadata1, read within its first 512
    bytes from the image registry the PoX names; a registry that is not set
    or does not answer `imageEpcUnits` gives 1 (PoxReader `epcNeedOf`, read
    at each placement and by `eligibleOffer`). An offer is placed on only
    when its enclave memory is at least the template's `mem` and its EPC
    units at least the need, beside the tests it passed before (AVAILABLE,
    unexpired, the resources, the GFLOPS band, accepting reserved orders):
    PoxReader's `committedOffers` (its ninth argument, the need) and
    `reservedOffer` (superviseWith's hint) apply both. The matching rule's
    ranking of the offers that pass is unchanged (no fit on the EPC). An
    offer one unit short of the need, or below the enclave memory, is
    skipped; the reservation stays unmatched until an offer covers it.
43. **The templates a passed-over offer declares** (OD9 part ii; weakness
    (d)). An offer without a trustedzone record is passed over only while
    every name of its `trustedzones=` list ends in `-unsafe`
    (spec/metadata-keys.md). On a network where a PoxMatching judges the
    offers, the members also read the open period's `tzMask` (the templates
    the node declared at `announce`, section 6) and pass the offer over only
    while every template whose bit it holds is registered under the name of
    a trustedzone of that list; a bit of any other template makes the offer
    one that is not passed over (reason 9). A passed-over offer's enclave
    memory, storage and EPC are unproven, so it is judged on reason 13 above
    100 GFLOPS and on no reason of 10 to 12, and the `-unsafe` templates it
    declares are its only list membership.

## 17. The result fields after v5 (3.19; SECURELOCK-HARDENING-SPEC.md 13.4)

The result string the trustedzone signs (`_addResultToOrder(orderId,
result)`, spec/result-v5.json) carries five fields after the fifteen of v5,
in this order: field 15 `landedAt`, field 16 `ranUntil`, field 17
`slClass`, field 18 `closeBlock`, field 19 `closeRows`. Fields are split at
`:`; field `i` is the bytes after the `i`-th colon up to the next colon or
the end of the string. Who reads what:

| v6 string | PoxMatching | members | node agent | runners |
|---|---|---|---|---|
| fewer than 18 fields | fields 15 to 17 not read (`absent`) | no closing position | code and ESR fields read as v5; no class | the task code |
| 18 or 19 fields | fields 15 to 17 read | no closing position | fields 15 to 17 read | the task code |
| 20 fields or more | fields 15 to 17 read; nothing after field 17 | fields 18 and 19 read (below) | as for 18 | the task code |

The round's writers (both trustedzones) write twenty fields. No
trustedzone at etny-nodenithy 54cb347 or etny-pynithy 536e50e writes v6, so
no deployed result changes. Positions 20 and 21 are reserved for the first
and last block of a relay wait in a later version.

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

**The closing position** (fields 18 and 19; OD4, OD6, OD52, OD53). Field 18
`closeBlock` is a block at which the DO request's row count is at least
`closeRows`: the final input sweep's closing read; field 19 `closeRows` is
the enclave's row cursor over all metadata rows of the DO request when its
input side ended. Both are plain decimal of the form above, at most
2^64 - 1. The pair 0/0 is no closing position. `closeBlock` 0 with
`closeRows` above 0 is an invalid form, as is a non-zero pair on an order
whose field 11 is `0f`. A DO request is a session request when its
metadata3, fixed at the add, begins `v3s:`; field 11 must agree with it (`0f`
on another request, a session status on a session request), and a result
that disagrees is an invalid form, not a ban cause. The enclave states, per
session status (result-v5.json `sessionStatusCodes`):

| field 11 | final input sweep | fields 18 / 19 | the members' lower bound on `closeRows` |
|---|---|---|---|
| 0 closed | runs | closing read / cursor | the position of the order's first close row in the request's row list, plus one |
| 1 complete-at-timeout, 2 expired-idle, 3 expired-unprocessed | runs | closing read / cursor | the row count at the first block whose timestamp reaches the input cutoff (the time the enclave's loop ends: `securelockCloseAt`, or the input cutoff after which rows are answered with late notices) |
| 4 flooded | runs | closing read / cursor | the position of the row that met the bound, plus one |
| 5 unsupported-securelock | runs (rows consumed and answered with notices) | closing read / cursor | as 0 or 1 by how the session ended |
| 6 transferred, 7 transfer-forced | does not run | the window's `toBlock` / the cursor at the transfer | the row count at `toBlock` |
| `0f` no session | none | 0 / 0 | not read |

The enclave reads the closing block before any pump of the final sweep, and
when the count at it is below the cursor (a lagging replica answered the
earlier pump) it reads the head again until it is not; it consumes no row at
or past the count at `closeBlock`; a final sweep that gets no closing read,
or does not read to the count at its closing block, ends the order
SESSION_RELAY_FAULT (39), never a stated short cursor or a block 0. The rows
a session reads, of every key, are bounded at 1,024 (OD53): a flood of rows
by the data owner ends the session as flooded (4), a dApp fault, never
charged to the node. Input rows are read at the network's landing depth
(`session_landed_depth_blocks`, OD52).

The members read fields 18 and 19 only after the signer is established, and
in this order: an invalid form; `closeBlock` above the block the result
landed in (`orderResultAt`, read for every judged order): invalid session;
`closeRows` above the request's row count at the judged block (no read at an
older block): invalid session; a lower bound that needs a read at an older
block (statuses 1 to 3, 6, 7) that does not answer: retried, never a
verdict; `closeRows` below the lower bound: invalid session, a node fault
(inputs withheld), a ban cause on the terms of an input mismatch; the input
root over the first `closeRows` rows, by the row grammar of section 20,
differing from field 7: invalid session, a ban cause. An input-side mismatch
is a ban cause only for rows at or below the landing depth. The members
read no `etny-si` row of a non-session order. A result with no closing
position decides neither a ban nor an invalid verdict from its input side
(OD6 part i); a session request of a dApp whose image bundles an older
trustedzone takes claims on a matching network only where V4's records name
a default release at or after the first release whose trustedzone writes
the closing position (no such release is listed at the time of writing;
this specification names its index once it is), and its input side decides
nothing otherwise (OD6 part ii).

**Field 13 in the round's writers** (OD3). Field 13 is
`etny-cu-delivered-gflops=<gflops>|<n0>|<t0>|<t1>|<validator>|<sig t0>|<sig t1>|<block0>|<block1>|<salt>`:
ten values, the salt the tenth, `sig t1` over the statement with nonce
`closeNonce(n0, salt, "<gflops>")` (capability-record.json `closeNonce`). A
reader tells it from the nine-value form of v5 (both statements over n0) by
its value count, not by the version word: on a network whose pricing names
a PoxMatching a nine-value field 13 is no probe (no strike, no admission);
elsewhere it is a probe as at 52e36e0. The rate bound of capability-record.json
applies to it with W = probeRounds x the node's record's `cuworkers` x
opsPerRound.

**The stored result.** The node adds the trustedzone's signed transaction to
its order with `_addResultToOrder(orderId, stored)`, inside one string.
PoxMatching's decoder and the validators' members read that string by one
grammar, the form both trustedzones write into `transaction.txt` and the
node agent copies between `v3:` and the result's CID:

```
stored = "v3:" "0x" 1*( hexdig hexdig ) [ ":" rest ]
hexdig = "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9"
       | "a" | "b" | "c" | "d" | "e" | "f"
rest   = any bytes (the result's CID); not read
```

The string is read as its bytes stand: nothing is trimmed, skipped or
folded. The hex run begins after `0x` and ends at the next colon or at the
end of the string; it holds an even count of digits and at most 4,096
bytes. A string that does not begin `v3:` is no v3 result (`notV3`). One
that begins `v3:` and leaves the grammar carries no transaction
(`noTransaction`): white space anywhere before the second colon, the prefix
written `0X` or left out, a digit `A` to `F`, an odd count of digits, a run
of more than 4,096 bytes, or bytes that are not the transaction below.

The run is the legacy transaction the trustedzone signed, in canonical RLP:

- one list that is all of the bytes, of nine items none of which is a list:
  nonce, gasPrice, gasLimit, to, value, data, v, r, s;
- every length in its shortest form: the short form below 56 bytes, the long
  form from 56 on with no leading zero byte; a single byte below `0x80` is
  written as itself and never as a one-byte string (`0x81` and the byte);
- `to` is empty or 20 bytes; v, r and s are read as big-endian integers of
  at most 8, 32 and 32 bytes (a leading zero byte inside those bounds is
  read as part of the integer);
- v is 27 or 28, or 35 and above (EIP-155: the chain id is `(v - 35) / 2`
  and the parity `(v - 35) % 2`); s is at most half the order of secp256k1;
- the signature recovers a signer over keccak256 of the RLP list of items 0
  to 5 as their bytes stand in the transaction, followed for EIP-155 by the
  chain id, an empty item and an empty item;
- `data` is exactly the call `_addResultToOrder(uint256,string)`: the
  selector `0x14e9f384`, the order id (below 2^64), the offset `0x40`, the
  string's length and its bytes padded with zero bytes to a whole word, and
  nothing after them; the string is valid UTF-8.

The grammar reads that a signature recovers, not whose it is: the members
compare the signer with the order's task wallet, the transaction's `to` with
the PoX and its chain id with the network's. For an order, the reading
answers `decoded` with the task code, the run of digits that is field 1 of
the result string (at most 2^64 - 1); `noCode` when field 1 is not a run of
digits; `otherOrder` when the call names another order. PoxMatching acts on
it as follows: `nodeFault` needs `decoded` (`NodeFaultRefused` reason 3 for
`notV3`, 4 for `noTransaction` and `noCode`, 5 for `otherOrder`); `notRun`
needs `notV3` or `noTransaction`, a result with no enclave transaction
(`NotRunRefused` reason 4 otherwise); `noteAmbiguous` needs `decoded` with
the three fields above. A node that stores a result outside the grammar has
stored no enclave transaction, whatever its bytes resemble: its order is
charged as one closed with no result of the enclave.

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

`stored` holds the vectors of the stored result's grammar, each with the form
of its bytes and what `readStored` of `make/result-fields.js`, the reference
implementation of the grammar, reads from it for the fixture's order:
`expected.status` (`decoded`, `notV3`, `noTransaction`, `otherOrder` or
`noCode`), `transaction` (whether the bytes carry a transaction: every
status but `notV3` and `noTransaction`) and, for a transaction, its order id
and its signer, with the task code when decoded. `canonical` and
`canonical-pre-eip155` are the two forms the trustedzones write (EIP-155,
and v 27 or 28); `no-rest`, `empty-rest`, `rest-not-read`, `length-bound`
(4,096 bytes), `v-leading-zero-byte` and `other-signer` are the other forms
that carry a transaction. `space-before-hex`, `one-byte-string-v` and
`upper-case-prefix` carry none, as do the other white space, prefix,
digit, length, RLP, signature and calldata forms of the group.
`storedGrammar` states the rule in the fixture's own words. The stored
vectors carry the writers' v6 result of twenty fields with task code 39.

`v6` holds the twenty-field form: `writers` (results of 18, 19 and 20
fields and the invalid forms, each with the closing position
`readClosingPosition` of `make/result-fields.js` reads for a session or
another request), `sessionStatuses` (one vector per status with fields 18
and 19, the lower bound's input and the members' verdict), and `field13`
(the ten- and nine-value forms and a ten-value one whose window is too long
for its GFLOPS).

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
| the period: `from`, offer, cpu, memory, storage, bandwidth, GFLOPS, tip, template mask, enclave memory, EPC | written once at the announce | `Announced(node, dpRequestId, from, cpu, memGB, storageGB, bandwidth, gflops, tipWei, tzMask, enclaveMemoryGB, epcUnits)`; the ordinal is the node's previous period's plus one, 0 for its first |
| the period's end: `to`, the reason, `toBlock` | written once | `Left(node, reason, at)`; `toBlock` is the block's number (a replacement, a pause, a placement, an offer's removal, an expiry, a missed mandate, a ban) |
| a skip: `at`, `until`, `prevAt`, `prevUntil` | changed by `skipImage` alone | `Skipped(node, imageKey, at, until, prevAt, prevUntil)`, the skip as the call leaves it |
| the parameter set of the request | fixed once it is a request's: only a set whose `effectAt` is above the block's time is replaced (section 2), and a request's set has `effectAt < T0` (`parameterSet(index)`) | `ParametersAdded(index, effectAt)`; `ParametersReplaced(index, effectAt)` for a set not yet in force |
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

## 19. The stored words (SF10)

PoxMatching's views answer some records as stored words, read from the
least significant bit (`words.json`, `make/words.js` run under Hardhat):

| view | bits |
|---|---|
| `failureOf` | kind 0-7, units 8-23, slot 24-55 (the request's slot, a strike's the slot of its time), at 56-87, corrected 88-95, code 96-103, uncharged 104-111, charged 112-119 |
| `chargedInOf` | sum 0-31, taken 32-47 |
| `servingOf` | orderId 0-47, orderHours 48-63, lastFailureSlot 64-95 (the slot of the block that recorded the last charged failed order, item 36), lastAskAt 96-127, settleAt 128-135, placed 136-143, failuresInLastSlot 144-159 |
| `accountOf`, `accountInChain`'s word | score 0-15, lastBeatSlot 16-47, firstBeatSlot 48-79, beats 80-103, lates 104-127, misses 128-151, failures 152-175, claims 176-199, pending 200-223, bannedUntil 224-255 |
| a claim's word (`claimsOfNodes`) | dpRequestId 0-47, blockNumber 48-95, time 96-127, then `packed`: round 128-131, step 132-135, state 136-139, the stated steps 140-159; tipWei 160-255 |
| `periodsOf`, first word | from 0-31, to 32-63, toBlock 64-111, dpRequestId 112-159, gflops 160-175, cpu 176-183, memGB 184-191, storageGB 192-199, bandwidth 200-207, tzMask 208-223, endReason 224-231, pastCount 232-255 |
| `periodsOf`, second word | tip 0-127, chainFirst 128-151, enclave memory 152-159, EPC units 160-175 |

`chargedInOf` holds no count of failed orders: the count of the failed
orders recorded in `lastFailureSlot` is `servingOf`'s `failuresInLastSlot`.
`words.json` holds, for three sequences of `matching-rank.json` replayed on
the in-process network, each word's fields decoded by these layouts (slots
in the case's coordinates; times, blocks and order ids left out).
`test/WordsVectors.test.js` regenerates it and compares it byte for byte,
and holds the layouts to the suites' decoders.

## 20. The rows of a session (SF20)

Both trustedzones, the validators' members and the runners' session clients
read a session's `etny-si` input rows and close rows by one grammar
(`session-rows.json`, `make/session-rows.js`):

```
input-row = "v1" ":" seq ":" orderId ":" cid ":" sha256    ; exactly five fields
close-row = "v1" ":" "close" ":" orderId [ ":" depth ]
seq, orderId = plain decimal (section 17), at most 2^64 - 1, nothing trimmed
depth = 1*9 digit
cid, sha256 = any bytes but ":"
```

A row's value is read as its bytes stand. An input row is taken for an
order when it is in the grammar, names the order and its `seq` is the next
expected one (from 0); a gap or a duplicate is skipped on every side. A row
outside the grammar is answered by the enclave with the malformed notice and
is kept out of the input root on every side; `cid` and `sha256` are not part
of the acceptance, and the root is over the whole value. A close row's depth
is at most the network's `session_landed_depth_max_blocks`. The runners
write integers into their rows, so none of the refused forms comes from
them. Until all three readers hold to the vectors, an input-side mismatch
decides no ban.
