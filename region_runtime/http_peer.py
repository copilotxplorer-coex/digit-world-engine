import json
import urllib.request

class HttpPeer:
    def __init__(self, endpoint):
        self.endpoint = endpoint.rstrip("/")

    def call(self, action, **payload):
        body = {
            "action": action,
        }

        body.update(payload)

        req = urllib.request.Request(
            self.endpoint + "/rpc",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json"
            },
        )

        with urllib.request.urlopen(
            req,
            timeout=30,
        ) as response:
            result = json.loads(
                response.read()
            )

        if not result.get("ok"):
            raise ValueError(
                result.get("error")
            )

        return result["result"]