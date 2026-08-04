"""Local development entrypoint. Production runs gunicorn against app:create_app.

Loads .env explicitly. The flask CLI does that through python-dotenv, but a
plain `python run.py` does not, so create_app would see an empty APP_ENV and
refuse to boot.
"""
from dotenv import load_dotenv

from app import create_app

load_dotenv()

app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
