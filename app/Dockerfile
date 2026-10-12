FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 ai && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin ai
WORKDIR /app
COPY store.py service.py ./
COPY static ./static
USER 1000:1000
CMD ["python", "service.py"]
