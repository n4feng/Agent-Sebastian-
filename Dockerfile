FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/sebastian/src
RUN groupadd --gid 10001 sebastian \
    && useradd --uid 10001 --gid 10001 --no-create-home sebastian \
    && mkdir -p /var/lib/sebastian/gateway /var/lib/sebastian/keys /run/sebastian /run/sebastian-control \
    && chown -R 10001:10001 /var/lib/sebastian /run/sebastian /run/sebastian-control \
    && chmod 700 /var/lib/sebastian/gateway /var/lib/sebastian/keys \
    && chmod 770 /run/sebastian /run/sebastian-control
COPY src /opt/sebastian/src
USER 10001:10001
WORKDIR /opt/sebastian
ENTRYPOINT ["python", "-m", "sebastian"]
