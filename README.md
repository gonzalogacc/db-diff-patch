# db-diff-patch

This tool computes an FK-safe SQL patch between two Postgres databases. It
also provides a `db-differ` CLI.

The CLI does three things. It creates a branch of a database. A branch is
two clones of a source database, named `base` and `modify`. The CLI makes
both clones with `CREATE DATABASE ... TEMPLATE`. The CLI computes the diff
between `base` and `modify`. The CLI applies the resulting patch to a
target database.

The CLI connects to a Postgres server through `--host` and `--port`. It
has no knowledge of GCP. To reach a Cloud SQL instance, open a tunnel to
it first, for example with the Cloud SQL Auth Proxy. Then point
`--host`/`--port` at that tunnel, the same as for any other Postgres
server.

## Quick Start

This is a real, runnable example. It uses project `testproject-235011`
and the test password `MyTestPassword123!`. `create_test_instance.sh`
sets this password. Use your own project ID and password if you set this
up yourself.

The full explanation of each step is in "Verifying the toolset against a
real GCP project" below. That section covers prerequisites and
troubleshooting.

```sh
uv sync

# one-time per project/network
./scripts/enable_private_services_access.sh
./scripts/create_test_instance.sh source-test-instance
# ^ prints the postgres user's password when done

# open one tunnel, reuse it for every command below
cloud-sql-proxy --port 5433 testproject-235011:us-central1:source-test-instance &

# If you do not want to use GCP, start from here
./scripts/seed_database.sh production_database postgres "MyTestPassword123!" 5433

# branch -> edit -> diff
uv run db-differ branch create smoke-test \
  --host 127.0.0.1 --port 5433 \
  --source-database production_database --username postgres --password "MyTestPassword123!"

./scripts/connect_psql.sh smoke_test_modify postgres "MyTestPassword123!" 5433
# => DELETE FROM some_table WHERE id = 1;

# --server-host is the server's own address, not the tunnel address.
# Cloud SQL rejects a loopback connection here, so use the private IP.
PRIVATE_IP=$(gcloud sql instances describe source-test-instance --format=json \
  | jq -r '.ipAddresses[] | select(.type=="PRIVATE") | .ipAddress')
uv run db-differ diff smoke-test \
  --host 127.0.0.1 --port 5433 \
  --server-host "${PRIVATE_IP}" --server-port 5432 \
  --username postgres --password "MyTestPassword123!" \
  -o patch.sql

# Apply back onto production_database on source-test-instance.
# smoke_test_base and smoke_test_modify were cloned from this database,
# so this step promotes the edit into "prod".
uv run db-differ apply patch.sql \
  --host 127.0.0.1 --port 5433 \
  --username postgres --dbname production_database --password "MyTestPassword123!"

# clean up
uv run db-differ branch drop smoke-test \
  --host 127.0.0.1 --port 5433 \
  --username postgres --password "MyTestPassword123!"

# If you are not using GCP, this step is not needed
./scripts/delete_test_instance.sh source-test-instance
kill %1  # stop the cloud-sql-proxy tunnel started above
```

## Connecting to a database

Every command (`branch create`/`list`/`drop`, `diff`, `apply`) takes only
`--host` and `--port`. This is a direct connection to any Postgres server
you can already reach. The CLI never talks to GCP itself.

For a Cloud SQL instance, open a tunnel to it yourself first. For
example: `cloud-sql-proxy --port PORT PROJECT:REGION:INSTANCE &`, as in
the Quick Start above. Then point `--host`/`--port` at
`127.0.0.1:PORT`.

`tests/test_cli_e2e.py` uses the same method. It drives the real CLI
against a disposable docker Postgres container, with no GCP project at
all.

`diff` also has a `--server-host`/`--server-port` pair. Use these when
the server's own address differs from the address your client connects
through. Two examples: a docker container's published port differs from
its internal `5432`, and Cloud SQL rejects `postgres_fdw`'s loopback
self-connection. See `diff()` in `src/cli.py` for exactly where `host`,
`port`, `server_host`, and `server_port` are used.

Each script in `scripts/` documents its own usage in its header comment.
Run a script with no arguments to see its defaults, or open the file to
read the header.

### Spinning up a local Postgres to try it against

`./scripts/start_local_postgres.sh` starts a disposable `postgres:16`
container and seeds it from `seed.sql`. `seed.sql` has the same FK-rich
fixture schema the test suite uses. This gives you something real to
point `branch`/`diff`/`apply` at, with no GCP project needed. The script
prints the connection details and a ready-to-paste `db-differ branch
create` command when it finishes. See the script's header for its full
usage and arguments.

## Verifying the toolset against a real GCP project

The unit tests (`uv run pytest`) exercise `branches.py`'s branch
create/list/drop mechanism, and the full CLI, for real against local
Postgres. They use `pytest-postgresql` and a disposable docker container
for this. None of these tests touch an actual Cloud SQL instance, since
the CLI has no knowledge of GCP. Before you rely on this tool for
anything that matters, walk through the full branch → diff → apply loop
by hand, against a **disposable** project and instance.

Branching works at the database level. A branch is two databases, `base`
and `modify`, created with `CREATE DATABASE ... TEMPLATE` on the same
instance as your source data. These are not separate Cloud SQL instances,
so there is no multi-minute clone wait. The instance still needs a
private IP, though. Cloud SQL rejects a `postgres_fdw` connection to the
loopback address, even from an instance to itself. So `diff`'s FDW hop
between `base` and `modify` must target the instance's real private IP.

### 0. Prerequisites

- A GCP project with the Cloud SQL Admin API enabled, and billing on.
- Private Services Access configured for the VPC you will use. This is a
  one-time, per-network setup. A private-IP Cloud SQL instance depends on
  it:
  ```sh
  ./scripts/enable_private_services_access.sh default
  ```
  This is safe to run again. Skip it if it is already set up for that
  network.
- A Cloud SQL for Postgres instance to use as `SOURCE`. Use a throwaway
  instance. Do not point this at real prod for a first run:
  ```sh
  ./scripts/create_test_instance.sh source-test-instance default
  ```
  This prints the `postgres` user's password when it finishes. Use that
  password for `--username`/`--password` in the steps below.
- The [Cloud SQL Auth Proxy](https://cloud.google.com/sql/docs/postgres/sql-proxy)
  binary (`cloud-sql-proxy`), installed and on `PATH`. `db-differ` itself
  never calls it. You start and stop it yourself.
- `gcloud auth application-default login`, or plain `gcloud auth login`.
  This makes `gcloud sql instances describe` work. The steps below use it
  to fetch the instance's connection name and private IP.
- `jq` on `PATH`. This pulls the private IP out of `gcloud`'s JSON
  output. `describe` returns one resource, not a list, so `gcloud`'s
  `--filter` flag does not apply here.
- `uv sync`, to install this project's dependencies.

### 1. Open a tunnel

`db-differ` only ever connects to `--host`/`--port`. Start the Auth Proxy
yourself once, and reuse it for every command below:

```sh
CONNECTION_NAME=$(gcloud sql instances describe source-test-instance --format="value(connectionName)")
cloud-sql-proxy --port 5433 "${CONNECTION_NAME}" &
```

### 2. Seed the database

`seed_database.sh` creates `production_database` on `SOURCE`, through the
tunnel just opened, if it does not already exist. Then it seeds that
database from `seed.sql` in this repo. `seed.sql` has the same FK-rich
fixture schema the test suite uses. See `schema.md` for details.

```sh
./scripts/seed_database.sh production_database postgres "<password>" 5433
```

Or point `branch create` below at a database you already have on
`SOURCE` instead.

### 3. Create a branch

```sh
uv run db-differ branch create smoke-test \
  --host 127.0.0.1 --port 5433 \
  --source-database production_database \
  --username postgres \
  --password "<password>"
```

This creates `smoke_test_base` (`CREATE DATABASE ... TEMPLATE
production_database`) and `smoke_test_modify` (templated off
`smoke_test_base`) on `source-test-instance`. No new instances are
created, so this step is quick.

```sh
uv run db-differ branch list --host 127.0.0.1 --port 5433 --username postgres --password "<password>"
```

This should now show `smoke_test`, with both database names.

### 4. Make a change on `modify`

Connect to the `modify` database directly, and edit some data. This
simulates the curation work this tool is meant to capture:

```sh
./scripts/connect_psql.sh smoke_test_modify postgres "<password>" 5433
=> DELETE FROM some_table WHERE id = 1;
```

### 5. Diff the branch

`postgres_fdw`'s self-hop needs the instance's private IP, not the
tunnel's loopback address. Fetch the private IP once:

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

Inspect `patch.sql`. It should be a `BEGIN; ... COMMIT;` script. It
should contain just the delete, or whatever you changed, in FK-safe
order.

### 6. Apply the patch back to `production_database`

`smoke_test_base` and `smoke_test_modify` were cloned from
`production_database` on `SOURCE`. So applying the patch back to
`production_database` promotes the edit you made on `modify` back into
the database it came from. `apply` is fully standalone, and reuses the
same tunnel:

```sh
uv run db-differ apply patch.sql \
  --host 127.0.0.1 --port 5433 \
  --username postgres --dbname production_database --password "<password>"
```

Point `--host`/`--dbname` at a different, truly disposable target
instead, if you would rather not change `SOURCE`'s own
`production_database`.

Then check `production_database` on `SOURCE` directly. Confirm it now
matches `modify`'s state for the table or tables you touched.

### 7. Clean up

```sh
uv run db-differ branch drop smoke-test --host 127.0.0.1 --port 5433 --username postgres --password "<password>"
```

This drops `smoke_test_base` and `smoke_test_modify`. This is fast,
since they are just databases. It does not touch `SOURCE` itself. Delete
that too, once you are done with the whole run:

```sh
./scripts/delete_test_instance.sh source-test-instance
kill %1  # stop the cloud-sql-proxy tunnel started in step 1
```

## Internals: manual FDW link (what `link_dbs` automates)

```sql
-- Link database 1 into database 2 using the postgres_fdw extension
\c database2;

CREATE EXTENSION IF NOT EXISTS postgres_fdw;
CREATE SERVER remote_server foreign data wrapper postgres_fdw options (host 'localhost', port '5432', dbname 'database1');
-- The password option is required for non-superusers. Cloud SQL's own
-- "postgres" role is not a true superuser there. link_dbs includes this
-- option automatically whenever the remote connection has a password.
CREATE USER MAPPING FOR user SERVER remote_server OPTIONS (user 'user', password 'password');
CREATE SCHEMA remote_server_schema;
IMPORT FOREIGN SCHEMA public from server remote_server into remote_server_schema;
```
