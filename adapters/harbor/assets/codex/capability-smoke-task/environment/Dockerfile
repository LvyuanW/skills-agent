FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends curl git ca-certificates bash procps && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN printf 'Write TB4_PREFLIGHT_OK and a newline to /app/answer.txt.\n' > /app/input.txt

COPY smoke.png /app/smoke.png
COPY child-task.txt /app/child-task.txt
