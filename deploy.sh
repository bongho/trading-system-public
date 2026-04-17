#!/bin/bash
# Ubuntu 서버 배포 스크립트
# 사용법: ./deploy.sh [pull|restart|logs|status|stop]

set -e

ACTION=${1:-"deploy"}
CONTAINER="trading-engine"

case "$ACTION" in
  deploy)
    echo "=== Trading System 배포 ==="

    # .env 파일 확인
    if [ ! -f ".env" ]; then
      echo "❌ .env 파일이 없습니다. .env.example을 복사해서 설정하세요."
      echo "   cp .env.example .env && nano .env"
      exit 1
    fi

    # 필수 디렉토리 생성
    mkdir -p data/logs data/backups sandbox/strategies sandbox/results src/strategies

    # 이미지 빌드 + 컨테이너 재시작
    docker compose build --no-cache
    docker compose up -d

    echo ""
    echo "✅ 배포 완료"
    echo "   로그 확인: ./deploy.sh logs"
    echo "   상태 확인: ./deploy.sh status"
    ;;

  pull)
    echo "=== 최신 코드 반영 ==="
    git pull
    docker compose build
    docker compose up -d
    echo "✅ 업데이트 완료"
    ;;

  restart)
    echo "=== 컨테이너 재시작 ==="
    docker compose restart
    echo "✅ 재시작 완료"
    ;;

  logs)
    docker compose logs -f --tail=100
    ;;

  status)
    echo "=== 컨테이너 상태 ==="
    docker compose ps
    echo ""
    echo "=== 리소스 사용량 ==="
    docker stats --no-stream "$CONTAINER" 2>/dev/null || echo "(컨테이너 미실행)"
    ;;

  stop)
    echo "=== 컨테이너 중지 ==="
    docker compose down
    echo "✅ 중지 완료"
    ;;

  *)
    echo "사용법: ./deploy.sh [deploy|pull|restart|logs|status|stop]"
    exit 1
    ;;
esac
