import time

from robotweb.state import StateStore


def test_first_publish_is_sent():
    store = StateStore()
    queue = store.subscribe()
    store.publish("a", {"value": 1, "ts": 1.0})
    store.flush()
    message = queue.get_nowait()
    assert message["type"] == "update"
    assert message["changes"] == {"a": {"value": 1, "ts": 1.0}}


def test_unchanged_value_is_not_resent_even_with_a_new_timestamp():
    store = StateStore(heartbeat_s=60)
    queue = store.subscribe()
    store.publish("a", {"value": 1, "ts": 1.0})
    store.flush()
    queue.get_nowait()

    store.publish("a", {"value": 1, "ts": 2.0})
    assert store.flush() is None
    assert queue.empty()
    assert store.get("a")["ts"] == 2.0  # the stored entry is still refreshed

    store.publish("a", {"value": 2, "ts": 3.0})
    store.flush()
    assert queue.get_nowait()["changes"]["a"]["value"] == 2


def test_changes_between_flushes_are_batched():
    store = StateStore()
    queue = store.subscribe()
    store.publish("a", {"value": 1})
    store.publish("a", {"value": 2})
    store.publish("b", {"value": 9})
    store.flush()
    message = queue.get_nowait()
    assert message["changes"] == {"a": {"value": 2}, "b": {"value": 9}}
    assert queue.empty()


def test_samples_reach_subscribers_and_history():
    store = StateStore()
    queue = store.subscribe()
    now = time.time()
    store.add_sample("pv.x", now - 1, 1.0)
    store.add_sample("pv.x", now, 2.0)
    store.flush()
    assert queue.get_nowait()["samples"] == {"pv.x": {"t": [now - 1, now], "v": [1.0, 2.0]}}
    assert store.snapshot()["history"]["pv.x"]["v"] == [1.0, 2.0]


def test_history_is_trimmed_to_the_window():
    store = StateStore(history_window_s=10)
    now = time.time()
    store.add_sample("pv.x", now - 60, 1.0)
    store.add_sample("pv.x", now - 5, 2.0)
    store.add_sample("pv.x", now, 3.0)
    assert store.snapshot()["history"]["pv.x"]["v"] == [2.0, 3.0]


def test_heartbeat_after_silence():
    store = StateStore(heartbeat_s=0)
    queue = store.subscribe()
    store.flush()
    message = queue.get_nowait()
    assert message["changes"] == {} and message["samples"] == {}


def test_slow_subscriber_is_dropped():
    store = StateStore(subscriber_queue_size=2, heartbeat_s=60)
    queue = store.subscribe()
    for i in range(3):
        store.publish("a", {"value": i})
        store.flush()
    # The queue was emptied and holds only the "you were dropped" marker.
    assert queue.get_nowait() is None
    assert queue.empty()
    store.publish("a", {"value": 99})
    store.flush()
    assert queue.empty()


def test_unsubscribed_queue_gets_nothing():
    store = StateStore()
    queue = store.subscribe()
    store.unsubscribe(queue)
    store.publish("a", {"value": 1})
    store.flush()
    assert queue.empty()
