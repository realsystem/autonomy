# The lab authority: owner keys, a signed registry and a signed policy bundle (make_authority.py),
# made with autonomy-core from THIS checkout. Build context: the autonomy repo root.
FROM python:3.12-slim
COPY core/python /src
RUN pip install --no-cache-dir /src && rm -rf /src
COPY ops/lab/authority/make_authority.py /usr/local/bin/make_authority.py
ENTRYPOINT ["python", "/usr/local/bin/make_authority.py"]
