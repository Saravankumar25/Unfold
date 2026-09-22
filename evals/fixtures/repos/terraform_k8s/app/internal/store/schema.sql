CREATE TABLE IF NOT EXISTS links (
    code       VARCHAR(16) PRIMARY KEY,
    url        TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE clicks (
    id         BIGSERIAL PRIMARY KEY,
    code       VARCHAR(16) NOT NULL REFERENCES links(code),
    clicked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
