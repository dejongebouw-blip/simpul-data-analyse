# simpul-data-analyse

Container-verpakking en ingang voor het extract-pakket `simpul_extract`.
De extractie draait in een Docker-image; op de hostmachine is geen Python
of virtualenv nodig.

## Bouwen

```
docker build -t simpul-extract .
```

## Draaien

De bestemming (Supabase) komt uitsluitend uit de omgeving. Er is geen
`--db`-vlag en geen lokaal databasepad; de container houdt geen staat.

```
docker run --rm \
  -e SUPABASE_URL="https://<project>.supabase.co" \
  -e SUPABASE_SECRET_KEY="<service-role-key>" \
  simpul-extract --delay 1.0
```

Als `SUPABASE_URL` of `SUPABASE_SECRET_KEY` ontbreekt, stopt de ingang met
een niet-nul exit code en een melding die alleen de naam van de ontbrekende
variabele noemt — nooit de waarde.

Voor de help:

```
docker run --rm simpul-extract --help
```

## Tests

Pakket-tests draaien binnen het image. De host-runner bouwt het image en
draait de gevraagde `unittest`-module daarin; zijn exit code is die van
de tests:

```
python3 tests/run_in_image.py tests.test_entrypoint
```

De host-side image-toets gebruikt alleen stdlib:

```
python3 -m unittest tests.test_image
```
