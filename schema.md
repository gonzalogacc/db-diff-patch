# Test fixture schema

Tables defined in `tests/test_functions.py::_populate_db` (and mirrored in
`seed.sql`). The test suite uses this schema to check FK-safe ordering,
primary-key handling, and diff correctness.

```mermaid
erDiagram
    USERS {
        int id PK
        string username
        string email
    }
    BOOKS {
        uuid book_uuid PK
        string title
        string author
    }
    ORDERS {
        int user_id PK_FK
        uuid book_uuid PK_FK
        datetime order_date
        int quantity
    }
    MOVIES {
        int id PK
        string title
        int release_year
    }
    TABLE_WITH_MULTIPLE_PKS {
        int pk1 PK
        int pk2 PK
        int c1
        int c2
        int c3
    }
    TABLE_WITHOUT_PK {
        int value
    }
    PUBLISHERS {
        int id PK
        string name
    }
    AUTHORS {
        int id PK
        string name
        int publisher_id FK
    }
    EDITIONS {
        int id PK
        string title
        int author_id FK
    }
    COUNTRIES {
        int id PK
        string name
    }
    REGIONS {
        int id PK
        string name
        int country_id FK
    }
    CITIES {
        int id PK
        string name
        int region_id FK
    }
    WAREHOUSES {
        int id PK
        string name
        int city_id FK
    }
    CARRIERS {
        int id PK
        string name
    }
    SHIPMENTS {
        int id PK
        string tracking_number
        int warehouse_id FK
        int carrier_id FK
    }
    EMPLOYEES {
        int id PK
        string name
        int manager_id FK
    }

    USERS ||--o{ ORDERS : "user_id"
    BOOKS ||--o{ ORDERS : "book_uuid"
    PUBLISHERS ||--o{ AUTHORS : "publisher_id"
    AUTHORS ||--o{ EDITIONS : "author_id"
    COUNTRIES ||--o{ REGIONS : "country_id"
    REGIONS ||--o{ CITIES : "region_id"
    CITIES ||--o{ WAREHOUSES : "city_id"
    WAREHOUSES ||--o{ SHIPMENTS : "warehouse_id"
    CARRIERS ||--o{ SHIPMENTS : "carrier_id"
    EMPLOYEES ||--o{ EMPLOYEES : "manager_id"
```

- `orders` has a depth-1 fan-in on two parents at once (`users` and `books`).
- `publishers -> authors -> editions` is a linear 3-deep FK chain.
- `publishers -> authors` alone is the 2-deep case, sharing tables with the chain above.
- `countries -> regions -> cities -> warehouses -> shipments` is a linear 5-deep FK chain.
- `shipments` also depends directly on `carriers` (an independent root table), so it has in-degree 2 from branches of different depth (depth-5 via warehouses, depth-1 via carriers) — a fan-in case for topo-sort testing.
- `employees.manager_id -> employees.id` is a self-referencing FK - a table-level topological sort has nothing meaningful to say about "employees before employees", so this edge is excluded from the table dependency graph rather than treated as a cycle (see `_build_table_graph` in `src/differ.py`).
- `movies` and `table_with_multiple_pks` carry no FKs (control cases).
- `table_without_pk` has no primary key at all - it exercises `check_deletions`/`check_insertions`/`check_updates` raising `NoPrimaryKeyError`, and `make_diff_patch` skipping it with a warning instead of aborting the whole diff.
