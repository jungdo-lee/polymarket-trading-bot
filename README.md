# Polymarket Trading Bot

Polymarket 예측 시장 룰 베이스 자동 거래봇. LLM 없이 시장 데이터 + 기술 지표만으로 동작.

## 아키텍처

```
WebSocket (실시간 오더북/가격)
       │
       ▼
  MarketStore ──→ PriceHistory
       │               │
       ▼               ▼
  ┌─────────────────────────┐
  │     EnsembleStrategy     │
  │  ┌───────┬───────┬─────┐ │
  │  │오더북  │모멘텀  │차익  │ │
  │  │불균형  │RSI/BB │거래  │ │
  │  │       │/EMA   │      │ │
  │  └───────┴───────┴─────┘ │
  └──────────┬──────────────┘
             │ Signal
             ▼
  ┌─────────────────────┐
  │   RiskManager        │
  │  Kelly Criterion     │
  │  손절/익절/트레일링   │
  └──────────┬──────────┘
             │
             ▼
  ┌─────────────────────┐
  │   Trader             │
  │  Paper / Live 분기   │
  └─────────────────────┘
```

## 3가지 전략

| 전략 | 원리 | 신호 조건 |
|------|------|-----------|
| **오더북 불균형** | bid/ask 상위 5레벨 볼륨 비율 | imbalance > 0.3 → BUY, < -0.3 → SELL |
| **모멘텀** | RSI(14) + 볼린저밴드(20) + EMA(12/26) | 3개 중 2개 이상 합의 시 신호 |
| **YES+NO 차익거래** | YES + NO 가격 합 < 1.0 | 양쪽 매수로 확정 차익 |

앙상블: 오더북(50%) + 모멘텀(50%) 가중 결합. 차익거래는 독립 실행.

## 청산 규칙 (5단계)

```
매수 진입
 ├─ 가격 -5%  → 손절 (stop loss)
 ├─ 가격 +4%  → 트레일링 스탑 활성화
 │   └─ 고점 대비 -3% 하락 → 이익 확정 청산
 ├─ 가격 +8%  → 익절 (take profit)
 ├─ 60분 경과 → 시간 초과 청산
 └─ 30분간 변동 없음 → 정체 청산
```

## 설치

```bash
# Python 3.11+ 필요
pip install -e ".[dev]"
```

## 설정

```bash
cp .env.example .env
```

`.env` 파일 편집:

```env
# 지갑 키 (페이퍼 모드는 빈 값 가능)
POLYMARKET_PRIVATE_KEY=0x...
CHAIN_ID=137

# 모드
PAPER_MODE=true

# 자금 관리
INITIAL_BANKROLL=1000
MAX_POSITION_PCT=0.10
KELLY_MULTIPLIER=0.25
MIN_EV_THRESHOLD=0.03
MAX_DAILY_LOSS=100
MAX_OPEN_POSITIONS=5

# 청산 설정
TAKE_PROFIT_PCT=0.08
STOP_LOSS_PCT=0.05
TRAILING_STOP_PCT=0.03
BREAKEVEN_TRIGGER_PCT=0.04
MAX_HOLD_MINUTES=60
STALE_EXIT_MINUTES=30

# 시장 필터
MIN_LIQUIDITY=5000
MIN_VOLUME_24H=1000

LOG_LEVEL=INFO
```

## 실행

```bash
# 페이퍼 트레이딩 (기본)
python -m src.main

# 종료: Ctrl+C → 결과 요약 출력 + logs/ 에 거래 내역 저장
```

## 결과 확인

```bash
# 최신 거래 내역
cat logs/paper_trades_*.json | python -m json.tool

# 요약 예시
# {
#   "initial_bankroll": 1000.0,
#   "current_bankroll": 1025.50,
#   "total_pnl": 25.50,
#   "wins": 8,
#   "losses": 3,
#   "win_rate": 0.7273
# }
```

## 로그 예시

```
signal_detected   strategy=ensemble side=BUY token=58897129... ev=0.0500 strength=0.47
trade_executed    strategy=ensemble side=BUY token=58897129... price=0.6500 size=38.46 ev=0.0500
trailing_activated token=58897129... pnl_pct=0.0400
position_exited   token=58897129... reason=trailing_stop pnl=$3.85 pnl_pct=5.12% hold_min=12.3
```

## 테스트

```bash
pytest tests/ -v
```

## 프로젝트 구조

```
polymarket/
├── config/settings.py            # 환경변수 기반 설정
├── src/
│   ├── main.py                   # 메인 루프 (asyncio)
│   ├── client/
│   │   ├── clob.py               # Polymarket CLOB API
│   │   ├── gamma.py              # 시장 탐색 API
│   │   └── websocket.py          # 실시간 데이터 스트리밍
│   ├── strategy/
│   │   ├── orderbook_imbalance.py
│   │   ├── momentum.py           # RSI + BB + EMA
│   │   ├── arbitrage.py          # YES+NO 차익
│   │   └── ensemble.py           # 신호 결합
│   ├── execution/
│   │   ├── risk.py               # Kelly + 청산 로직
│   │   ├── paper.py              # 페이퍼 트레이딩
│   │   └── trader.py             # 실행 분기
│   └── data/
│       ├── market_store.py       # 시장 데이터 캐시
│       └── price_history.py      # 가격 시계열
├── tests/
├── logs/                         # 거래 내역 JSON
├── .env.example
└── pyproject.toml
```

## 실거래 전환

1. `.env`에서 `PAPER_MODE=false` 설정
2. `POLYMARKET_PRIVATE_KEY`에 Polygon 지갑 키 입력
3. 지갑에 USDC 입금 (Polygon 네트워크)
4. 소액으로 시작 권장 (`INITIAL_BANKROLL=100`, `MAX_POSITION_PCT=0.05`)
