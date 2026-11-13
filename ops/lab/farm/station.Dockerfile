# The Field Station cell (M10) built from THIS checkout. Build context: the autonomy repo root.
FROM python:3.12-slim
COPY core/python /src
RUN pip install --no-cache-dir /src PyYAML && rm -rf /src
RUN mkdir /data && chown 1000 /data
USER 1000
VOLUME /data
CMD ["autonomy", "station", "run", "--config", "/config/station.json"]
