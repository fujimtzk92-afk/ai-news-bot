import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import feedparser
import pytest
import tweepy

import auto_post
import metrics

NOW = datetime(2026, 11, 12, 3, 0, tzinfo=timezone.utc)  # 日本時間の12:00
FeedEntry = feedparser.util.FeedParserDict


class FakeClaude:
    """決めた文を順に返す。受け取った指示文も覚えておく"""

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []
        self.messages = self

    def create(self, **kwargs):
        self.prompts.append(kwargs["messages"][0]["content"])
        text = self.answers.pop(0)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


class FakeX:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def create_tweet(self, **kwargs):
        if self.fail:
            raise tweepy.TweepyException("boom")
        self.calls.append(kwargs)
        return SimpleNamespace(data={"id": "111"})


def log_row(now, kind="news", minutes_ago=120, **extra):
    posted = now - timedelta(minutes=minutes_ago)
    row = {
        "date_jst": posted.astimezone(auto_post.JST).strftime("%Y-%m-%d"),
        "posted_at": int(posted.timestamp()),
        "kind": kind, "theme": "", "tweet_id": "1", "article_url": "", "feed": "",
        "text": "前の投稿",
    }
    row.update(extra)
    return row


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """記録を一時の場所に置き、外への通信をすべて止める"""
    monkeypatch.setattr(auto_post, "AUTO_LOG_FILE", str(tmp_path / "auto.jsonl"))
    monkeypatch.setattr(auto_post, "load_posted", lambda: [])
    monkeypatch.setattr(auto_post, "collect_candidates", lambda used, now: [])
    monkeypatch.setattr(auto_post, "fetch_article_text", lambda link: "article body")
    sent = []
    monkeypatch.setattr(auto_post, "send_telegram", sent.append)
    return sent


def test_next_kind_rotates_through_the_day():
    log = [log_row(NOW, minutes_ago=60 * (i + 1)) for i in range(3)]
    assert auto_post.next_kind([], NOW) == auto_post.KINDS[0]
    assert auto_post.next_kind(log, NOW) == auto_post.KINDS[3]


def test_next_kind_stops_at_daily_limit():
    log = [log_row(NOW, minutes_ago=60) for _ in range(auto_post.POSTS_PER_DAY)]
    assert auto_post.next_kind(log, NOW) is None


def test_next_kind_does_not_count_yesterday():
    log = [log_row(NOW, minutes_ago=60 * 24) for _ in range(auto_post.POSTS_PER_DAY)]
    assert auto_post.next_kind(log, NOW) == auto_post.KINDS[0]


def test_next_kind_waits_after_a_recent_post():
    assert auto_post.next_kind([log_row(NOW, minutes_ago=5)], NOW) is None


@pytest.mark.parametrize("text", [
    "詳しくは https://example.com で",
    "example.com を見た",
    "Character.AI が発表した",
    "@someone に聞きたい",
    "これは大きい #AI",
    "",
    "あ" * 161,
])
def test_find_problem_rejects(text):
    assert auto_post.find_problem(text, "news") is not None


@pytest.mark.parametrize("text", [
    "GPT-5.5が出た。値段は3.5ドル。",
    "AIに任せる範囲を、どこで線引きしている？",
])
def test_find_problem_accepts_plain_text(text):
    assert auto_post.find_problem(text, "news") is None


def test_pick_theme_avoids_recent_ones():
    recent = auto_post.QUESTION_THEMES[:6]
    log = [log_row(NOW, kind="question", theme=t) for t in recent]
    for seed in range(20):
        assert auto_post.pick_theme(log, random.Random(seed)) not in recent


def test_run_posts_and_records(setup, monkeypatch):
    x = FakeX()
    claude = FakeClaude(["AIの速報だ。"])
    entry = FeedEntry(link="https://a.example/1", title="t", summary="s", feed_name="Feed")
    monkeypatch.setattr(auto_post, "collect_candidates", lambda used, now: [entry])

    assert auto_post.run(claude, lambda: x, NOW) == "AIの速報だ。"

    assert x.calls == [{"text": "AIの速報だ。"}]
    row = auto_post.load_log()[0]
    assert row["kind"] == "news"
    assert row["article_url"] == "https://a.example/1"
    assert row["date_jst"] == "2026-11-12"
    assert "article body" in claude.prompts[0]


def test_run_falls_back_to_question_without_articles(setup):
    x = FakeX()
    auto_post.run(FakeClaude(["どこまで任せる？"]), lambda: x, NOW)
    assert auto_post.load_log()[0]["kind"] == "question"
    assert auto_post.load_log()[0]["theme"] in auto_post.QUESTION_THEMES


def test_run_does_not_reuse_an_article(setup, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        auto_post, "collect_candidates", lambda used, now: seen.update(used=used) or []
    )
    monkeypatch.setattr(auto_post, "load_posted", lambda: ["https://a.example/old"])
    # 昨日の投稿にして、今日の1本目（記事を使う型）になるようにする
    auto_post.append_log(log_row(NOW, minutes_ago=60 * 24, article_url="https://a.example/1"))
    auto_post.run(FakeClaude(["問いだ。"]), FakeX, NOW)
    assert seen["used"] == ["https://a.example/old", "https://a.example/1"]


def test_run_retries_then_posts_clean_text(setup):
    x = FakeX()
    claude = FakeClaude(["https://example.com を見て", "きれいな文だ。"])
    assert auto_post.run(claude, lambda: x, NOW) == "きれいな文だ。"
    assert x.calls == [{"text": "きれいな文だ。"}]


def test_run_skips_when_text_never_passes(setup):
    x = FakeX()
    claude = FakeClaude(["example.com", "example.com"])
    assert auto_post.run(claude, lambda: x, NOW) is None
    assert x.calls == []
    assert auto_post.load_log() == []
    assert len(setup) == 1


def test_run_does_nothing_at_daily_limit(setup):
    for _ in range(auto_post.POSTS_PER_DAY):
        auto_post.append_log(log_row(NOW))
    claude = FakeClaude([])
    assert auto_post.run(claude, FakeX, NOW) is None
    assert claude.prompts == []


def test_dry_run_does_not_post_or_record(setup):
    def no_x():
        raise AssertionError("dry-runでXに触ってはいけない")

    assert auto_post.run(FakeClaude(["問いだ。"]), no_x, NOW, dry_run=True) == "問いだ。"
    assert auto_post.load_log() == []
    assert setup == []


def test_failed_post_is_reported_and_not_recorded(setup):
    with pytest.raises(tweepy.TweepyException):
        auto_post.run(FakeClaude(["問いだ。"]), lambda: FakeX(fail=True), NOW)
    assert auto_post.load_log() == []
    assert len(setup) == 1


def test_metrics_reads_auto_posts(setup, tmp_path, monkeypatch):
    monkeypatch.setattr(metrics, "POSTS_LOG_FILE", str(tmp_path / "none.csv"))
    auto_post.append_log(log_row(NOW, kind="question", tweet_id="42"))
    assert metrics.load_posts_log() == [
        {"tweet_id": "42", "draft_choice": "auto-question", "link_mode": "none"}
    ]
