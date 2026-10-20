FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TRAVEL_DATA_DIR=/app/data
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 travel \
    && useradd --uid 10001 --gid travel --no-create-home travel \
    && mkdir -p /app/data && chown travel:travel /app/data
COPY travel_mcp ./travel_mcp
USER 10001:10001
EXPOSE 8030
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8030/health',timeout=3)"
CMD ["python", "-m", "travel_mcp", "serve"]
