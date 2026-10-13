FROM python:3.10-slim

WORKDIR /app

RUN pip install uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Create log directory so that logging works out of the box
RUN mkdir -p /var/log

COPY scripts/ /app/scripts/

ENV MAX_RETRIES=5
ENV PORT=8080

CMD ["uv", "run", "python", "scripts/slacksrv.py"]
