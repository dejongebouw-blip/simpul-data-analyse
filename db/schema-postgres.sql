-- simpul_raw — dicht schema voor de ruwe extractie (issue 11, SC-9, US-9).
--
-- Idempotent: dit bestand mag opnieuw draaien op een database waar het
-- schema al staat. `create schema if not exists`, `create table if not
-- exists` en `drop policy if exists` vóór iedere policy-aanmaak (van welke
-- er hier geen enkele voorkomt) zorgen daarvoor.
--
-- Alleen de secret key (server-side, achter PostgREST met
-- `Content-Profile: simpul_raw`) mag lezen of schrijven. De publishable key
-- (`anon`/`authenticated`) krijgt hier niets: RLS staat aan op elke tabel
-- zonder een enkele policy, dus is elke tabel standaard dicht voor die
-- rollen, en de directe grants worden er bovenop expliciet ingetrokken —
-- ook voor tabellen die hier later bijkomen.

create schema if not exists simpul_raw;

create table if not exists simpul_raw.customer (
    id bigint primary key,
    customer_number text,
    title text,
    address text,
    zipcode text,
    city text,
    phone text,
    mobile text,
    display_status text,
    tasks_status text,
    url_show text,
    email text,
    fetched_at timestamptz
);

create table if not exists simpul_raw.project (
    id bigint primary key,
    project_number text,
    name text,
    customer_title text,
    customer_address text,
    customer_zipcode text,
    customer_city text,
    customer_phone text,
    customer_mobile text,
    url_show text,
    project_location text,
    status_id integer,
    invoiceable_amount numeric,
    fetched_at timestamptz
);

create table if not exists simpul_raw.supplier (
    id bigint primary key,
    name text,
    address text,
    zipcode text,
    city text,
    email text,
    phone text,
    mobile text,
    url_show text,
    text text,
    fetched_at timestamptz
);

create table if not exists simpul_raw.extraction_run (
    id bigint generated always as identity primary key,
    run_id uuid,
    started_at timestamptz,
    finished_at timestamptz,
    entity text,
    rows_stored integer,
    source_total integer,
    complete boolean,
    note text
);

create table if not exists simpul_raw.session_cookie (
    name text primary key,
    value text,
    updated_at timestamptz
);

-- RLS aan op elke tabel, geen policies: standaard dicht voor anon/authenticated.
alter table simpul_raw.customer enable row level security;
alter table simpul_raw.project enable row level security;
alter table simpul_raw.supplier enable row level security;
alter table simpul_raw.extraction_run enable row level security;
alter table simpul_raw.session_cookie enable row level security;

drop policy if exists customer_no_policy on simpul_raw.customer;
drop policy if exists project_no_policy on simpul_raw.project;
drop policy if exists supplier_no_policy on simpul_raw.supplier;
drop policy if exists extraction_run_no_policy on simpul_raw.extraction_run;
drop policy if exists session_cookie_no_policy on simpul_raw.session_cookie;

-- Directe grants intrekken: schema, bestaande tabellen, en toekomstige tabellen.
revoke all on schema simpul_raw from anon, authenticated;
revoke all on all tables in schema simpul_raw from anon, authenticated;
alter default privileges in schema simpul_raw revoke all on tables from anon, authenticated;
