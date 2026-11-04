"""週1回、自分の投稿の数字を記録し、伸びと分析をTelegramへ報告する。投稿はしない。"""
import csv
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone

import anthropic
import tweepy
from dotenv import load_dotenv

from main import POSTS_LOG_FILE, ask_claude, send_telegram

METRICS_FILE = "metrics.csv"
METRICS_FIELDS = [
    "fetched_at", "tweet_id", "created_at", "impressions",
    "likes", "reposts", "replies", "bookmarks", "followers",
]
MAX_POSTS = 30  # 1件ごとに課金されるので、週1回に必要な分だけ読む
MIN_AGE_DAYS = 3  # 投稿直後は表示回数が伸びきっていないので比べない
DRAFT_NAMES = {"1": "案1 速報", "2": "案2 解説", "3": "案3 意見"}


def fetch_rows(x_client, now):
    me = x_client.get_me(user_auth=True, user_fields=["public_metrics"]).data
    res = x_client.get_users_tweets(
        me.id,
        user_auth=True,
        max_results=MAX_POSTS,
        exclude=["replies", "retweets"],
        tweet_fields=["created_at", "public_metrics"],
    )
    rows = []
    for tweet in res.data or []:
        m = tweet.public_metrics
        rows.append({
            "fetched_at": now.strftime("%Y-%m-%d %H:%M"),
            "tweet_id": tweet.id,
            "created_at": tweet.created_at.strftime("%Y-%m-%d %H:%M"),
            "impressions": m.get("impression_count", 0),
            "likes": m["like_count"],
            "reposts": m["retweet_count"],
            "replies": m["reply_count"],
            "bookmarks": m.get("bookmark_count", 0),
            "followers": me.public_metrics["followers_count"],
            "text": tweet.text,  # CSVには残さず、分析にだけ使う
        })
    return rows


def append_rows(rows):
    is_new = not os.path.exists(METRICS_FILE)
    with open(METRICS_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=METRICS_FIELDS, extrasaction="ignore")
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def load_snapshots():
    """これまでの記録を、取得した日時ごとにまとめて古い順に返す"""
    if not os.path.exists(METRICS_FILE):
        return []
    snapshots = {}
    with open(METRICS_FILE, newline="") as f:
        for row in csv.DictReader(f):
            snapshots.setdefault(row["fetched_at"], []).append(row)
    return [snapshots[key] for key in sorted(snapshots)]


def gained_impressions(current, previous):
    """前回の記録から増えた表示回数。新しい投稿は全部が増加分になる"""
    before = {str(r["tweet_id"]): int(r["impressions"]) for r in previous}
    return sum(int(r["impressions"]) - before.get(str(r["tweet_id"]), 0) for r in current)


def build_growth_lines(rows, snapshots, now):
    if not rows:
        return []
    lines = []
    if snapshots:
        previous = snapshots[-1]
        since = previous[0]["fetched_at"][:10]
        change = int(rows[0]["followers"]) - int(previous[0]["followers"])
        lines.append(f"{since}からのフォロワー増減: {change:+d}人")
        gained = gained_impressions(rows, previous)
        line = f"{since}から増えた表示回数: {gained}"
        if len(snapshots) >= 2:
            line += f"（その前の期間は {gained_impressions(previous, snapshots[-2])}）"
        lines.append(line)
    else:
        lines.append("前回の記録がないので、増減は次回から出る")

    cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
    recent = [r for r in rows if r["created_at"] >= cutoff]
    lines.append(f"直近7日の投稿: {len(recent)}件")
    if recent:
        top = max(recent, key=lambda r: int(r["impressions"]))
        preview = " ".join(top.get("text", "").split())[:40]
        lines.append(f"直近7日で最も表示された投稿: {top['impressions']}回「{preview}」")
        lines.append(f"https://x.com/i/status/{top['tweet_id']}")
    return lines


def load_posts_log():
    if not os.path.exists(POSTS_LOG_FILE):
        return []
    with open(POSTS_LOG_FILE, newline="") as f:
        return [row for row in csv.DictReader(f) if row["status"] == "posted"]


def group_medians(posts, rows, key, now):
    """投稿から3日以上たったものだけを、keyの値ごとに中央値でまとめる"""
    cutoff = now - timedelta(days=MIN_AGE_DAYS)
    by_id = {str(r["tweet_id"]): r for r in rows}
    groups = {}
    for post in posts:
        row = by_id.get(str(post["tweet_id"]))
        if not row:
            continue
        created = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M")
        if created.replace(tzinfo=timezone.utc) > cutoff:
            continue
        groups.setdefault(post[key], []).append(int(row["impressions"]))
    return {k: (statistics.median(v), len(v)) for k, v in sorted(groups.items())}


def build_report(posts, rows, now, snapshots=()):
    followers = rows[0]["followers"] if rows else "不明"
    lines = [f"週次の数字（フォロワー {followers}人）"]
    lines.extend(build_growth_lines(rows, snapshots, now))
    if rows:
        lines.append(f"直近{len(rows)}件の表示回数の中央値: "
                     f"{statistics.median(int(r['impressions']) for r in rows):g}")
        lines.append(f"いいね合計 {sum(int(r['likes']) for r in rows)} / "
                     f"返信合計 {sum(int(r['replies']) for r in rows)}")

    for title, key, names in [
        ("リンクの置き場所", "link_mode", {}),
        ("案の型", "draft_choice", DRAFT_NAMES),
    ]:
        medians = group_medians(posts, rows, key, now)
        if medians:
            lines.append(f"\n{title}（表示回数の中央値、件数）")
            for value, (median, count) in medians.items():
                lines.append(f"- {names.get(value, value)}: {median:g}（{count}件）")
    return "\n".join(lines)


def build_analysis_prompt(report):
    return f"""次はXアカウントの週次の数字です。発信者に向けて、日本語の常体で3行以内の分析を書いてください。

1行目: 数字で分かる変化
2行目: 考えられる理由。必ず「仮説」と書く
3行目: 次の1週間で試すことを1つ

条件:
- 下の数字に書かれていないことを事実として書かない
- 比べる件数がどちらか10件未満なら、差について「件数が少なくまだ判断できない」と書く
- 表示回数が少しでも増えたら成功、のように甘く評価しない。伸びていなければそう書く
- 前置きと見出しは書かない

数字:
{report}"""


def write_analysis(client, report):
    """分析に失敗しても、数字の報告は届ける"""
    try:
        return ask_claude(client, build_analysis_prompt(report), 2000).strip()
    except anthropic.APIError as err:
        print("分析に失敗:", type(err).__name__)
        return "分析を作れなかった。数字だけ報告する。"


def main():
    load_dotenv()
    dry_run = "--dry-run" in sys.argv
    now = datetime.now(timezone.utc)
    x_client = tweepy.Client(
        consumer_key=os.getenv("X_CONSUMER_KEY"),
        consumer_secret=os.getenv("X_CONSUMER_SECRET"),
        access_token=os.getenv("X_ACCESS_TOKEN"),
        access_token_secret=os.getenv("X_ACCESS_TOKEN_SECRET"),
    )
    rows = fetch_rows(x_client, now)
    report = build_report(load_posts_log(), rows, now, load_snapshots())
    report += "\n\n分析\n" + write_analysis(anthropic.Anthropic(), report)
    print(report)
    if dry_run:
        return
    append_rows(rows)
    send_telegram(report)


if __name__ == "__main__":
    main()
