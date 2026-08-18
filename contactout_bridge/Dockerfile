ARG PYTHON_BASE=python:3.12-slim
FROM ${PYTHON_BASE}

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m patchright install --with-deps chromium \
    && useradd --create-home --uid 10001 bridge \
    && mkdir -p /data /ms-playwright \
    && chown -R bridge:bridge /data /ms-playwright

COPY --chown=bridge:bridge . ./contactout_bridge
USER bridge

EXPOSE 8790
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8790/live', timeout=3).read()"

ENTRYPOINT []
CMD ["python", "-m", "contactout_bridge.server"]
