# One image for every PodCare service (edge and cloud); each container picks its service with `command:`.
# Multi-arch friendly (amd64 laptops/cloud, arm64 Raspberry Pi hub): docker buildx build --platform linux/amd64,linux/arm64
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
# Bake a default model and the example bench runs so every service starts without extra steps.
# The cloud trainer later publishes newer versions to the model registry; edge hubs pull them.
RUN python train.py --sessions 150 > /dev/null && python make_examples.py > /dev/null

RUN useradd --create-home --uid 10001 podcare && mkdir -p /data && chown -R podcare /app /data
USER podcare
EXPOSE 8000 9001 9002 9003 9004
CMD ["python", "server.py"]
