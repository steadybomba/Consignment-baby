#!/usr/bin/env bash
set -e
echo "Building frontend..."
if [ -d frontend ]; then
  cd frontend
  npm ci --silent
  npm run build
  cd ..
  rm -rf static/admin-app || true
  mkdir -p static/admin-app
  # Vite outputs to frontend/dist or frontend-dist depending on config. Check both.
  if [ -d frontend/dist ]; then
    cp -r frontend/dist/* static/admin-app/
  elif [ -d frontend-dist ]; then
    cp -r frontend-dist/* static/admin-app/
  else
    echo "Warning: frontend build folder not found."
  fi
else
  echo "No frontend directory — skipping frontend build."
fi
echo "Build complete."
