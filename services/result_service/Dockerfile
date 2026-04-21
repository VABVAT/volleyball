FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY shared /app/shared
COPY services/result_service /app/services/result_service
COPY services/__init__.py /app/services/__init__.py

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["sh", "-c", "exec python -m uvicorn services.result_service.main:app --host 0.0.0.0 --port ${METRICS_PORT:-8006}"]
