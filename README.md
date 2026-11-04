# AI News Bot

## What it does
This bot turns AI news into Japanese drafts once a day. I choose one draft on Telegram, and the bot posts it on X.

## How it works
Get news from several RSS feeds -> skip posted and old articles -> Claude selects one article -> Claude writes three drafts -> I choose on Telegram -> post on X -> record the result in `posted.txt` and `posts.csv`

Once a week, `metrics.py` saves the numbers for my posts to `metrics.csv` and sends a report to Telegram. It compares draft types and link placement (in the post or in a reply).

## Tech stack
Python, Claude API, X API (tweepy), RSS (feedparser), GitHub Actions

## What I learned
- **Keeping keys safe:** I need to put access keys outside the code to avoid exposing them. I put them in a `.env` file and excluded it with `.gitignore`. On GitHub, I used Secrets.
- **Avoiding duplicate posts:** I made `posted.txt` to record URLs in order to avoid posting the same article twice.
- **Claude's response format:** At first, I could not get the summary. The first block of Claude's response was a thinking block, not the answer. I fixed it by taking only the blocks with the type `text`.
- **Scheduled runs:** Scheduled runs on GitHub Actions are sometimes delayed or skipped. I changed the time from :30 to :47 because the start and middle of each hour are busy.

## How to run
1. Clone this repository.
2. Install the libraries.