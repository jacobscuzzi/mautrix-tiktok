FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The request signer is a third-party dependency and is NOT baked in by
# default. The operator installs it explicitly (it runs untrusted code that
# computes TikTok's request signatures), e.g. one of:
#   RUN pip install --no-cache-dir SignerPy
#   RUN pip install --no-cache-dir "git+https://github.com/is-L7N/SignerPy"
# or mount a vetted signer and point signer_cmd at it. Build with
# --build-arg INSTALL_SIGNER=1 after reviewing the source if you want it here.

COPY bridge/ bridge/
COPY signer/ signer/
COPY config.docker.yaml config.yaml

# BRIDGE_MASTER_KEY (base64 32 bytes) and BRIDGE_PROXY (per-user proxy URL)
# are injected at runtime. Without a proxy, egress uses the host IP, which
# TikTok rate-limits (probe returns rate_limited / needs-reauth).
ENTRYPOINT ["python", "-m", "bridge.cmd.run"]
CMD ["probe"]
