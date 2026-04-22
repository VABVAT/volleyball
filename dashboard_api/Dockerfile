FROM python:3.12-slim

WORKDIR /app

COPY dashboard_api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY dashboard_api /app/dashboard_api

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["python", "-m", "uvicorn", "dashboard_api.main:app", "--host", "0.0.0.0", "--port", "8090"]

