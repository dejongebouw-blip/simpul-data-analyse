-- simpul_raw — dicht schema voor de ruwe extractie (issue 11, SC-9, US-9).
--
-- Dit bestand IS de definitie van het schema. Het beschrijft de database niet
-- achteraf; wat hier staat hoort er te staan, en het bestand controleert dat
-- ook zelf (zie "Driftcontrole" onderaan).
--
-- Waarom die controle er is. Tot 2026-08-29 bestond dit bestand uit
-- `create table if not exists` en verder niets. De vijf tabellen bestónden al,
-- aangemaakt door een eerdere versie, dus elke `create` sloeg stilzwijgend
-- over en het bestand veranderde nooit iets aan een tabeldefinitie. Het draaide
-- foutloos en het loog: `complete` was live een generated column, `id` op
-- `extraction_run` een serial in plaats van een identity, drie tabellen hadden
-- een `default now()` die hier niet stond, en `invoiceable_amount` had live een
-- precisie. Dat kostte drie extractierondes tegen de echte bron. Een
-- vierde ronde legde bloot dat de typen ook niet uit de bron kwamen maar uit
-- de véldnamen: `status_id` heet naar een id en draagt een label, en
-- `project_location` is een object, geen tekst. Beide zijn nagemeten tegen de
-- echte bron voordat ze hier kwamen te staan, niet afgeleid uit hun naam. Een
-- schemabestand dat stil kan afdrijven van de database is geen bron van
-- waarheid, alleen een gerucht. De driftcontrole hieronder maakt het verschil
-- luidruchtig: wijkt de database af, dan faalt dit bestand met de kolom erbij.
--
-- Idempotent: dit bestand mag opnieuw draaien op een database waar het schema
-- al goed staat. Het kan een bestaande, afgedreven tabel niet zelf herstellen —
-- het meldt de afwijking en stopt. Herstellen is een bewuste, aparte handeling
-- (tabel droppen en dit bestand opnieuw draaien, of een migratie schrijven).
--
-- Alleen de secret key (server-side, achter PostgREST met
-- `Content-Profile: simpul_raw`) mag lezen of schrijven. De publishable key
-- (`anon`/`authenticated`) krijgt hier niets: RLS staat aan op elke tabel
-- zonder een enkele policy, dus is elke tabel standaard dicht voor die
-- rollen, en de directe grants worden er bovenop expliciet ingetrokken —
-- ook voor tabellen die hier later bijkomen. De secret key draait als
-- `service_role` en krijgt onderaan expliciet wel `usage` op het schema plus
-- lees- en schrijfrechten op de tabellen; zonder die grants is het schema ook
-- voor de schrijflaag dicht.

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
    fetched_at timestamptz default now()
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
    project_location jsonb,
    status_id text,
    fetched_at timestamptz default now()
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
    fetched_at timestamptz default now()
);

-- `complete` is afgeleid, niet aangeleverd. De database rekent het vinkje uit
-- de twee getallen in dezelfde rij, zodat er nooit een auditregel kan bestaan
-- die zegt "951 van 951 weggeschreven, niet volledig". De voorwaarde staat
-- voluit: zonder een door de bron gemeld totaal is de ronde niet bevestigd, en
-- dat is `false` — niet NULL. Daardoor blijft elke latere query `= false` in
-- plaats van `is not true`, en mist niemand per ongeluk juist de rondes die hij
-- zocht. Wat er misging staat in `note`.
--
-- De schrijflaag stuurt deze kolom dus niet mee; doet ze dat toch, dan weigert
-- PostgREST de rij met 428C9. Zie `simpul_extract/completeness.py`.
create table if not exists simpul_raw.extraction_run (
    id bigint generated always as identity primary key,
    run_id uuid,
    started_at timestamptz default now(),
    finished_at timestamptz,
    entity text,
    rows_stored integer,
    source_total integer,
    complete boolean generated always as (source_total is not null and rows_stored = source_total) stored,
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
revoke all on all sequences in schema simpul_raw from anon, authenticated;
alter default privileges in schema simpul_raw revoke all on tables from anon, authenticated;
alter default privileges in schema simpul_raw revoke all on sequences from anon, authenticated;

-- De secret-key-route juist wel openzetten. `service_role` heeft BYPASSRLS, dus
-- RLS met nul policies sluit hem niet buiten -- maar zonder `usage` op het
-- schema komt hij er evengoed niet in, en zonder table grants evenmin. H2 heeft
-- dat gat aangetoond: de schema-ACL stond op `{postgres=UC/postgres}` en de in
-- deze ronde nieuw aangemaakte `session_cookie` had alleen grants voor
-- `postgres`. De `alter default privileges` hieronder houdt dat gat dicht voor
-- elke tabel die er later bijkomt.
grant usage on schema simpul_raw to service_role;
grant select, insert, update, delete on all tables in schema simpul_raw to service_role;
grant usage, select on all sequences in schema simpul_raw to service_role;
alter default privileges in schema simpul_raw grant select, insert, update, delete on tables to service_role;
alter default privileges in schema simpul_raw grant usage, select on sequences to service_role;

-- ---------------------------------------------------------------------------
-- Driftcontrole
--
-- Vergelijkt wat er werkelijk staat met wat hierboven is verklaard: per kolom
-- de naam, het type, of hij afgeleid is, of hij een identity is en of hij een
-- default heeft. Volgorde doet niet mee -- die zegt niets over gedrag. Bij een
-- verschil faalt dit bestand met de betrokken kolommen in de melding, in plaats
-- van foutloos te draaien en niets te doen.
-- ---------------------------------------------------------------------------
do $$
declare
    verwacht constant text[][] := array[
        -- tabel, kolom, type, afgeleid(s/''), identity(a/''), default(t/f)
        ['customer','id','bigint','','','f'],
        ['customer','customer_number','text','','','f'],
        ['customer','title','text','','','f'],
        ['customer','address','text','','','f'],
        ['customer','zipcode','text','','','f'],
        ['customer','city','text','','','f'],
        ['customer','phone','text','','','f'],
        ['customer','mobile','text','','','f'],
        ['customer','display_status','text','','','f'],
        ['customer','tasks_status','text','','','f'],
        ['customer','url_show','text','','','f'],
        ['customer','email','text','','','f'],
        ['customer','fetched_at','timestamp with time zone','','','t'],
        ['project','id','bigint','','','f'],
        ['project','project_number','text','','','f'],
        ['project','name','text','','','f'],
        ['project','customer_title','text','','','f'],
        ['project','customer_address','text','','','f'],
        ['project','customer_zipcode','text','','','f'],
        ['project','customer_city','text','','','f'],
        ['project','customer_phone','text','','','f'],
        ['project','customer_mobile','text','','','f'],
        ['project','status_id','text','','','f'],
        ['project','url_show','text','','','f'],
        ['project','project_location','jsonb','','','f'],
        ['project','fetched_at','timestamp with time zone','','','t'],
        ['supplier','id','bigint','','','f'],
        ['supplier','name','text','','','f'],
        ['supplier','address','text','','','f'],
        ['supplier','zipcode','text','','','f'],
        ['supplier','city','text','','','f'],
        ['supplier','email','text','','','f'],
        ['supplier','phone','text','','','f'],
        ['supplier','mobile','text','','','f'],
        ['supplier','url_show','text','','','f'],
        ['supplier','text','text','','','f'],
        ['supplier','fetched_at','timestamp with time zone','','','t'],
        ['extraction_run','id','bigint','','a','f'],
        ['extraction_run','run_id','uuid','','','f'],
        ['extraction_run','started_at','timestamp with time zone','','','t'],
        ['extraction_run','finished_at','timestamp with time zone','','','f'],
        ['extraction_run','entity','text','','','f'],
        ['extraction_run','rows_stored','integer','','','f'],
        ['extraction_run','source_total','integer','','','f'],
        ['extraction_run','complete','boolean','s','','f'],
        ['extraction_run','note','text','','','f'],
        ['session_cookie','name','text','','','f'],
        ['session_cookie','value','text','','','f'],
        ['session_cookie','updated_at','timestamp with time zone','','','f']
    ];
    verschil text;
    uitdrukking text;
begin
    with verklaard as (
        select verwacht[i][1] as tbl, verwacht[i][2] as kol,
               verwacht[i][3] as typ, verwacht[i][4] as afgeleid,
               verwacht[i][5] as ident, verwacht[i][6]::boolean as heeft_default
        from generate_subscripts(verwacht, 1) as i
    ),
    aanwezig as (
        select c.relname::text as tbl, a.attname::text as kol,
               format_type(a.atttypid, a.atttypmod) as typ,
               a.attgenerated::text as afgeleid,
               a.attidentity::text as ident,
               a.atthasdef and a.attgenerated = '' as heeft_default
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        join pg_attribute a on a.attrelid = c.oid
        where n.nspname = 'simpul_raw' and c.relkind = 'r'
          and a.attnum > 0 and not a.attisdropped
    )
    select string_agg(regel, e'\n' order by regel) into verschil
    from (
        select format('  ontbreekt of wijkt af: %s.%s %s afgeleid=%L identity=%L default=%s',
                      tbl, kol, typ, afgeleid, ident, heeft_default) as regel
        from (select * from verklaard except select * from aanwezig) as v
        union all
        select format('  onverwacht aanwezig:  %s.%s %s afgeleid=%L identity=%L default=%s',
                      tbl, kol, typ, afgeleid, ident, heeft_default)
        from (select * from aanwezig except select * from verklaard) as a
    ) as regels;

    if verschil is not null then
        raise exception e'simpul_raw wijkt af van db/schema-postgres.sql:\n%', verschil;
    end if;

    -- De afleiding van `complete` apart, want die staat niet in de typen.
    select pg_get_expr(a.attgenerated_expr, a.attrelid) into uitdrukking
    from (
        select att.attrelid, ad.adbin as attgenerated_expr
        from pg_attribute att
        join pg_attrdef ad on ad.adrelid = att.attrelid and ad.adnum = att.attnum
        where att.attrelid = 'simpul_raw.extraction_run'::regclass
          and att.attname = 'complete'
    ) as a;

    if uitdrukking is null
       or position('source_total is not null' in lower(replace(uitdrukking, '(', ''))) = 0
       or position('rows_stored = source_total' in lower(replace(replace(uitdrukking, '(', ''), ')', ''))) = 0
    then
        raise exception 'simpul_raw.extraction_run.complete wordt niet afgeleid zoals verklaard, maar als: %',
                        coalesce(uitdrukking, '<geen afleiding>');
    end if;
end
$$;
