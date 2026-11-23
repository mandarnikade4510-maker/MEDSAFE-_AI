import urllib.request

try:
    urllib.request.urlopen("https://api.anthropic.com", timeout=15)
    print("Reached the server")
except Exception as e:
    print(type(e).__name__, "-", e)