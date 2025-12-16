
-- Table: positions
CREATE TABLE polls (
    poll_id         SERIAL PRIMARY KEY,
    meeting_id      INT NOT NULL,
    poll_name       VARCHAR(255) NOT NULL,
    is_open         BOOLEAN NOT NULL DEFAULT TRUE,
);

-- Table: nominations
CREATE TABLE votes (
    poll_id         INT NOT NULL,
    username        VARCHAR(255) NOT NULL,
    accepted        BOOLEAN NOT NULL DEFAULT FALSE,

    PRIMARY KEY (poll_id, username),

    CONSTRAINT fk_votes_polls
        FOREIGN KEY (poll_id)
            REFERENCES polls(poll_id)
            ON DELETE CASCADE
);