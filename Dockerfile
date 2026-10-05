# CatNav Compass + Adoption weekly refresh — Cloud Run job image.
# Security: for production, pin the base image by digest (python:3.12-slim@sha256:...)
# so a re-tagged upstream image can't change what runs. Get it with:
#   gcloud artifacts docker images describe python:3.12-slim  (or `docker pull` + `docker inspect`)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Service account via Application Default Credentials — no keys in the image.
    CATNAV_BACKEND=api \
    # Raw pulls contain personal data: keep them on the job's ephemeral in-memory disk only.
    CATNAV_DATA_DIR=/tmp/catnav/data \
    CATNAV_OUTPUT_DIR=/tmp/catnav/out \
    CATNAV_SHARE_COPY_DIR=

WORKDIR /app

# Security: --require-hashes refuses any package whose hash isn't in the lockfile
# (protects against tampered or substituted packages on the index).
COPY requirements.txt .
RUN pip install --require-hashes -r requirements.txt

COPY *.py *_template.html ./

# Fail the build (not a Monday run) if the Sydney timezone isn't available.
RUN python -c "from zoneinfo import ZoneInfo; ZoneInfo('Australia/Sydney')"

# Security: run as an unprivileged user, not root.
RUN useradd --create-home --uid 10001 catnav
USER catnav

CMD ["python", "refresh.py"]
