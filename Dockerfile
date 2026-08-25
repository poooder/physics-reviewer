FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system reviewer \
    && useradd --system --gid reviewer --home-dir /app reviewer

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY physics_reviewer ./physics_reviewer
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /data \
    && chown -R reviewer:reviewer /app /data

EXPOSE 8011

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "physics_reviewer.api:app", "--host", "0.0.0.0", "--port", "8011"]
