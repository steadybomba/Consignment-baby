# Build stage (Node)
FROM node:20-bullseye AS node-builder
WORKDIR /work/frontend

# Copy only the manifest files first (better caching)
COPY frontend/package*.json ./

# Install dependencies
RUN npm install --legacy-peer-deps

# Copy the rest of the frontend source
COPY frontend/ .

# Build the frontend
RUN npm run build

# Runtime (Python)
FROM python:3.11-slim
WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends build-essential ca-certificates && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY . .

# Replace old admin app build if any
RUN rm -rf static/admin-app || true
RUN mkdir -p static/admin-app
COPY --from=node-builder /work/frontend/dist/ static/admin-app/

# Environment and startup
ENV PORT=10000
EXPOSE 10000
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:10000", "--workers", "3"]
