FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    TZ=Europe/Copenhagen

WORKDIR /app
COPY ./requirements.txt app.py ./

# playwright brings its own chromium, so no selenium server is needed
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

CMD ["python", "-u", "app.py"]
