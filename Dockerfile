FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY src/ src/

VOLUME /data

ENV BRIDGE_MAPPING_FILE_PATH=/data/mapping.json
ENV BRIDGE_LOG_LEVEL=INFO

CMD ["python", "-m", "src.main"]
