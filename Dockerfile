# The service image: API, worker, migrations and the demo seed share it (docker-compose.yml picks the command).
FROM python:3.11.17-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# onnxruntime (OCR) needs the OpenMP runtime; OpenCV headless needs GLib
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY testamur ./testamur
RUN pip install ".[ocr,agents,postgres,service]" \
 && python -c "from testamur.extract import RapidOcr; RapidOcr()"

RUN useradd --create-home --uid 10001 testamur
USER testamur
WORKDIR /home/testamur
EXPOSE 8000
CMD ["testamur", "serve", "--host", "0.0.0.0", "--port", "8000"]
