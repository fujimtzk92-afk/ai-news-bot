import os
import time
import requests
import tweepy
import feedparser
import anthropic
from dotenv import load_dotenv
 
load_dotenv()
 
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
 
WAIT_HOURS = 3  # 返信を待つ時間（これを過ぎたら何も投稿せず終了）
 
# 発信者の視点
PERSPECTIVE = """
- 読者: AIの波に乗りたい日本の一人起業家・副業層・ビジネスパーソン
- 発信者: アメリカでCSを学ぶ日本人。英語圏のAIニュースを日本で一番早く、わかりやすく届ける
- 判断基準: このニュースで、個人の仕事やお金の稼ぎ方の何が変わるか
"""
 
 
# ---------- 1. 新しい記事を探す ----------
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
 
 
# ---------- 2. AIに3案作らせる ----------
prompt = f"""次の英語のAIニュースを読んで、日本語のXの投稿文を3案作ってください。
 
発信者の設定:
{PERSPECTIVE}
 
3案はそれぞれ次の型で書く:
- 案1【速報】何が起きたかを固有名詞や数字で具体的に書く。最後に「なぜこれが大きいか」を一言。140文字以内
- 案2【実用】読者が自分の仕事にどう活かせるか、何が変わるかを具体的に書く。250〜350文字
- 案3【意見】発信者としての意見を一つ言い切り、その理由を書く。賛成と反対が分かれるくらいはっきり。250〜350文字
 
全案共通の条件:
- 最初に何が起きたかを1文で書き、その後に型に沿った内容を書く
- 1行目は、読者がスクロールを止めたくなる書き出しにする
- 長い案は、最初の2〜3行だけで要点と続きを読みたくなる理由が伝わるようにする
- 常体（だ・である調）、短い文、適度に改行する
- タイトルと概要に書かれていない事実や数字は書かない
- 発信者の体験談（使ってみた、など）は作らない
- 「〜すべき」「注目が集まる」など、誰でも言える締めは使わない
- ハッシュタグと絵文字は使わない
- 案と案の間は「===」だけの行で区切る
- 投稿文以外は何も出力しない
 
タイトル: {entry.title}
概要: {entry.get("summary", "")}
"""
 
client = anthropic.Anthropic()
message = client.messages.create(
    model="claude-haiku-5-5",
    max_tokens=16000,
    messages=[{"role": "user", "content": prompt}],
)
 
text = ""
for block in message.content:
    if block.type == "text":
        text += block.text
 
drafts = [d.strip() for d in text.split("===") if d.strip()]
print(message.stop_reason)
print(text)
if not drafts:
    print("案が作れなかった")
    exit()
 
 
# ---------- 3. Telegramに3案を送る ----------
def send_telegram(msg):
    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
    )
 
 
# 古いメッセージを読み飛ばす（前回の返信を拾わないため）
res = requests.get(f"{TELEGRAM_API}/getUpdates").json()
offset = 0
if res["result"]:
    offset = res["result"][-1]["update_id"] + 1
 
msg = f"【{entry.title}】\n{entry.link}\n\n"
for i, d in enumerate(drafts, start=1):
    msg += f"--- 案{i} ---\n{d}\n\n"
msg += "番号で返信 → 投稿 / 0 → スキップ"
send_telegram(msg)
print("Telegramに送った。返信待ち...")
 
 
# ---------- 4. 返信を待つ ----------
choice = None
deadline = time.time() + WAIT_HOURS * 3600
 
while time.time() < deadline and choice is None:
    res = requests.get(
        f"{TELEGRAM_API}/getUpdates",
        params={"offset": offset, "timeout": 30},
    ).json()
 
    for update in res["result"]:
        offset = update["update_id"] + 1
        reply = update.get("message", {}).get("text", "").strip()
        if reply.isdigit() and 0 <= int(reply) <= len(drafts):
            choice = int(reply)
            break
        send_telegram(f"0〜{len(drafts)}の番号で返信して")
 
if choice is None:
    print("時間切れ。投稿しなかった")
    exit()
 
if choice == 0:
    send_telegram("スキップした")
    with open("posted.txt", "a") as f:
        f.write(entry.link + "\n")
    exit()
 
 
# ---------- 5. Xに投稿する ----------
post = drafts[choice - 1] + "\n\n" + entry.link
 
x_client = tweepy.Client(
    consumer_key=os.getenv("X_CONSUMER_KEY"),
    consumer_secret=os.getenv("X_CONSUMER_SECRET"),
    access_token=os.getenv("X_ACCESS_TOKEN"),
    access_token_secret=os.getenv("X_ACCESS_TOKEN_SECRET"),
)
x_client.create_tweet(text=post)
 
with open("posted.txt", "a") as f:
    f.write(entry.link + "\n")
 
send_telegram(f"投稿した：\n{post}")
print("投稿した：")
print(post)