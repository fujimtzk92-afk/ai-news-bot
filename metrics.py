"""週1回、自分の投稿の数字を記録し、案の型とリンクの置き場所ごとにTelegramへ報告する。投稿はしない。"""
import csv
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone

import tweepy
from dotenv import load_dotenv

from main import POSTS_LOG_FILE, send_telegram

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
        })
    return rows


def append_rows(rows):
    is_new = not os.path.exists(METRICS_FILE)
    with open(METRICS_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=METRICS_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


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


def build_report(posts, rows, now):
    followers = rows[0]["followers"] if rows else "不明"
    lines = [f"週次の数字（フォロワー {followers}人）"]
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
    report = build_report(load_posts_log(), rows, now)
    print(report)
    if dry_run:
        return
    append_rows(rows)
    send_telegram(report)


if __name__ == "__main__":
    main()
