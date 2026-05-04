import os
from dotenv import load_dotenv
from bitpy.rest_api import BitgetAPI

load_dotenv()


def get_client() -> BitgetAPI:
    api_key = os.getenv("BITGET_API_KEY", "")
    api_secret = os.getenv("BITGET_SECRET_KEY", "")
    passphrase = os.getenv("BITGET_PASSPHRASE", "")

    missing = [
        name
        for name, val in [
            ("BITGET_API_KEY", api_key),
            ("BITGET_SECRET_KEY", api_secret),
            ("BITGET_PASSPHRASE", passphrase),
        ]
        if not val
    ]
    if missing:
        raise ValueError(f"Missing env vars: {', '.join(missing)}")

    client = BitgetAPI(api_key=api_key, secret_key=api_secret, api_passphrase=passphrase)

    if os.getenv("BITGET_IS_DEMO", "false").lower() == "true":
        client.account.request_handler.static_headers["x-simulated-trading"] = "1"

    return client
