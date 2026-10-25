import os 
import feedparser
import anthropic
from dotenv import load_dotenv

load_dotenv()

url = "https://techcrunch.com/category/artificial-intelligence/feed/"
feed = feedparser.parse(url)
entry = feed.entries[0]

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
print(text)
print(entry.link)
print(message.stop_reason)