# Python 3.12, matching backend/.python-version. NOT 3.14: pydantic-core and
# selectolax have no wheels for it and fall back to source builds needing a C
# toolchain. Developing on a runtime the deploy does not use is the "works on
# my machine" bug factory.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a code change does not invalidate the pip layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root. Nothing here needs to write to the filesystem — the trace lives in
# Postgres — so there is no reason to run as root.
RUN useradd --create-home --uid 1000 agent && chown -R agent:agent /app
USER agent

# Render supplies PORT and ignores EXPOSE; this documents the default.
EXPOSE 8000

# Migrate, then serve. Render's Pre-Deploy Command is a paid-tier feature, so
# migrations have to run from the start command — a lesson Project 1 paid for
# (see CollegeRag/DEPLOYMENT_JOURNAL.md).
#
# ONE worker, deliberately. Project 1 OOM'd on Render's 512Mi free tier with
# four. This app also keeps in-memory rate-limit counters and runs agent loops
# as in-process background tasks, both of which are per-process — a second
# worker would silently halve the effective rate limit and split run state.
# --proxy-headers is not optional here. Render terminates TLS at its edge and
# forwards plain HTTP, so without it request.url.scheme is "http" even on an
# https:// request. The identity cookie decides Secure/SameSite from that
# scheme, so getting it wrong makes the browser reject the cookie entirely and
# every cross-site request arrives anonymous.
#
# --forwarded-allow-ips="*" trusts the forwarding headers. Safe because only
# Render's proxy can reach this container; it would NOT be safe if the port
# were publicly routable.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
