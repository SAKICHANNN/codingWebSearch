FROM python:3.12-slim

LABEL org.opencontainers.image.title="codingWebSearch"
LABEL org.opencontainers.image.description="MCP web search server for coding agents — 21 tools, 7 search engines"
LABEL org.opencontainers.image.url="https://github.com/SAKICHANNN/codingWebSearch"
LABEL org.opencontainers.image.version="0.6.0"

WORKDIR /app

# Install only what's needed
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

ENV PYTHONUNBUFFERED=1

RUN adduser --disabled-password --gecos "" appuser \
    && chown -R appuser:appuser /app
USER appuser

ENTRYPOINT ["python", "server.py"]
