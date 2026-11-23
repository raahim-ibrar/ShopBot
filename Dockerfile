FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY shopbot ./shopbot
RUN useradd --no-create-home appuser
USER appuser

EXPOSE 10000
CMD ["sh", "-c", "exec python -m uvicorn shopbot.api:app --host 0.0.0.0 --port ${PORT:-10000}"]
