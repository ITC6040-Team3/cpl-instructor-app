#!/bin/bash
set -e

echo "=== Installing Python dependencies ==="
python -m pip install --upgrade pip
python -m pip install --no-cache-dir -r /home/site/wwwroot/requirements.txt

echo "=== Installing Microsoft ODBC Driver 18 for SQL Server ==="
apt-get update
apt-get install -y curl gnupg2 apt-transport-https unixodbc-dev

curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add -
curl https://packages.microsoft.com/config/ubuntu/22.04/prod.list > /etc/apt/sources.list.d/mssql-release.list

apt-get update
ACCEPT_EULA=Y apt-get install -y msodbcsql18

echo "=== Starting app ==="
exec gunicorn --bind=0.0.0.0:${PORT:-8000} --timeout 600 app:app
