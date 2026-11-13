ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12
FROM ${BUILD_FROM}
# Pin a release tag of autonomy-core; the default builds from main for development.
ARG AUTONOMY_REF=main
RUN pip install --no-cache-dir "autonomy-core @ git+https://github.com/realsystem/autonomy.git@${AUTONOMY_REF}#subdirectory=core/python"
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/run.sh"]
