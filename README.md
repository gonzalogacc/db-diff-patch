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

This is a real, runnable example. It can use a gcp project and the test password `MyTestPassword123!`. `create_test_instance.sh` sets this password. Use your own project ID and password if you set this
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

Every command (`branch create`/`list`/`drop`, `diff`, `apply`) takes `--host` and `--port`. This is a direct connection to a Postgres server.

For a Cloud SQL instance, open a tunnel to it yourself first. For example: `cloud-sql-proxy --port PORT PROJECT:REGION:INSTANCE &`, as in the Quick Start above. Then point `--host`/`--port` at `127.0.0.1:PORT`.

`diff` also has a `--server-host`/`--server-port` pair. Use these when
the server's own address differs from the address your client connects
through. Two examples: a docker container's published port differs from
its internal `5432`, and Cloud SQL rejects `postgres_fdw`'s loopback
self-connection. See `diff()` in `src/cli.py` for exactly where `host`,
`port`, `server_host`, and `server_port` are used.

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
