FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --create-home appuser && chown -R appuser /srv
USER appuser

EXPOSE 8000
# One worker keeps the in-memory rate limiter accurate; threads handle concurrency.
CMD ["sh", "-c", "gunicorn app.main:app --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 60"]
