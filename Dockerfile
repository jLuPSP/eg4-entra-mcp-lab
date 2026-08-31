FROM python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN addgroup --system --gid 10001 app && adduser --system --uid 10001 --ingroup app app
COPY pyproject.toml README.md uv.lock /app/
COPY src /app/src
RUN python -m pip install --upgrade pip && python -m pip install uv==0.9.26 && uv sync --locked --no-dev --no-editable
ENV PATH="/app/.venv/bin:$PATH"
USER app
