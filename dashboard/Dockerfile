FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY dashboard /app/dashboard

ENV PYTHONUNBUFFERED=1

CMD ["streamlit", "run", "/app/dashboard/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
