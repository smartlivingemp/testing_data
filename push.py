import requests
import certifi

# API endpoint and headers
url = "https://toppily.com/api/v1/buy-other-package"
headers = {
    "x-api-key": "68b5ec28de8abe4f99a77a5434e032fc78b8d2d8",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

# Request body (shared bundle style)
payload = {
    "recipient_msisdn": "0246363567",  # Change to the number you want to test
    "network_id": 3,                   # MTN
    "shared_bundle": 1000,              # Bundle size
    "trx_ref": "testConsoleOrder1"      # Unique transaction ref
}

try:
    resp = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=20,
        verify=certifi.where()  # fixes SSL cert issue
    )
    print("HTTP Status:", resp.status_code)
    print("Response Body:", resp.text)
except requests.RequestException as e:
    print("HTTP Error:", e)
