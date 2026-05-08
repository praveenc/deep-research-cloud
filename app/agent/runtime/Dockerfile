FROM --platform=linux/arm64 python:3.13-slim-bookworm

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent code
COPY . .

# AgentCore Runtime requires port 8080
EXPOSE 8080

# OTel auto-instrumentation entrypoint (per Strands docs)
# aws-opentelemetry-distro provides the opentelemetry-instrument wrapper
ENTRYPOINT ["opentelemetry-instrument", "python", "main.py"]
