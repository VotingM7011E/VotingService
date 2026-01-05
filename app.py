import os
import uuid
import random
import requests

from flask import Flask, request, jsonify, make_response, render_template
from flask import Blueprint
from flask_sqlalchemy import SQLAlchemy

from keycloak_auth import keycloak_protect, check_role
from models import Base, Poll, PollOption, Vote, VoteSelection
from mq import start_consumer, publish_event

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

def on_event(event: dict):
    # event envelope: {event_type, data, ...}
    et = event.get("event_type")
    data = event.get("data", {})

    if et == "voting.create":
        # IMPORTANT: we need app context for db.session
        with app.app_context():
            try:
                # expected payload: {"vote": {...}} OR just vote_data
                vote_data = data.get("vote") or data
                print(f"📥 Received voting.create event with data: {vote_data}")
                result = create_poll_from_vote_data(vote_data)
                print(f"✅ Successfully created poll: {result}")
            except Exception as e:
                print(f"❌ Failed to create poll from vote data: {e}")
                import traceback
                traceback.print_exc()

# Start consumer thread (after app exists)
start_consumer(
    queue=os.getenv("MQ_QUEUE", "voting-service"),
    bindings=os.getenv("MQ_BINDINGS", "voting.create").split(","),
    on_event=on_event,
)

# TODO: Should also verify meeting_uuid so it always matches an existing meeting
def create_poll_from_vote_data(vote_data: dict):
    # Validate required fields
    meeting_id = vote_data.get("meeting_id")
    poll_id = vote_data.get("poll_id")
    poll_type = vote_data.get("pollType")
    options = vote_data.get("options", [])

    if not meeting_id:
        raise ValueError("Missing 'meeting_id'")
    # poll_id is optional. If provided, we'll use it as the DB id, otherwise let the DB assign one.
    if poll_type not in ["single", "ranked"]:
        raise ValueError("Invalid 'pollType'. Must be 'single' or 'ranked'")
    if not options or len(options) < 2:
        raise ValueError("At least 2 options are required")

    # Determine expected_voters: prefer provided value, otherwise ask PermissionService
    expected_voters = vote_data.get("expected_voters")
    if expected_voters is None:
        try:
            permission_base = os.getenv(
                "PERMISSION_SERVICE_URL",
                "http://permission-service.permission-service-dev.svc.cluster.local",
            )
            url = f"{permission_base}/meetings/{meeting_id}/roles/vote/users"
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            users = resp.json()
            if isinstance(users, list):
                expected_voters = len(users)
        except requests.exceptions.RequestException:
            expected_voters = None

    poll = Poll(id=poll_id, meeting_id=meeting_id, poll_type=poll_type, expected_voters=expected_voters)
    db.session.add(poll)
    db.session.flush()  # Flush to get the auto-generated poll.id
    
    # Now poll.id is available (either provided or auto-generated)
    for index, option_value in enumerate(options):
        db.session.add(PollOption(
            poll_id=poll.id,  # Use poll.id instead of poll_id variable
            option_value=option_value,
            option_order=index,
        ))

    db.session.commit()

    # Publish a created event so originator (e.g., MotionService) can correlate
    try:
        event_data = {
            "poll_uuid": str(poll.uuid),
            "poll_id": getattr(poll, "id", None),
            "meeting_id": poll.meeting_id,
            "origin": vote_data.get("origin"),
        }
        publish_event("voting.created", event_data)
    except Exception:
        # best-effort publish
        pass

    return {
        "uuid": str(poll.uuid),
        "meeting_id": poll.meeting_id,
        "pollType": poll.poll_type,
        "options": options,
    }

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
    
    user_id = request.user["preferred_username"]
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
    # Expects 
    # data = {
    #  "vote": [
    #     "option1",
    #     "option2"
    #   ]
    # }
    
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
    
    selected = data.get("vote", [])
    
    if not selected: 
        return jsonify({"error": "No options selected"}), 400
    
    user_id = request.user["preferred_username"]
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
    # After committing the vote, check if poll is complete and notify creator via MQ
    try:
        total_votes = db.session.query(Vote).filter(Vote.poll_id == poll.id).count()
        expected = getattr(poll, "expected_voters", None)

        if expected is not None and total_votes >= expected and not getattr(poll, "completed", False):
            # mark completed and persist
            poll.completed = True
            db.session.add(poll)
            db.session.commit()

            # build simple results summary
            poll_options = db.session.query(PollOption).filter(PollOption.poll_id == poll.id).all()
            results = {}
            for option in poll_options:
                if poll.poll_type == "single":
                    count = db.session.query(VoteSelection).filter(
                        VoteSelection.poll_option_id == option.id
                    ).count()
                else:
                    count = db.session.query(VoteSelection).filter(
                        VoteSelection.poll_option_id == option.id,
                        VoteSelection.rank_order == 1
                    ).count()
                results[option.option_value] = count

            event_data = {
                "poll_id": str(poll.uuid),
                "meeting_id": poll.meeting_id,
                "results": results,
                "total_votes": total_votes,
            }

            try:
                publish_event("voting.completed", event_data)
            except Exception:
                # best-effort publish; don't fail the request
                pass

    except Exception:
        # If anything goes wrong while checking/completing, ignore to not break voting
        pass

    return jsonify({"message": "Vote recorded successfully"}), 200

# GET /polls/{poll_uuid}/vote - Check whether the authenticated user has voted
@blueprint.route("/polls/<poll_uuid>/vote", methods=["GET"])
@keycloak_protect
def has_voted(poll_uuid):

    try:
        poll_uuid_obj = uuid.UUID(poll_uuid)
    except ValueError:
        return jsonify({"error": "Invalid poll UUID"}), 400

    poll = db.session.query(Poll).filter(Poll.uuid == poll_uuid_obj).first()
    if not poll:
        return jsonify({"error": "Poll not found"}), 404

    user_id = request.user["preferred_username"]
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    if not check_role(request.user, poll.meeting_id, "vote"):
        return jsonify({"error": "Forbidden"}), 403

    existing_vote = db.session.query(Vote).filter(
        Vote.poll_id == poll.id,
        Vote.user_id == user_id
    ).first()

    return jsonify({"has_voted": existing_vote is not None}), 200


# GET /polls/{poll_uuid}/votes - Get vote count information
@blueprint.route("/polls/<poll_uuid>/votes", methods=["GET"])
@keycloak_protect
def get_vote_count(poll_uuid):

    try: 
        poll_uuid_obj = uuid.UUID(poll_uuid)
    except ValueError: 
        return jsonify({"error": "Invalid poll UUID"}), 400
    
    poll = db.session.query(Poll).filter(Poll.uuid == poll_uuid_obj).first()
    
    if not poll:
        return jsonify({"error": "Poll not found"}), 404
    
    user_id = request.user["preferred_username"]
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
    
    # Get eligible voters from permission-service (role="vote").
    # If the permission service is unavailable, fall back to counting
    # already cast votes to avoid failing the endpoint.
    try:
        permission_base = os.getenv(
            "PERMISSION_SERVICE_URL",
            "http://permission-service.permission-service-dev.svc.cluster.local",
        )
        url = f"{permission_base}/meetings/{poll.meeting_id}/roles/vote/users"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        users = resp.json()
        if isinstance(users, list):
            eligible_voters = len(users)
        else:
            eligible_voters = 0
    except requests.exceptions.RequestException:
        # Graceful fallback: count unique users who have already voted
        eligible_voters = db.session.query(Vote).filter(
            Vote.poll_id == poll.id
        ).count()
    
    return jsonify({
        "eligible_voters": eligible_voters,
        "votes": votes
    }), 200

app.register_blueprint(blueprint)

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)