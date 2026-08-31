# db-diff-patch

Computes an FK-safe SQL patch between two Postgres databases, and (for GCP)
a `db-differ` CLI that branches a database on a Cloud SQL instance, diffs
the branch, and applies the resulting patch to a target database.

## Manual FDW link (what `link_dbs` automates)

```sql
-- Link database 1 into database 2 usinf postgres_fdw extension
\c database2;

CREATE EXTENSION IF NOT EXISTS postgres_fdw;
CREATE SERVER remote_server foreign data wrapper postgres_fdw options (host 'localhost', port '5432', dbname 'database1');
CREATE USER MAPPING FOR user SERVER remote_server OPTIONS (user 'user');
CREATE SCHEMA remote_server_schema;
IMPORT FOREIGN SCHEMA public from server remote_server into remote_server_schema;
```

## Scripts

Everything below lives in `scripts/`, runs from the repo root, and takes
plain positional arguments (no flags) - run one with no arguments to use its
defaults, shown below.

| Script | Usage | What it does |
|---|---|---|
| `enable_private_services_access.sh` | `[NETWORK=default]` | One-time, per-network setup that `create_test_instance.sh` depends on for its private IP. Safe to re-run. See the script's header. |
| `create_test_instance.sh` | `[INSTANCE_NAME=test-pg-instance] [NETWORK=default]` | Creates a disposable Cloud SQL Postgres instance with both a public and a private IP. Prints the `postgres` user's password when done. |
| `seed_database.sh` | `[INSTANCE_NAME] [DBNAME=postgres] [DBUSER=postgres] [PASSWORD] [PORT] [SQL_FILE=seed.sql]` | Tunnels through the Auth Proxy and runs a SQL file against the instance non-interactively. |
| `connect_psql.sh` | `[INSTANCE_NAME] [DBNAME=postgres] [DBUSER=postgres] [PASSWORD] [PORT]` | Same tunnel, but drops you into an interactive `psql` session instead. |
| `delete_test_instance.sh` | `[INSTANCE_NAME=test-pg-instance]` | Deletes an instance and its disk. No undo. |

`PORT` defaults to a freshly picked free port each run (not a fixed one),
so a leftover proxy process from an earlier run can't make a new one
silently connect to the wrong (stale) tunnel.

**These are positional, not flags** - to set `PASSWORD` you must also pass
`DBNAME` and `DBUSER` before it (even if they're just the defaults), or it
lands in the wrong slot. `seed_database.sh source-test-instance
"mypassword"` puts `"mypassword"` into `DBNAME`, not `PASSWORD` - use
`seed_database.sh source-test-instance postgres postgres "mypassword"`.

All of them act on whatever project your `gcloud` config currently points
at - run `gcloud config set project YOUR_PROJECT` first if needed.
`seed_database.sh` and `connect_psql.sh` also need the `cloud-sql-proxy`
binary on `PATH`, and both stop their tunnel automatically when they exit.

Typical order for a fresh project:

```sh
./scripts/enable_private_services_access.sh          # once per network
./scripts/create_test_instance.sh source-test-instance
./scripts/seed_database.sh source-test-instance postgres postgres "<password>"
./scripts/connect_psql.sh source-test-instance postgres postgres "<password>" # whenever you want to poke at it by hand
./scripts/delete_test_instance.sh source-test-instance # when you're done
```

## Verifying the toolset against a real GCP project

The unit tests (`uv run pytest`) mock every GCP/Auth Proxy call for
`gcp_admin.py`/`auth_proxy.py`, and exercise `branches.py`'s branch
create/list/drop mechanism for real against local Postgres (via
`pytest-postgresql`) - but none of that touches an actual Cloud SQL
instance. Before relying on this against anything that matters, walk
through the full branch → diff → apply loop by hand against a
**disposable** project/instance.

Branching works at the database level: a branch is two databases (`base`,
`modify`) created with `CREATE DATABASE ... TEMPLATE` *on the same
instance* as your source data - not separate Cloud SQL instances, so no
multi-minute clone waits. It still needs a private IP on that instance
though: Cloud SQL rejects `postgres_fdw` connecting to the loopback address
outright, even for an instance connecting to itself, so `diff`'s FDW hop
between `base` and `modify` has to target the instance's real private IP.

### 0. Prerequisites

- A GCP project with the Cloud SQL Admin API enabled and billing on.
- Private Services Access configured for the VPC you'll use (a one-time,
  per-network setup that a private-IP Cloud SQL instance depends on):
  ```sh
  ./scripts/enable_private_services_access.sh default
  ```
  Safe to re-run. Skip this if it's already set up for that network.
- A Cloud SQL for Postgres instance to use as `SOURCE` - a throwaway
  instance, don't point this at real prod for a first run:
  ```sh
  ./scripts/create_test_instance.sh source-test-instance default
  ```
  It prints the `postgres` user's password when done - that's your
  `--username`/password for the steps below.
- `seed.sql` in this repo creates the same FK-rich fixture schema the test
  suite uses (see `schema.md`) and seeds it with sample rows - run it once
  against `SOURCE` (or point `branch create` at whatever database you
  already have there instead):
  ```sh
  ./scripts/seed_database.sh source-test-instance postgres postgres "<password>"
  ```
- The [Cloud SQL Auth Proxy](https://cloud.google.com/sql/docs/postgres/sql-proxy)
  binary (`cloud-sql-proxy`) installed and on `PATH`.
- `gcloud auth application-default login` run locally (Application Default
  Credentials - what `google.auth.default()` picks up).
- `uv sync` to install this project's dependencies.

### 1. Sanity-check connectivity

```sh
uv run db-differ list-databases --project YOUR_PROJECT
```

Confirms Admin API auth works and shows `source-test-instance` in the list.

### 2. Create a branch

```sh
uv run db-differ branch create smoke-test \
  --project YOUR_PROJECT \
  --instance source-test-instance \
  --source-database postgres \
  --username postgres \
  --password "<password>"
```

Creates `smoke_test_base` (`CREATE DATABASE ... TEMPLATE postgres`) and
`smoke_test_modify` (templated off `smoke_test_base`) on
`source-test-instance`. No new instances, so this should be quick.

```sh
uv run db-differ branch list --project YOUR_PROJECT --instance source-test-instance --username postgres --password "<password>"
```

should now show `smoke_test` with both database names.

### 3. Make a change on `modify`

Connect to the `modify` database directly and edit some data, to simulate
the curation work this tool is meant to capture:

```sh
./scripts/connect_psql.sh source-test-instance smoke_test_modify postgres "<password>"
=> DELETE FROM some_table WHERE id = 1;
```

### 4. Diff the branch

```sh
uv run db-differ diff smoke-test \
  --project YOUR_PROJECT \
  --instance source-test-instance \
  --username postgres \
  --password "<password>" \
  -o patch.sql
```

Inspect `patch.sql` - it should be a `BEGIN; ... COMMIT;` script containing
just the delete (or whatever you changed), in FK-safe order.

### 5. Apply the patch to a disposable target

**Don't point this at real prod on a first run.** Create one more scratch
instance (or reuse any disposable database you don't mind mutating) to
play the role of "prod" for this test, tunnel to it, and apply - `apply` is
fully standalone and doesn't care whether the target is on GCP at all:

```sh
cloud-sql-proxy --port 5434 YOUR_PROJECT:REGION:some-disposable-instance &
uv run db-differ apply patch.sql \
  --host 127.0.0.1 --port 5434 \
  --username YOUR_USER --dbname YOUR_DB
```

Then check the target database directly and confirm it now matches
`modify`'s state for the table(s) you touched.

### 6. Clean up

```sh
uv run db-differ branch drop smoke-test --project YOUR_PROJECT --instance source-test-instance --username postgres --password "<password>"
```

drops `smoke_test_base`/`smoke_test_modify` (fast - they're just
databases). It doesn't touch `SOURCE` itself or the scratch "prod" instance
from step 5 - delete those too once you're done with the whole run:

```sh
./scripts/delete_test_instance.sh source-test-instance
./scripts/delete_test_instance.sh some-disposable-instance
```

and stop any `cloud-sql-proxy` processes you started by hand.
