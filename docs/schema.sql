-- Table to store polls
CREATE TABLE polls (
    id SERIAL PRIMARY KEY,
    uuid UUID UNIQUE NOT NULL DEFAULT gen_random_uuid(),
    meeting_id VARCHAR(255) NOT NULL,
    poll_type VARCHAR(50) NOT NULL CHECK (poll_type IN ('single', 'ranked')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
);

-- Table to store poll options
CREATE TABLE poll_options (
    id SERIAL PRIMARY KEY,
    poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
    option_value VARCHAR(255) NOT NULL,
    option_order INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(poll_id, option_value),
    UNIQUE(poll_id, option_order)
);

-- Table to store votes
CREATE TABLE votes (
    id SERIAL PRIMARY KEY,
    poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
    user_id VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(poll_id, user_id)
);

-- Table to store vote selections (supports both single and ranked voting)
CREATE TABLE vote_selections (
    id SERIAL PRIMARY KEY,
    vote_id INTEGER NOT NULL REFERENCES votes(id) ON DELETE CASCADE,
    poll_option_id INTEGER NOT NULL REFERENCES poll_options(id) ON DELETE CASCADE,
    rank_order INTEGER, -- NULL for single choice, 1,2,3...  for ranked choice
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(vote_id, poll_option_id),
    UNIQUE(vote_id, rank_order)
);
