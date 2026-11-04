from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import feedparser
import tweepy

import main
import metrics

NOW = 1_800_000_000
FeedEntry = feedparser.util.FeedParserDict  # 記事の形を本物に合わせる


def make_entry(link, age_days=0, title="title"):
    published = datetime.fromtimestamp(NOW - age_days * 86400, timezone.utc).timetuple()
    return FeedEntry(link=link, title=title, published_parsed=published, summary="s")


def fake_parse(entries_by_url):
    return lambda url: SimpleNamespace(entries=entries_by_url.get(url, []))


class FakeX:
    def __init__(self, fail_on_reply=False, fail_on_post=False):
        self.calls = []
        self.fail_on_reply = fail_on_reply
        self.fail_on_post = fail_on_post

    def create_tweet(self, **kwargs):
        if self.fail_on_post or (self.fail_on_reply and "in_reply_to_tweet_id" in kwargs):
            raise tweepy.TweepyException("boom")
        self.calls.append(kwargs)
        return SimpleNamespace(data={"id": "111"})


def test_collect_skips_posted_and_old(monkeypatch):
    url = "https://feed.example/rss"
    monkeypatch.setattr(main, "FEEDS", {"Feed": url})
    entries = [make_entry("posted"), make_entry("old", age_days=4), make_entry("new")]
    result = main.collect_candidates(["posted"], NOW, parse=fake_parse({url: entries}))
    assert [e.link for e in result] == ["new"]
    assert result[0]["feed_name"] == "Feed"


def test_collect_drops_entry_without_date(monkeypatch):
    url = "https://feed.example/rss"
    monkeypatch.setattr(main, "FEEDS", {"Feed": url})
    entries = [FeedEntry(link="undated", title="t")]
    assert main.collect_candidates([], NOW, parse=fake_parse({url: entries})) == []


def test_collect_limits_each_feed_and_survives_empty_feed(monkeypatch):
    monkeypatch.setattr(main, "FEEDS", {"Down": "down", "Up": "up"})
    entries = [make_entry(f"link{i}") for i in range(main.MAX_PER_FEED + 3)]
    result = main.collect_candidates([], NOW, parse=fake_parse({"up": entries}))
    assert len(result) == main.MAX_PER_FEED


def test_pick_index_falls_back_to_first():
    assert main.pick_index("3", 5) == 3
    assert main.pick_index("番号は2です", 5) == 2
    assert main.pick_index("なし", 5) == 1
    assert main.pick_index("99", 5) == 1
    assert main.pick_index("0", 5) == 1


def test_parse_drafts_ignores_empty_parts():
    assert main.parse_drafts("a\n===\nb\n===\n") == ["a", "b"]
    assert main.parse_drafts("") == []


def test_parse_choice():
    assert main.parse_choice(" 2 ", 3) == 2
    assert main.parse_choice("0", 3) == 0
    assert main.parse_choice("4", 3) is None
    assert main.parse_choice("投稿して", 3) is None
    assert main.parse_choice("", 3) is None


def test_only_owner_can_choose():
    own = {"message": {"chat": {"id": 123}, "text": "1"}}
    other = {"message": {"chat": {"id": 999}, "text": "1"}}
    no_message = {"edited_message": {"chat": {"id": 123}}}
    assert main.is_from_owner(own, "123")
    assert not main.is_from_owner(other, "123")
    assert not main.is_from_owner(no_message, "123")


def test_wait_ignores_stranger_reply(monkeypatch):
    monkeypatch.setattr(main, "TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(main, "send_telegram", lambda msg: None)
    updates = [
        {"update_id": 1, "message": {"chat": {"id": 999}, "text": "1"}},
        {"update_id": 2, "message": {"chat": {"id": 123}, "text": "2"}},
    ]
    monkeypatch.setattr(main, "get_updates", lambda offset, timeout: updates)
    assert main.wait_for_choice(None, 3) == 2


def test_link_mode_alternates_by_day():
    day = date(2026, 11, 4)
    modes = {main.link_mode_for(day + timedelta(days=i)) for i in range(2)}
    assert modes == {"inline", "reply"}


def test_build_post_modes():
    assert main.build_post("本文", "URL", "Feed", "inline") == ("本文\n\nURL", None)
    assert main.build_post("本文", "URL", "Feed", "reply") == ("本文", "出典: Feed\nURL")


def test_post_to_x_replies_to_own_post():
    x = FakeX()
    assert main.post_to_x(x, "本文", "出典") == "111"
    assert x.calls == [{"text": "本文"}, {"text": "出典", "in_reply_to_tweet_id": "111"}]


def test_post_to_x_keeps_post_when_reply_fails(monkeypatch):
    sent = []
    monkeypatch.setattr(main, "send_telegram", sent.append)
    assert main.post_to_x(FakeX(fail_on_reply=True), "本文", "出典") == "111"
    assert len(sent) == 1


def test_log_post_writes_header_once(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "POSTS_LOG_FILE", str(tmp_path / "posts.csv"))
    row = dict.fromkeys(main.POSTS_LOG_FIELDS, "x")
    main.log_post(row)
    main.log_post(row)
    lines = (tmp_path / "posts.csv").read_text().splitlines()
    assert len(lines) == 3


def metric_row(tweet_id, impressions, created):
    return {
        "tweet_id": tweet_id, "impressions": impressions, "created_at": created,
        "likes": 0, "replies": 0, "followers": 14,
    }


def test_report_compares_only_mature_posts():
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)
    posts = [
        {"tweet_id": "1", "link_mode": "inline", "draft_choice": "2"},
        {"tweet_id": "2", "link_mode": "reply", "draft_choice": "2"},
        {"tweet_id": "3", "link_mode": "reply", "draft_choice": "3"},
        {"tweet_id": "4", "link_mode": "reply", "draft_choice": "3"},
    ]
    rows = [
        metric_row("1", 100, "2026-11-01 00:00"),
        metric_row("2", 300, "2026-11-02 00:00"),
        metric_row("3", 500, "2026-11-03 00:00"),
        metric_row("4", 9999, "2026-11-19 00:00"),  # 新しすぎるので比較に入らない
    ]
    report = metrics.build_report(posts, rows, now)
    assert "inline: 100（1件）" in report
    assert "reply: 400（2件）" in report
    assert "案3 意見: 500（1件）" in report


def test_report_without_data():
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)
    assert "フォロワー 不明" in metrics.build_report([], [], now)
