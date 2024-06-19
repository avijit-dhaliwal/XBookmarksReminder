from flask import Flask, redirect, url_for, session, request, render_template
import tweepy
import os
from transformers import pipeline
from email_validator import validate_email, EmailNotValidError
import smtplib
from email.mime.text import MIMEText
import schedule
import time
from threading import Thread
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
db = SQLAlchemy(app)

API_KEY = ''
API_SECRET_KEY = ''
CALLBACK_URL = ''

auth = tweepy.OAuth1UserHandler(API_KEY, API_SECRET_KEY, CALLBACK_URL)

summarizer = pipeline("summarization")

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    twitter_id = db.Column(db.String(64), unique=True, nullable=False)
    username = db.Column(db.String(64), nullable=False)
    access_token = db.Column(db.String(255), nullable=False)
    access_token_secret = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255))
    bookmarks = db.relationship('Bookmark', backref='user', lazy=True)

class Bookmark(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tweet_id = db.Column(db.String(64), unique=True, nullable=False)
    text = db.Column(db.Text, nullable=False)
    summary = db.Column(db.Text, nullable=False)
    opened = db.Column(db.Boolean, default=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/login')
def login():
    redirect_url = auth.get_authorization_url()
    session['request_token'] = auth.request_token
    return redirect(redirect_url)

@app.route('/callback')
def callback():
    request_token = session.pop('request_token', None)
    auth.request_token = request_token
    auth.get_access_token(request.args.get('oauth_verifier'))
    api = tweepy.API(auth)
    user_info = api.me()._json
    user = User.query.filter_by(twitter_id=user_info['id_str']).first()
    if not user:
        user = User(
            twitter_id=user_info['id_str'],
            username=user_info['screen_name'],
            access_token=auth.access_token,
            access_token_secret=auth.access_token_secret
        )
        db.session.add(user)
    else:
        user.access_token = auth.access_token
        user.access_token_secret = auth.access_token_secret
    db.session.commit()
    session['user_id'] = user.id
    return redirect(url_for('bookmarks'))

@app.route('/bookmarks', methods=['GET', 'POST'])
def bookmarks():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))
    
    user = User.query.get(user_id)
    auth.set_access_token(user.access_token, user.access_token_secret)
    api = tweepy.API(auth)

    if request.method == 'POST':
        email = request.form['email']
        try:
            v = validate_email(email)
            user.email = v["email"]
            db.session.commit()
        except EmailNotValidError as e:
            return str(e)

    tweets = api.favorites(count=20)
    for tweet in tweets:
        if not Bookmark.query.filter_by(tweet_id=tweet.id_str).first():
            new_bookmark = Bookmark(
                tweet_id=tweet.id_str,
                text=tweet.text,
                summary=summarize_text(tweet.text),
                user_id=user.id
            )
            db.session.add(new_bookmark)
            db.session.commit()

    bookmarks = Bookmark.query.filter_by(user_id=user.id).all()
    return render_template('bookmarks.html', bookmarks=bookmarks, email=user.email)

def summarize_text(text):
    summary = summarizer(text, max_length=50, min_length=25, do_sample=False)
    return summary[0]['summary_text']

def send_email(to_email, subject, body):
    from_email = "your_email@example.com"
    password = "your_email_password"

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = from_email
    msg['To'] = to_email

    with smtplib.SMTP_SSL('smtp.example.com', 465) as server:
        server.login(from_email, password)
        server.sendmail(from_email, to_email, msg.as_string())

def check_unopened_bookmarks():
    users = User.query.all()
    for user in users:
        if user.email:
            unopened = Bookmark.query.filter_by(user_id=user.id, opened=False).all()
            if unopened:
                email_body = '\n\n'.join([
                    f"Original: {bookmark.text}\nSummary: {bookmark.summary}"
                    for bookmark in unopened
                ])
                send_email(user.email, "Unopened Twitter Bookmarks", email_body)

def run_scheduler():
    schedule.every().day.at("12:00").do(check_unopened_bookmarks)
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == '__main__':
    db.create_all()
    Thread(target=run_scheduler).start()
    app.run(debug=True)
