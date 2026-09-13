# db-diff-patch

Computes an FK-safe SQL patch between two Postgres databases, and a
`db-differ` CLI that branches a database (base + modify clones via
`CREATE DATABASE ... TEMPLATE`), diffs the branch, and applies the
resulting patch to a target database. The CLI only ever speaks plain
`--host`/`--port` Postgres - it has no GCP awareness at all. To reach a
Cloud SQL instance, open a tunnel to it yourself first (e.g. the Cloud SQL
Auth Proxy) and point `--host`/`--port` at that tunnel, same as any other
reachable Postgres server.

## Quick Start

Real, runnable example against project `testproject-235011`, using the
throwaway test password `MyTestPassword123!` that `create_test_instance.sh`
sets - swap in your own project id and password if you're setting this up
fresh. Full explanation of every step (why, prerequisites, troubleshooting)
is in "Verifying the toolset against a real GCP project" below.

```sh
uv sync

# one-time per project/network
./scripts/enable_private_services_access.sh
./scripts/create_test_instance.sh source-test-instance
# ^ prints the postgres user's password when done

# open one tunnel, reuse it for every command below
cloud-sql-proxy --port 5433 testproject-235011:us-central1:source-test-instance &

# If you don't want to user GCP start from here
./scripts/seed_database.sh production_database postgres "MyTestPassword123!" 5433

# branch -> edit -> diff
uv run db-differ branch create smoke-test \
  --host 127.0.0.1 --port 5433 \
  --source-database production_database --username postgres --password "MyTestPassword123!"

./scripts/connect_psql.sh smoke_test_modify postgres "MyTestPassword123!" 5433
# => DELETE FROM some_table WHERE id = 1;

# --server-host: postgres_fdw's own view of the server for its self-hop -
# Cloud SQL rejects loopback there, so it needs the instance's private IP
PRIVATE_IP=$(gcloud sql instances describe source-test-instance --format=json \
  | jq -r '.ipAddresses[] | select(.type=="PRIVATE") | .ipAddress')
uv run db-differ diff smoke-test \
  --host 127.0.0.1 --port 5433 \
  --server-host "${PRIVATE_IP}" --server-port 5432 \
  --username postgres --password "MyTestPassword123!" \
  -o patch.sql

# apply back onto production_database on source-test-instance - the
# database smoke_test_base/modify were cloned from, so this is effectively "prod"
uv run db-differ apply patch.sql \
  --host 127.0.0.1 --port 5433 \
  --username postgres --dbname production_database --password "MyTestPassword123!"

# clean up
uv run db-differ branch drop smoke-test \
  --host 127.0.0.1 --port 5433 \
  --username postgres --password "MyTestPassword123!"

# If you are not using GCP this is not needed
./scripts/delete_test_instance.sh source-test-instance
kill %1  # stop the cloud-sql-proxy tunnel started above
```

## Scripts

Everything below lives in `scripts/`, runs from the repo root, and takes
plain positional arguments (no flags) - run one with no arguments to use its
defaults, shown below.

| Script | Usage | What it does |
|---|---|---|
| `enable_private_services_access.sh` | `[NETWORK=default]` | One-time, per-network setup that `create_test_instance.sh` depends on for its private IP. Safe to re-run. See the script's header. |
| `create_test_instance.sh` | `[INSTANCE_NAME=test-pg-instance] [NETWORK=default]` | Creates a disposable Cloud SQL Postgres instance with both a public and a private IP. Prints the `postgres` user's password when done. |
| `seed_database.sh` | `[DBNAME=production_database] [DBUSER=postgres] [PASSWORD] [PORT=5433] [SQL_FILE=seed.sql]` | Creates DBNAME if it doesn't exist yet, then runs a SQL file against it - a Postgres server already reachable at `127.0.0.1:PORT` (e.g. a tunnel you started yourself). |
| `connect_psql.sh` | `[DBNAME=postgres] [DBUSER=postgres] [PASSWORD] [PORT=5433]` | Same connection, but drops you into an interactive `psql` session instead. |
| `delete_test_instance.sh` | `[INSTANCE_NAME=test-pg-instance]` | Deletes an instance and its disk. No undo. |

`seed_database.sh` and `connect_psql.sh` don't manage a tunnel themselves -
open one yourself first (see "Connecting to a database" below) and pass its
port. This is also why they no longer take `INSTANCE_NAME`: they only ever
talk to `127.0.0.1:PORT`, whatever that happens to be pointed at.

**These are positional, not flags** - to set `PASSWORD` you must also pass
`DBNAME` and `DBUSER` before it (even if they're just the defaults), or it
lands in the wrong slot. `seed_database.sh production_database
"mypassword"` puts `"mypassword"` into `DBUSER`, not `PASSWORD` - use
`seed_database.sh production_database postgres "mypassword"`.

`create_test_instance.sh`/`delete_test_instance.sh`/
`enable_private_services_access.sh` act on whatever project your `gcloud`
config currently points at - run `gcloud config set project YOUR_PROJECT`
first if needed.

## Connecting to a database

Every command (`branch create`/`list`/`drop`, `diff`, `apply`) takes just
`--host`/`--port` - a direct connection to any already-reachable Postgres
server. The CLI never talks to GCP itself; for a Cloud SQL instance, open a
tunnel to it yourself (e.g. `cloud-sql-proxy --port PORT
PROJECT:REGION:INSTANCE &`, as in the Quick Start above) and point
`--host`/`--port` at `127.0.0.1:PORT`. This is also how
`tests/test_cli_e2e.py` drives the real CLI against a disposable docker
Postgres container for testing, without any GCP project at all.

For `diff` specifically, if the target's server-facing address differs
from what the client connects through (e.g. a docker container's published
port, or a Cloud SQL instance's loopback address rejected by
`postgres_fdw`'s server-to-server hop), pass `--server-host`/`--server-port`
to override what that hop should use - see `diff()` in `src/cli.py` for
where `host`/`port` and `server_host`/`server_port` are applied.

### Spinning up a local Postgres to try it against

`./scripts/start_local_postgres.sh [DBNAME=postgres] [PORT=5433] [SQL_FILE=seed.sql]`
starts a disposable `postgres:16` container (`docker run --rm -d`, no named
volume) and seeds it from `seed.sql` - the same FK-rich fixture schema the
test suite uses - so you have something real to point `branch`/`diff`/`apply`
at without any GCP project. It prints the connection details and a
ready-to-paste `db-differ branch create` command when done. Needs `docker`
and `psql` on `PATH`. Stop and remove everything with `docker stop
<container name>` (printed at the end).

## Verifying the toolset against a real GCP project

The unit tests (`uv run pytest`) exercise `branches.py`'s branch
create/list/drop mechanism and the full CLI for real against local Postgres
(via `pytest-postgresql` and a disposable docker container) - but none of
that touches an actual Cloud SQL instance, since the CLI no longer knows
GCP exists. Before relying on this against anything that matters, walk
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
- The [Cloud SQL Auth Proxy](https://cloud.google.com/sql/docs/postgres/sql-proxy)
  binary (`cloud-sql-proxy`) installed and on `PATH` - `db-differ` itself
  never calls it; you start and stop it yourself.
- `gcloud auth application-default login` (or plain `gcloud auth login`) so
  `gcloud sql instances describe` works, used below to fetch the instance's
  connection name and private IP.
- `jq` on `PATH`, to pull the private IP out of `gcloud`'s JSON output
  (`describe` returns one resource, not a list, so `gcloud`'s own
  `--filter` flag doesn't apply here).
- `uv sync` to install this project's dependencies.

### 1. Open a tunnel

`db-differ` only ever connects to `--host`/`--port`, so start the Auth
Proxy yourself once and reuse it for every command below:

```sh
CONNECTION_NAME=$(gcloud sql instances describe source-test-instance --format="value(connectionName)")
cloud-sql-proxy --port 5433 "${CONNECTION_NAME}" &
```

### 2. Seed the database

`seed_database.sh` creates `production_database` on `SOURCE` (through the
tunnel just opened) if it doesn't already exist, then seeds it from
`seed.sql` in this repo - the same FK-rich fixture schema the test suite
uses (see `schema.md`):

```sh
./scripts/seed_database.sh production_database postgres "<password>" 5433
```

(Or point `branch create` below at whatever database you already have on
`SOURCE` instead.)

### 3. Create a branch

```sh
uv run db-differ branch create smoke-test \
  --host 127.0.0.1 --port 5433 \
  --source-database production_database \
  --username postgres \
  --password "<password>"
```

Creates `smoke_test_base` (`CREATE DATABASE ... TEMPLATE production_database`)
and `smoke_test_modify` (templated off `smoke_test_base`) on
`source-test-instance`. No new instances, so this should be quick.

```sh
uv run db-differ branch list --host 127.0.0.1 --port 5433 --username postgres --password "<password>"
```

should now show `smoke_test` with both database names.

### 4. Make a change on `modify`

Connect to the `modify` database directly and edit some data, to simulate
the curation work this tool is meant to capture:

```sh
./scripts/connect_psql.sh smoke_test_modify postgres "<password>" 5433
=> DELETE FROM some_table WHERE id = 1;
```

### 5. Diff the branch

`postgres_fdw`'s self-hop needs the instance's private IP, not the tunnel's
loopback address - fetch it once:

```sh
PRIVATE_IP=$(gcloud sql instances describe source-test-instance --format=json \
  | jq -r '.ipAddresses[] | select(.type=="PRIVATE") | .ipAddress')

uv run db-differ diff smoke-test \
  --host 127.0.0.1 --port 5433 \
  --server-host "${PRIVATE_IP}" --server-port 5432 \
  --username postgres \
  --password "<password>" \
  -o patch.sql
```

Inspect `patch.sql` - it should be a `BEGIN; ... COMMIT;` script containing
just the delete (or whatever you changed), in FK-safe order.

### 6. Apply the patch back to `production_database`

`smoke_test_base`/`smoke_test_modify` were cloned from `production_database`
on `SOURCE`, so applying the patch back to `production_database` there is
effectively "promoting" the edit you made on `modify` back into the
database it came from - `apply` is fully standalone and reuses the same
tunnel:

```sh
uv run db-differ apply patch.sql \
  --host 127.0.0.1 --port 5433 \
  --username postgres --dbname production_database --password "<password>"
```

(Point `--host`/`--dbname` at a different, truly disposable target instead
if you'd rather not mutate `SOURCE`'s own `production_database`.)

Then check `production_database` on `SOURCE` directly and confirm it now
matches `modify`'s state for the table(s) you touched.

### 7. Clean up

```sh
uv run db-differ branch drop smoke-test --host 127.0.0.1 --port 5433 --username postgres --password "<password>"
```

drops `smoke_test_base`/`smoke_test_modify` (fast - they're just
databases). It doesn't touch `SOURCE` itself - delete that too once you're
done with the whole run:

```sh
./scripts/delete_test_instance.sh source-test-instance
kill %1  # stop the cloud-sql-proxy tunnel started in step 1
```

## Internals: manual FDW link (what `link_dbs` automates)

```sql
-- Link database 1 into database 2 usinf postgres_fdw extension
\c database2;

CREATE EXTENSION IF NOT EXISTS postgres_fdw;
CREATE SERVER remote_server foreign data wrapper postgres_fdw options (host 'localhost', port '5432', dbname 'database1');
-- the password option is required for non-superusers (Cloud SQL's own
-- "postgres" role isn't a true superuser there) - link_dbs includes it
-- automatically whenever the remote connection has a password.
CREATE USER MAPPING FOR user SERVER remote_server OPTIONS (user 'user', password 'password');
CREATE SCHEMA remote_server_schema;
IMPORT FOREIGN SCHEMA public from server remote_server into remote_server_schema;
```
