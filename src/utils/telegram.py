import threading

import httpx

from config.settings import settings
from src.utils.logger import get_logger

log = get_logger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"

_REASON_KR = {
    "stop_loss": "\u274c \uc190\uc808",
    "take_profit": "\u2705 \uc775\uc808",
    "trailing_stop": "\U0001f4c9 \ud2b8\ub808\uc77c\ub9c1 \uc2a4\ud0d1",
    "max_hold_time": "\u23f0 \ucd5c\ub300 \ubcf4\uc720\uc2dc\uac04 \ucd08\uacfc",
    "stale_position": "\U0001f4a4 \uc7a5\uae30 \ubbf8\ubcc0\ub3d9",
    "price_gap": "\u26a1 \uae09\uaca9\ud55c \uac00\uaca9 \ubcc0\ub3d9",
}

_DIVIDER = "\u2500" * 14


def _translate_reason(reason: str) -> str:
    for key, kr in _REASON_KR.items():
        if key in reason:
            detail = reason.split("(", 1)
            return f"{kr} ({detail[1]}" if len(detail) > 1 else kr
    if "sell_signal" in reason:
        strategy = reason.replace("sell_signal ", "").strip("()")
        return f"\U0001f4e1 \ub9e4\ub3c4 \uc2dc\uadf8\ub110 ({strategy})"
    return reason


def _is_configured() -> bool:
    return bool(
        settings.telegram_enabled
        and settings.telegram_bot_token
        and settings.telegram_chat_id
    )


def send_message(text: str) -> None:
    """Send a Telegram message asynchronously (non-blocking)."""
    if not _is_configured():
        return
    thread = threading.Thread(target=_send_sync, args=(text,), daemon=True)
    thread.start()


def _send_sync(text: str) -> None:
    url = _API_BASE.format(token=settings.telegram_bot_token)
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = httpx.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            log.warning("telegram_send_failed", status=resp.status_code)
    except Exception as e:
        log.warning("telegram_send_error", error=str(e))


def notify_buy(
    token_id: str,
    strategy: str,
    price: float,
    size: float,
    ev: float,
    bankroll: float,
) -> None:
    mode = "\ubaa8\uc758" if settings.paper_mode else "\uc2e4\uac70\ub798"
    cost = price * size
    text = (
        f"\U0001f7e2 <b>[{mode}] \ub9e4\uc218</b>\n"
        f"{_DIVIDER}\n"
        f"\U0001f3af \ud1a0\ud070: <code>{token_id[:16]}...</code>\n"
        f"\U0001f4ca \uc804\ub7b5: {strategy}\n"
        f"\U0001f4b0 \ub9e4\uc218\uac00: {price:.4f}\n"
        f"\U0001f4e6 \uc218\ub7c9: {size:.2f}\uc8fc\n"
        f"\U0001f4b5 \ud22c\uc790\uae08: ${cost:.2f}\n"
        f"\U0001f4c8 \uae30\ub300\uc218\uc775(EV): {ev:.4f}\n"
        f"{_DIVIDER}\n"
        f"\U0001f3e6 \uc794\uace0: ${bankroll:.2f}"
    )
    send_message(text)


def notify_sell(
    token_id: str,
    reason: str,
    entry_price: float,
    exit_price: float,
    pnl: float,
    pnl_pct: float,
    hold_min: float,
    bankroll: float,
    *,
    total_trades: int = 0,
    wins: int = 0,
    total_pnl: float = 0.0,
    win_rate: float = 0.0,
) -> None:
    mode = "\ubaa8\uc758" if settings.paper_mode else "\uc2e4\uac70\ub798"
    result_icon = "\U0001f7e2" if pnl >= 0 else "\U0001f534"
    sign = "+" if pnl >= 0 else ""
    result = "\uc218\uc775" if pnl >= 0 else "\uc190\uc2e4"
    kr_reason = _translate_reason(reason)
    text = (
        f"{result_icon} <b>[{mode}] \ub9e4\ub3c4 - {result}</b>\n"
        f"{_DIVIDER}\n"
        f"\U0001f3af \ud1a0\ud070: <code>{token_id[:16]}...</code>\n"
        f"\U0001f4cc \uc0ac\uc720: {kr_reason}\n"
        f"\U0001f4b0 \uc9c4\uc785: {entry_price:.4f} \u2192 \uccad\uc0b0: {exit_price:.4f}\n"
        f"\U0001f4b8 \uc190\uc775: {sign}${pnl:.2f} ({sign}{pnl_pct:.2%})\n"
        f"\u23f1 \ubcf4\uc720: {hold_min:.1f}\ubd84\n"
        f"{_DIVIDER}\n"
        f"\U0001f3e6 \uc794\uace0: ${bankroll:.2f}"
    )
    # \ub204\uc801 \ud1b5\uacc4 (\ub370\uc774\ud130\uac00 \uc788\uc744 \ub54c\ub9cc \ud45c\uc2dc)
    if total_trades > 0:
        pnl_icon = "\U0001f4c8" if total_pnl >= 0 else "\U0001f4c9"
        total_sign = "+" if total_pnl >= 0 else ""
        text += (
            f"\n{_DIVIDER}\n"
            f"\U0001f4ca <b>\ub204\uc801 \uc2e4\uc801</b>\n"
            f"\U0001f3b0 \ucd1d \uac70\ub798: {total_trades}\ud68c\n"
            f"\U0001f3c6 \uc2b9\ub960: {win_rate:.1%} ({wins}\uc2b9/{total_trades - wins}\ud328)\n"
            f"{pnl_icon} \ub204\uc801 \uc190\uc775: {total_sign}${total_pnl:.2f}"
        )
    send_message(text)


def notify_arbitrage(
    condition_id: str,
    yes_price: float,
    no_price: float,
    profit_pct: float,
    bankroll: float,
) -> None:
    mode = "\ubaa8\uc758" if settings.paper_mode else "\uc2e4\uac70\ub798"
    total = yes_price + no_price
    text = (
        f"\u2696\ufe0f <b>[{mode}] \ucc28\uc775\uac70\ub798</b>\n"
        f"{_DIVIDER}\n"
        f"\U0001f3af \ub9c8\ucf13: <code>{condition_id[:16]}...</code>\n"
        f"\U0001f7e2 YES: {yes_price:.4f} | \U0001f534 NO: {no_price:.4f}\n"
        f"\U0001f4ca \ud569\uacc4: {total:.4f} (1.0 \ubbf8\ub9cc = \ucc28\uc775)\n"
        f"\U0001f4b0 \uc608\uc0c1 \uc218\uc775\ub960: +{profit_pct:.2%}\n"
        f"{_DIVIDER}\n"
        f"\U0001f3e6 \uc794\uace0: ${bankroll:.2f}"
    )
    send_message(text)
