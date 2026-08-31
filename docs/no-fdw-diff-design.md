# Alternative diff architecture: no postgres_fdw

Written up for later evaluation, not implemented. See the branch/diff/apply
toolset plan for context on why this came up.

**Update:** branching moved from Cloud SQL instance-level (fast-cloning two
separate instances) to database-level (`CREATE DATABASE ... TEMPLATE` for
`base`/`modify` on the *same* instance as SOURCE). That narrows this
problem but doesn't eliminate it: Cloud SQL rejects `postgres_fdw`
connections to the loopback address outright ("connection to the loopback
address is not allowed"), even for an instance connecting to itself - live
testing confirmed this - so the FDW hop still needs the instance's real
private IP, not `localhost`. What the database-level move *did* remove is
the cross-instance/VPC-peering-between-two-different-instances case this
doc was originally about, and the multi-minute clone wait. The
no-FDW/independent-connections alternative below would still remove the
private-IP requirement entirely if that's ever worth doing.

## The constraint that motivates this

Today's diff engine (`src/differ.py`) works over a single connection: `link_dbs`
sets up `postgres_fdw` so the "local" database can see the "remote" database's
tables as if they were local (schema `remote_server_schema`), and
`check_deletions`/`check_insertions`/`check_updates` run a single server-side
`SELECT ... EXCEPT SELECT ...` per table across that FDW link.

The catch: FDW is a **server-to-server** connection. The Postgres server
hosting "local" has to be able to open a direct connection to the server
hosting "remote" - separately from whatever the *client* (our CLI) uses to
reach either of them.

For the branch/diff/apply toolset, "local" and "remote" are two independently
fast-cloned Cloud SQL instances (`modify` and `base`). The CLI reaches both
of them through the Cloud SQL Auth Proxy, which only tunnels the *client's*
connection - it does nothing for `modify`'s Postgres server reaching `base`'s
Postgres server directly. So the current approach requires `base` and
`modify` to have private IP in the same VPC (or otherwise be network-reachable
to each other), which is an extra infrastructure prerequisite on top of what
the CLI itself needs.

## The alternative: two independent connections, diffed client-side

Instead of one FDW-linked connection running a server-side `EXCEPT`, open two
ordinary connections - one to `base`, one to `modify`, both reached the same
way the CLI already reaches everything else (through the Auth Proxy) - and
compute the difference in Python.

Naive version: fetch every row from both sides, diff in memory. Works, but
pulls every column of every row over the wire for tables where almost nothing
changed - wasteful for anything but small tables.

Better version, per table:

1. **Compare key sets cheaply.** Fetch just the primary key columns (plus a
   cheap per-row fingerprint, e.g. `md5(t::text)` computed server-side) from
   both sides:
   ```sql
   SELECT id, md5(t::text) FROM my_table t ORDER BY id;
   ```
   Do this on both connections, and diff the two `{pk: hash}` maps in Python
   with plain set operations:
   - keys only in `modify` → inserts
   - keys only in `base` → deletes
   - keys in both, but with a different hash → candidates for update
   - keys in both with the same hash → unchanged, skip entirely
2. **Only pull full rows where needed.** For insert candidates, fetch the
   full row from `modify`. For update candidates, fetch the full row from
   both sides and diff column-by-column (same logic `check_updates` already
   has). Deletes don't need a row fetch at all - the key is enough.
3. **Batch instead of `.all()`-ing whole tables.** Page through each table by
   primary key range (`WHERE id > :last_id ORDER BY id LIMIT :batch_size`)
   rather than materializing it all at once, so memory stays bounded
   regardless of table size.

## What would change in the code

- `check_deletions` / `check_insertions` / `check_updates` currently take one
  `engine` plus `local_table`/`remote_table` (both reachable through it via
  FDW). They'd need to take **two engines** - one per side - and drive the
  key-set/hash comparison described above instead of a single SQL `EXCEPT`.
- `get_tables` currently reflects one FDW-linked engine and derives both
  `TablePair.prod`/`TablePair.clone` from it. It would need to reflect two
  engines independently and match tables up by name (schema drift handling,
  already a known gap, becomes slightly more visible here since there's no
  single reflect call to notice a mismatch for you).
- `make_diff_patch` / `link_dbs` would no longer need `postgres_fdw`,
  `CREATE SERVER`, `CREATE USER MAPPING`, or the `remote_server_schema`
  plumbing at all - it would just take two `DBConnection`s directly.
- The topological sort / `sort_ops` FK-safe ordering work is unaffected -
  it operates on the resulting `DatabaseOperation` list either way.

## Trade-offs

**For:**
- No `postgres_fdw` extension requirement, no inter-instance networking
  requirement between `base` and `modify` - works purely through the Auth
  Proxy, so it's actually simpler infra-wise for the branch toolset.
- Also just makes the library more generally portable - it would work to
  diff any two reachable Postgres databases, GCP or not, same VPC or not.

**Against:**
- More network round trips: two table scans (one per side) instead of one
  server-side `EXCEPT`, plus a second pass to fetch full rows for the actual
  changes.
- More client-side CPU/memory for the hashing and set-difference work,
  though the batching approach above bounds it.
- A bigger refactor - it touches the signature of every `check_*` function
  and `get_tables`/`make_diff_patch`, not an additive change.

## Suggested next step

Before committing to a rewrite: prototype the key-set/hash approach against
one real, reasonably large table and measure it against the current FDW
`EXCEPT` approach on the same data, to see whether the extra round trips
actually matter in practice for the table sizes this tool will see. If the
performance difference is small, the portability/no-VPC-requirement win
alone is probably enough to justify switching.
