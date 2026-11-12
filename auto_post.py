"""1日に決まった回数、リンクなしの投稿を自動で出す。人の確認は入らない。

リンク入りの投稿は料金が高いので、ここでは出さない（それは main.py の役目）。
`python auto_post.py --dry-run` は文を作って表示するだけで、投稿も記録もしない。
"""
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import anthropic
import tweepy

from main import (
    PERSPECTIVE,
    ask_claude,
    collect_candidates,
    fetch_article_text,
    load_posted,
    select_entry,
    send_telegram,
)

AUTO_LOG_FILE = "auto_posts.jsonl"
POSTS_PER_DAY = 10   # 1日の上限。予定より多く動いても、これ以上は投稿しない
MIN_GAP_MINUTES = 40  # 手動の実行が重なっても、続けて投稿しない
RECENT_TO_AVOID = 30  # 似た投稿を避けるために見せる、最近の投稿の数
MAX_ATTEMPTS = 2
JST = timezone(timedelta(hours=9))

# 1日の中で回す順番。記事がない時は「問い」に切り替える
KINDS = [
    "news", "question", "opinion", "news", "explain",
    "question", "news", "opinion", "question", "explain",
]

KIND_RULES = {
    "news": (
        "【速報】何が起きたかを、固有名詞や数字で具体的に書く。"
        "最後に「なぜこれが大きいか」を一言。140文字以内",
        160,
    ),
    "explain": (
        "【解説】このニュースの背景と、これから何が起きそうかを書く。"
        "読者の仕事に直接関係する場合はそれも書く。200〜300文字",
        350,
    ),
    "opinion": (
        "【意見】発信者としての意見を一つ言い切り、その理由を書く。"
        "多くの人が当然だと思っていることに反対するか、意外な角度から言う。200〜300文字",
        350,
    ),
    "question": (
        "【問い】読者が自分の答えを考えたくなる問いを一つ書く。答えが人によって分かれる問いにする。"
        "発信者の仮の答えを一言添えてもよい。140文字以内",
        160,
    ),
}

QUESTION_THEMES = [
    "仕事でAIに任せる範囲と、人が確かめる範囲",
    "AIがある時代の勉強のしかた",
    "AIと就職、キャリア",
    "AIへの頼み方、指示の出し方",
    "AIの間違いとの付き合い方",
    "AIが書いた文章や画像の見分け",
    "AIに作業を任せる「エージェント」",
    "AIと起業、小さな事業",
    "AIと英語学習",
    "AIと創作",
    "AIの使い分けと料金",
    "AIが広まった後に価値が上がる力",
]

COMMON_RULES = """- 1行目は、読者がスクロールを止めたくなる書き出しにする
- 常体（だ・である調）、短い文、適度に改行する
- 英語の専門用語はそのまま使わず、日本語で分かる言い方にする
- 発信者の体験談（使ってみた、など）は作らない
- 相手を見下す言い方はしない
- 「〜すべき」「注目が集まる」など、誰でも言える締めは使わない
- URL、「〇〇.com」のようなサイトの住所、@から始まるアカウント名は書かない
- ハッシュタグと絵文字は使わない
- 下の「最近の投稿」と同じ話題、同じ書き出し、同じ締め方を避ける
- 投稿文だけを出力する。見出しや【】の型の名前は付けない"""

# Xがリンクとして扱う書き方。リンク入りは料金が高いので、見つけたら投稿しない
LINK_PATTERN = re.compile(
    r"https?://|www\.|[A-Za-z0-9-]+\.(?:com|ai|io|org|net|jp|co|dev|app|gov|edu|me|tv|xyz)\b",
    re.IGNORECASE,
)


# ---------- 記録 ----------
def load_log():
    if not os.path.exists(AUTO_LOG_FILE):
        return []
    with open(AUTO_LOG_FILE, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def append_log(row):
    with open(AUTO_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------- 今回、何を出すか ----------
def next_kind(log, now):
    """今回の型を返す。今日の上限に達したか、前の投稿から間がなければ None"""
    today = now.astimezone(JST).strftime("%Y-%m-%d")
    todays = [r for r in log if r["date_jst"] == today]
    if len(todays) >= POSTS_PER_DAY:
        return None
    if log and now.timestamp() - log[-1]["posted_at"] < MIN_GAP_MINUTES * 60:
        return None
    return KINDS[len(todays) % len(KINDS)]


def pick_theme(log, rng=random):
    """最近使った題を避けて、問いの題を選ぶ"""
    used = [r.get("theme") for r in log[-6:]]
    fresh = [t for t in QUESTION_THEMES if t not in used]
    return rng.choice(fresh or QUESTION_THEMES)


def recent_texts(log):
    lines = [" ".join(r["text"].split())[:80] for r in log[-RECENT_TO_AVOID:]]
    return "\n".join(f"- {line}" for line in lines) or "（まだない）"


# ---------- 文を作る ----------
def build_prompt(kind, recent, entry=None, article_text="", theme=""):
    rule, _ = KIND_RULES[kind]
    head = f"""日本語のXの投稿文を1つ書いてください。

発信者の設定:
{PERSPECTIVE}

今回の型:
{rule}

条件:
{COMMON_RULES}
"""
    if kind == "question":
        return head + f"""- 特定の製品の新しい情報、数字、日付、最近の出来事は書かない（確かめられないため）

問いの題: {theme}

最近の投稿:
{recent}
"""
    return head + f"""- 最初に何が起きたかを1文で書く
- 記事の中の具体的な名前・数字・事実を使う。記事に書かれていない事実や数字は書かない
- 「〜という記事が出た」のように記事そのものを話題にせず、記事の中身を直接書く

最近の投稿:
{recent}

タイトル: {entry.title}

記事の本文:
{article_text}
"""


def find_problem(text, kind):
    """投稿してはいけない理由を返す。問題がなければ None"""
    if not text:
        return "文が空"
    if len(text) > KIND_RULES[kind][1]:
        return f"長すぎる（{len(text)}文字）"
    if LINK_PATTERN.search(text):
        return "リンクとして扱われる書き方がある"
    if "@" in text or "＠" in text:
        return "アカウント名がある"
    if "#" in text or "＃" in text:
        return "ハッシュタグがある"
    if "===" in text:
        return "区切りの記号が残っている"
    return None


def write_post(client, kind, prompt):
    for _ in range(MAX_ATTEMPTS):
        text = ask_claude(client, prompt, 8000).strip()
        problem = find_problem(text, kind)
        if problem is None:
            return text
        print("作り直す:", problem)
    return None


def choose_material(client, kind, log, now):
    """記事を使う型なら記事を選ぶ。使える記事がなければ「問い」に切り替える"""
    if kind == "question":
        return kind, None, ""
    used = load_posted() + [r["article_url"] for r in log if r.get("article_url")]
    candidates = collect_candidates(used, now.timestamp())
    if not candidates:
        print("使える記事がないので、問いにする")
        return "question", None, ""
    entry = select_entry(client, candidates, used)
    article_text = fetch_article_text(entry.link) or entry.get("summary", "")
    return kind, entry, article_text


# ---------- 実行 ----------
def run(client, make_x_client, now, dry_run=False, rng=random):
    """投稿した文を返す。投稿しなかった時は None"""
    log = load_log()
    kind = next_kind(log, now)
    if kind is None:
        print("今日の上限に達したか、前の投稿から間がないので、何もしない")
        return None

    kind, entry, article_text = choose_material(client, kind, log, now)
    theme = pick_theme(log, rng) if kind == "question" else ""
    prompt = build_prompt(kind, recent_texts(log), entry, article_text, theme)
    text = write_post(client, kind, prompt)
    if text is None:
        print("投稿できる文が作れなかった")
        if not dry_run:
            send_telegram(f"自動投稿：投稿できる文が作れず、今回は飛ばした（型: {kind}）")
        return None

    print(f"型: {kind} / {len(text)}文字")
    print(text)
    if dry_run:
        return text

    try:
        tweet_id = make_x_client().create_tweet(text=text).data["id"]
    except tweepy.TweepyException as err:
        send_telegram(f"自動投稿：Xへの投稿に失敗した: {err}")
        raise
    append_log({
        "date_jst": now.astimezone(JST).strftime("%Y-%m-%d"),
        "posted_at": int(now.timestamp()),
        "kind": kind,
        "theme": theme,
        "tweet_id": str(tweet_id),
        "article_url": entry.link if entry else "",
        "feed": entry["feed_name"] if entry else "",
        "text": text,
    })
    return text


def make_x_client():
    return tweepy.Client(
        consumer_key=os.getenv("X_CONSUMER_KEY"),
        consumer_secret=os.getenv("X_CONSUMER_SECRET"),
        access_token=os.getenv("X_ACCESS_TOKEN"),
        access_token_secret=os.getenv("X_ACCESS_TOKEN_SECRET"),
    )


def main():
    dry_run = "--dry-run" in sys.argv
    try:
        run(anthropic.Anthropic(), make_x_client, datetime.now(timezone.utc), dry_run)
    except tweepy.TweepyException:
        sys.exit(1)
    except anthropic.APIError as err:
        print("文の作成に失敗:", type(err).__name__)
        sys.exit(1)


if __name__ == "__main__":
    main()
