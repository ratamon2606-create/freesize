FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt requirements-inference.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-inference.txt
COPY app.py ./app.py
COPY static ./static
RUN mkdir -p /app/data
EXPOSE 3000
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","3000","--proxy-headers"]
