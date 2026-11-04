import calendar
import csv
import os
import re
import sys
import time
from datetime import datetime, timezone

import anthropic
import feedparser
import requests
import trafilatura
import tweepy
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

WAIT_HOURS = 3  # 返信を待つ時間（これを過ぎたら何も投稿せず終了）
MODEL = "claude-haiku-5-5"

POSTED_FILE = "posted.txt"
POSTS_LOG_FILE = "posts.csv"
POSTS_LOG_FIELDS = [
    "date_utc", "status", "tweet_id", "article_url", "feed",
    "draft_choice", "link_mode", "length",
]

# OpenAIとMIT Technology Reviewは本文を取得できず、中身の薄い案になるので入れない
FEEDS = {
    "Google AI": "https://blog.google/technology/ai/rss/",
    "Google DeepMind": "https://deepmind.google/blog/rss.xml",
    "TechCrunch": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "Ars Technica": "https://arstechnica.com/ai/feed/",
}
MAX_PER_FEED = 5
MAX_AGE_DAYS = 3
RECENT_POSTS_TO_AVOID = 10

# 発信者の視点
PERSPECTIVE = """
- 読者: AIの最新の動きを、日本語で早く正確に知りたい日本のビジネスパーソン・起業家
- 発信者: アメリカでCSを学ぶ日本人。英語圏のAIニュースを日本で一番早く、わかりやすく届ける
- 判断基準: このニュースが何を意味するか。AI業界の流れの中でどこに位置づくか
- 読者の仕事への影響は、自然につながる場合だけ書く。無理につなげない
"""


# ---------- 1. 新しい記事を探す ----------
def load_posted():
    if not os.path.exists(POSTED_FILE):
        return []
    with open(POSTED_FILE) as f:
        return f.read().splitlines()


def is_recent(entry, now):
    """公開日が新しい記事だけ通す。公開日がない記事は古さが分からないので除く"""
    published = entry.get("published_parsed")
    if not published:
        return False
    return now - calendar.timegm(published) <= MAX_AGE_DAYS * 86400


def collect_candidates(posted, now, parse=feedparser.parse):
    candidates = []
    for name, url in FEEDS.items():
        # 1つの配信元が落ちていても、他の配信元で続ける
        entries = parse(url).entries
        fresh = [e for e in entries if e.link not in posted and is_recent(e, now)]
        for entry in fresh[:MAX_PER_FEED]:
            entry["feed_name"] = name
            candidates.append(entry)
    return candidates


def build_select_prompt(candidates, posted):
    article_list = ""
    for i, e in enumerate(candidates, start=1):
        summary = e.get("summary", "")[:300]
        article_list += f"{i}. [{e['feed_name']}] {e.title}\n{summary}\n\n"
    recent = "\n".join(posted[-RECENT_POSTS_TO_AVOID:])

    return f"""次のAIニュースの中から、Xで投稿するものを1本選んでください。

読者と発信者:
{PERSPECTIVE}

選ぶ基準（上ほど優先）:
- AI業界の流れを変える大きなニュース（大手AI企業の新製品・新機能、料金の変更、大きな資金調達や上場、規制など）
- 多くの人が使うAIツールや機能の変化
- 日本ではまだあまり知られていないが、今後大きくなりそうな動き
- 同じ話題が複数あるときは、企業の公式発表を選ぶ

避けるもの:
- AIとの関係が薄い、特定の業界だけの話
- 小さな企業の細かいニュース
- 最近投稿した記事と同じ話題

最近投稿した記事のURL:
{recent}

記事一覧:
{article_list}
選んだ記事の番号だけを出力してください。"""


def pick_index(answer, count):
    """返ってきた文字から番号を取り出す。うまく取れなければ1本目を使う"""
    numbers = re.findall(r"\d+", answer)
    index = int(numbers[0]) if numbers else 1
    return index if 1 <= index <= count else 1


def ask_claude(client, prompt, max_tokens):
    message = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in message.content if b.type == "text")


def select_entry(client, candidates, posted):
    if len(candidates) == 1:
        return candidates[0]
    answer = ask_claude(client, build_select_prompt(candidates, posted), 8000)
    return candidates[pick_index(answer, len(candidates)) - 1]


def fetch_article_text(link):
    """記事のページから本文を取り出す。失敗したら空文字を返す"""
    try:
        html = trafilatura.fetch_url(link)
        if not html:
            return ""
        body = trafilatura.extract(html) or ""
        return body[:10000]  # 長すぎる記事は先頭だけ使う
    except Exception as err:
        print("本文の取得に失敗:", type(err).__name__)
        return ""


# ---------- 2. AIに3案作らせる ----------
def build_draft_prompt(entry, article_text):
    return f"""次の英語のAIニュースを読んで、日本語のXの投稿文を3案作ってください。

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


def parse_drafts(text):
    return [d.strip() for d in text.split("===") if d.strip()]


# ---------- 3. Telegramで3案を送り、返信を待つ ----------
def send_telegram(msg):
    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=30,
        )
    except requests.RequestException as err:
        # URLにトークンが入るので、エラーの本文はログに出さない
        print("Telegramへの送信に失敗:", type(err).__name__)


def get_updates(offset=None, timeout=0):
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    try:
        res = requests.get(
            f"{TELEGRAM_API}/getUpdates", params=params, timeout=timeout + 15
        ).json()
    except (requests.RequestException, ValueError) as err:
        print("Telegramの受信に失敗:", type(err).__name__)
        time.sleep(5)
        return []
    if not res.get("ok"):
        print("Telegramの受信に失敗:", res.get("description"))
        time.sleep(5)
        return []
    return res["result"]


def is_from_owner(update, owner_chat_id):
    """他人がbotに番号を送っても投稿されないよう、自分のチャットだけ受け付ける"""
    chat_id = update.get("message", {}).get("chat", {}).get("id")
    return str(chat_id) == str(owner_chat_id)


def parse_choice(reply, draft_count):
    reply = reply.strip()
    if reply.isdigit() and 0 <= int(reply) <= draft_count:
        return int(reply)
    return None


def wait_for_choice(offset, draft_count):
    deadline = time.time() + WAIT_HOURS * 3600
    while time.time() < deadline:
        for update in get_updates(offset, timeout=30):
            offset = update["update_id"] + 1
            if not is_from_owner(update, TELEGRAM_CHAT_ID):
                continue
            reply = update.get("message", {}).get("text", "")
            choice = parse_choice(reply, draft_count)
            if choice is not None:
                return choice
            send_telegram(f"0〜{draft_count}の番号で返信して")
    return None


# ---------- 4. Xに投稿する ----------
def link_mode_for(day):
    """リンクの置き場所を日ごとに交互に変え、表示回数を比べる"""
    return "inline" if day.toordinal() % 2 == 0 else "reply"


def build_post(draft, link, feed, mode):
    """本文と、リンクを入れる返信の文を返す"""
    if mode == "inline":
        return f"{draft}\n\n{link}", None
    return draft, f"出典: {feed}\n{link}"


def post_to_x(x_client, text, reply_text):
    """投稿のIDを返す。返信だけ失敗しても本文は投稿済みなので止めない"""
    tweet_id = x_client.create_tweet(text=text).data["id"]
    if reply_text:
        try:
            x_client.create_tweet(text=reply_text, in_reply_to_tweet_id=tweet_id)
        except tweepy.TweepyException as err:
            send_telegram(f"出典の返信に失敗した（本文は投稿済み）: {err}")
    return tweet_id


def mark_posted(link):
    with open(POSTED_FILE, "a") as f:
        f.write(link + "\n")


def log_post(row):
    is_new = not os.path.exists(POSTS_LOG_FILE)
    with open(POSTS_LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=POSTS_LOG_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def main():
    posted = load_posted()
    candidates = collect_candidates(posted, time.time())
    if not candidates:
        print("新しい記事がない")
        return

    client = anthropic.Anthropic()
    entry = select_entry(client, candidates, posted)
    print("選んだ記事:", entry.title)

    # 記事の本文を取ってくる。取れなければRSSの概要を使う
    article_text = fetch_article_text(entry.link)
    warning = ""
    if article_text:
        print(f"本文を取得した（{len(article_text)}文字）")
    else:
        article_text = entry.get("summary", "")
        warning = "※本文を取得できず、短い概要だけで書いた案です\n\n"
        print("本文が取れなかったので、概要を使う")

    drafts = parse_drafts(ask_claude(client, build_draft_prompt(entry, article_text), 16000))
    if not drafts:
        print("案が作れなかった")
        return

    # 古いメッセージを読み飛ばす（前回の返信を拾わないため）
    updates = get_updates()
    offset = updates[-1]["update_id"] + 1 if updates else None

    msg = f"【{entry['feed_name']}】{entry.title}\n{entry.link}\n\n{warning}"
    for i, d in enumerate(drafts, start=1):
        msg += f"--- 案{i} ---\n{d}\n\n"
    msg += "番号で返信 → 投稿 / 0 → スキップ"
    send_telegram(msg)
    print("Telegramに送った。返信待ち...")

    choice = wait_for_choice(offset, len(drafts))
    if choice is None:
        print("時間切れ。投稿しなかった")
        return

    now = datetime.now(timezone.utc)
    row = {
        "date_utc": now.strftime("%Y-%m-%d %H:%M"),
        "status": "skipped",
        "tweet_id": "",
        "article_url": entry.link,
        "feed": entry["feed_name"],
        "draft_choice": choice,
        "link_mode": "",
        "length": "",
    }

    if choice == 0:
        send_telegram("スキップした")
        mark_posted(entry.link)
        log_post(row)
        return

    draft = drafts[choice - 1]
    mode = link_mode_for(now.date())
    text, reply_text = build_post(draft, entry.link, entry["feed_name"], mode)
    x_client = tweepy.Client(
        consumer_key=os.getenv("X_CONSUMER_KEY"),
        consumer_secret=os.getenv("X_CONSUMER_SECRET"),
        access_token=os.getenv("X_ACCESS_TOKEN"),
        access_token_secret=os.getenv("X_ACCESS_TOKEN_SECRET"),
    )
    try:
        tweet_id = post_to_x(x_client, text, reply_text)
    except tweepy.TweepyException as err:
        send_telegram(f"Xへの投稿に失敗した: {err}")
        sys.exit(1)

    mark_posted(entry.link)
    row.update(status="posted", tweet_id=tweet_id, link_mode=mode, length=len(draft))
    log_post(row)

    send_telegram(f"投稿した（リンク: {mode}）：\n{text}")
    print("投稿した：")
    print(text)


if __name__ == "__main__":
    main()
