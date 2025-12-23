from flask import Flask, request, jsonify, make_response,render_template
from flask import Blueprint

from flask_pymongo import PyMongo
from keycloak_auth import keycloak_protect
import os
import uuid
import random

blueprint = Blueprint('blueprint', __name__)

app = Flask(__name__)

# Root health check (for Kubernetes)
@app.get("/")
def root():
    return "VotingService API running"

@app.route("/private")
@keycloak_protect
def private():
    return jsonify({
        "message": "Protected route",
        "user": request.user
    })

@app.route("/public")
def public():
    return {"message": "Public route"}

app.register_blueprint(blueprint)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)