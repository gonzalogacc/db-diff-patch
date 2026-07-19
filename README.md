# db-diff-patch

```sql
-- Link database 1 into database 2 usinf postgres_fdw extension
\c database2;

CREATE EXTENSION IF NOT EXISTS postgres_fdw;
CREATE SERVER remote_server foreign data wrapper postgres_fdw options (host 'localhost', port '5432', dbname 'database1');
CREATE USER MAPPING FOR user SERVER remote_server OPTIONS (user 'user');
CREATE SCHEMA remote_server_schema;
IMPORT FOREIGN SCHEMA public from server remote_server into remote_server_schema;
```