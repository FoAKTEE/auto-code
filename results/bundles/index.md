# Live bundle index (D9)

Archive `/data/haiyangw/claude/zkqec-archive`; re-audited offline with each bundle's embedded evaluator; indexed at `94f79e3b3a2a` (2026-10-15T20:30:23Z). Machine-readable: `index.json`.

| campaign | bundle sha256 | MB | evaluator commit | ledger head | audit | confirmation | satisfied | USD | requests | note |
|---|---|---|---|---|---|---|---|---|---|---|
| m1-live-1 | `12d884ceeb4c9428a7340f3b07d1afab564121ff0f8acd97cda4d4ceb71da782` | 0.01 | `6c2a27462390` | 1 | ok | none (pre-B6) | 0/0 | 0.0000 | 0 | pre-hardening; 8 launch failures (I1); not acceptance evidence |
| m1-live-2 | `cd7c53578b3ff2df011c299344c6141e4ead3341833ccf27ee9ed17c221ee85a` | 0.03 | `a7218bb24165` | 36 | ok | none (pre-B6) | 2/2 | 0.6524 | 4 | pre-hardening; duplicate request (I2); not acceptance evidence |
| m1-live-3 | `3bdd699b0b2d6a41ce64205aeffa6b8d98635c2c4510723983a86db1793b107a` | 131.86 | `7d8c36d2e628` | 50 | ok | none (pre-B6) | 2/2 | 0.3595 | 2 | first hardening round (7d8c36d); superseded by the milestone review (B1-B8) |
| m1-live-4 | `f4f8dd9d8077d520c940efa36c31af39a1b048bd8ea5be28ea0e020a5e1e10b3` | 131.87 | `addb11ba0fb8` | 50 | FAIL | linked | 2/2 | 0.2952 | 2 | snapshot run (addb11b); controls refused in snapshot mode (I9); audit fails; not acceptance evidence |
| m1-live-5 | `924c2f02e3d7f3553c9bd28e27ebc38684631c1e1c04dbf8b9f598097151ccf2` | 131.87 | `9458710ad385` | 50 | ok | linked | 2/2 | 0.6775 | 2 | second hardening round, read-only snapshot (9458710); controls passed; M1 acceptance run |

Every bundle listed carries the labels `single-implementation, non-evaluation` (D11); full label sets are in `index.json`.
