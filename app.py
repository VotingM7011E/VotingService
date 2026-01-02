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
    return "VotingService API running\n Poll count: {poll_count}".format(poll_count = poll_count)

# POST /polls/ - Create a new poll
# Should be moved to consume RabbitMQ messages.
# Should also verify meeting_uuid so it always matches an existing meeting
@blueprint.route("/polls/", methods=["POST"])
def create_poll():
    data = request.get_json()
    
    if not data or "vote" not in data: 
        return jsonify({"error": "Missing 'vote' in request body"}), 400
    
    vote_data = data["vote"]
    
    # Validate required fields
    meeting_id = vote_data.get("meeting_id")
    poll_type = vote_data.get("pollType")
    options = vote_data.get("options", [])
    
    if not meeting_id: 
        return jsonify({"error": "Missing 'meeting_id'"}), 400
    
    if poll_type not in ["single", "ranked"]:
        return jsonify({"error": "Invalid 'pollType'. Must be 'single' or 'ranked'"}), 400
    
    if not options or len(options) < 2:
        return jsonify({"error": "At least 2 options are required"}), 400
    
    # Create the poll
    poll = Poll(
        meeting_id=meeting_id,
        poll_type=poll_type
    )
    db.session.add(poll)
    db.session.flush()  # Get the poll ID
    
    # Create poll options
    for index, option_value in enumerate(options):
        poll_option = PollOption(
            poll_id=poll.id,
            option_value=option_value,
            option_order=index
        )
        db.session.add(poll_option)
    
    db.session.commit()
    
    return jsonify({
        "uuid": str(poll.uuid),
        "meeting_id": poll.meeting_id,
        "pollType": poll.poll_type,
        "options": options
    }), 200


# GET /polls/{poll_uuid}/ - Get poll information
@blueprint.route("/polls/<poll_uuid>/", methods=["GET"])
@keycloak_protect
def get_poll(poll_uuid):
    try:
        poll_uuid_obj = uuid.UUID(poll_uuid)
    except ValueError: 
        return jsonify({"error": "Invalid poll UUID"}), 400
    
    poll = db.session.query(Poll).filter(Poll.uuid == poll_uuid_obj).first()
    
    if not poll: 
        return jsonify({"error": "Poll not found"}), 404
    
    user_id = request.user.preferred_username
    if not user_id: 
        return jsonify({"error": "Unauthorized"}), 401

    if not check_role(request.user, poll.meeting_id, "view"):
        return jsonify({"error": "Forbidden"}), 403

    # Get poll options ordered by option_order
    options = db.session.query(PollOption).filter(
        PollOption.poll_id == poll.id
    ).order_by(PollOption.option_order).all()
    
    option_values = [opt.option_value for opt in options]
    
    return jsonify({
        "meeting_id": poll.meeting_id,
        "pollType":  poll.poll_type,
        "options": option_values
    }), 200


# POST /polls/{poll_uuid}/vote - Vote on a poll
# Probably out of scope but should votes be anonymized?
@blueprint.route("/polls/<poll_uuid>/vote", methods=["POST"])
@keycloak_protect
def add_vote(poll_uuid):
    try:
        poll_uuid_obj = uuid.UUID(poll_uuid)
    except ValueError:
        return jsonify({"error": "Invalid poll UUID"}), 400
    
    poll = db.session.query(Poll).filter(Poll.uuid == poll_uuid_obj).first()
    
    if not poll:
        return jsonify({"error":  "Poll not found"}), 404
    
    data = request.get_json()
    if not data or "vote" not in data: 
        return jsonify({"error": "Missing 'vote' in request body"}), 400
    
    vote_data = data["vote"]
    selected = vote_data.get("selected", [])
    
    if not selected: 
        return jsonify({"error": "No options selected"}), 400
    
    user_id = request.user.preferred_username
    if not user_id: 
        return jsonify({"error": "Unauthorized"}), 401

    if not check_role(request.user, poll.meeting_id, "vote"):
        return jsonify({"error": "Forbidden"}), 403

    # Get valid poll options
    poll_options = db.session.query(PollOption).filter(
        PollOption.poll_id == poll.id
    ).all()
    option_map = {opt.option_value: opt.id for opt in poll_options}
    
    # Validate selected options
    for option in selected:
        if option not in option_map:
            return jsonify({"error": f"Invalid option: {option}"}), 400
    
    # Validate based on poll type
    if poll.poll_type == "single" and len(selected) > 1:
        return jsonify({"error": "Single choice poll allows only one selection"}), 400
    
    # TODO: Verify so that for ranked votes you need to rank all options

    # Check if user already voted
    existing_vote = db.session.query(Vote).filter(
        Vote.poll_id == poll.id,
        Vote.user_id == user_id
    ).first()
    
    if existing_vote:
        return jsonify({"error":  "User has already voted on this poll"}), 409
    
    # Create the vote
    vote = Vote(
        poll_id=poll.id,
        user_id=user_id
    )
    db.session.add(vote)
    db.session.flush()
    
    # Create vote selections
    for index, option_value in enumerate(selected):
        poll_option_id = option_map[option_value]
        rank_order = index + 1 if poll.poll_type == "ranked" else None
        
        vote_selection = VoteSelection(
            vote_id=vote.id,
            poll_option_id=poll_option_id,
            rank_order=rank_order
        )
        db.session.add(vote_selection)
    
    db.session.commit()
    
    return jsonify({"message": "Vote recorded successfully"}), 200


# GET /polls/{poll_uuid}/vote - Get vote count information
@blueprint.route("/polls/<poll_uuid>/vote", methods=["GET"])
@keycloak_protect
def get_vote_count(poll_uuid):
    try: 
        poll_uuid_obj = uuid.UUID(poll_uuid)
    except ValueError: 
        return jsonify({"error": "Invalid poll UUID"}), 400
    
    poll = db.session.query(Poll).filter(Poll.uuid == poll_uuid_obj).first()
    
    if not poll:
        return jsonify({"error": "Poll not found"}), 404
    
    user_id = request.user.preferred_username
    if not user_id: 
        return jsonify({"error": "Unauthorized'"}), 401

    if not check_role(request.user, poll.meeting_id, "vote"):
        return jsonify({"error": "Forbidden"}), 403

    # Get all poll options
    poll_options = db.session.query(PollOption).filter(
        PollOption.poll_id == poll.id
    ).order_by(PollOption.option_order).all()
    
    # Count votes for each option
    votes = {}
    for option in poll_options:
        if poll.poll_type == "single":
            # For single choice, count all selections
            count = db.session.query(VoteSelection).filter(
                VoteSelection.poll_option_id == option.id
            ).count()
        else:
            # For ranked choice, count first-choice votes (rank_order = 1)
            count = db.session.query(VoteSelection).filter(
                VoteSelection.poll_option_id == option.id,
                VoteSelection.rank_order == 1
            ).count()
        votes[option.option_value] = count
    
    # Get total number of voters (unique users who voted)
    # TODO: this is wrong and needs to be checked with permissionservice.
    eligible_voters = db.session.query(Vote).filter(
        Vote.poll_id == poll.id
    ).count()
    
    return jsonify({
        "eligible_voters": eligible_voters,
        "votes": votes
    }), 200

# @blueprint.route("/private")
# @keycloak_protect
# def private():
#     return jsonify({
#         "message": "Protected route",
#         "user": request.user
#     })
# 
# @blueprint.route("/public")
# def public():
#     return {"message": "Public route"}

app.register_blueprint(blueprint)

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)