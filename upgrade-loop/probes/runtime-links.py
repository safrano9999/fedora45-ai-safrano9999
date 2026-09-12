"""Check every URL rendered by this instance's Citadel, without exposing query credentials."""
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import json
import os
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = set()

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if value and (key == "href" or key.startswith("data-url-")) and value.startswith(("http://", "https://")):
                self.urls.add(value)


def check(url):
    p = urlsplit(url)
    result = {"url": urlunsplit((p.scheme, p.netloc.rsplit("@", 1)[-1], p.path, "", "")), "status": "FAIL"}
    try:
        with urlopen(url, timeout=10) as response:
            response.read(1024)
            result.update(http_status=response.status, status="PASS" if response.status == 200 else "FAIL")
    except Exception as error:
        result["error"] = type(error).__name__
    return result


try:
    with urlopen("http://127.0.0.1:" + os.environ.get("CITADEL_WEBUI_PORT", "8000"), timeout=10) as response:
        parser = Links()
        parser.feed(response.read(4_000_000).decode())
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(check, sorted(parser.urls)))
    print(json.dumps({"status": "PASS" if results and all(r["status"] == "PASS" for r in results) else "FAIL", "results": results}))
except Exception as error:
    print(json.dumps({"status": "FAIL", "error": type(error).__name__}))
