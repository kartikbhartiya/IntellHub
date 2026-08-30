# Must match "stack" in app-config.json and the CI matrix. A different
# minor version means a different scikit-learn, the registry refuses every
# artifact, and the container retrains in-process.
FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 9000

ENV PORT=9000
ENV FLASK_ENV=production

CMD ["python", "main.py"]
