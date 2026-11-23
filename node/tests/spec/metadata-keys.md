# DP-request metadata3 and metadata4 (plan amendment 1)

The four metadata strings of a DP request are `metadata1` = node uuid,
`metadata2` = protocol version (`v3`), `metadata3` = geography and the
validator time statements, `metadata4` = capability list. metadata3 and
metadata4 are lists of `key=value` entries separated by `;`. Readers split
on `;` and take each entry up to its first `=` as the key; an entry without
`=` is a bare flag. Values never contain `;`. Commas are value characters
(the trustedzone list, the geography).

The example values below are those of `capability-record.json`, generated
by `spec/make/capability-record.js`; this file is written by
`spec/make/metadata-keys.js` from that fixture.

## metadata4

```
trustedzones=<a,b>;ipfs=<peer id>;startrow=v1;cugflops=<gflops>;cuv=<cuv>;cuat=<t1>;cusig=<DER hex of the record signature>;cuheap=<SCONE_HEAP>;cuworkers=<n>
```

| key | value | written by |
|---|---|---|
| `trustedzones` | comma-separated trustedzone image names of this network the node runs | agent |
| `ipfs` | the node's Kubo peer id; empty when Kubo did not answer | agent |
| `startrow` | `v1`: the agent adds the `etny-st` started row when it starts an order | agent |
| `cugflops` | the record's gflops, field 4 of the line: the node's attested GFLOPS; one CU is 100 GFLOPS (`250` = 2.5 CU); at most `CU_MAX x 100` = 6,400 | agent, from the record |
| `cuv` | the benchmark version of the record (benchmark.json `cuv`); at least `CU_VERSION_MIN` (4) | agent, from the record |
| `cuat` | `t1` of the record: the unix second the benchmark ended, for the CU_TTL check | agent, from the record |
| `cusig` | the trustedzone's DER hex P-384 signature over the record line | agent, from the record |
| `cuheap` | the `SCONE_HEAP` of the record's last field (`heap=`) | agent, from the record |
| `cuworkers` | the benchmark worker count of the record's last field (`workers=`) | agent, from the record |

A DP request without `cugflops` is an unattested offer; the PoX records it
at one CU, 100 GFLOPS. The validators pass it over, counting it one CU, only
when its `trustedzones=` list names at least one trustedzone, every name it
lists ends in `-unsafe` and the network admits `-unsafe` trustedzones
(ethernity-cas `Deployment::admits_unsafe`): an `-unsafe` trustedzone
contacts no CAS, so its node never carries a record, and an `-unsafe`
network bakes the PoX of its CAS twin. Any other offer without `cugflops`
(naming no trustedzone, naming one that can carry a record, or on a network
that admits no `-unsafe` trustedzone) is cancelled with reason 9
(`RecordMissing`, below) where the network's validators vote; where they do
not vote it counts one CU. The `cu` prefix names the capability
record's key family: `cugflops`, `cuv`, `cuat`, `cusig`, `cuheap` and
`cuworkers` here, and the `cuval`, `cun`, `cut0`, `cut1`, `cub0`,
`cub1`, `cusig0`, `cusig1` group of metadata3. The agent appends the six
metadata4 `cu*` keys after `startrow=v1` and only when it holds a record;
after a reason-9 cancel on a network it adds no DP request without a record
there, unless the request names only `-unsafe` trustedzones, until its
integration test has produced one.

Example:

```
trustedzones=ecld-nodenithy-amoy,ecld-pynithy-amoy;ipfs=12D3KooWQYhTNQdmr3ArTeUHRYzFg94BKyTkoWBDWez9kSCVe2Xo;startrow=v1;cugflops=250;cuv=4;cuat=1761724830;cusig=30660231009ed1a9f357e9c1f5a1c47d972f39a9972f8d535bfbb02994bb7b26a5a416e2d76d0cc9bd655c2212698c628462a199fe023100a21975f8209b93d18bec1a6f6d1c4daf43010f9cdc97d292fff39b5ac9a2a880c1092ac048af8c937ec33044a8e49a26;cuheap=256M;cuworkers=4
```

## metadata3

```
<lat,lon>;cuval=<validator address lower-case>;cun=<run nonce>;cut0=<t0>;cut1=<t1>;cub0=<block0>;cub1=<block1>;cusig0=<sig t0>;cusig1=<sig t1> -- the first ;-field is the geography and may be empty, in which case the string starts with ;
```

| position / key | value | written by |
|---|---|---|
| first `;`-field | the geography `<lat,lon>`; may be empty | agent |
| `cuval` | the validator that signed both time statements, lower-case 0x hex | agent, from the record |
| `cun` | the 32-byte run nonce of the record, 0x hex | agent, from the record |
| `cut0` | `t0` of the record | agent, from the record |
| `cut1` | `t1` of the record | agent, from the record |
| `cub0` | the `block` field of the t0 statement, decimal | agent, from line 1 of `capability.time.etny` |
| `cub1` | the `block` field of the t1 statement, decimal | agent, from line 2 of `capability.time.etny` |
| `cusig0` | the validator's 65-byte signature over the t0 statement, 0x hex | agent, from the record |
| `cusig1` | the validator's 65-byte signature over the t1 statement, 0x hex | agent, from the record |

The geography is always the first field, so a reader takes index 0 of the
`;` split as the geography (possibly the empty string) and parses
`key=value` entries from index 1 on. `cub0` and `cub1` are not in the
record line: the agent takes them from the `block` field of the two
`GET /validator/time` answers the trustedzone writes to
`capability.time.etny` in the integration bucket, t0 then t1, one JSON
object per LF-terminated line (plan amendment 17; `capabilityTimeEtny` of
`capability-record.json`), and a wrong value fails only the recovery of
`cusig0` / `cusig1` below.

Example:

```
44.4268,26.1025;cuval=0x2a0707de23c2c09d6ff7fadbde34c06cdb0e6278;cun=0xf9324b49b24c8561481ef6b84ec6cbeabd1ccd691e3e55f109d1243ea864e569;cut0=1761724800;cut1=1761724830;cub0=29000000;cub1=29000015;cusig0=0x32dac36e060115ab6b0c8904fe92c8588affd0fec146186f68a61902a2f89d7f20c5392feeb2db341616f71eb82f6309fde0c8f6bdd9cd0237f21e43482551a81c;cusig1=0x00f6af616becd151b753b09ee4830c51df8449e840a57a135160facdccf410a8203b15fc4d879e3c9a9cd94befa207d5032f1ef951731f26d5db40bc0d85278c1b
```

Example with the geography empty (the string starts with `;`):

```
;cuval=0x2a0707de23c2c09d6ff7fadbde34c06cdb0e6278;cun=0xf9324b49b24c8561481ef6b84ec6cbeabd1ccd691e3e55f109d1243ea864e569;cut0=1761724800;cut1=1761724830;cub0=29000000;cub1=29000015;cusig0=0x32dac36e060115ab6b0c8904fe92c8588affd0fec146186f68a61902a2f89d7f20c5392feeb2db341616f71eb82f6309fde0c8f6bdd9cd0237f21e43482551a81c;cusig1=0x00f6af616becd151b753b09ee4830c51df8449e840a57a135160facdccf410a8203b15fc4d879e3c9a9cd94befa207d5032f1ef951731f26d5db40bc0d85278c1b
```

## Key names

metadata4: `trustedzones`, `ipfs`, `startrow`, `cugflops`, `cuv`, `cuat`, `cusig`, `cuheap`, `cuworkers`

metadata3: geography (positional, first field), `cuval`, `cun`, `cut0`, `cut1`, `cub0`, `cub1`, `cusig0`, `cusig1`

## What the validators rebuild from the DP request

The record line itself is in the node's integration bucket, which the
validators do not read, so every signed field is baked, on chain or in these
keys: the line's fields as listed below, and the two time statements' fields
(`chainId` and `registry` baked, `node` = the request's `dproc`,
`nonce` = `cun`, `t` = `cut0` / `cut1`, `block` = `cub0` / `cub1`). A
validator rebuilds (`<gflops>` from `cugflops`, the other `cu*` placeholders
from the keys of their names)

```
etny-cu|v1|<baked chainId>:<baked pox>|<dproc lower-case>|<gflops>|<cuv>|<cuval>|<cun>|<cut0>|<cut1>|<cusig0>|<cusig1>|heap=<cuheap>;workers=<cuworkers>
```

and checks:

- `cusig` verifies over that line under the certificate of an approved
  trustedzone of the network (ECDSA P-384, SHA-256 prehash, DER);
- `cusig0` / `cusig1` recover to `cuval` over the statements of
  `time-statement.json` rebuilt with `node` = the request's `dproc`,
  `nonce` = `cun`, `t` = `cut0` / `cut1` and `block` = `cub0` / `cub1`,
  and `cuval` is a seated member of the registry;
- `cut1 - cut0` is at most `BENCHMARK_MAX_SECONDS`; `cuat` equals `cut1`
  and is within `CU_TTL` of the block timestamp; `cugflops` is at most
  `CU_MAX x 100` (6,400); `cuv` is at least `CU_VERSION_MIN` (4).

A failed check, or a missing record on an offer that is not passed over,
cancels the offer where the network's validators vote, with a reason code:
the `uint8` the validators carry in `voteCancel` and the PoX in
`DPRequestCancelled` (ethernity-cas
`capability.rs` `Reason`; PRICING-DESIGN.md section 9). Reasons 1 to 8
are judged on an offer that carries `cugflops`, reason 9 on one that does
not, reason 10 on an offer that states an enclave memory:

| code | reason | check |
|---|---|---|
| 1 | `RecordSignatureInvalid` | `cusig` verifies under no certificate and no session key of the trustedzone name |
| 2 | `RecordNodeMismatch` | the rebuilt line's node or PoX differs from the request's |
| 3 | `TimestampNotSeated` | `cusig0` / `cusig1` do not recover to `cuval`, or `cuval` is not seated |
| 4 | `RunTooLong` | `cut1 - cut0` above `BENCHMARK_MAX_SECONDS` |
| 5 | `RecordExpired` | `cuat` is not `cut1`, or more than `CU_TTL` from the block timestamp either way |
| 6 | `GflopsAboveMax` | `cugflops` above `CU_MAX x 100` (6,400) |
| 7 | `DeliveredBelowAttested` | the node's window holds `CU_STRIKES` strikes (a probe delivered below 80 % of the attested gflops) |
| 8 | `CuVersionBelowMinimum` | `cuv` below `CU_VERSION_MIN` (4); checked after the `GflopsAboveMax` check |
| 9 | `RecordMissing` | the offer carries no `cugflops` and is not passed over: its `trustedzones=` list names none, names a trustedzone that can carry a record (one without the `-unsafe` suffix), or the network admits no `-unsafe` trustedzone |
| 10 | `EnclaveMemoryAboveRecord` | the offer states more enclave memory than its capability record shows (D147) |

`cun`, `cuheap` and `cuworkers` carry the three fields of the line that no
other key or chain state holds (the run nonce and the `heap=;workers=`
field); every other field is baked, the request's `dproc`, or another key.
The line carries no block numbers, so `cub0` and `cub1` are the only
source of the statements' `block` field; they are unsigned by the
trustedzone and enter nothing but the recovery above. The rebuilt line of
the example is `lineFromMetadata.rebuilt` of `capability-record.json` and
equals its `line`.
