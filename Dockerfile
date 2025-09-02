# Build stage (Node)
FROM node:20-bullseye AS node-builder
WORKDIR /work/frontend
COPY frontend/package.json frontend/package-lock.json* ./
COPY frontend/ .
RUN npm install --legacy-peer-deps && npm run build

# Runtime (Python)
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential ca-certificates && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN rm -rf static/admin-app || true
RUN mkdir -p static/admin-app
COPY --from=node-builder /work/frontend/dist/ static/admin-app/
ENV PORT=10000
EXPOSE 10000
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:10000", "--workers", "3"]
