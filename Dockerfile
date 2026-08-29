FROM python@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217

WORKDIR /app

# Ongebufferde stdout/stderr: een ronde duurt minuten en moet haar
# voortgang tonen terwijl ze loopt, niet pas als het proces eindigt.
ENV PYTHONUNBUFFERED=1

RUN groupadd --system simpul && useradd --system --gid simpul --create-home simpul

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY simpul_extract/ ./simpul_extract/

USER simpul

ENTRYPOINT ["python3", "-m", "simpul_extract"]
