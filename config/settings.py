from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # Polymarket
    polymarket_private_key: str = ""
    polymarket_funder: str = ""          # 프록시 지갑 주소 (Polymarket UI에서 확인)
    polymarket_signature_type: int = 0   # 0=EOA, 1=POLY_PROXY, 2=POLY_GNOSIS_SAFE
    chain_id: int = 137

    # Trading mode
    paper_mode: bool = True

    # Bankroll & risk
    initial_bankroll: float = 1000.0
    max_position_pct: float = 0.10
    kelly_multiplier: float = 0.25
    min_ev_threshold: float = 0.03
    max_daily_loss: float = 100.0
    max_open_positions: int = 5

    # Exit strategy
    take_profit_pct: float = 0.08       # 익절: 진입가 대비 +8%
    stop_loss_pct: float = 0.05         # 손절: 진입가 대비 -5%
    trailing_stop_pct: float = 0.03     # 트레일링 스탑: 고점 대비 -3% 하락 시 청산
    breakeven_trigger_pct: float = 0.04 # +4% 이상 수익 시 → 트레일링 스탑 활성화
    max_hold_minutes: float = 60.0      # 최대 보유 시간 (분)
    stale_exit_minutes: float = 30.0    # 30분간 가격 변동 없으면 청산

    # Market selection
    min_liquidity: float = 5000.0
    min_volume_24h: float = 1000.0

    # Fee & slippage
    trading_fee_pct: float = 0.0          # Polymarket 거래 수수료 (현재 0%)
    slippage_pct: float = 0.02            # 예상 슬리피지 (2%, 오더북 깊이에 따라 조정)
    min_order_size: float = 5.0           # Polymarket CLOB 최소 주문 크기 (shares)

    # Entry filters
    cooldown_minutes: float = 10.0        # 같은 토큰 재진입 쿨다운
    avoid_mid_price_low: float = 0.40     # 이 가격 이상 ~
    avoid_mid_price_high: float = 0.60    # ~ 이 가격 이하는 진입 회피 (최대 불확실 구간)
    max_price_gap_pct: float = 0.15       # 진입가 대비 15% 이상 갭 발생 시 즉시 청산

    # Telegram notifications
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_enabled: bool = True       # 토큰+chat_id 있으면 자동 활성화

    # Logging
    log_level: str = "INFO"

    # API URLs
    clob_host: str = "https://clob.polymarket.com"
    gamma_host: str = "https://gamma-api.polymarket.com"
    ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
