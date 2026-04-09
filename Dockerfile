FROM python:3.12-slim

WORKDIR /app

# 시스템 의존성
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Python 의존성 (레이어 캐싱 활용)
COPY pyproject.toml .
COPY src/__init__.py src/__init__.py
RUN pip install --no-cache-dir .

# 소스 복사
COPY src/ src/

# 데이터 디렉토리
RUN mkdir -p data/logs data/backups sandbox/strategies sandbox/results

CMD ["python", "-m", "src.main"]
