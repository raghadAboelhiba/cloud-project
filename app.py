import requests
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask import g
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
import os, uuid, requests, boto3
from flask import app
from werkzeug.utils import secure_filename



app =Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "replace_this_with_a_random_string")

DB_URI= "mysql+mysqlconnector://admin:DNehlhs3pb1hXI2wbmqA@database-1.cjemq4cu6yc9.ap-south-1.rds.amazonaws.com/rj"

app.config['SQLALCHEMY_DATABASE_URI']= DB_URI

db= SQLAlchemy(app)
bcrypt = Bcrypt(app)

# S3 configuration
S3_BUCKET = os.environ.get("AWS_S3_BUCKET")
S3_REGION = os.environ.get("AWS_S3_REGION")
s3 = boto3.client("s3", region_name=S3_REGION)

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(100))

class Book(db.Model):
    __tablename__ = 'books'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    author = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    cover = db.Column(db.String(255))

class Review(db.Model):
    __tablename__ = 'reviews'  
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    book_id = db.Column(db.Integer, db.ForeignKey('books.id'), nullable=False)
    rating = db.Column(db.Integer)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, server_default=db.func.now())


import uuid
import requests
from io import BytesIO
import traceback

def sync_trending_books_on_startup():
    """
    1) Fetch top-10 bestsellers from Google Books
    2) Download each thumbnail into memory
    3) Upload to S3 under covers/trending/<uuid>.jpg
    4) Create or update Book.cover with the S3 URL
    """
    try:
        resp = requests.get(
            "https://www.googleapis.com/books/v1/volumes",
            params={"q": "bestseller", "langRestrict": "en", "maxResults": 10}
        )
        resp.raise_for_status()
    except Exception as e:
        app.logger.error("Failed to fetch trending list: %s\n%s",
                         e, traceback.format_exc())
        return

    for item in resp.json().get("items", []):
        info        = item.get("volumeInfo", {})
        title       = info.get("title", "No title")
        authors     = info.get("authors", ["Unknown"])
        description = info.get("description", "No description available.")
        thumb_url   = info.get("imageLinks", {}).get("thumbnail", "")
        author_str  = ", ".join(authors)

        # Skip if the book already exists and has an S3 URL
        existing = Book.query.filter_by(title=title, author=author_str).first()
        if existing and existing.cover and existing.cover.startswith(f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/"):
            continue

        cover_url = None
        if thumb_url:
            try:
                # Download into raw bytes
                img_resp = requests.get(thumb_url, headers={"User-Agent":"Mozilla/5.0"})
                img_resp.raise_for_status()
                img_bytes = img_resp.content

                # Prepare an in-memory file
                img_file = BytesIO(img_bytes)
                key = f"covers/trending/{uuid.uuid4().hex}.jpg"

                # Upload to S3
                s3.upload_fileobj(
                    Fileobj=img_file,
                    Bucket=S3_BUCKET,
                    Key=key,
                    ExtraArgs={
                        "ContentType": "image/jpeg"
                    }
)


                cover_url = f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{key}"
                app.logger.info("Uploaded '%s' → %s", title, cover_url)

            except Exception as e:
                app.logger.error(
                    "Sync failed for %s:\n%s\n%s",
                    thumb_url, e, traceback.format_exc()
                )
                # skip updating this book
                continue

        # Create or update the Book record
        if not existing:
            book = Book(
                title=title,
                author=author_str,
                description=description,
                cover=cover_url
            )
            db.session.add(book)
        else:
            existing.cover = cover_url
            db.session.add(existing)

    db.session.commit()




@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        email = request.form["email"]
        password = request.form["password"]

        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            flash("Username already taken.")
            return redirect(url_for("register"))

        hashed_password = bcrypt.generate_password_hash(password).decode("utf-8")
        new_user = User(username=username, email=email, password=hashed_password)

        db.session.add(new_user)
        db.session.commit()

        session["user_id"] = new_user.id
        session["username"] = new_user.username
        flash("Account created and logged in!")
        return redirect(url_for("index"))

    return render_template("register.html")

@app.route("/")
def index():
    all_books = Book.query.all()
    reviews_dict = {}
    for book in all_books:
        book_reviews = Review.query.filter_by(book_id=book.id).all()
        reviews_dict[book.id] = book_reviews

    return render_template("index.html", books=all_books, reviews_dict=reviews_dict, user=session.get("username"))
    #return("running")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        user = User.query.filter_by(username=username).first()

        if user and bcrypt.check_password_hash(user.password, password):
            session["user_id"] = user.id
            session["username"] = user.username
            flash("Logged in successfully!")
            return redirect(url_for("index"))
        else:
            flash("Invalid username or password.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("username", None)
    flash("You have been logged out.")
    return redirect(url_for("index"))

@app.route("/profile")
def profile():
    if "username" not in session:
        return redirect(url_for("login"))

    user = User.query.filter_by(username=session["username"]).first()
    user_reviews = Review.query.filter_by(user_id=user.id).all()
    all_books = Book.query.all()  

    return render_template("profile.html", reviews=user_reviews, books=all_books, user=session["username"])

@app.route("/review/<int:book_id>", methods=["GET", "POST"])
def review(book_id):
    if "user_id" not in session:
        flash("Please log in first.")
        return redirect(url_for("login"))

    book = Book.query.get_or_404(book_id)

    reviews = (
        db.session.query(Review, User)
        .join(User, Review.user_id == User.id)
        .filter(Review.book_id == book_id)
        .all()
    )

    if request.method == "POST":
        rating = int(request.form["rating"])
        comment = request.form["comment"]

        new_review = Review(
            user_id=session["user_id"],
            book_id=book.id,
            rating=rating,
            comment=comment
        )
        db.session.add(new_review)
        db.session.commit()
        flash("Review submitted!")
        return redirect(url_for("review", book_id=book_id))

    return render_template("review.html", book=book, reviews=reviews, user=session["username"])

@app.route("/search", methods=["GET", "POST"])
def search():
    results = []
    if request.method == "POST":
        query = request.form.get("query")
        if query:
            data = requests.get(
                f"https://www.googleapis.com/books/v1/volumes?q={query}"
            ).json()
            for item in data.get("items", []):
                vol = item.get("volumeInfo", {})
                results.append({
                    "title": vol.get("title", "N/A"),
                    "author": ", ".join(vol.get("authors", [])),
                    "description": vol.get("description", "No description available."),
                    "cover": vol.get("imageLinks", {}).get("thumbnail", "")
                })
    return render_template("search.html", results=results, user=session.get("username"))

@app.route("/add_and_review", methods=["POST"])
def add_and_review():
    if "user_id" not in session:
        flash("Please log in first.")
        return redirect(url_for("login"))

    title       = request.form["title"]
    author      = request.form["author"]
    description = request.form["description"]

    # 1) Did the user upload their own file?
    file = request.files.get("cover_file")
    if file and file.filename:
        filename = secure_filename(file.filename)
        key = f"covers/{uuid.uuid4().hex}_{filename}"
        s3.upload_fileobj(
            Fileobj=file,
            Bucket=S3_BUCKET,
            Key=key,
            ExtraArgs={"ACL":"public-read", "ContentType": file.content_type}
        )
        cover_url = f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{key}"
    else:
        # 2) No file: fall back to the hidden Google thumbnail field
        cover_url = request.form.get("cover")

    # Create or fetch the Book record
    book = Book.query.filter_by(title=title, author=author).first()
    if not book:
        book = Book(
            title=title,
            author=author,
            description=description,
            cover=cover_url
        )
        db.session.add(book)
        db.session.commit()

    return redirect(url_for("review", book_id=book.id))


if __name__ == "__main__":
    with app.app_context():
        #  One-time cleanup
        for book in Book.query.all():
            if not book.cover or not book.cover.strip().lower().startswith("http"):
                print(f"Deleting: {book.title}")
                db.session.delete(book)
        db.session.commit()

        # Sync trending books
        sync_trending_books_on_startup()

    #  Run the app
    app.run(host="0.0.0.0", port=8000, debug=True)

