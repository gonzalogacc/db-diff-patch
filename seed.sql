-- Seed data for the SOURCE Cloud SQL instance used in the real-environment
-- smoke test (see README.md, "Verifying the toolset against a real GCP
-- project"). Mirrors the FK-rich fixture schema used by the test suite
-- (tests/test_functions.py::_populate_db, documented in schema.md), so the
-- same insert/update/delete/FK-ordering scenarios exercised in the unit
-- tests can be walked through by hand against a real database.
--
-- Usage:
--   psql "host=127.0.0.1 port=5433 dbname=YOUR_DB user=YOUR_USER" -f seed.sql
--
-- Safe to re-run: drops and recreates every table it manages.

BEGIN;

DROP TABLE IF EXISTS shipments CASCADE;
DROP TABLE IF EXISTS carriers CASCADE;
DROP TABLE IF EXISTS warehouses CASCADE;
DROP TABLE IF EXISTS cities CASCADE;
DROP TABLE IF EXISTS regions CASCADE;
DROP TABLE IF EXISTS countries CASCADE;
DROP TABLE IF EXISTS editions CASCADE;
DROP TABLE IF EXISTS authors CASCADE;
DROP TABLE IF EXISTS publishers CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS books CASCADE;
DROP TABLE IF EXISTS movies CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS table_with_multiple_pks CASCADE;

-- ---------------------------------------------------------------------
-- Schema
-- ---------------------------------------------------------------------

CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR,
    email VARCHAR
);

CREATE TABLE movies (
    id SERIAL PRIMARY KEY,
    title VARCHAR,
    release_year INTEGER
);

CREATE TABLE books (
    book_uuid UUID PRIMARY KEY,
    title VARCHAR,
    author VARCHAR
);

CREATE TABLE orders (
    user_id INTEGER NOT NULL REFERENCES users (id),
    book_uuid UUID NOT NULL REFERENCES books (book_uuid),
    order_date TIMESTAMP,
    quantity INTEGER,
    PRIMARY KEY (user_id, book_uuid)
);

CREATE TABLE publishers (
    id SERIAL PRIMARY KEY,
    name VARCHAR
);

CREATE TABLE authors (
    id SERIAL PRIMARY KEY,
    name VARCHAR,
    publisher_id INTEGER REFERENCES publishers (id)
);

CREATE TABLE editions (
    id SERIAL PRIMARY KEY,
    title VARCHAR,
    author_id INTEGER REFERENCES authors (id)
);

-- 5-deep FK chain: countries -> regions -> cities -> warehouses -> shipments,
-- with shipments also depending on carriers (a second, independent root),
-- so shipments has in-degree 2 from two branches of different depth.
CREATE TABLE countries (
    id SERIAL PRIMARY KEY,
    name VARCHAR
);

CREATE TABLE regions (
    id SERIAL PRIMARY KEY,
    name VARCHAR,
    country_id INTEGER REFERENCES countries (id)
);

CREATE TABLE cities (
    id SERIAL PRIMARY KEY,
    name VARCHAR,
    region_id INTEGER REFERENCES regions (id)
);

CREATE TABLE warehouses (
    id SERIAL PRIMARY KEY,
    name VARCHAR,
    city_id INTEGER REFERENCES cities (id)
);

CREATE TABLE carriers (
    id SERIAL PRIMARY KEY,
    name VARCHAR
);

CREATE TABLE shipments (
    id SERIAL PRIMARY KEY,
    tracking_number VARCHAR,
    warehouse_id INTEGER REFERENCES warehouses (id),
    carrier_id INTEGER REFERENCES carriers (id)
);

CREATE TABLE table_with_multiple_pks (
    pk1 INTEGER NOT NULL,
    pk2 INTEGER NOT NULL,
    c1 INTEGER,
    c2 INTEGER,
    c3 INTEGER,
    PRIMARY KEY (pk1, pk2)
);

-- ---------------------------------------------------------------------
-- Data
-- ---------------------------------------------------------------------

INSERT INTO users (username, email) VALUES
    ('alice', 'alice@example.com'),
    ('bob', 'bob@example.com'),
    ('charlie', 'charlie@example.com'),
    ('david', 'david@example.com'),
    ('eve', 'eve@example.com'),
    ('frank', 'frank@example.com'),
    ('grace', 'grace@example.com'),
    ('heidi', 'heidi@example.com'),
    ('ivan', 'ivan@example.com'),
    ('judy', 'judy@example.com');

INSERT INTO movies (title, release_year) VALUES
    ('Movie 1', 2001),
    ('Movie 2', 2002),
    ('Movie 3', 2003);

INSERT INTO books (book_uuid, title, author) VALUES
    ('11111111-1111-1111-1111-111111111111', 'Book 1', 'Author 1'),
    ('22222222-2222-2222-2222-222222222222', 'Book 2', 'Author 2'),
    ('33333333-3333-3333-3333-333333333333', 'Book 3', 'Author 3');

INSERT INTO orders (user_id, book_uuid, order_date, quantity) VALUES
    (1, '11111111-1111-1111-1111-111111111111', '2024-01-01', 1),
    (2, '22222222-2222-2222-2222-222222222222', '2024-01-02', 2),
    (3, '33333333-3333-3333-3333-333333333333', '2024-01-03', 3);

INSERT INTO publishers (id, name) VALUES
    (1, 'Publisher 1'),
    (2, 'Publisher 2');

INSERT INTO authors (id, name, publisher_id) VALUES
    (1, 'Author 1', 1),
    (2, 'Author 2', 1),
    (3, 'Author 3', 2);

INSERT INTO editions (title, author_id) VALUES
    ('Edition 1', 1),
    ('Edition 2', 2),
    ('Edition 3', 3);

INSERT INTO countries (id, name) VALUES
    (1, 'Country 1'),
    (2, 'Country 2');

INSERT INTO regions (id, name, country_id) VALUES
    (1, 'Region 1', 1),
    (2, 'Region 2', 2);

INSERT INTO cities (id, name, region_id) VALUES
    (1, 'City 1', 1),
    (2, 'City 2', 2);

INSERT INTO warehouses (id, name, city_id) VALUES
    (1, 'Warehouse 1', 1),
    (2, 'Warehouse 2', 2);

INSERT INTO carriers (id, name) VALUES
    (1, 'Carrier 1'),
    (2, 'Carrier 2');

INSERT INTO shipments (tracking_number, warehouse_id, carrier_id) VALUES
    ('TRACK-001', 1, 1),
    ('TRACK-002', 2, 2),
    ('TRACK-003', 1, 2);

INSERT INTO table_with_multiple_pks (pk1, pk2, c1, c2, c3) VALUES
    (1, 11, 111, 1111, 11111),
    (2, 22, 222, 2222, 22222),
    (3, 33, 333, 3333, 33333);

COMMIT;
