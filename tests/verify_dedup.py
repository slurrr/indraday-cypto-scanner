from collections import OrderedDict
import threading

sent_alerts = OrderedDict()
sent_alerts_lock = threading.Lock()

def mock_handle_alerts(alerts):
    new_unique_alerts = []
    with sent_alerts_lock:
        for alert in alerts:
            key = alert
            if key not in sent_alerts:
                sent_alerts[key] = True
                new_unique_alerts.append(alert)
                if len(sent_alerts) > 5:  # Test with small limit
                    sent_alerts.popitem(last=False)
    return new_unique_alerts

# Test 1: Fill up
print("Adding 1-5...")
mock_handle_alerts([1, 2, 3, 4, 5])
print(f"Set: {list(sent_alerts.keys())}")
assert len(sent_alerts) == 5

# Test 2: Add one more, expect 1 to be evicted
print("Adding 6...")
mock_handle_alerts([6])
print(f"Set: {list(sent_alerts.keys())}")
assert len(sent_alerts) == 5
assert 1 not in sent_alerts
assert 6 in sent_alerts

# Test 3: Re-add evicted 1, should be accepted
print("Re-adding 1...")
new = mock_handle_alerts([1])
print(f"New accepted: {new}")
print(f"Set: {list(sent_alerts.keys())}")
assert 1 in new
assert 1 in sent_alerts
assert 2 not in sent_alerts # 2 should be evicted next

print("All logic tests passed.")
