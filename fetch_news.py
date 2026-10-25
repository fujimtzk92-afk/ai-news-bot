import feedparser

url = "https://techcrunch.com/category/artificial-intelligence/feed/"

feed = feedparser.parse(url)

for entry in feed.entries[:5]:
    print(entry.title)
    print(entry.link)
    print("---")