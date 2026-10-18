FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend
WORKDIR /app
COPY requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt && useradd --uid 10001 --create-home jana
COPY backend /app/backend
COPY tools/parse_stream.py /app/tools/parse_stream.py
COPY site/index.html site/styles.css site/app.mjs /app/site/
COPY site/src /app/site/src
COPY data/public /app/data/public
RUN chmod 0444 /app/data/public/gazetteer.sqlite && test -r /app/data/public/gazetteer.sqlite
USER jana
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/ready', timeout=3)"
CMD ["uvicorn", "jana.api:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
