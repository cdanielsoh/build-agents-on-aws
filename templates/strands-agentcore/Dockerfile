# AgentCore Runtime requires linux/arm64.
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY agent/ ./

EXPOSE 8080

# opentelemetry-instrument wires the agent into the ADOT sidecar that AgentCore
# Runtime provides. Do NOT set OTEL_* env vars here — the sidecar configures them
# for hosted agents, and overriding them silently breaks trace delivery.
CMD ["opentelemetry-instrument", "python", "app.py"]
