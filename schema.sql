-- Reference DDL. The app also auto-creates this via SQLAlchemy on startup.
CREATE TABLE IF NOT EXISTS responses (
    id         BIGINT       NOT NULL AUTO_INCREMENT,
    genre      VARCHAR(20)  NOT NULL,
    created_at DATETIME(6)  NOT NULL,
    payload    JSON         NOT NULL,
    PRIMARY KEY (id),
    KEY ix_responses_genre_created (genre, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
