# AI News Bot

## What it does
This bot summarizes and translates AI-related news into Japanese and posts it on X automatically, three times a day.

## How it works
Get news from RSS -> check the URLs of posted news -> select one article -> summarize it with Claude -> post it on X -> record the URL -> repeat it three times a day with GitHub Actions

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