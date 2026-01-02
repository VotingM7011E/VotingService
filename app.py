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
def get_poll(poll_uuid):
    try:
        poll_uuid_obj = uuid.UUID(poll_uuid)
    except ValueError: 
        return jsonify({"error": "Invalid poll UUID"}), 400
    
    poll = db.session.query(Poll).filter(Poll.uuid == poll_uuid_obj).first()
    
    if not poll: 
        return jsonify({"error": "Poll not found"}), 404
    
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
# TODO: Add keycloak verification of username and voting permission
@blueprint.route("/polls/<poll_uuid>/vote", methods=["POST"])
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
    
    # Get user_id from request (could come from auth header or request body)
    # For now, we'll expect it in the request or generate one
    user_id = vote_data.get("user_id") #TODO: yeeta denna rad, endast keycloak
    if not user_id:
        # Try to get from auth header if available
        auth_header = request.headers.get("Authorization")
        if auth_header:
            try:
                from keycloak_auth import verify_token
                parts = auth_header.split()
                if len(parts) == 2:
                    token = parts[1]
                    user_info = verify_token(token)
                    user_id = user_info.get("sub")
            except:
                pass
        
        if not user_id: 
            return jsonify({"error": "Missing 'user_id'"}), 400
    
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
# TODO: only allowed for manager role?
@blueprint.route("/polls/<poll_uuid>/vote", methods=["GET"])
def get_vote_count(poll_uuid):
    try: 
        poll_uuid_obj = uuid.UUID(poll_uuid)
    except ValueError: 
        return jsonify({"error": "Invalid poll UUID"}), 400
    
    poll = db.session.query(Poll).filter(Poll.uuid == poll_uuid_obj).first()
    
    if not poll:
        return jsonify({"error": "Poll not found"}), 404
    
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