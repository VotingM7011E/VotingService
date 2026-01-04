from sqlalchemy import (
    Column, Integer, String, ForeignKey, TIMESTAMP, func, CheckConstraint, UniqueConstraint, text
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, declarative_base
import sqlalchemy

Base = declarative_base()

class Poll(Base):
    __tablename__ = "polls"

    id = Column(Integer, primary_key=True)
    uuid = Column(UUID(as_uuid=True), unique=True, nullable=False, server_default=text("gen_random_uuid()"))
    meeting_id = Column(String(255), nullable=False)
    poll_type = Column(String(50), nullable=False)
    # Number of eligible voters when the poll was created. May be null if unknown.
    expected_voters = Column(Integer, nullable=True)
    # Whether the poll has been completed and notification sent.
    completed = Column(sqlalchemy.Boolean, nullable=False, server_default=text("false"))
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.current_timestamp())

    __table_args__ = (
        CheckConstraint("poll_type IN ('single', 'ranked')", name="poll_type_check"),
    )

    options = relationship("PollOption", back_populates="poll", cascade="all, delete-orphan")
    votes = relationship("Vote", back_populates="poll", cascade="all, delete-orphan")


class PollOption(Base):
    __tablename__ = "poll_options"

    id = Column(Integer, primary_key=True)
    poll_id = Column(Integer, ForeignKey('polls.id', ondelete="CASCADE"), nullable=False)
    option_value = Column(String(255), nullable=False)
    option_order = Column(Integer, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.current_timestamp())

    poll = relationship("Poll", back_populates="options")
    selections = relationship("VoteSelection", back_populates="option", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint('poll_id', 'option_value', name='uix_polloption_pollid_value'),
        UniqueConstraint('poll_id', 'option_order', name='uix_polloption_pollid_order'),
    )


class Vote(Base):
    __tablename__ = "votes"

    id = Column(Integer, primary_key=True)
    poll_id = Column(Integer, ForeignKey('polls.id', ondelete="CASCADE"), nullable=False)
    user_id = Column(String(255), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.current_timestamp())

    poll = relationship("Poll", back_populates="votes")
    selections = relationship("VoteSelection", back_populates="vote", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint('poll_id', 'user_id', name='uix_votes_pollid_userid'),
    )


class VoteSelection(Base):
    __tablename__ = "vote_selections"

    id = Column(Integer, primary_key=True)
    vote_id = Column(Integer, ForeignKey('votes.id', ondelete="CASCADE"), nullable=False)
    poll_option_id = Column(Integer, ForeignKey('poll_options.id', ondelete="CASCADE"), nullable=False)
    rank_order = Column(Integer, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.current_timestamp())

    vote = relationship("Vote", back_populates="selections")
    option = relationship("PollOption", back_populates="selections")

    __table_args__ = (
        UniqueConstraint('vote_id', 'poll_option_id', name='uix_voteselections_voteid_optionid'),
        UniqueConstraint('vote_id', 'rank_order', name='uix_voteselections_voteid_rank'),
    )