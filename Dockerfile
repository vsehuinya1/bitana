FROM python:3.11-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim

RUN useradd -r -m bitana
WORKDIR /opt/bitana

COPY --from=builder /install /usr/local
COPY . .
RUN mkdir -p data logs && chown -R bitana:bitana /opt/bitana

USER bitana

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -sf http://localhost:8080/health || exit 1

EXPOSE 8080
ENTRYPOINT ["python", "main.py"]
CMD ["--mode", "paper"]
