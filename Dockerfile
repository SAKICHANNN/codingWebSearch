FROM python:3.12-slim

LABEL org.opencontainers.image.title="codingWebSearch"
LABEL org.opencontainers.image.description="MCP web search server for coding agents — 16 tools, 6 search engines"
LABEL org.opencontainers.image.url="https://github.com/SAKICHANNN/codingWebSearch"
LABEL org.opencontainers.image.version="0.3.0"

WORKDIR /app

# Install only what's needed
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

ENV PYTHONUNBUFFERED=1

EXPOSE 8080

ENTRYPOINT ["python", "server.py"]
