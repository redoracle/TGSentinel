ARG APP_VERSION=dev
ARG BUILD_DATE
ARG GIT_COMMIT

FROM python:3.14-slim AS metadata
ARG APP_VERSION
ARG BUILD_DATE
ARG GIT_COMMIT

RUN apt-get update && apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /tmp
COPY .git /tmp/.git

RUN build_date="${BUILD_DATE:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}" && \
    git_commit="${GIT_COMMIT:-$(git --git-dir=/tmp/.git rev-parse --short=10 HEAD)}" && \
    cat <<EOF > /build_info.json
{"app_version":"${APP_VERSION}","build_date":"${build_date}","git_commit":"${git_commit}"}
EOF

FROM python:3.14-slim
ARG APP_VERSION
ARG BUILD_DATE
ARG GIT_COMMIT

ENV POETRY_VIRTUALENVS_CREATE=false \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

COPY src /app/src
# COPY config /app/config
COPY ui /app/ui
COPY tools /app/tools
COPY README.md /app/README.md
COPY --from=metadata /build_info.json /app/build_info.json

# create data dir for session/db
RUN mkdir -p /app/data
ENV PYTHONPATH=/app/src

# Copy entrypoint
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Expose UI port (optional service)
EXPOSE 5000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "tgsentinel.main"]
