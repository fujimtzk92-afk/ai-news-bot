import tweepy
import os 
import feedparser
import anthropic
from dotenv import load_dotenv

load_dotenv()

url = "https://techcrunch.com/category/artificial-intelligence/feed/"
feed = feedparser.parse(url)

posted = []
if os.path.exists("posted.txt"):
    with open("posted.txt") as f:
        posted = f.read().splitlines()


entry = None
for e in feed.entries:
    if e.link not in posted:
        entry = e
        break

if entry is None:
    print("新しい記事がない")
    exit()

prompt = f"""次の英語のAIニュースを日本語で要約して、Xの投稿文を作ってください。
条件:
- 100文字以内
- 何が起きたかを最初に書く
- ハッシュタグと絵文字は使わない
- 投稿文だけを出力する

タイトル: {entry.title}
概要: {entry.summary}
"""

client = anthropic.Anthropic()
message = client.messages.create(
    model="claude-haiku-5-5",
    max_tokens=2000,
    messages=[{"role": "user", "content": prompt}],
)
text = ""
for block in message.content:
    if block.type == "text":
        text = block.text
post = text + "\n\n" + entry.link   

x_client = tweepy.Client(
    consumer_key=os.getenv("X_CONSUMER_KEY"),
    consumer_secret=os.getenv("X_CONSUMER_SECRET"),
    access_token=os.getenv("X_ACCESS_TOKEN"),
    access_token_secret=os.getenv("X_ACCESS_TOKEN_SECRET"),
)

x_client.create_tweet(text=post)
with open("posted.txt", "a") as f:
    f.write(entry.link + "\n")
print("投稿した：")
print(post)