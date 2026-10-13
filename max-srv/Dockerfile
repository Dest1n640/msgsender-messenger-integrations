FROM python:3.10-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -sSf -o /usr/local/share/ca-certificates/russian_trusted_root_ca.crt \
       https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt \
    && curl -sSf -o /usr/local/share/ca-certificates/russian_trusted_sub_ca.crt \
       https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt \
    && update-ca-certificates \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /var/log /app/data

COPY scripts/ /app/scripts/

ENV MAX_RETRIES=5
ENV PORT=8080

CMD ["python", "scripts/maxsrv.py"]
