# db-diff-patch

This tool computes a SQL patch between two Postgres databases. It
also provides a `db-differ` CLI.

The CLI does three things:
- It creates a branch of a database. A branch is
two clones of a source database, named `base` and `modify`. The CLI makes
both clones with `CREATE DATABASE ... TEMPLATE`. 
- Computes the diff between `base` and `modify`. 
- Applies the resulting patch to a target database.

## Quick Start

Two real, runnable examples. Use the local one to try the tool with no
GCP project at all. Use the GCP one against a real Cloud SQL instance.

### Local database

```sh
uv sync

./scripts/start_local_postgres.sh production_database 5433
# ^ starts a disposable local Postgres container and seeds production_database

# branch -> edit -> diff
uv run db-differ branch create smoke-test \
  --host 127.0.0.1 --port 5433 \
  --source-database production_database --username postgres --password postgres

./scripts/connect_psql.sh smoke_test_modify postgres postgres 5433
# => DELETE FROM some_table WHERE id = 1;

# --server-host/--server-port: the container publishes port 5433 for your
# client, but Postgres inside the container reaches itself at port 5432.
uv run db-differ diff smoke-test \
  --host 127.0.0.1 --port 5433 \
  --server-host localhost --server-port 5432 \
  --username postgres --password postgres \
  -o patch.sql

# apply back onto production_database - smoke_test_base and
# smoke_test_modify were cloned from it, so this promotes the edit into "prod"
uv run db-differ apply patch.sql \
  --host 127.0.0.1 --port 5433 \
  --username postgres --dbname production_database --password postgres

# clean up
uv run db-differ branch drop smoke-test \
  --host 127.0.0.1 --port 5433 \
  --username postgres --password postgres
docker stop <container name>  # printed by start_local_postgres.sh
```

### GCP (Cloud SQL)

Real, runnable example against project `testproject-235011`, using the
throwaway test password `MyTestPassword123!` that `create_test_instance.sh`
sets. Use your own project ID and password if you set this up yourself.

```sh
uv sync

# one-time per project/network
./scripts/enable_private_services_access.sh
./scripts/create_test_instance.sh source-test-instance
# ^ prints the postgres user's password when done

# open one tunnel, reuse it for every command below
cloud-sql-proxy --port 5433 testproject-235011:us-central1:source-test-instance &

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

# apply back onto production_database on source-test-instance - it is
# where smoke_test_base and smoke_test_modify were cloned from, so this
# promotes the edit into "prod"
uv run db-differ apply patch.sql \
  --host 127.0.0.1 --port 5433 \
  --username postgres --dbname production_database --password "MyTestPassword123!"

# clean up
uv run db-differ branch drop smoke-test \
  --host 127.0.0.1 --port 5433 \
  --username postgres --password "MyTestPassword123!"
./scripts/delete_test_instance.sh source-test-instance
kill %1  # stop the cloud-sql-proxy tunnel started above
```

## Connecting to a database

Every command (`branch create`/`list`/`drop`, `diff`, `apply`) takes `--host` and `--port`. This is a direct connection to a Postgres server.

For a Cloud SQL instance, open a tunnel to it yourself first. For example: `cloud-sql-proxy --port PORT PROJECT:REGION:INSTANCE &`, as in the Quick Start above. Then point `--host`/`--port` at `127.0.0.1:PORT`.

`diff` connects to two different addresses.

`--host`/`--port` is the address your client uses to run SQL against the
Postgres server. This is a normal, direct connection.

`--server-host`/`--server-port` is the address the Postgres server itself
uses to reach the other database. `diff` links `base` and `modify`
together with `postgres_fdw`. `postgres_fdw` opens its own connection,
from inside the Postgres server process, to whatever address you give it.
This can be a different address than the one your client used.

Two examples:
- A docker container publishes a port for your client. A process inside
  the container reaches itself through a different address, for example
  `localhost:5432`.
- A Cloud SQL instance rejects a loopback connection for `postgres_fdw`,
  even from an instance to itself. Use the instance's private IP instead.

`--server-host`/`--server-port` default to `--host`/`--port`. Set them
only when the two addresses differ. See `diff()` in `src/cli.py` for
where each address is used.

### Spinning up a local Postgres to try it against

`./scripts/start_local_postgres.sh` starts a disposable `postgres:16`
container and seeds it from `seed.sql`. `seed.sql` has the same FK-rich
fixture schema the test suite uses. This gives you something real to
point `branch`/`diff`/`apply` at, with no GCP project needed. The script
prints the connection details and a ready-to-paste `db-differ branch
create` command when it finishes. See the script's header for its full
usage and arguments.



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
