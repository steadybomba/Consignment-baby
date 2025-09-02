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
  # common Vite output locations
  if [ -d frontend/dist ]; then
    cp -r frontend/dist/* static/admin-app/
  elif [ -d frontend-dist ]; then
    cp -r frontend-dist/* static/admin-app/
  else
    echo "Warning: frontend build folder not found."
  fi
else
  echo "No frontend directory found, skipping frontend build."
fi
echo "Build complete."
