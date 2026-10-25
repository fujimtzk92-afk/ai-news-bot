import os                   
import tweepy               
from dotenv import load_dotenv  

load_dotenv()               


client = tweepy.Client(
    consumer_key=os.getenv("X_CONSUMER_KEY"),
    consumer_secret=os.getenv("X_CONSUMER_SECRET"),
    access_token=os.getenv("X_ACCESS_TOKEN"),
    access_token_secret=os.getenv("X_ACCESS_TOKEN_SECRET"),
)

client.create_tweet(text="Hello from my bot")  
print("投稿できた") 