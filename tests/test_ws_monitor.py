import threading
import time
from unittest.mock import MagicMock, patch


def test_fill_event_set_by_order_id():
    import src.ws_monitor as ws
    # Reset shared state
    ws.fill_events.clear()
    ws.fill_results.clear()

    order_id = "test_order_123"
    event = threading.Event()
    ws.fill_events[order_id] = event

    # Simulate ws_monitor receiving a fill message
    ws.handle_order_event({
        "ordId": order_id,
        "status": "full_fill",
        "avgPx": "50000",
        "accFillSz": "0.02",
    })

    assert event.is_set()
    assert ws.fill_results[order_id]["avgPx"] == "50000"


def test_fill_event_cleanup_on_timeout():
    import src.ws_monitor as ws
    ws.fill_events.clear()
    ws.fill_results.clear()

    order_id = "timeout_order"
    ws.fill_events[order_id] = threading.Event()
    ws.fill_results[order_id] = {"avgPx": "50000"}

    # Simulate finally-block cleanup (what executor does)
    try:
        filled = ws.fill_events[order_id].wait(timeout=0.01)
    finally:
        ws.fill_events.pop(order_id, None)
        ws.fill_results.pop(order_id, None)

    assert order_id not in ws.fill_events
    assert order_id not in ws.fill_results
