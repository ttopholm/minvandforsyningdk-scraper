FROM python:3.12-slim

COPY ./requirements.txt app.py ./
RUN pip install --no-cache-dir -r requirements.txt
CMD ["python", "-u", "/app.py"]