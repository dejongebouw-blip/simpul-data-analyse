FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Vaste uid/gid voor de runtime-gebruiker; geen root in de container.
ARG APP_UID=10001
ARG APP_GID=10001

RUN groupadd --system --gid ${APP_GID} app \
 && useradd --system --uid ${APP_UID} --gid ${APP_GID} \
            --home-dir /home/app --create-home app

WORKDIR /app

# Dependency-laag eerst, zodat rebuilds bij codewijzigingen de cache raken.
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Pakketbron.
COPY simpul_extract/ ./simpul_extract/

RUN chown -R app:app /app

USER app

ENTRYPOINT ["python3", "-m", "simpul_extract"]
