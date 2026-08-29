# simpul-extract

Containerronde die de Simpul-extractie draait zonder Python op de host.

## Bouwen

```
docker build -t simpul-extract .
```

## Draaien

```
docker run --rm simpul-extract --help
```

## Tests draaien in het image

De host draagt geen third-party Python packages, dus tests draaien in het
image zelf:

```
python3 tests/run_in_image.py tests.test_entrypoint
```
