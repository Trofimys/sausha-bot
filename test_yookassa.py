import base64
import http.client
import ssl
import json
import os

shop_id = "1401939"
secret_key = "live_LCmYnnWo8T0cRqLsiJH9UoPDlhe5uqnKpc_Hlk5qAnI"

auth_header = f"Basic {base64.b64encode(f'{shop_id}:{secret_key}'.encode()).decode()}"

ssl_ctx = ssl.create_default_context()
if hasattr(ssl, "OP_IGNORE_UNEXPECTED_EOF"):
    ssl_ctx.options |= ssl.OP_IGNORE_UNEXPECTED_EOF

print("Auth header:", auth_header)

conn = http.client.HTTPSConnection(
    "api.yookassa.ru",
    443,
    context=ssl_ctx,
    timeout=15,
)
headers = {
    "Authorization": auth_header,
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Connection": "close",
}
conn.request("GET", "/v3/payments", headers=headers)
response = conn.getresponse()
raw_data = response.read().decode("utf-8", errors="replace")
conn.close()

print(f"Status: {response.status}")
print(f"Body: {raw_data}")
