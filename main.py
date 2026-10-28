import os
import re
import time
import requests
import tweepy
import feedparser
import anthropic
import trafilatura
from dotenv import load_dotenv
 
load_dotenv()
 
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
 
WAIT_HOURS = 3  # 返信を待つ時間（これを過ぎたら何も投稿せず終了）
 
# 発信者の視点
PERSPECTIVE = """
- 読者: AIの最新の動きを、日本語で早く正確に知りたい日本のビジネスパーソン・起業家
- 発信者: アメリカでCSを学ぶ日本人。英語圏のAIニュースを日本で一番早く、わかりやすく届ける
- 判断基準: このニュースが何を意味するか。AI業界の流れの中でどこに位置づくか
- 読者の仕事への影響は、自然につながる場合だけ書く。無理につなげない
"""
 
 
def fetch_article_text(link):
    """記事のページから本文を取り出す。失敗したら空文字を返す"""
    try:
        html = trafilatura.fetch_url(link)
        if not html:
            return ""
        body = trafilatura.extract(html) or ""
        return body[:10000]  # 長すぎる記事は先頭だけ使う
    except Exception as err:
        print("本文の取得に失敗:", err)
        return ""
 
 
# ---------- 1. 新しい記事を探す ----------
url = "https://techcrunch.com/category/artificial-intelligence/feed/"
feed = feedparser.parse(url)
 
posted = []
if os.path.exists("posted.txt"):
    with open("posted.txt") as f:
        posted = f.read().splitlines()
 
# まだ投稿していない記事を最大10本集める
candidates = [e for e in feed.entries if e.link not in posted][:10]
 
if not candidates:
    print("新しい記事がない")
    exit()
 
client = anthropic.Anthropic()
 
# 候補が1本ならそれを使う。2本以上ならAIに一番いいものを選ばせる
if len(candidates) == 1:
    entry = candidates[0]
else:
    article_list = ""
    for i, e in enumerate(candidates, start=1):
        summary = e.get("summary", "")[:300]
        article_list += f"{i}. {e.title}\n{summary}\n\n"
 
    select_prompt = f"""次のAIニュースの中から、Xで投稿するものを1本選んでください。
 
読者と発信者:
{PERSPECTIVE}
 
選ぶ基準（上ほど優先）:
- AI業界の流れを変える大きなニュース（大手AI企業の新製品・新機能、料金の変更、大きな資金調達や上場、規制など）
- 多くの人が使うAIツールや機能の変化
- 日本ではまだあまり知られていないが、今後大きくなりそうな動き
 
避けるもの:
- AIとの関係が薄い、特定の業界だけの話
- 小さな企業の細かいニュース
 
記事一覧:
{article_list}
選んだ記事の番号だけを出力してください。"""
 
    res = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=8000,
        messages=[{"role": "user", "content": select_prompt}],
    )
    answer = ""
    for block in res.content:
        if block.type == "text":
            answer += block.text
 
    # 返ってきた文字から番号を取り出す。うまく取れなければ1本目を使う
    numbers = re.findall(r"\d+", answer)
    index = int(numbers[0]) if numbers else 1
    if not 1 <= index <= len(candidates):
        index = 1
    entry = candidates[index - 1]
 
print("選んだ記事:", entry.title)
 
# 記事の本文を取ってくる。取れなければRSSの概要を使う
article_text = fetch_article_text(entry.link)
if article_text:
    print(f"本文を取得した（{len(article_text)}文字）")
else:
    article_text = entry.get("summary", "")
    print("本文が取れなかったので、概要を使う")
 
 
# ---------- 2. AIに3案作らせる ----------
prompt = f"""次の英語のAIニュースを読んで、日本語のXの投稿文を3案作ってください。
 
発信者の設定:
{PERSPECTIVE}
 
3案はそれぞれ次の型で書く:
- 案1【速報】何が起きたかを固有名詞や数字で具体的に書く。最後に「なぜこれが大きいか」を一言。140文字以内
- 案2【解説】このニュースの背景と、これから何が起きそうかを書く。読者の仕事に直接関係する場合はそれも書く。250〜350文字
- 案3【意見】発信者としての意見を一つ言い切り、その理由を書く。多くの人が当然だと思っていることに反対するか、意外な角度から言う。250〜350文字
 
全案共通の条件:
- 最初に何が起きたかを1文で書き、その後に型に沿った内容を書く
- 1行目は、読者がスクロールを止めたくなる書き出しにする
- 長い案は、最初の2〜3行だけで要点と続きを読みたくなる理由が伝わるようにする
- 記事の中の具体的な名前・数字・事実を使う。ぼんやりした説明で済ませない
- 「〜という記事が出た」のように記事そのものを話題にせず、記事の中身を直接書く
- 常体（だ・である調）、短い文、適度に改行する
- 記事に書かれていない事実や数字は書かない
- 英語の専門用語はそのまま使わず、日本語で分かる言い方にする
- 発信者の体験談（使ってみた、など）は作らない
- 自分の意見への反論を先回りして和らげない
- 相手を見下す言い方はしない
- 「〜すべき」「注目が集まる」など、誰でも言える締めは使わない。最後の一文は具体的な行動か、意外な一言で終わる
- ハッシュタグと絵文字は使わない
- 案と案の間は「===」だけの行で区切る
- 投稿文以外は何も出力しない
 
タイトル: {entry.title}
 
記事の本文:
{article_text}
"""
 
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