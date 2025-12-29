import os
import uuid
import random

from flask import Flask, request, jsonify, make_response, render_template
from flask import Blueprint
from flask_sqlalchemy import SQLAlchemy

from keycloak_auth import keycloak_protect
from models import Base, Poll, PollOption, Vote, VoteSelection

db = SQLAlchemy(model_class=Base)

blueprint = Blueprint('blueprint', __name__)

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URI")

db.init_app(app)

@blueprint.after_request 
def after_request(response):
    header = response.headers
    header['Access-Control-Allow-Origin'] = '*'
    header['Access-Control-Allow-Headers'] = "*"
    header['Access-Control-Allow-Methods'] = "*"
    # Other headers can be added here if needed
    return response

# Root health check (for Kubernetes)
@blueprint.get("/")
def root():
    poll_count = db.session.query(Poll).count()
    return "VotingService API running\n Poll count: {poll_count}"

@blueprint.route("/private")
@keycloak_protect
def private():
    return jsonify({
        "message": "Protected route",
        "user": request.user
    })

@blueprint.route("/public")
def public():
    return {"message": "Public route"}

app.register_blueprint(blueprint)

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)