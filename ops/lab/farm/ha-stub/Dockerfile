FROM python:3.12-alpine
COPY server.py /app/server.py
RUN adduser -D -u 1000 stub
USER stub
EXPOSE 8123
HEALTHCHECK --interval=3s --timeout=2s --retries=15 CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8123/health', timeout=2)" || exit 1
CMD ["python", "-u", "/app/server.py"]
