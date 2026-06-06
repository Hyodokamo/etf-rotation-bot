FROM python:3.12-slim

# Metadata
LABEL org.opencontainers.image.title="etf-rotation-bot" \
      org.opencontainers.image.description="AI監査型ETFローテーションBot — advisory only, no auto-trade" \
      org.opencontainers.image.source="https://github.com/hyodo-tetsuro/etf-rotation-bot"

# Set timezone via build arg; runtime TZ is set via env_file / environment in compose
ARG TZ=Asia/Tokyo
ENV TZ=${TZ}

WORKDIR /app

# Install dependencies first (layer-cache friendly — only re-runs when requirements change)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code (excludes .env, personal CSVs, logs via .dockerignore)
COPY . .

# Create volume-mount target directories with correct ownership before switching user.
# These will be bind-mounted at runtime (data/logs/reports/outputs), so the directories
# themselves exist inside the image but their *contents* come from the host volume.
RUN mkdir -p data logs reports outputs \
 && addgroup --system botgroup \
 && adduser --system --ingroup botgroup --home /app botuser \
 && chown -R botuser:botgroup /app

USER botuser

# Default command: safe dry-run scan, Slack disabled.
# Override in docker compose with `command:` or via `docker compose run`.
# Never passes --allow-watchlist-update — watchlist.csv is not updated by default.
CMD ["python", "scripts/daily_signal_check.py", "--no-slack"]
